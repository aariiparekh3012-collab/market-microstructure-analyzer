"""Data-quality gate and quarantine regression tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from backend.api import streamer as sm
from backend.api.streamer import Streamer
from backend.data_quality import DataQualityGate, QuarantineStore
from backend.models import BookLevel, OrderBookSnapshot


def _snap(i: int = 0, symbol: str = "AAA") -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol=symbol,
        ts=datetime(2026, 9, 7, 9, 15, tzinfo=UTC) + timedelta(milliseconds=i),
        bids=[BookLevel(100.00 - 0.05 * j, 100, 2) for j in range(5)],
        asks=[BookLevel(100.05 + 0.05 * j, 100, 2) for j in range(5)],
        ltp=100.05,
        ltq=10,
        volume=100 + i,
    )


def test_symbol_is_the_only_automatic_repair():
    gate = DataQualityGate(["AAA"])
    result = gate.validate(_snap(symbol="  aaa "))

    assert result.accepted
    assert result.snapshot.symbol == "AAA"
    assert result.repaired_fields == ("symbol",)
    assert gate.summary()["repaired"] == 1


def test_crossed_book_is_rejected_without_advancing_sequence_state():
    gate = DataQualityGate(["AAA"])
    crossed = _snap()
    crossed.asks[0] = BookLevel(99.95, 100, 2)

    rejected = gate.validate(crossed)
    accepted = gate.validate(_snap())

    assert not rejected.accepted
    assert "crossed_or_locked_book" in rejected.rejection_reasons
    assert accepted.accepted


def test_timestamp_and_same_session_volume_regressions_are_rejected():
    gate = DataQualityGate(["AAA"])
    assert gate.validate(_snap(10)).accepted

    old = gate.validate(_snap(9))
    lower_volume = gate.validate(replace(_snap(11), volume=50))

    assert "non_monotonic_timestamp" in old.rejection_reasons
    assert "cumulative_volume_decrease" in lower_volume.rejection_reasons


def test_quarantine_store_writes_machine_readable_jsonl(tmp_path):
    gate = DataQualityGate(["AAA"])
    result = gate.validate(replace(_snap(), ltp=float("nan")))
    store = QuarantineStore(tmp_path / "quarantine" / "rejected.jsonl")

    store.append(result)
    record = json.loads(store.path.read_text(encoding="utf-8").strip())

    assert record["rejection_reasons"] == ["invalid_ltp"]
    assert record["snapshot"]["symbol"] == "AAA"
    assert record["snapshot"]["ts"].endswith("+00:00")


def test_streamer_quarantines_before_storage_or_analytics(monkeypatch, tmp_path):
    monkeypatch.setattr(sm.settings, "redis_url", "")
    monkeypatch.setattr(sm.settings, "symbols", "AAA")
    monkeypatch.setattr(sm.settings, "tick_store_dir", tmp_path / "ticks")
    monkeypatch.setattr(sm.settings, "data_quarantine_path", tmp_path / "rejected.jsonl")
    streamer = Streamer()
    appended: list[OrderBookSnapshot] = []
    monkeypatch.setattr(streamer.tick_store, "append", appended.append)

    async def exercise() -> None:
        await streamer._handle(replace(_snap(), ltq=-1))
        await streamer._handle(_snap(1, symbol=" aaa "))

    asyncio.run(exercise())

    assert len(appended) == 1
    assert appended[0].symbol == "AAA"
    assert streamer.metrics.ticks_ingested == 1
    assert streamer.data_quality.summary()["rejected"] == 1
    assert streamer.data_quality.summary()["quarantine_writes"] == 1
