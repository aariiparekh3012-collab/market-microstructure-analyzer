"""Background streamer: pulls from DataSource, feeds analytics, fans out to WS clients."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from contextlib import suppress
from dataclasses import asdict
from typing import Any

from ..analytics.engine import Engine
from ..config import settings
from ..ingestion.factory import make_source
from ..models import Anomaly, OrderBookSnapshot
from ..storage.state_cache import StateCache, _serialize_snapshot
from ..storage.tick_store import TickStore

log = logging.getLogger(__name__)


class Streamer:
    def __init__(self) -> None:
        self.engine = Engine()
        self.cache = StateCache(settings.redis_url)
        self.tick_store = TickStore(settings.tick_store_dir, settings.parquet_roll_minutes)
        self._book_subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._metric_subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._alert_subs: set[asyncio.Queue] = set()
        self._recent_alerts: list[Anomaly] = []
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="streamer")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self.tick_store.flush()

    def subscribe_book(self, symbol: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._book_subs[symbol].add(q)
        return q

    def unsubscribe_book(self, symbol: str, q: asyncio.Queue) -> None:
        self._book_subs[symbol].discard(q)

    def subscribe_metrics(self, symbol: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._metric_subs[symbol].add(q)
        return q

    def unsubscribe_metrics(self, symbol: str, q: asyncio.Queue) -> None:
        self._metric_subs[symbol].discard(q)

    def subscribe_alerts(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._alert_subs.add(q)
        return q

    def unsubscribe_alerts(self, q: asyncio.Queue) -> None:
        self._alert_subs.discard(q)

    def recent_alerts(self, limit: int = 100) -> list[dict]:
        return [_anomaly_to_dict(a) for a in self._recent_alerts[-limit:]]

    def volume_profile(self, symbol: str) -> dict:
        return {str(k): v for k, v in self.engine.volume_profile(symbol).items()}

    async def _run(self) -> None:
        source = make_source()
        log.info("Streamer starting with source=%s symbols=%s", source.name, settings.symbol_list)
        try:
            async for snap in source.stream(settings.symbol_list):
                self._handle(snap)
        except asyncio.CancelledError:
            log.info("Streamer cancelled")
            raise
        except Exception:
            log.exception("Streamer crashed")
            raise

    def _handle(self, snap: OrderBookSnapshot) -> None:
        self.tick_store.append(snap)
        self.cache.put_snapshot(snap)
        metrics, anomalies = self.engine.process(snap)
        _broadcast(self._book_subs.get(snap.symbol, set()), _serialize_snapshot(snap))
        _broadcast(self._metric_subs.get(snap.symbol, set()), metrics)
        for a in anomalies:
            self._recent_alerts.append(a)
            if len(self._recent_alerts) > 500:
                self._recent_alerts = self._recent_alerts[-500:]
            _broadcast(self._alert_subs, _anomaly_to_dict(a))


def _anomaly_to_dict(a: Anomaly) -> dict:
    d = asdict(a)
    d["ts"] = a.ts.isoformat()
    return d


def _broadcast(subs: set[asyncio.Queue], payload: Any) -> None:
    if not subs:
        return
    msg = json.dumps(payload, default=str)
    for q in list(subs):
        if q.full():
            with suppress(Exception):
                q.get_nowait()
        with suppress(Exception):
            q.put_nowait(msg)


streamer = Streamer()
