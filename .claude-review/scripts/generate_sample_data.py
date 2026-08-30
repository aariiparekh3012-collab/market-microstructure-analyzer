#!/usr/bin/env python3
"""Generate deterministic synthetic tick, metric, and anomaly CSV files."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.analytics.engine import Engine  # noqa: E402
from backend.ingestion.mock_source import MockSource  # noqa: E402
from backend.models import OrderBookSnapshot  # noqa: E402

DEFAULT_SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]


def _tick_row(snapshot: OrderBookSnapshot) -> dict[str, object]:
    row: dict[str, object] = {
        "timestamp": snapshot.ts.isoformat(),
        "symbol": snapshot.symbol,
        "ltp": snapshot.ltp,
        "ltq": snapshot.ltq,
        "volume": snapshot.volume,
    }
    for index in range(5):
        bid = snapshot.bids[index] if index < len(snapshot.bids) else None
        ask = snapshot.asks[index] if index < len(snapshot.asks) else None
        level = index + 1
        row[f"bid_px_{level}"] = bid.price if bid else None
        row[f"bid_qty_{level}"] = bid.qty if bid else None
        row[f"ask_px_{level}"] = ask.price if ask else None
        row[f"ask_qty_{level}"] = ask.qty if ask else None
    return row


def _write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


async def generate(
    output_dir: Path,
    symbols: list[str],
    ticks_per_symbol: int,
    seed: int,
) -> None:
    engine = Engine()
    source = MockSource(
        symbols=symbols,
        max_ticks=ticks_per_symbol * len(symbols),
        realtime=False,
        seed=seed,
        start_time=datetime(2026, 1, 2, 3, 45, tzinfo=UTC),
    )
    tick_rows: list[dict[str, object]] = []
    metric_rows: list[dict[str, object]] = []
    anomaly_rows: list[dict[str, object]] = []

    async for snapshot in source.stream():
        metrics, anomalies = engine.process(snapshot)
        tick_rows.append(_tick_row(snapshot))
        metric_rows.append(metrics)
        for anomaly in anomalies:
            anomaly_rows.append(
                {
                    "timestamp": anomaly.ts.isoformat(),
                    "symbol": anomaly.symbol,
                    "kind": anomaly.kind,
                    "severity": anomaly.severity,
                    "detail": json.dumps(anomaly.detail, sort_keys=True),
                }
            )

    tick_fields = list(tick_rows[0])
    metric_fields = list(metric_rows[0])
    anomaly_fields = ["timestamp", "symbol", "kind", "severity", "detail"]
    _write_csv(output_dir / "ticks.csv", tick_rows, tick_fields)
    _write_csv(output_dir / "metrics.csv", metric_rows, metric_fields)
    _write_csv(output_dir / "anomalies.csv", anomaly_rows, anomaly_fields)

    manifest = {
        "generator": "MockSource",
        "seed": seed,
        "symbols": symbols,
        "ticks_per_symbol": ticks_per_symbol,
        "total_ticks": len(tick_rows),
        "total_anomalies": len(anomaly_rows),
        "purpose": "software validation only; not empirical market evidence",
    }
    (output_dir / "sample_data_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Generated {len(tick_rows):,} ticks and {len(anomaly_rows):,} anomalies "
        f"in {output_dir} (seed={seed})."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticks-per-symbol", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args()
    if args.ticks_per_symbol <= 0:
        parser.error("--ticks-per-symbol must be positive")
    asyncio.run(generate(args.output, DEFAULT_SYMBOLS, args.ticks_per_symbol, args.seed))


if __name__ == "__main__":
    main()
