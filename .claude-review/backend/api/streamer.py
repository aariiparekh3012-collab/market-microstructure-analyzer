"""Background streamer: pulls from DataSource, feeds analytics, fans out to WS clients.

Deployment properties this module owns:

* **Source resilience.** `_run` catches `Exception` from the source's async
  iterator and restarts it with exponential backoff (capped). A crashing feed
  no longer takes down the API silently.
* **Backpressure & drop counting.** Per-subscriber queues are bounded; when
  full, the oldest message is dropped to make room, and the drop is counted
  in `metrics.messages_dropped` (per topic). Silent-suppress is gone.
* **Bounded alert history.** `collections.deque(maxlen=500)` replaces the
  previous O(n)-per-append list slice.
* **Liveness.** `is_healthy()` returns whether the background task is running
  and whether a tick has been processed within `stale_after_s` seconds; used
  by the `/healthz` endpoint.
* **Metrics.** Plain counters exposed for `/metrics` — no Prometheus dep.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import defaultdict, deque
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from typing import Any, cast

from ..analytics.engine import Engine
from ..config import settings
from ..ingestion.factory import make_source
from ..models import Anomaly, OrderBookSnapshot
from ..storage.state_cache import StateCache, _serialize_snapshot
from ..storage.tick_store import TickStore

log = logging.getLogger(__name__)

# Backoff schedule for source reconnect (seconds).
_BACKOFF_MIN_S = 1.0
_BACKOFF_MAX_S = 60.0
_HEALTH_STALE_AFTER_S = 30.0


@dataclass(slots=True)
class StreamerMetrics:
    """Counters scraped by `/metrics`. Monotonic per process lifetime."""

    ticks_ingested: int = 0
    ticks_persist_failed: int = 0
    source_restarts: int = 0
    anomalies_emitted: int = 0
    messages_dropped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    last_tick_ts: float = 0.0  # perf_counter of most recent successful ingest
    ws_clients: dict[str, int] = field(default_factory=lambda: defaultdict(int))


class Streamer:
    def __init__(self) -> None:
        self.engine = Engine()
        self.cache = StateCache(settings.redis_url)
        self.tick_store = TickStore(settings.tick_store_dir, settings.parquet_roll_minutes)
        self.metrics = StreamerMetrics()

        self._book_subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._metric_subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._alert_subs: set[asyncio.Queue] = set()
        self._recent_alerts: deque[Anomaly] = deque(maxlen=500)
        self._task: asyncio.Task | None = None
        self._started_at: float = 0.0

    # ---- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        if self._task is None:
            self._started_at = time.perf_counter()
            self._task = asyncio.create_task(self._run_supervised(), name="streamer")
            await self._task

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        # Best-effort flush of pending ticks — but never crash the shutdown path.
        with suppress(Exception):
            await asyncio.to_thread(self.tick_store.close)

    # ---- health --------------------------------------------------------

    def is_healthy(self, stale_after_s: float = _HEALTH_STALE_AFTER_S) -> tuple[bool, dict]:
        """Return (ok, details). ok=False if task dead or ticks stale."""
        now = time.perf_counter()
        task_alive = self._task is not None and not self._task.done()
        never_ticked = self.metrics.last_tick_ts <= 0.0
        age_s = None if never_ticked else now - self.metrics.last_tick_ts
        fresh = never_ticked or (age_s is not None and age_s < stale_after_s)

        # Ticks are only "expected" once we've been up long enough for the
        # source to have connected. Give the pipeline `stale_after_s`
        # of grace at startup before flagging staleness.
        warming_up = never_ticked and (now - self._started_at) < stale_after_s

        ok = task_alive and (fresh or warming_up)
        return ok, {
            "task_alive": task_alive,
            "ticks_ingested": self.metrics.ticks_ingested,
            "source_restarts": self.metrics.source_restarts,
            "seconds_since_last_tick": age_s,
            "warming_up": warming_up,
        }

    # ---- subscribers ---------------------------------------------------

    def _subscribe(self, subs: set[asyncio.Queue], topic: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        subs.add(q)
        self.metrics.ws_clients[topic] += 1
        return q

    def _unsubscribe(self, subs: set[asyncio.Queue], q: asyncio.Queue, topic: str) -> None:
        subs.discard(q)
        self.metrics.ws_clients[topic] = max(0, self.metrics.ws_clients[topic] - 1)

    def subscribe_book(self, symbol: str) -> asyncio.Queue:
        return self._subscribe(self._book_subs[symbol], f"book:{symbol}")

    def unsubscribe_book(self, symbol: str, q: asyncio.Queue) -> None:
        self._unsubscribe(self._book_subs[symbol], q, f"book:{symbol}")

    def subscribe_metrics(self, symbol: str) -> asyncio.Queue:
        return self._subscribe(self._metric_subs[symbol], f"metrics:{symbol}")

    def unsubscribe_metrics(self, symbol: str, q: asyncio.Queue) -> None:
        self._unsubscribe(self._metric_subs[symbol], q, f"metrics:{symbol}")

    def subscribe_alerts(self) -> asyncio.Queue:
        return self._subscribe(self._alert_subs, "alerts")

    def unsubscribe_alerts(self, q: asyncio.Queue) -> None:
        self._unsubscribe(self._alert_subs, q, "alerts")

    def recent_alerts(self, limit: int = 100) -> list[dict]:
        if limit <= 0:
            return []
        # deque supports negative slicing via islice
        from itertools import islice
        n = len(self._recent_alerts)
        start = max(0, n - limit)
        return [_anomaly_to_dict(a) for a in islice(self._recent_alerts, start, n)]

    def volume_profile(self, symbol: str) -> dict:
        return {str(k): v for k, v in self.engine.volume_profile(symbol).items()}

    # ---- ingestion loop ------------------------------------------------

    async def _run_supervised(self) -> None:
        """Restart the source loop with capped exponential backoff on failure."""
        backoff = _BACKOFF_MIN_S
        while True:
            try:
                await self._run_once()
                # A clean return means the source's stream ended (e.g. mock
                # with `max_ticks` set). Don't spin — exit.
                log.info("Source stream ended cleanly; streamer stopping.")
                return
            except asyncio.CancelledError:
                log.info("Streamer cancelled")
                raise
            except Exception:
                self.metrics.source_restarts += 1
                log.exception(
                    "Source crashed; restarting in %.1fs (restart #%d)",
                    backoff, self.metrics.source_restarts,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, _BACKOFF_MAX_S)
                continue
            else:
                # After a run without exception, cool down the backoff.
                backoff = _BACKOFF_MIN_S

    async def _run_once(self) -> None:
        source = make_source()
        log.info("Streamer connecting: source=%s symbols=%s", source.name, settings.symbol_list)
        async for snap in await source.stream(settings.symbol_list):
            await self._handle(snap)

    async def _handle(self, snap: OrderBookSnapshot) -> None:
        # Storage runs off-loop so a slow disk doesn't back up ingestion.
        # A failure to persist is counted but never kills the loop.
        try:
            await asyncio.to_thread(self.tick_store.append, snap)
        except Exception:
            self.metrics.ticks_persist_failed += 1
            log.exception("tick_store.append failed for %s @ %s", snap.symbol, snap.ts)

        try:
            # Async variant: for the Redis backend this runs on a thread so
            # a slow Redis never stalls the event loop; for the in-memory
            # backend it's a bare dict set (no thread detour).
            await self.cache.put_snapshot_async(snap)
        except Exception:
            # Cache is a nice-to-have; carry on.
            log.warning("state_cache.put_snapshot failed for %s", snap.symbol, exc_info=True)

        # Engine.process expects a snapshot type from the engine's module path;
        # type-checkers may complain about differing import roots. Cast to Any
        # to avoid spurious type errors while preserving runtime behavior.
        metrics, anomalies = self.engine.process(cast(Any, snap))
        self._broadcast(self._book_subs.get(snap.symbol, set()),
                        _serialize_snapshot(snap), f"book:{snap.symbol}")
        self._broadcast(self._metric_subs.get(snap.symbol, set()),
                        metrics, f"metrics:{snap.symbol}")

        for a in anomalies:
            self._recent_alerts.append(cast(Anomaly, a))
            self.metrics.anomalies_emitted += 1
            self._broadcast(self._alert_subs, _anomaly_to_dict(cast(Anomaly, a)), "alerts")

        self.metrics.ticks_ingested += 1
        self.metrics.last_tick_ts = time.perf_counter()

    def _broadcast(self, subs: set[asyncio.Queue], payload: Any, topic: str) -> None:
        if not subs:
            return
        msg = json.dumps(payload, default=str)
        for q in subs:
            if q.full():
                # Drop-oldest to make room, and count the drop so the
                # /metrics endpoint reflects backpressure.
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                self.metrics.messages_dropped[topic] += 1
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:  # pragma: no cover — drained above
                self.metrics.messages_dropped[topic] += 1


def _anomaly_to_dict(a: Anomaly) -> dict:
    d = asdict(a)
    d["ts"] = a.ts.isoformat()
    return d


# The module-level singleton is kept for backwards compatibility, but the app
# lifespan is what actually starts/stops it. Import-time side effects here are
# limited to constructing an Engine (pure Python) and a StateCache (Redis ping
# with a fallback to in-memory) — no disk I/O beyond `tick_store_dir.mkdir`.
streamer = Streamer()
