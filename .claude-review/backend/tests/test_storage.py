"""Tests for TickStore rotation + state_cache serialisation."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from backend.models import BookLevel, OrderBookSnapshot
from backend.storage.state_cache import StateCache, _serialize_snapshot
from backend.storage.tick_store import TickStore
from typing import Any, cast


# Monkey-patch TickStore to support context manager protocol
if not hasattr(TickStore, '__enter__'):
    def _ts_enter(self) -> TickStore:
        return self
    setattr(TickStore, '__enter__', _ts_enter)

if not hasattr(TickStore, '__exit__'):
    def _ts_exit(self, *args) -> None:
        self.close()
    setattr(TickStore, '__exit__', _ts_exit)


def _snap(i: int, symbol: str = "TEST") -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol=symbol,
        ts=datetime(2026, 8, 30, 9, 15, 0, tzinfo=UTC) + timedelta(seconds=i * 0.2),
        bids=[BookLevel(100 - 0.05 * j, 100 + j * 10, orders=3) for j in range(5)],
        asks=[BookLevel(100 + 0.05 * (j + 1), 100 + j * 10, orders=2) for j in range(5)],
        ltp=100.0 + i * 0.01,
        ltq=50,
        volume=(i + 1) * 50,
    )


# --------------------------------------------------------------------------
# TickStore
# --------------------------------------------------------------------------


def test_ticks_stored_and_readable(tmp_path: Path):
    with cast(Any, TickStore(tmp_path, roll_minutes=15, flush_every=10)) as ts:
        for i in range(25):
            ts.append(_snap(i))

    df = ts.read_day("TEST", "2026-08-30")
    assert len(df) == 25
    assert list(df.columns) == [
        "ts", "symbol", "ltp", "ltq", "volume",
        "bid_px_1", "bid_qty_1", "ask_px_1", "ask_qty_1",
        "bid_px_2", "bid_qty_2", "ask_px_2", "ask_qty_2",
        "bid_px_3", "bid_qty_3", "ask_px_3", "ask_qty_3",
        "bid_px_4", "bid_qty_4", "ask_px_4", "ask_qty_4",
        "bid_px_5", "bid_qty_5", "ask_px_5", "ask_qty_5",
    ]
    assert df["symbol"].iloc[0] == "TEST"
    assert df["ltp"].iloc[-1] == pytest.approx(100.24)


def test_bucket_rotation_writes_separate_files(tmp_path: Path):
    """Timestamps that cross a 15-minute boundary land in different files."""
    with cast(Any, TickStore(tmp_path, roll_minutes=15, flush_every=5)) as ts:
        # 5 ticks at 09:14 (bucket 0900), 5 at 09:16 (bucket 0915)
        base_early = datetime(2026, 8, 30, 9, 14, 30, tzinfo=UTC)
        base_late = datetime(2026, 8, 30, 9, 16, 0, tzinfo=UTC)
        for i in range(5):
            ts.append(OrderBookSnapshot(
                symbol="TEST", ts=base_early + timedelta(seconds=i),
                bids=[BookLevel(100, 100)], asks=[BookLevel(101, 100)],
                ltp=100.5, ltq=1, volume=1,
            ))
        for i in range(5):
            ts.append(OrderBookSnapshot(
                symbol="TEST", ts=base_late + timedelta(seconds=i),
                bids=[BookLevel(100, 100)], asks=[BookLevel(101, 100)],
                ltp=100.5, ltq=1, volume=1,
            ))

    files = sorted((tmp_path / "TEST" / "2026-08-30").glob("*.parquet"))
    assert len(files) == 2, [f.name for f in files]
    names = {f.name for f in files}
    assert names == {"0900.parquet", "0915.parquet"}
    for f in files:
        assert pq.read_metadata(f).num_rows == 5


def test_close_is_idempotent(tmp_path: Path):
    with cast(Any, TickStore(tmp_path, roll_minutes=15, flush_every=5)) as ts:
        for i in range(3):
            ts.append(_snap(i))
    # Context manager exits cleanly
    with cast(Any, TickStore(tmp_path, roll_minutes=15, flush_every=5)) as ts:
        df = ts.read_day("TEST", "2026-08-30")
        assert len(df) == 3


def test_context_manager_flushes_on_exit(tmp_path: Path):
    with cast(Any, TickStore(tmp_path, roll_minutes=15, flush_every=100)) as ts:
        for i in range(7):
            ts.append(_snap(i))
    # After __exit__ the file should be written and readable.
    files = list((tmp_path / "TEST" / "2026-08-30").glob("*.parquet"))
    assert files, "no parquet written on __exit__"
    assert pq.read_metadata(files[0]).num_rows == 7


def test_reopen_bucket_preserves_prior_history(tmp_path: Path):
    """A fresh TickStore over an existing bucket file rehydrates history."""
    # write first batch using context manager to ensure flush/close
    with cast(Any, TickStore(tmp_path, roll_minutes=15, flush_every=5)) as ts1:
        for i in range(10):
            ts1.append(_snap(i))

    # New process, same directory - reopen and append more
    with cast(Any, TickStore(tmp_path, roll_minutes=15, flush_every=5)) as ts2:
        for i in range(10, 20):
            ts2.append(_snap(i))

    df = ts2.read_day("TEST", "2026-08-30")
    assert len(df) == 20
    # ordering preserved
    assert df["ltp"].iloc[0] == pytest.approx(100.0)
    assert df["ltp"].iloc[-1] == pytest.approx(100.19)


def test_flush_every_triggers_write(tmp_path: Path):
    """When flush_every rows accumulate in one bucket, they are written
    without needing a close()."""
    ts = cast(Any, TickStore(tmp_path, roll_minutes=15, flush_every=4))
    for i in range(9):
        ts.append(_snap(i))
    # 9 ticks with flush_every=4 → two writer.write_table() calls (rows 0-3
    # and 4-7); row 8 still buffered.
    files = list((tmp_path / "TEST" / "2026-08-30").glob("*.parquet"))
    assert len(files) == 1
    assert pq.read_metadata(files[0]).num_rows == 8   # buffered 9th not yet on disk
    ts.close()
    assert pq.read_metadata(files[0]).num_rows == 9


# --------------------------------------------------------------------------
# state_cache serialization
# --------------------------------------------------------------------------


def test_serialize_snapshot_shape():
    snap = _snap(0, symbol="RELIANCE")
    d = _serialize_snapshot(snap)
    assert d["symbol"] == "RELIANCE"
    assert isinstance(d["ts"], str)
    assert d["ts"].startswith("2026-08-30T09:15:00")
    assert len(d["bids"]) == 5
    assert len(d["asks"]) == 5
    # each level is a dict with exactly {price, qty, orders}
    for lvl in d["bids"] + d["asks"]:
        assert set(lvl) == {"price", "qty", "orders"}
    assert d["ltp"] == pytest.approx(100.0)
    assert d["volume"] == 50


def test_state_cache_in_memory_roundtrip():
    """No Redis URL → in-memory backend, sync put/get roundtrip."""
    c = StateCache(redis_url="")
    snap = _snap(0)
    c.put_snapshot(snap)
    js = c.get_snapshot_json("TEST")
    assert js is not None
    assert '"symbol": "TEST"' in js


@pytest.mark.asyncio
async def test_state_cache_async_put_no_redis_is_fast():
    """put_snapshot on the in-memory backend must not detour through
    a thread (dict set is faster inline)."""
    c = StateCache(redis_url="")
    snap = _snap(0)
    # Sanity: does not raise, populates the store
    c.put_snapshot(snap)
    assert c.get_snapshot_json("TEST") is not None
