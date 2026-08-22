"""
Execution strategy simulator — TWAP and VWAP child-order simulators
that walk the order book and measure slippage / market impact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

# ── Result container ────────────────────────────────────────────────

@dataclass
class ExecutionResult:
    """Aggregated execution statistics for one simulation run."""

    fills: list[tuple[int, float, float, float]]  # (tick_idx, fill_qty, fill_price, slippage_bps)
    avg_fill_price: float
    arrival_price: float          # midprice at t=0
    vwap_price: float             # session VWAP across all ticks
    total_slippage_bps: float     # vs arrival price
    vwap_slippage_bps: float      # vs session VWAP
    implementation_shortfall: float  # (arrival - avg_fill) in bps, sign-aware
    last_fill_slippage_bps: float

    @property
    def market_impact_bps(self) -> float:
        """Deprecated alias retained for compatibility.

        The simulator does not model causal market impact; it measures the last
        fill's slippage relative to arrival.
        """
        return self.last_fill_slippage_bps


# ── Book-walking helper ─────────────────────────────────────────────

def _walk_book(
    tick: pd.Series,
    qty: float,
    side: Literal["buy", "sell"],
) -> tuple[float, float]:
    """Walk one side of the order-book for *qty* shares.

    Returns (filled_qty, vwap_fill_price).
    If the book cannot fully fill *qty*, we fill as much as possible.
    """
    prefix = "ask" if side == "buy" else "bid"

    total_filled = 0.0
    total_cost = 0.0
    remaining = qty

    for level in range(1, 6):
        px = tick.get(f"{prefix}_px_{level}", np.nan)
        sz = tick.get(f"{prefix}_qty_{level}", 0)
        if pd.isna(px) or sz <= 0 or remaining <= 0:
            continue
        fill = min(remaining, sz)
        total_filled += fill
        total_cost += fill * px
        remaining -= fill
        if remaining <= 0:
            break

    if total_filled == 0:
        return 0.0, np.nan
    return total_filled, total_cost / total_filled


def _midprice(tick: pd.Series) -> float:
    return (tick["bid_px_1"] + tick["ask_px_1"]) / 2.0


def _session_vwap(ticks: pd.DataFrame) -> float:
    """Volume-weighted average price across all ticks using ltp & ltq."""
    qty = (
        ticks["ltq"]
        if "ltq" in ticks.columns
        else ticks["volume"].diff().clip(lower=1).fillna(1)
    )
    total = (ticks["ltp"] * qty).sum()
    vol = qty.sum()
    if vol == 0:
        return ticks["ltp"].mean()
    return total / vol


# ── Executors ───────────────────────────────────────────────────────

class TWAPExecutor:
    """Time-Weighted Average Price execution: equal-sized child orders
    spread evenly across the tick window."""

    def __init__(
        self,
        target_qty: float,
        num_slices: int = 10,
        side: Literal["buy", "sell"] = "buy",
    ):
        self.target_qty = target_qty
        self.num_slices = num_slices
        self.side = side

    def schedule(self, n_ticks: int) -> list[tuple[int, float]]:
        """Return [(tick_index, child_qty), …]."""
        if n_ticks <= 0 or self.num_slices <= 0 or self.target_qty <= 0:
            return []
        # If fewer ticks than slices, compress into available ticks
        effective_slices = min(self.num_slices, n_ticks)
        base_qty = self.target_qty / effective_slices
        indices = np.linspace(0, n_ticks - 1, effective_slices, dtype=int)

        schedule: list[tuple[int, float]] = []
        remaining = self.target_qty
        for i, idx in enumerate(indices):
            child_qty = (
                remaining if i == len(indices) - 1 else math.floor(base_qty)
            )
            child_qty = min(child_qty, remaining)
            if child_qty > 0:
                schedule.append((int(idx), child_qty))
                remaining -= child_qty
        return schedule


class VWAPExecutor:
    """Volume-Weighted Average Price execution: child order sizes
    proportional to the historical volume profile."""

    def __init__(
        self,
        target_qty: float,
        num_slices: int = 10,
        side: Literal["buy", "sell"] = "buy",
    ):
        self.target_qty = target_qty
        self.num_slices = num_slices
        self.side = side

    def schedule(self, n_ticks: int, volumes: np.ndarray) -> list[tuple[int, float]]:
        """Return [(tick_index, child_qty), …] weighted by volume buckets."""
        if n_ticks <= 0 or self.num_slices <= 0 or self.target_qty <= 0:
            return []
        effective_slices = min(self.num_slices, n_ticks)
        indices = np.linspace(0, n_ticks - 1, effective_slices, dtype=int)

        # Build volume buckets around each scheduled tick
        bucket_vols = np.zeros(effective_slices)
        boundaries = np.concatenate([
            [0],
            ((indices[:-1] + indices[1:]) / 2).astype(int),
            [n_ticks],
        ])
        for i in range(effective_slices):
            lo, hi = int(boundaries[i]), int(boundaries[i + 1])
            bucket_vols[i] = volumes[lo:hi].sum()

        total_vol = bucket_vols.sum()
        if total_vol == 0:
            # Fallback to equal weighting
            weights = np.ones(effective_slices) / effective_slices
        else:
            weights = bucket_vols / total_vol

        schedule: list[tuple[int, float]] = []
        remaining = self.target_qty
        for i, idx in enumerate(indices):
            if i == len(indices) - 1:
                child_qty = remaining
            else:
                child_qty = math.floor(self.target_qty * weights[i])
            child_qty = min(child_qty, remaining)
            if child_qty > 0:
                schedule.append((int(idx), child_qty))
                remaining -= child_qty
        return schedule


# ── Main simulation entry-point ─────────────────────────────────────

def simulate(
    ticks_df: pd.DataFrame,
    executor: TWAPExecutor | VWAPExecutor,
) -> ExecutionResult:
    """Run a child-order simulation over *ticks_df* using *executor*.

    Parameters
    ----------
    ticks_df : DataFrame
        Must contain bid_px_1..5, bid_qty_1..5, ask_px_1..5, ask_qty_1..5,
        ltp, and volume columns.  Should be pre-filtered to one symbol.
    executor : TWAPExecutor | VWAPExecutor
        Configured executor instance.

    Returns
    -------
    ExecutionResult
    """
    ticks_df = ticks_df.reset_index(drop=True)
    n_ticks = len(ticks_df)
    if n_ticks == 0:
        raise ValueError("execution simulation requires at least one tick")

    # Arrival price = midprice of first tick
    arrival = _midprice(ticks_df.iloc[0])

    # Session VWAP
    vwap = _session_vwap(ticks_df)

    # Build volume profile (per-tick traded quantity)
    if "ltq" in ticks_df.columns:
        vol_profile = ticks_df["ltq"].values.astype(float)
    else:
        vol_profile = ticks_df["volume"].diff().clip(lower=0).fillna(0).values.astype(float)

    # Get schedule
    if isinstance(executor, VWAPExecutor):
        schedule = executor.schedule(n_ticks, vol_profile)
    else:
        schedule = executor.schedule(n_ticks)

    side = executor.side

    # Execute each child order
    fills: list[tuple[int, float, float, float]] = []
    total_filled = 0.0
    total_cost = 0.0

    for tick_idx, child_qty in schedule:
        tick = ticks_df.iloc[tick_idx]
        filled, fill_px = _walk_book(tick, child_qty, side)
        if filled == 0 or np.isnan(fill_px):
            continue

        # Slippage vs arrival in bps
        if side == "buy":
            slip_bps = (fill_px - arrival) / arrival * 10_000
        else:
            slip_bps = (arrival - fill_px) / arrival * 10_000

        fills.append((tick_idx, filled, fill_px, round(slip_bps, 4)))
        total_filled += filled
        total_cost += filled * fill_px

    if total_filled == 0:
        return ExecutionResult(
            fills=[],
            avg_fill_price=np.nan,
            arrival_price=arrival,
            vwap_price=vwap,
            total_slippage_bps=0.0,
            vwap_slippage_bps=0.0,
            implementation_shortfall=0.0,
            last_fill_slippage_bps=0.0,
        )

    avg_fill = total_cost / total_filled

    if side == "buy":
        total_slip_bps = (avg_fill - arrival) / arrival * 10_000
        vwap_slip_bps = (avg_fill - vwap) / vwap * 10_000
        impl_shortfall = (avg_fill - arrival) / arrival * 10_000
    else:
        total_slip_bps = (arrival - avg_fill) / arrival * 10_000
        vwap_slip_bps = (vwap - avg_fill) / vwap * 10_000
        impl_shortfall = (arrival - avg_fill) / arrival * 10_000

    # Market impact: difference between last fill price and arrival, in bps
    last_fill_px = fills[-1][2]
    if side == "buy":
        impact_bps = (last_fill_px - arrival) / arrival * 10_000
    else:
        impact_bps = (arrival - last_fill_px) / arrival * 10_000

    return ExecutionResult(
        fills=fills,
        avg_fill_price=round(avg_fill, 4),
        arrival_price=round(arrival, 4),
        vwap_price=round(vwap, 4),
        total_slippage_bps=round(total_slip_bps, 4),
        vwap_slippage_bps=round(vwap_slip_bps, 4),
        implementation_shortfall=round(impl_shortfall, 4),
        last_fill_slippage_bps=round(impact_bps, 4),
    )
