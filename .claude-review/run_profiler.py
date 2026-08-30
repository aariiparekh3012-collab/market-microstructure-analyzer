"""Run the latency profiler and generate a performance report."""

import argparse
import asyncio
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from backend.analytics.profiler import ProfiledEngine
from backend.ingestion.mock_source import MockSource


async def main(args):
    symbols = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]
    source = MockSource(
        symbols=symbols,
        max_ticks=args.ticks,
        realtime=False,
        seed=args.seed,
    )
    engine = ProfiledEngine()
    total = 0

    print(f"Running profiler ({args.ticks:,} ticks, no sleep, seed={args.seed})...")

    async for snap in source.stream():
        engine.process(snap)
        total += 1

    print(f"Processed {total} ticks\n")

    # Save raw timings
    os.makedirs(args.output, exist_ok=True)
    timings_path = os.path.join(args.output, "latency_timings.csv")
    summary_path = os.path.join(args.output, "latency_summary.json")
    with open(timings_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "tick_id", "symbol", "total_us", "spread_us", "ofi_us",
            "vwap_us", "volume_us", "kyle_us", "amihud_us", "roll_us",
            "trade_quote_us", "anomaly_us", "overhead_us",
        ])
        for t in engine.timings:
            writer.writerow([t.tick_id, t.symbol, f"{t.total_us:.2f}",
                             f"{t.spread_us:.2f}", f"{t.ofi_us:.2f}",
                             f"{t.vwap_us:.2f}", f"{t.volume_us:.2f}",
                             f"{t.kyle_us:.2f}", f"{t.amihud_us:.2f}",
                             f"{t.roll_us:.2f}", f"{t.trade_quote_us:.2f}",
                             f"{t.anomaly_us:.2f}", f"{t.overhead_us:.2f}"])

    # Print summary
    s = engine.summary()

    print("=" * 65)
    print("  LATENCY PROFILER REPORT")
    print("  Real-Time Market Microstructure Analyzer")
    print("=" * 65)
    print(f"\n  Total ticks profiled: {s['total_ticks']:,}")
    print(f"  Symbols: {', '.join(symbols)}")

    print(f"\n{'─' * 65}")
    print(f"  {'Module':<22} {'Mean':>8} {'Median':>8} {'P95':>8} {'P99':>8} {'Max':>8}")
    print(f"  {'':22} {'(μs)':>8} {'(μs)':>8} {'(μs)':>8} {'(μs)':>8} {'(μs)':>8}")
    print(f"{'─' * 65}")

    for name, label in [
        ("spread", "Spread"), ("ofi", "OFI"), ("vwap", "VWAP"),
        ("volume", "Volume"), ("kyle", "Kyle Lambda"),
        ("amihud", "Amihud"), ("roll", "Roll Spread"),
        ("trade_quote", "Trade/Quote Var"),
        ("anomaly_detection", "Anomaly Detection"),
    ]:
        ms = s["modules"][name]
        pct = s["mean_breakdown_pct"][name]
        print(f"  {label:<22} {ms['mean_us']:>7.1f} {ms['median_us']:>7.1f} "
              f"{ms['p95_us']:>7.1f} {ms['p99_us']:>7.1f} {ms['max_us']:>7.1f}  ({pct:.1f}%)")

    oh = s["overhead"]
    oh_pct = s["mean_breakdown_pct"]["overhead"]
    print(f"  {'Overhead':<22} {oh['mean_us']:>7.1f} {oh['median_us']:>7.1f} "
          f"{oh['p95_us']:>7.1f} {oh['p99_us']:>7.1f} {oh['max_us']:>7.1f}  ({oh_pct:.1f}%)")

    print(f"{'─' * 65}")
    ts = s["total"]
    print(f"  {'TOTAL':<22} {ts['mean_us']:>7.1f} {ts['median_us']:>7.1f} "
          f"{ts['p95_us']:>7.1f} {ts['p99_us']:>7.1f} {ts['max_us']:>7.1f}")
    print(f"{'─' * 65}")

    # Throughput
    mean_us = ts["mean_us"]
    tps = 1_000_000 / mean_us if mean_us > 0 else 0
    print(f"\n  Throughput: {tps:,.0f} ticks/sec (mean)")
    print(f"  At P99:    {1_000_000 / ts['p99_us']:,.0f} ticks/sec")
    print(f"\n  Budget at 200ms tick interval: {mean_us / 200_000 * 100:.2f}% utilised")
    print(f"  Headroom: {200_000 - mean_us:,.0f} μs per tick")

    print(f"\n{'=' * 65}")

    # Save summary as JSON
    with open(summary_path, "w") as f:
        json.dump(s, f, indent=2)

    print(f"\nSaved: {timings_path}, {summary_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticks", type=int, default=15_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="data")
    parsed = parser.parse_args()
    if parsed.ticks <= 0:
        parser.error("--ticks must be positive")
    asyncio.run(main(parsed))
