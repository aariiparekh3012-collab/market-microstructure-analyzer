"""OFI Signal Backtester.

Backtests directional strategies driven by Order Flow Imbalance (OFI)
z-score signals. Designed for tick-level microstructure data produced
by the MMA data pipeline.

Perf note: the rolling z-score was previously a Python for-loop calling
`window.mean()` / `window.std(ddof=1)` on each of ~15k ticks — measured
37× slower than pandas' vectorised rolling. The state-machine trading
loop is still Python (position bookkeeping doesn't vectorise cleanly),
but the z-score pass dominated runtime and is now vectorised.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OFIStrategy:
    """Parameterised OFI z-score strategy.

    Entry: long when z > entry_threshold, short when z < -entry_threshold.
    Exit:  flatten when |z| < exit_threshold.
    """

    ofi_column: str = "ofi_300s"
    entry_threshold: float = 1.5
    exit_threshold: float = 0.3
    lookback: int = 100
    position_size: int = 1
    transaction_cost_bps: float = 1.0
    initial_capital: float = 100_000.0

    def __repr__(self) -> str:
        return (
            f"OFIStrategy(ofi={self.ofi_column}, entry={self.entry_threshold}, "
            f"exit={self.exit_threshold}, lb={self.lookback}, "
            f"size={self.position_size}, cost={self.transaction_cost_bps}bps, "
            f"capital={self.initial_capital})"
        )


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BacktestResult:
    trades: list[dict] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    total_pnl: float = 0.0
    num_trades: int = 0
    win_rate: float = 0.0
    avg_pnl_per_trade: float = 0.0
    sharpe_ratio: float = 0.0  # unannualised mean/std of completed trade returns
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
# Helpers
# ---------------------------------------------------------------------------


def _rolling_zscore(series: pd.Series, lookback: int) -> np.ndarray:
    """Vectorised rolling z-score.

    Uses `pd.rolling(...).mean() / .std(ddof=1)` (both C-implemented).
    The `.shift(1)` matches the previous implementation's semantics: the
    z-score at index i is computed from the window `[i-lookback : i]`
    (strictly prior data — no lookahead).
    """
    r = series.rolling(window=lookback, min_periods=2)
    mu = r.mean().shift(1)
    sigma = r.std(ddof=1).shift(1)
    # sigma == 0 → z is undefined; the previous impl silently emitted 0.
    z = (series - mu) / sigma.where(sigma > 1e-12)
    return z.fillna(0.0).to_numpy(dtype=np.float64)


def _close_trade(
    trades: list[dict], entry_tick: int, exit_tick: int, position: int,
    entry_px: float, exit_px: float, position_size: int, cost_multiplier: float,
) -> float:
    """Record a completed trade and return its net PnL. Shared between the
    intra-loop exit branch and the end-of-data force-close."""
    raw_pnl = position * (exit_px - entry_px) * position_size
    entry_cost = cost_multiplier * entry_px * position_size
    exit_cost = cost_multiplier * exit_px * position_size
    trade_pnl = raw_pnl - entry_cost - exit_cost
    trades.append({
        "entry_tick": entry_tick,
        "exit_tick": exit_tick,
        "side": "long" if position == 1 else "short",
        "entry_px": round(entry_px, 4),
        "exit_px": round(exit_px, 4),
        "pnl": round(trade_pnl, 4),
        "holding_ticks": exit_tick - entry_tick,
    })
    return raw_pnl - exit_cost   # portion to add to cumulative_pnl (entry_cost was subtracted at entry)


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


def _validate_backtest_inputs(df: pd.DataFrame, strategy: OFIStrategy) -> None:
    """Raise ValueError when required columns are missing."""
    required = {"midprice", strategy.ofi_column}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"backtest data is missing columns: {sorted(missing)}")


def _apply_trade_signal(
    z: float,
    px: float,
    idx: int,
    position: int,
    entry_tick: int,
    entry_px: float,
    cum_pnl: float,
    trades: list[dict],
    pos_size: int,
    entry_thr: float,
    exit_thr: float,
    cost_mult: float,
) -> tuple[int, int, float, float]:
    """Process one tick's signal and update the strategy state."""
    if position == 0:
        if z <= entry_thr and z >= -entry_thr:
            return position, entry_tick, entry_px, cum_pnl
        new_position = 1 if z > 0 else -1
        new_entry_tick = idx
        new_entry_px = px
        return (
            new_position,
            new_entry_tick,
            new_entry_px,
            cum_pnl - cost_mult * px * pos_size,
        )

    if abs(z) < exit_thr:
        return (
            0,
            0,
            0.0,
            cum_pnl + _close_trade(
                trades,
                entry_tick,
                idx,
                position,
                entry_px,
                px,
                pos_size,
                cost_mult,
            ),
        )

    return position, entry_tick, entry_px, cum_pnl


def _aggregate_stats(trades: list[dict], pos_size: int) -> tuple[float, float, float, float]:
    """Compute profit, drawdown, and Sharpe summary metrics."""
    num_trades = len(trades)
    if not num_trades:
        return 0.0, 0.0, 0.0, 0.0

    pnls = np.fromiter((t["pnl"] for t in trades), dtype=np.float64, count=num_trades)
    wins = int((pnls > 0).sum())
    win_rate = wins / num_trades
    avg_pnl = float(pnls.mean())
    gross_profit = float(pnls[pnls > 0].sum())
    gross_loss = float(-pnls[pnls < 0].sum())
    profit_factor = (gross_profit / gross_loss) if gross_loss > 1e-12 else float("inf")

    if num_trades > 1:
        trade_returns = np.fromiter(
            (t["pnl"] / (t["entry_px"] * pos_size) for t in trades),
            dtype=np.float64,
            count=num_trades,
        )
        sample_std = trade_returns.std(ddof=1)
        sharpe = float(trade_returns.mean() / sample_std) if sample_std > 1e-12 else 0.0
    else:
        sharpe = 0.0

    return win_rate, avg_pnl, profit_factor, sharpe


def _compute_drawdown(equity: np.ndarray) -> tuple[float, float]:
    """Return the max absolute drawdown and percentage drawdown."""
    if not equity.size:
        return 0.0, 0.0

    running_max = np.maximum.accumulate(equity)
    drawdowns = running_max - equity
    idx = int(drawdowns.argmax())
    max_dd = float(drawdowns[idx])
    peak_at_dd = float(running_max[idx])
    max_dd_pct = (max_dd / peak_at_dd * 100.0) if abs(peak_at_dd) > 1e-12 else 0.0
    return max_dd, max_dd_pct


def run_backtest(df: pd.DataFrame, strategy: OFIStrategy) -> BacktestResult:
    """Run the OFI z-score backtest on a single-symbol DataFrame.

    Parameters
    ----------
    df : DataFrame
        Must contain at minimum: ``midprice`` and the column named by
        ``strategy.ofi_column``.
    """
    _validate_backtest_inputs(df, strategy)
    if df.empty:
        return BacktestResult()

    df = df.reset_index(drop=True)
    prices = df["midprice"].to_numpy(dtype=np.float64)
    ofi = df[strategy.ofi_column]

    zscores = _rolling_zscore(ofi, strategy.lookback)

    cost_mult = strategy.transaction_cost_bps / 10_000.0
    pos_size = strategy.position_size
    entry_thr = strategy.entry_threshold
    exit_thr = strategy.exit_threshold

    n = len(df)
    position = 0
    entry_tick = 0
    entry_px = 0.0
    trades: list[dict] = []
    equity = np.full(n, strategy.initial_capital, dtype=np.float64)
    cum_pnl = 0.0

    for i in range(n):
        z = zscores[i]
        px = prices[i]
        position, entry_tick, entry_px, cum_pnl = _apply_trade_signal(
            z,
            px,
            i,
            position,
            entry_tick,
            entry_px,
            cum_pnl,
            trades,
            pos_size,
            entry_thr,
            exit_thr,
            cost_mult,
        )
        unrealised = position * (px - entry_px) * pos_size if position else 0.0
        equity[i] = strategy.initial_capital + cum_pnl + unrealised

    if position != 0:
        cum_pnl += _close_trade(
            trades,
            entry_tick,
            n - 1,
            position,
            entry_px,
            prices[-1],
            pos_size,
            cost_mult,
        )
        equity[-1] = strategy.initial_capital + cum_pnl

    num_trades = len(trades)
    win_rate, avg_pnl, profit_factor, sharpe = _aggregate_stats(trades, pos_size)
    max_dd, max_dd_pct = _compute_drawdown(equity)

    return BacktestResult(
        trades=trades,
        equity_curve=equity.tolist(),
        total_pnl=float(cum_pnl),
        num_trades=num_trades,
        win_rate=win_rate,
        avg_pnl_per_trade=avg_pnl,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        profit_factor=profit_factor,
    )
