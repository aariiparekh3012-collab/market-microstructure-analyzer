"""Latest-snapshot cache. Uses Redis if reachable, else a threadsafe dict."""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict
from typing import Any

from ..models import OrderBookSnapshot

log = logging.getLogger(__name__)


class _InMemoryCache:
    def __init__(self) -> None:
        self._d: dict[str, Any] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._d[key] = value

    def get(self, key: str) -> Any | None:
        with self._lock:
            return self._d.get(key)


class StateCache:
    def __init__(self, redis_url: str | None = None) -> None:
        self._impl: Any
        self._is_redis = False
        if redis_url:
            try:
                import redis
                self._impl = redis.Redis.from_url(redis_url, decode_responses=True)
                self._impl.ping()
                self._is_redis = True
                log.info("StateCache using Redis at %s", redis_url)
            except Exception as e:
                log.warning("Redis unreachable (%s) — falling back to in-memory cache", e)
                self._impl = _InMemoryCache()
        else:
            self._impl = _InMemoryCache()

    @staticmethod
    def _snap_key(symbol: str) -> str:
        return f"ob:{symbol}"

    def put_snapshot(self, snap: OrderBookSnapshot) -> None:
        payload = json.dumps(_serialize_snapshot(snap))
        self._impl.set(self._snap_key(snap.symbol), payload)

    def get_snapshot_json(self, symbol: str) -> str | None:
        return self._impl.get(self._snap_key(symbol))


def _serialize_snapshot(snap: OrderBookSnapshot) -> dict:
    d = asdict(snap)
    d["ts"] = snap.ts.isoformat()
    return d
