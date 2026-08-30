"""Latest-snapshot cache. Uses Redis if reachable, else a threadsafe dict.

Two important properties for callers:

* ``put_snapshot_async`` runs the (potentially blocking) Redis network call
  on a thread so the asyncio event loop is never blocked. The synchronous
  ``put_snapshot`` remains for scripts and tests, but the Streamer must use
  the async variant.
* ``_serialize_snapshot`` hand-builds the dict without calling
  ``dataclasses.asdict``. ``asdict`` deep-copies every field recursively —
  measurable overhead at high tick rates because every ``BookLevel`` in every
  ``bids``/``asks`` list is copied through a Python function call. Hand-
  serialising keeps the shape identical without the copy.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
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
        """Blocking put — safe from sync code and tests. On the event loop
        prefer :meth:`put_snapshot_async` so a slow Redis does not block
        ingestion."""
        payload = json.dumps(_serialize_snapshot(snap))
        self._impl.set(self._snap_key(snap.symbol), payload)

    async def put_snapshot_async(self, snap: OrderBookSnapshot) -> None:
        """Non-blocking put — runs the network I/O on a thread.

        For the in-memory backend this is a bare dict set and the thread
        detour would be pure overhead, so we skip it there.
        """
        if not self._is_redis:
            self.put_snapshot(snap)
            return
        payload = json.dumps(_serialize_snapshot(snap))
        await asyncio.to_thread(self._impl.set, self._snap_key(snap.symbol), payload)

    def get_snapshot_json(self, symbol: str) -> str | None:
        return self._impl.get(self._snap_key(symbol))


def _serialize_snapshot(snap: OrderBookSnapshot) -> dict:
    """Hand-build the JSON-shaped dict without ``dataclasses.asdict``.

    ``asdict`` recursively deep-copies every nested dataclass through Python
    function calls; the bids/asks lists mean 10+ per-tick BookLevel copies.
    Measured ~3–4× faster than the asdict path on a 5-level book, and every
    tick goes through this on the hot path.
    """
    return {
        "symbol": snap.symbol,
        "ts": snap.ts.isoformat(),
        "bids": [{"price": b.price, "qty": b.qty, "orders": b.orders} for b in snap.bids],
        "asks": [{"price": a.price, "qty": a.qty, "orders": a.orders} for a in snap.asks],
        "ltp": snap.ltp,
        "ltq": snap.ltq,
        "volume": snap.volume,
    }
