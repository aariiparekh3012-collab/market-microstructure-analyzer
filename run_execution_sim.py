#!/usr/bin/env python3
"""
Runner for the execution strategy simulator.

Loads tick data, runs TWAP and VWAP simulations at various target quantities,
prints comparison tables, and saves results to CSV.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd

from backend.analytics.execution_sim import (
    TWAPExecutor,
    VWAPExecutor,
    simulate,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TICKS_PATH = os.path.join(DATA_DIR, "ticks.csv")
RESULTS_PATH = os.path.join(DATA_DIR, "execution_results.csv")
SYMBOL = "RELIANCE"
NUM_SLICES = 10


def run_one(ticks: pd.DataFrame, strategy: str, target_qty: int) -> dict:
    if strategy == "TWAP":
        executor = TWAPExecutor(target_qty=target_qty, num_slices=NUM_SLICES, side="buy")
    else:
        executor = VWAPExecutor(target_qty=target_qty, num_slices=NUM_SLICES, side="buy")

    result = simulate(ticks, executor)

    return {
        "strategy": strategy,
        "target_qty": target_qty,
        "num_fills": len(result.fills),
        "total_filled": sum(f[1] for f in result.fills),
        "avg_fill_price": result.avg_fill_price,
        "arrival_price": result.arrival_price,
        "vwap_price": result.vwap_price,
        "arrival_slippage_bps": result.total_slippage_bps,
        "vwap_slippage_bps": result.vwap_slippage_bps,
        "impl_shortfall_bps": result.implementation_shortfall,
        "last_fill_slippage_bps": result.last_fill_slippage_bps,
    }


def main():
    # ── Load data ───────────────────────────────────────────────────
    if not os.path.exists(TICKS_PATH):
        raise SystemExit(
            "Missing data/ticks.csv. Run: python scripts/generate_sample_data.py"
        )
    print(f"Loading ticks from {TICKS_PATH} ...")
    ticks_all = pd.read_csv(TICKS_PATH)
    ticks = ticks_all[ticks_all["symbol"] == SYMBOL].copy()
    print(f"  {SYMBOL}: {len(ticks)} ticks\n")

    # ── 1. Head-to-head: TWAP vs VWAP at target_qty=500 ────────────
    print("=" * 72)
    print(f"  TWAP vs VWAP comparison  |  symbol={SYMBOL}  qty=500  slices={NUM_SLICES}")
    print("=" * 72)

    rows = [
        run_one(ticks, "TWAP", 500),
        run_one(ticks, "VWAP", 500),
    ]

    fmt = "{:<10} {:>12} {:>18} {:>16} {:>14}"
    print(fmt.format("Strategy", "Avg Fill", "Arrival Slip(bps)", "VWAP Slip(bps)", "Last Fill(bps)"))
    print("-" * 72)
    for r in rows:
        print(fmt.format(
            r["strategy"],
            f"{r['avg_fill_price']:.4f}",
            f"{r['arrival_slippage_bps']:.2f}",
            f"{r['vwap_slippage_bps']:.2f}",
            f"{r['last_fill_slippage_bps']:.2f}",
        ))
    print()

    # ── 2. Market impact scaling across target quantities ───────────
    quantities = [100, 500, 1000, 2000]
    all_rows = []

    print("=" * 88)
    print(f"  Market impact scaling  |  symbol={SYMBOL}  slices={NUM_SLICES}")
    print("=" * 88)

    fmt2 = "{:<10} {:>10} {:>12} {:>18} {:>16} {:>14}"
    print(fmt2.format("Strategy", "Qty", "Avg Fill", "Arrival Slip(bps)", "VWAP Slip(bps)", "Last Fill(bps)"))
    print("-" * 88)

    for qty in quantities:
        for strat in ("TWAP", "VWAP"):
            r = run_one(ticks, strat, qty)
            all_rows.append(r)
            print(fmt2.format(
                r["strategy"],
                r["target_qty"],
                f"{r['avg_fill_price']:.4f}",
                f"{r['arrival_slippage_bps']:.2f}",
                f"{r['vwap_slippage_bps']:.2f}",
                f"{r['last_fill_slippage_bps']:.2f}",
            ))
    print()

    # ── 3. Save results ────────────────────────────────────────────
    results_df = pd.DataFrame(all_rows)
    results_df.to_csv(RESULTS_PATH, index=False)
    print(f"Results saved to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
