"""Tests for the resilient Streamer — reconnect, backoff, metrics, health.

None of these hit Redis or disk (TickStore is stubbed with an in-memory
fake). They all run in <1s under pytest.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from backend.api import streamer as sm
from backend.api.streamer import Streamer, _anomaly_to_dict
from backend.models import Anomaly, BookLevel, OrderBookSnapshot


# --------------------------------------------------------------------------
# fixtures & helpers
# --------------------------------------------------------------------------


def _mk_snap(i: int = 0, symbol: str = "TEST") -> OrderBookSnapshot:
    """Deterministic tick generator."""
    return OrderBookSnapshot(
        symbol=symbol,
        ts=datetime(2026, 8, 30, 9, 15, 0, tzinfo=UTC) + timedelta(seconds=i * 0.2),
        bids=[BookLevel(100 - 0.05 * j, 100 + j * 10) for j in range(5)],
        asks=[BookLevel(100 + 0.05 * (j + 1), 100 + j * 10) for j in range(5)],
        ltp=100.0 + i * 0.01,
        ltq=50,
        volume=(i + 1) * 50,
    )


class _StubTickStore:
    """Drop-in for TickStore that never touches disk."""

    def __init__(self) -> None:
        self.appended: list[OrderBookSnapshot] = []
        self.close_called = 0

    def append(self, snap: OrderBookSnapshot) -> None:
        self.appended.append(snap)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.close_called += 1


@pytest.fixture
def stub_streamer(monkeypatch):
    """Streamer with in-memory cache and stubbed tick store."""
    # Force the in-memory cache path (redis_url=""); the Streamer's __init__
    # reads settings.redis_url so we set it before instantiation.
    monkeypatch.setattr(sm.settings, "redis_url", "")
    s = Streamer()
    s.tick_store = _StubTickStore()
    return s


class _CountingSource:
    """AsyncIterator that yields `total` ticks then returns."""

    name = "counting"

    def __init__(self, total: int) -> None:
        self.total = total

    async def stream(self, symbols) -> AsyncIterator[OrderBookSnapshot]:
        for i in range(self.total):
            yield _mk_snap(i)
            await asyncio.sleep(0)


class _CrashingSource:
    """Raises after emitting `raise_after` ticks. Increments an attempt
    counter each time stream() is invoked so the test can distinguish
    a reconnect from the first connect."""

    name = "crashing"

    def __init__(self, raise_after: int) -> None:
        self.raise_after = raise_after
        self.attempts = 0

    async def stream(self, symbols) -> AsyncIterator[OrderBookSnapshot]:
        self.attempts += 1
        for i in range(self.raise_after):
            yield _mk_snap(i)
            await asyncio.sleep(0)
        raise RuntimeError(f"synthetic crash on attempt {self.attempts}")


# --------------------------------------------------------------------------
# is_healthy() unit tests
# --------------------------------------------------------------------------


def test_is_healthy_warming_up_task_alive_ok(stub_streamer, monkeypatch):
    """Right after start, no ticks yet, task alive → healthy (warm-up)."""
    async def _noop():
        while True:
            await asyncio.sleep(60)

    async def _go():
        stub_streamer._started_at = _now()
        stub_streamer._task = asyncio.create_task(_noop())
        ok, det = stub_streamer.is_healthy()
        assert ok
        assert det["warming_up"] is True
        assert det["ticks_ingested"] == 0
        stub_streamer._task.cancel()

    asyncio.run(_go())


def test_is_healthy_task_dead_not_ok(stub_streamer):
    """A None/done task means not healthy no matter how fresh things look."""
    stub_streamer._task = None
    stub_streamer.metrics.last_tick_ts = _now()
    stub_streamer.metrics.ticks_ingested = 500
    ok, det = stub_streamer.is_healthy()
    assert not ok
    assert det["task_alive"] is False


def test_is_healthy_stale_ticks_not_ok(stub_streamer, monkeypatch):
    """A task that is running but hasn't produced a tick in >stale_after_s is unhealthy."""
    async def _noop():
        while True:
            await asyncio.sleep(60)

    async def _go():
        stub_streamer._task = asyncio.create_task(_noop())
        stub_streamer._started_at = _now() - 3600  # long past warm-up
        stub_streamer.metrics.ticks_ingested = 500
        stub_streamer.metrics.last_tick_ts = _now() - 60  # 60s stale
        ok, det = stub_streamer.is_healthy(stale_after_s=30.0)
        assert not ok
        assert det["seconds_since_last_tick"] > 30
        stub_streamer._task.cancel()

    asyncio.run(_go())


def test_is_healthy_fresh_ticks_ok(stub_streamer):
    """Task alive, tick within the last second → ok."""
    async def _noop():
        while True:
            await asyncio.sleep(60)

    async def _go():
        stub_streamer._task = asyncio.create_task(_noop())
        stub_streamer.metrics.ticks_ingested = 100
        stub_streamer.metrics.last_tick_ts = _now()
        ok, _ = stub_streamer.is_healthy()
        assert ok
        stub_streamer._task.cancel()

    asyncio.run(_go())


# --------------------------------------------------------------------------
# reconnect / backoff
# --------------------------------------------------------------------------


def test_streamer_restarts_source_after_crash(stub_streamer, monkeypatch):
    """A source that crashes then works: streamer restarts once, counter goes to 1."""
    src = _CrashingSource(raise_after=3)
    # Make source recovering: second call gives 5 ticks then returns cleanly.
    call_count = {"n": 0}

    class _Recovering:
        name = "recovering"
        async def stream(self, symbols):
            call_count["n"] += 1
            if call_count["n"] == 1:
                for i in range(3):
                    yield _mk_snap(i)
                    await asyncio.sleep(0)
                raise RuntimeError("first attempt crash")
            for i in range(5):
                yield _mk_snap(i + 100)
                await asyncio.sleep(0)

    monkeypatch.setattr(sm, "make_source", lambda: _Recovering())
    monkeypatch.setattr(sm, "_BACKOFF_MIN_S", 0.01)  # keep the test fast
    monkeypatch.setattr(sm, "_BACKOFF_MAX_S", 0.05)

    async def _go():
        await stub_streamer.start()
        # Wait for both attempts to complete. Second attempt returns cleanly
        # so the supervised loop exits.
        for _ in range(200):
            await asyncio.sleep(0.02)
            if stub_streamer._task is None or stub_streamer._task.done():
                break
        assert call_count["n"] >= 2, "source should have been re-invoked after crash"
        assert stub_streamer.metrics.source_restarts >= 1
        assert stub_streamer.metrics.ticks_ingested == 8   # 3 + 5
        await stub_streamer.stop()

    asyncio.run(_go())


def test_streamer_backoff_grows(stub_streamer, monkeypatch):
    """Repeated crashes → source_restarts_total keeps ticking upward."""
    monkeypatch.setattr(sm, "make_source", lambda: _CrashingSource(raise_after=1))
    monkeypatch.setattr(sm, "_BACKOFF_MIN_S", 0.005)
    monkeypatch.setattr(sm, "_BACKOFF_MAX_S", 0.02)

    async def _go():
        await stub_streamer.start()
        # Let a handful of crash-restart cycles happen.
        await asyncio.sleep(0.3)
        assert stub_streamer.metrics.source_restarts >= 3, (
            f"expected >=3 restarts after 300ms of crash-loop, got "
            f"{stub_streamer.metrics.source_restarts}"
        )
        await stub_streamer.stop()

    asyncio.run(_go())


def test_streamer_clean_end_no_restart(stub_streamer, monkeypatch):
    """A source that returns cleanly triggers no restart."""
    monkeypatch.setattr(sm, "make_source", lambda: _CountingSource(total=5))

    async def _go():
        await stub_streamer.start()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if stub_streamer._task is None or stub_streamer._task.done():
                break
        assert stub_streamer.metrics.ticks_ingested == 5
        assert stub_streamer.metrics.source_restarts == 0
        await stub_streamer.stop()

    asyncio.run(_go())


# --------------------------------------------------------------------------
# metrics / dropped messages / deque bound
# --------------------------------------------------------------------------


def test_ingest_increments_ticks_counter(stub_streamer, monkeypatch):
    monkeypatch.setattr(sm, "make_source", lambda: _CountingSource(total=25))

    async def _go():
        await stub_streamer.start()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if stub_streamer.metrics.ticks_ingested >= 25:
                break
        assert stub_streamer.metrics.ticks_ingested == 25
        await stub_streamer.stop()

    asyncio.run(_go())


def test_broadcast_drops_oldest_and_counts_it(stub_streamer):
    """When a subscriber's queue is full, drop-oldest and count the drop."""
    async def _go():
        q = stub_streamer.subscribe_metrics("TEST")
        # Fill to maxsize (200)
        for i in range(200):
            q.put_nowait(f"msg-{i}")
        # One more broadcast → oldest dropped, newest appended, counter++
        stub_streamer._broadcast(
            stub_streamer._metric_subs["TEST"],
            {"n": 999},
            "metrics:TEST",
        )
        assert stub_streamer.metrics.messages_dropped["metrics:TEST"] == 1
        # Queue still 200, oldest removed
        assert q.qsize() == 200
        stub_streamer.unsubscribe_metrics("TEST", q)

    asyncio.run(_go())


def test_recent_alerts_bounded_to_500(stub_streamer):
    """Deque maxlen keeps memory bounded."""
    for i in range(700):
        stub_streamer._recent_alerts.append(
            Anomaly(
                symbol="TEST",
                ts=datetime(2026, 8, 30, tzinfo=UTC),
                kind="volume_spike",
                detail={"i": i},
            )
        )
    assert len(stub_streamer._recent_alerts) == 500
    # Oldest 200 evicted
    recent = stub_streamer.recent_alerts(limit=1)
    assert recent[0]["detail"]["i"] == 699


def test_recent_alerts_limit_semantics(stub_streamer):
    for i in range(50):
        stub_streamer._recent_alerts.append(
            Anomaly(
                symbol="X",
                ts=datetime(2026, 8, 30, tzinfo=UTC),
                kind="k",
                detail={"i": i},
            )
        )
    # last 10 in oldest→newest order
    out = stub_streamer.recent_alerts(limit=10)
    assert len(out) == 10
    assert [a["detail"]["i"] for a in out] == list(range(40, 50))
    assert stub_streamer.recent_alerts(limit=0) == []


def test_ws_client_gauge_tracks_sub_unsub(stub_streamer):
    async def _go():
        assert stub_streamer.metrics.ws_clients.get("book:AAA", 0) == 0
        q1 = stub_streamer.subscribe_book("AAA")
        q2 = stub_streamer.subscribe_book("AAA")
        assert stub_streamer.metrics.ws_clients["book:AAA"] == 2
        stub_streamer.unsubscribe_book("AAA", q1)
        assert stub_streamer.metrics.ws_clients["book:AAA"] == 1
        stub_streamer.unsubscribe_book("AAA", q2)
        assert stub_streamer.metrics.ws_clients["book:AAA"] == 0

    asyncio.run(_go())


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _now() -> float:
    import time
    return time.perf_counter()


def test_anomaly_to_dict_isoformats_ts():
    a = Anomaly(
        symbol="X",
        ts=datetime(2026, 8, 30, 9, 15, 0, tzinfo=UTC),
        kind="spread_blowup",
        detail={"z": 3.4},
    )
    d = _anomaly_to_dict(a)
    assert d["ts"] == "2026-08-30T09:15:00+00:00"
    assert d["symbol"] == "X"
    assert d["kind"] == "spread_blowup"
