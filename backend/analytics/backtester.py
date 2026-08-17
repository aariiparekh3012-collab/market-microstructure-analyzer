"""
OFI Signal Backtester
---------------------
Backtests mean-reversion strategies driven by Order Flow Imbalance (OFI)
z-score signals.  Designed to work with tick-level microstructure data
produced by the MMA data pipeline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class OFIStrategy:
    """Parameterised OFI z-score strategy.

    Entry:  go long  when OFI z-score >  entry_threshold
            go short when OFI z-score < -entry_threshold
    Exit:   flatten  when |z-score|    <  exit_threshold
    """

    def __init__(
        self,
        ofi_column: str = "ofi_300s",
        entry_threshold: float = 1.5,
        exit_threshold: float = 0.3,
        lookback: int = 100,
        position_size: int = 1,
        transaction_cost_bps: float = 1.0,
    ):
        self.ofi_column = ofi_column
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.lookback = lookback
        self.position_size = position_size
        self.transaction_cost_bps = transaction_cost_bps

    def __repr__(self) -> str:
        return (
            f"OFIStrategy(ofi={self.ofi_column}, entry={self.entry_threshold}, "
            f"exit={self.exit_threshold}, lb={self.lookback}, "
            f"size={self.position_size}, cost={self.transaction_cost_bps}bps)"
        )


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class BacktestResult:
    trades: List[Dict]
    equity_curve: List[float]
    total_pnl: float = 0.0
    num_trades: int = 0
    win_rate: float = 0.0
    avg_pnl_per_trade: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0

    def summary_dict(self) -> dict:
        return {
            "total_pnl": round(self.total_pnl, 4),
            "num_trades": self.num_trades,
            "win_rate": round(self.win_rate, 4),
            "avg_pnl_per_trade": round(self.avg_pnl_per_trade, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "profit_factor": round(self.profit_factor, 4),
        }


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

def _rolling_zscore(series: pd.Series, lookback: int) -> np.ndarray:
    """Compute rolling z-score using expanding window until *lookback* bars
    are available, then a fixed rolling window."""
    arr = series.values.astype(np.float64)
    n = len(arr)
    zscores = np.zeros(n, dtype=np.float64)

    for i in range(2, n):  # need at least 2 points for ddof=1
        window_start = max(0, i - lookback)
        window = arr[window_start:i]
        mu = window.mean()
        sigma = window.std(ddof=1)
        if sigma > 1e-12:
            zscores[i] = (arr[i] - mu) / sigma
    return zscores


def run_backtest(df: pd.DataFrame, strategy: OFIStrategy) -> BacktestResult:
    """Run the OFI z-score backtest on a single-symbol DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain at minimum: ``midprice`` and the column named by
        ``strategy.ofi_column``.
    strategy : OFIStrategy

    Returns
    -------
    BacktestResult
    """
    df = df.reset_index(drop=True)
    prices = df["midprice"].values.astype(np.float64)
    ofi_values = df[strategy.ofi_column].values.astype(np.float64)
    n = len(df)

    # Pre-compute z-scores
    zscores = _rolling_zscore(pd.Series(ofi_values), strategy.lookback)

    # Transaction cost helper
    cost_multiplier = strategy.transaction_cost_bps / 10_000.0

    # State
    position = 0        # +1 long, -1 short, 0 flat
    entry_tick = 0
    entry_px = 0.0

    trades: List[Dict] = []
    equity_curve = np.zeros(n, dtype=np.float64)
    cumulative_pnl = 0.0
    unrealised = 0.0

    for i in range(n):
        z = zscores[i]
        px = prices[i]

        if position == 0:
            # --- look for entry ---
            if z > strategy.entry_threshold:
                position = 1
                entry_tick = i
                entry_px = px
                cumulative_pnl -= cost_multiplier * px * strategy.position_size
            elif z < -strategy.entry_threshold:
                position = -1
                entry_tick = i
                entry_px = px
                cumulative_pnl -= cost_multiplier * px * strategy.position_size
        else:
            # --- look for exit ---
            if abs(z) < strategy.exit_threshold:
                raw_pnl = position * (px - entry_px) * strategy.position_size
                exit_cost = cost_multiplier * px * strategy.position_size
                trade_pnl = raw_pnl - exit_cost

                cumulative_pnl += raw_pnl - exit_cost
                trades.append({
                    "entry_tick": entry_tick,
                    "exit_tick": i,
                    "side": "long" if position == 1 else "short",
                    "entry_px": round(entry_px, 4),
                    "exit_px": round(px, 4),
                    "pnl": round(trade_pnl, 4),
                    "holding_ticks": i - entry_tick,
                })
                position = 0
                unrealised = 0.0

        # Mark-to-market unrealised PnL
        if position != 0:
            unrealised = position * (px - entry_px) * strategy.position_size
        else:
            unrealised = 0.0

        equity_curve[i] = cumulative_pnl + unrealised

    # --- If still in a position at end, force-close ---
    if position != 0:
        px = prices[-1]
        raw_pnl = position * (px - entry_px) * strategy.position_size
        exit_cost = cost_multiplier * px * strategy.position_size
        trade_pnl = raw_pnl - exit_cost
        cumulative_pnl += raw_pnl - exit_cost
        trades.append({
            "entry_tick": entry_tick,
            "exit_tick": n - 1,
            "side": "long" if position == 1 else "short",
            "entry_px": round(entry_px, 4),
            "exit_px": round(px, 4),
            "pnl": round(trade_pnl, 4),
            "holding_ticks": (n - 1) - entry_tick,
        })
        equity_curve[-1] = cumulative_pnl

    # --- Aggregate statistics ---
    num_trades = len(trades)
    total_pnl = cumulative_pnl

    if num_trades > 0:
        pnls = np.array([t["pnl"] for t in trades])
        wins = (pnls > 0).sum()
        win_rate = wins / num_trades
        avg_pnl = pnls.mean()

        gross_profit = pnls[pnls > 0].sum() if (pnls > 0).any() else 0.0
        gross_loss = abs(pnls[pnls < 0].sum()) if (pnls < 0).any() else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 1e-12 else float("inf")
    else:
        win_rate = 0.0
        avg_pnl = 0.0
        profit_factor = 0.0

    # Sharpe: annualised assuming 5 ticks/sec
    eq = equity_curve
    tick_returns = np.diff(eq)
    if len(tick_returns) > 1 and tick_returns.std() > 1e-12:
        ticks_per_year = 5 * 252 * 6.25 * 3600  # 5 ticks/s * 252 days * 6.25 hrs/day * 3600 s/hr
        sharpe = (tick_returns.mean() / tick_returns.std()) * math.sqrt(ticks_per_year)
    else:
        sharpe = 0.0

    # Max drawdown
    running_max = np.maximum.accumulate(eq)
    drawdowns = running_max - eq
    max_dd = drawdowns.max() if len(drawdowns) > 0 else 0.0
    peak_at_dd = running_max[drawdowns.argmax()] if len(drawdowns) > 0 else 0.0
    max_dd_pct = (max_dd / peak_at_dd * 100) if abs(peak_at_dd) > 1e-12 else 0.0

    return BacktestResult(
        trades=trades,
        equity_curve=eq.tolist(),
        total_pnl=total_pnl,
        num_trades=num_trades,
        win_rate=win_rate,
        avg_pnl_per_trade=avg_pnl,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        profit_factor=profit_factor,
    )
