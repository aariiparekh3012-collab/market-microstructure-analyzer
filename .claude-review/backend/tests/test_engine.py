from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from backend.analytics.engine import Engine
from backend.ingestion.mock_source import MockSource


def _run_engine(count: int = 120, seed: int = 7):
    async def collect():
        engine = Engine()
        source = MockSource(
            symbols=["RELIANCE"],
            max_ticks=count,
            realtime=False,
            seed=seed,
            start_time=datetime(2026, 1, 2, tzinfo=UTC),
        )
        outputs = []
        async for snapshot in source.stream():
            outputs.append(engine.process(snapshot))
        return engine, outputs

    return asyncio.run(collect())


def test_engine_processes_full_warmup_without_error():
    engine, outputs = _run_engine()
    metrics, _ = outputs[-1]

    assert len(outputs) == 120
    assert len(metrics) == 22
    assert metrics["kyle_lambda"] is not None
    assert metrics["trade_variance_share"] is not None
    assert 0.0 <= metrics["trade_variance_share"] <= 1.0
    assert engine.volume_profile("RELIANCE")


def test_engine_does_not_create_state_for_unknown_profile():
    engine = Engine()
    assert engine.volume_profile("UNKNOWN") == {}


def test_mock_source_is_reproducible():
    _, first = _run_engine(count=20, seed=99)
    _, second = _run_engine(count=20, seed=99)
    first_metrics = [item[0] for item in first]
    second_metrics = [item[0] for item in second]
    assert first_metrics == second_metrics
