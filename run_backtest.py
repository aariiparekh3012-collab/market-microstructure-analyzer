#!/usr/bin/env python3
"""
Run OFI signal backtest across all symbols in metrics.csv.

Usage:
    cd /root/mma && python run_backtest.py
"""

import sys
import os

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from backend.analytics.backtester import OFIStrategy, run_backtest


DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
METRICS_PATH = os.path.join(DATA_DIR, "metrics.csv")
RESULTS_PATH = os.path.join(DATA_DIR, "backtest_results.csv")
EQUITY_PATH = os.path.join(DATA_DIR, "equity_curves.csv")


def main():
    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------
    print(f"Loading data from {METRICS_PATH} ...")
    df = pd.read_csv(METRICS_PATH)
    symbols = df["symbol"].unique().tolist()
    print(f"Symbols: {symbols}\n")

    strategy = OFIStrategy()  # default params
    print(f"Strategy: {strategy}\n")

    # ------------------------------------------------------------------
    # Run per-symbol backtests
    # ------------------------------------------------------------------
    all_trades = []
    equity_frames = []
    summary_rows = []

    for sym in symbols:
        sym_df = df[df["symbol"] == sym].copy().reset_index(drop=True)
        result = run_backtest(sym_df, strategy)

        # Collect trades
        for t in result.trades:
            t["symbol"] = sym
            all_trades.append(t)

        # Collect equity curve
        eq_df = pd.DataFrame({
            "tick": range(len(result.equity_curve)),
            "equity": result.equity_curve,
            "symbol": sym,
        })
        equity_frames.append(eq_df)

        summary_rows.append({
            "symbol": sym,
            "trades": result.num_trades,
            "win_rate": result.win_rate,
            "sharpe": result.sharpe_ratio,
            "max_dd": result.max_drawdown,
            "max_dd_pct": result.max_drawdown_pct,
            "pnl": result.total_pnl,
            "profit_factor": result.profit_factor,
            "avg_pnl": result.avg_pnl_per_trade,
        })

    # ------------------------------------------------------------------
    # Print summary table
    # ------------------------------------------------------------------
    summary_df = pd.DataFrame(summary_rows)
    fmt = {
        "win_rate": "{:.2%}".format,
        "sharpe": "{:.3f}".format,
        "max_dd": "{:.2f}".format,
        "max_dd_pct": "{:.2f}%".format,
        "pnl": "{:.2f}".format,
        "profit_factor": "{:.3f}".format,
        "avg_pnl": "{:.4f}".format,
    }
    print("=" * 100)
    print("BACKTEST RESULTS")
    print("=" * 100)

    header = f"{'Symbol':<12} {'Trades':>7} {'Win Rate':>10} {'Sharpe':>10} {'Max DD':>12} {'Max DD%':>10} {'PnL':>14} {'PF':>8}"
    print(header)
    print("-" * 100)
    for _, row in summary_df.iterrows():
        line = (
            f"{row['symbol']:<12} "
            f"{int(row['trades']):>7} "
            f"{row['win_rate']:>9.2%} "
            f"{row['sharpe']:>10.3f} "
            f"{row['max_dd']:>12.2f} "
            f"{row['max_dd_pct']:>9.2f}% "
            f"{row['pnl']:>14.2f} "
            f"{row['profit_factor']:>8.3f}"
        )
        print(line)
    print("-" * 100)

    # Totals
    total_pnl = summary_df["pnl"].sum()
    total_trades = int(summary_df["trades"].sum())
    avg_wr = summary_df["win_rate"].mean() if total_trades > 0 else 0
    print(f"{'TOTAL':<12} {total_trades:>7} {avg_wr:>9.2%} {'':>10} {'':>12} {'':>10} {total_pnl:>14.2f}")
    print("=" * 100)

    # ------------------------------------------------------------------
    # Save outputs
    # ------------------------------------------------------------------
    trades_df = pd.DataFrame(all_trades)
    if not trades_df.empty:
        cols = ["symbol", "entry_tick", "exit_tick", "side", "entry_px", "exit_px", "pnl", "holding_ticks"]
        trades_df = trades_df[cols]
    trades_df.to_csv(RESULTS_PATH, index=False)
    print(f"\nPer-trade log saved to {RESULTS_PATH} ({len(trades_df)} trades)")

    eq_all = pd.concat(equity_frames, ignore_index=True)
    eq_pivot = eq_all.pivot(index="tick", columns="symbol", values="equity")
    eq_pivot.to_csv(EQUITY_PATH)
    print(f"Equity curves saved to {EQUITY_PATH} ({len(eq_pivot)} ticks)")


if __name__ == "__main__":
    main()
