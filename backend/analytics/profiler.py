"""Latency profiler for the analytics pipeline.

Instruments each analytics module at the per-tick level with nanosecond
timing, collected for percentile/breakdown reporting.

The processing pipeline itself is the same one the production `Engine`
runs — profiling is achieved by supplying a timing hook into
`engine._run_modules`, which prevents the "two copies of the metrics dict
that must be kept in sync" bug the previous ProfiledEngine had.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from backend.analytics.engine import SymbolAnalytics, _run_modules
from backend.models import Anomaly, OrderBookSnapshot


@dataclass(slots=True)
class TimingRecord:
    """Single tick's timing breakdown in microseconds."""
    tick_id: int
    symbol: str
    total_us: float
    spread_us: float
    ofi_us: float
    vwap_us: float
    volume_us: float
    kyle_us: float
    amihud_us: float
    roll_us: float
    trade_quote_us: float
    anomaly_us: float
    overhead_us: float  # total − sum(modules)


# Fields on TimingRecord that correspond to a named `timer(name)` call in
# `_run_modules`. Kept as a plain tuple so both the profiler and the summary
# report can iterate without a magic-string in the middle.
_MODULES: tuple[str, ...] = (
    "spread",
    "ofi",
    "vwap",
    "volume",
    "kyle",
    "amihud",
    "roll",
    "trade_quote",
    "anomaly",
)


class _NsBlock:
    """Context manager returned by `_NsTimer.__call__` — records ns into a dict."""

    __slots__ = ("_store", "_key", "_t0")

    def __init__(self, store: dict[str, int], key: str) -> None:
        self._store = store
        self._key = key

    def __enter__(self) -> None:
        self._t0 = time.perf_counter_ns()

    def __exit__(self, *_exc: object) -> None:
        self._store[self._key] = time.perf_counter_ns() - self._t0


class _NsTimer:
    """Timer hook passed into `_run_modules`. Fills a per-tick ns-timing dict."""

    __slots__ = ("store",)

    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    def __call__(self, name: str, /) -> _NsBlock:
        return _NsBlock(self.store, name)


class ProfiledEngine:
    """Same pipeline as `Engine`, with per-module latency timings.

    Metrics semantics come from `engine._run_modules`, so a new signal added
    in the base engine automatically flows through the profiler with no
    parallel edit needed.
    """

    def __init__(self) -> None:
        self._analytics: dict[str, SymbolAnalytics] = {}
        self.timings: list[TimingRecord] = []
        self._tick_counter = 0

    def _get(self, symbol: str) -> SymbolAnalytics:
        sa = self._analytics.get(symbol)
        if sa is None:
            sa = self._analytics[symbol] = SymbolAnalytics()
        return sa

    def process(
        self, snap: OrderBookSnapshot
    ) -> tuple[dict[str, Any], list[Anomaly]]:
        self._tick_counter += 1
        sa = self._get(snap.symbol)

        timer = _NsTimer()
        t_total_start = time.perf_counter_ns()
        metrics, anomalies = _run_modules(sa, snap, timer)
        t_total = time.perf_counter_ns() - t_total_start

        module_sum = sum(timer.store.values())
        self.timings.append(TimingRecord(
            tick_id=self._tick_counter,
            symbol=snap.symbol,
            total_us=t_total / 1000,
            spread_us=timer.store.get("spread", 0) / 1000,
            ofi_us=timer.store.get("ofi", 0) / 1000,
            vwap_us=timer.store.get("vwap", 0) / 1000,
            volume_us=timer.store.get("volume", 0) / 1000,
            kyle_us=timer.store.get("kyle", 0) / 1000,
            amihud_us=timer.store.get("amihud", 0) / 1000,
            roll_us=timer.store.get("roll", 0) / 1000,
            trade_quote_us=timer.store.get("trade_quote", 0) / 1000,
            anomaly_us=timer.store.get("anomaly", 0) / 1000,
            overhead_us=(t_total - module_sum) / 1000,
        ))

        return metrics, anomalies

    def volume_profile(self, symbol: str) -> dict[float, int]:
        analytics = self._analytics.get(symbol)
        return analytics.volume_profile.profile if analytics else {}

    def summary(self) -> dict[str, Any]:
        """Aggregate summary report over every recorded tick."""
        if not self.timings:
            return {}

        # One np.array per column, driven by _MODULES so adding a module in
        # `_run_modules` and TimingRecord shows up here automatically.
        totals = np.fromiter((t.total_us for t in self.timings), dtype=float)
        overheads = np.fromiter((t.overhead_us for t in self.timings), dtype=float)
        module_arrays: dict[str, np.ndarray] = {
            m: np.fromiter(
                (getattr(t, f"{m}_us") for t in self.timings), dtype=float
            )
            for m in _MODULES
        }

        def stats(arr: np.ndarray) -> dict[str, float]:
            return {
                "mean_us": float(np.mean(arr)),
                "median_us": float(np.median(arr)),
                "p95_us": float(np.percentile(arr, 95)),
                "p99_us": float(np.percentile(arr, 99)),
                "max_us": float(np.max(arr)),
                "min_us": float(np.min(arr)),
                "std_us": float(np.std(arr)),
            }

        total_mean = float(np.mean(totals))
        breakdown = {
            m: float(np.mean(arr) / total_mean * 100)
            for m, arr in module_arrays.items()
        }
        breakdown["overhead"] = float(np.mean(overheads) / total_mean * 100)

        return {
            "total_ticks": len(self.timings),
            "total": stats(totals),
            "modules": {m: stats(arr) for m, arr in module_arrays.items()},
            "overhead": stats(overheads),
            "mean_breakdown_pct": breakdown,
        }
