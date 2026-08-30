"""Execution strategy simulator.

TWAP / VWAP child-order simulators that walk the top-of-book plus four
depth levels and measure slippage vs arrival and vs session VWAP.

The book replay is *static*: simulated child orders do not perturb the
subsequent book state. `last_fill_slippage_bps` is therefore a slippage
measurement, not causal market impact.

Perf note: the previous implementation called `ticks_df.iloc[tick_idx]`
inside the fill loop (per-child-order Series construction) and then read
each of ten book fields with `Series.get(default=np.nan)`. Both are slow
on a pandas Series. This version extracts the ten book columns into
plain numpy arrays once and indexes them positionally.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ExecutionResult:
    """Aggregated execution statistics for one simulation run."""

    # (tick_idx, fill_qty, fill_price, slippage_bps_vs_arrival)
    fills: list[tuple[int, float, float, float]] = field(default_factory=list)
    avg_fill_price: float = float("nan")
    arrival_price: float = 0.0
    vwap_price: float = 0.0
    total_slippage_bps: float = 0.0
    vwap_slippage_bps: float = 0.0
    implementation_shortfall: float = 0.0
    last_fill_slippage_bps: float = 0.0

    @property
    def market_impact_bps(self) -> float:
        """Deprecated alias retained for existing callers. See docstring."""
        return self.last_fill_slippage_bps


# ---------------------------------------------------------------------------
# Book extraction (once per simulation)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _BookArrays:
    """Ten book columns pulled into numpy so the inner fill loop indexes
    plain arrays rather than a fresh pandas Series per child order."""

    bid_px: np.ndarray  # shape (n_ticks, 5)
    bid_qty: np.ndarray
    ask_px: np.ndarray
    ask_qty: np.ndarray

    @classmethod
    def from_df(cls, df: pd.DataFrame) -> "_BookArrays":
        def _stack(prefix: str, field: str, dtype) -> np.ndarray:
            cols = [f"{prefix}_{field}_{i}" for i in range(1, 6)]
            missing = [c for c in cols if c not in df.columns]
            if missing:
                raise ValueError(f"execution sim: missing book columns: {missing}")
            return df[cols].to_numpy(dtype=dtype, copy=False)

        return cls(
            bid_px=_stack("bid", "px", np.float64),
            bid_qty=_stack("bid", "qty", np.float64),
            ask_px=_stack("ask", "px", np.float64),
            ask_qty=_stack("ask", "qty", np.float64),
        )


def _walk_book(
    book: _BookArrays, tick_idx: int, qty: float, side: Literal["buy", "sell"],
) -> tuple[float, float]:
    """Walk one side of the book at `tick_idx` for `qty` shares.

    Returns (filled_qty, vwap_fill_price). Partial fills are honoured.
    """
    if side == "buy":
        pxs = book.ask_px[tick_idx]
        qts = book.ask_qty[tick_idx]
    else:
        pxs = book.bid_px[tick_idx]
        qts = book.bid_qty[tick_idx]

    total_filled = 0.0
    total_cost = 0.0
    remaining = qty
    for k in range(5):
        px = pxs[k]
        sz = qts[k]
        if not np.isfinite(px) or sz <= 0 or remaining <= 0:
            continue
        fill = remaining if remaining < sz else sz
        total_filled += fill
        total_cost += fill * px
        remaining -= fill
        if remaining <= 0:
            break

    if total_filled == 0.0:
        return 0.0, float("nan")
    return total_filled, total_cost / total_filled


def _midprice(book: _BookArrays, tick_idx: int) -> float:
    return (book.bid_px[tick_idx, 0] + book.ask_px[tick_idx, 0]) / 2.0


def _session_vwap(ticks: pd.DataFrame) -> float:
    """VWAP across all ticks using ltp × ltq (falls back to Δvolume)."""
    if "ltq" in ticks.columns:
        qty = ticks["ltq"].to_numpy(dtype=np.float64)
    else:
        qty = ticks["volume"].diff().clip(lower=1).fillna(1).to_numpy(dtype=np.float64)
    ltp = ticks["ltp"].to_numpy(dtype=np.float64)
    vol = float(qty.sum())
    if vol == 0.0:
        return float(ltp.mean())
    return float((ltp * qty).sum() / vol)


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


class TWAPExecutor:
    """Equal-sized child orders spread evenly across the tick window."""

    __slots__ = ("target_qty", "num_slices", "side")

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
        if n_ticks <= 0 or self.num_slices <= 0 or self.target_qty <= 0:
            return []
        effective = min(self.num_slices, n_ticks)
        base_qty = self.target_qty / effective
        indices = np.linspace(0, n_ticks - 1, effective, dtype=int)

        out: list[tuple[int, float]] = []
        remaining = self.target_qty
        last = len(indices) - 1
        for i, idx in enumerate(indices):
            child_qty = remaining if i == last else math.floor(base_qty)
            child_qty = min(child_qty, remaining)
            if child_qty > 0:
                out.append((int(idx), float(child_qty)))
                remaining -= child_qty
        return out


class VWAPExecutor:
    """Child-order sizes proportional to a supplied volume profile."""

    __slots__ = ("target_qty", "num_slices", "side")

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
        if n_ticks <= 0 or self.num_slices <= 0 or self.target_qty <= 0:
            return []
        effective = min(self.num_slices, n_ticks)
        indices = np.linspace(0, n_ticks - 1, effective, dtype=int)

        # Volume buckets around each scheduled index — vectorised via cumsum.
        boundaries = np.concatenate([
            [0],
            ((indices[:-1] + indices[1:]) / 2).astype(int),
            [n_ticks],
        ])
        cum = np.concatenate([[0.0], np.cumsum(volumes, dtype=np.float64)])
        bucket_vols = cum[boundaries[1:]] - cum[boundaries[:-1]]

        total_vol = float(bucket_vols.sum())
        weights = (
            bucket_vols / total_vol
            if total_vol > 0.0
            else np.full(effective, 1.0 / effective)
        )

        out: list[tuple[int, float]] = []
        remaining = self.target_qty
        last = len(indices) - 1
        for i, idx in enumerate(indices):
            child_qty = (
                remaining if i == last
                else math.floor(self.target_qty * weights[i])
            )
            child_qty = min(child_qty, remaining)
            if child_qty > 0:
                out.append((int(idx), float(child_qty)))
                remaining -= child_qty
        return out


# ---------------------------------------------------------------------------
# Main simulation entry point
# ---------------------------------------------------------------------------


def simulate(
    ticks_df: pd.DataFrame,
    executor: TWAPExecutor | VWAPExecutor,
) -> ExecutionResult:
    """Run a child-order simulation over `ticks_df` using `executor`.

    `ticks_df` must contain `bid_px_1..5`, `bid_qty_1..5`, `ask_px_1..5`,
    `ask_qty_1..5`, `ltp`, and `volume` (or `ltq`) columns, pre-filtered
    to one symbol.
    """
    ticks_df = ticks_df.reset_index(drop=True)
    n_ticks = len(ticks_df)
    if n_ticks == 0:
        raise ValueError("execution simulation requires at least one tick")

    book = _BookArrays.from_df(ticks_df)
    arrival = _midprice(book, 0)
    vwap = _session_vwap(ticks_df)

    if "ltq" in ticks_df.columns:
        vol_profile = ticks_df["ltq"].to_numpy(dtype=np.float64)
    else:
        vol_profile = ticks_df["volume"].diff().clip(lower=0).fillna(0).to_numpy(dtype=np.float64)

    schedule = (
        executor.schedule(n_ticks, vol_profile)
        if isinstance(executor, VWAPExecutor)
        else executor.schedule(n_ticks)
    )
    side = executor.side
    sign = 1.0 if side == "buy" else -1.0

    fills: list[tuple[int, float, float, float]] = []
    total_filled = 0.0
    total_cost = 0.0

    for tick_idx, child_qty in schedule:
        filled, fill_px = _walk_book(book, tick_idx, child_qty, side)
        if filled == 0.0 or not np.isfinite(fill_px):
            continue
        slip_bps = sign * (fill_px - arrival) / arrival * 10_000.0
        fills.append((tick_idx, filled, fill_px, round(float(slip_bps), 4)))
        total_filled += filled
        total_cost += filled * fill_px

    if total_filled == 0.0:
        return ExecutionResult(arrival_price=arrival, vwap_price=vwap)

    avg_fill = total_cost / total_filled
    slip_arrival = sign * (avg_fill - arrival) / arrival * 10_000.0
    slip_vwap = sign * (avg_fill - vwap) / vwap * 10_000.0
    last_slip = sign * (fills[-1][2] - arrival) / arrival * 10_000.0

    return ExecutionResult(
        fills=fills,
        avg_fill_price=round(float(avg_fill), 4),
        arrival_price=round(float(arrival), 4),
        vwap_price=round(float(vwap), 4),
        total_slippage_bps=round(float(slip_arrival), 4),
        vwap_slippage_bps=round(float(slip_vwap), 4),
        # Implementation shortfall is the sign-aware slippage of the achieved
        # average fill vs the arrival price. For a static book replay this
        # coincides with `total_slippage_bps` — kept separate for API clarity
        # and to make the semantics obvious to callers.
        implementation_shortfall=round(float(slip_arrival), 4),
        last_fill_slippage_bps=round(float(last_slip), 4),
    )
