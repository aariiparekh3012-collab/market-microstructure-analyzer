"""Central analytics engine — aggregates all per-symbol analytics.

`Engine.process` is the sole owner of the metrics-dict schema. Instrumented
variants (`ProfiledEngine`) run the same modules through `_ModuleRunner` hooks
so a new signal added here does not need to be added anywhere else.
"""

from __future__ import annotations

from typing import Any, Protocol

from backend.analytics.amihud import AmihudEstimator
from backend.analytics.anomaly_detector import AnomalyDetector
from backend.analytics.hasbrouck import TradeQuoteVarianceEstimator
from backend.analytics.kyle_lambda import KyleLambdaEstimator
from backend.analytics.order_flow import OFICalculator
from backend.analytics.roll_spread import RollSpreadEstimator
from backend.analytics.spread import quoted_spread, relative_spread, weighted_spread
from backend.analytics.volume import VolumeProfile
from backend.analytics.vwap import SessionVWAP
from backend.models import Anomaly, OrderBookSnapshot


class SymbolAnalytics:
    """Holds all analytics state for a single symbol."""

    __slots__ = (
        "ofi",
        "vwap",
        "volume_profile",
        "anomaly_detector",
        "kyle_lambda",
        "amihud",
        "roll_spread",
        "trade_quote_variance",
    )

    def __init__(self) -> None:
        self.ofi = OFICalculator()
        self.vwap = SessionVWAP()
        self.volume_profile = VolumeProfile(bucket_size=0.5)
        self.anomaly_detector = AnomalyDetector(
            spread_window=300,
            volume_window=300,
            ofi_window=300,
            z_threshold=3.0,
        )
        self.kyle_lambda = KyleLambdaEstimator(window=300)
        self.amihud = AmihudEstimator(window=300)
        self.roll_spread = RollSpreadEstimator(window=200)
        self.trade_quote_variance = TradeQuoteVarianceEstimator(window=500)


class _ModuleTimer(Protocol):
    """Hook invoked by `_run_modules` around each analytics module.

    The base `Engine` uses a no-op timer; `ProfiledEngine` supplies one that
    records `time.perf_counter_ns()` into a per-tick timing record.
    """

    def __call__(self, name: str, /) -> "_TimerHandle": ...


class _TimerHandle(Protocol):
    def __enter__(self) -> None: ...
    def __exit__(self, *exc: object) -> None: ...


class _NoopTimer:
    """Zero-overhead timer used by the production Engine."""

    def __call__(self, _name: str, /) -> "_NoopTimer":
        return self

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: object) -> None:
        return None


_NOOP_TIMER = _NoopTimer()


def _run_modules(
    sa: SymbolAnalytics,
    snap: OrderBookSnapshot,
    timer: _ModuleTimer = _NOOP_TIMER,
) -> tuple[dict[str, Any], list[Anomaly]]:
    """Run every analytics module against `snap` and build the metrics dict.

    This is the single source of truth for what a "tick's metrics" contains.
    A profiler wraps each module call in its own timing context via `timer`;
    the default no-op timer has zero measurable overhead.
    """
    # Cache midprice / ltp once — cheaper than re-walking properties from
    # each estimator (there are 5+ midprice reads per tick otherwise).
    mid = snap.midprice
    ltp = snap.ltp

    with timer("spread"):
        qs = quoted_spread(snap)
        rs = relative_spread(snap)
        ws = weighted_spread(snap, depth=5)

    with timer("ofi"):
        ofi_event = sa.ofi.update(snap)
        ofi_rolling = sa.ofi.rolling(snap.ts)

    with timer("vwap"):
        sa.vwap.update(snap)
        vwap_val = sa.vwap.vwap
        vwap_stdev = sa.vwap.stdev
        vwap_dev = (ltp - vwap_val) / vwap_val if (ltp is not None and vwap_val) else 0.0

    with timer("volume"):
        sa.volume_profile.update(snap)
        cum_delta = sa.volume_profile.cumulative_delta

    with timer("kyle"):
        kyle_res = sa.kyle_lambda.update(snap)

    with timer("amihud"):
        amihud_res = sa.amihud.update(snap)

    with timer("roll"):
        roll_res = sa.roll_spread.update(snap)

    with timer("trade_quote"):
        variance_res = sa.trade_quote_variance.update(snap)

    with timer("anomaly"):
        anomalies = sa.anomaly_detector.check(snap, ofi_event)

    metrics: dict[str, Any] = {
        "symbol": snap.symbol,
        "timestamp": snap.ts.isoformat(),
        "ltp": ltp,
        "midprice": mid,
        "spread": qs,
        "relative_spread": rs,
        "weighted_spread": ws,
        "ofi_60s": ofi_rolling["ofi_60s"],
        "ofi_300s": ofi_rolling["ofi_300s"],
        "ofi_900s": ofi_rolling["ofi_900s"],
        "vwap": vwap_val,
        "vwap_dev": vwap_dev,
        "vwap_stdev": vwap_stdev,
        "cum_delta": cum_delta,
        "kyle_lambda": kyle_res.lambda_val if kyle_res else None,
        "kyle_r2": kyle_res.r_squared if kyle_res else None,
        "kyle_t_stat": kyle_res.t_statistic if kyle_res else None,
        "amihud_illiq": amihud_res.illiq_avg if amihud_res else None,
        "roll_spread_bps": roll_res.implied_spread_bps if roll_res else None,
        "roll_serial_cov": roll_res.serial_cov if roll_res else None,
        "trade_variance_share": (
            variance_res.trade_variance_share if variance_res else None
        ),
        "avg_signed_trade_return_bps": (
            variance_res.avg_signed_return_bps if variance_res else None
        ),
    }
    return metrics, anomalies


class Engine:
    """Process order-book snapshots and return metrics + anomalies."""

    def __init__(self) -> None:
        self._analytics: dict[str, SymbolAnalytics] = {}

    def _get(self, symbol: str) -> SymbolAnalytics:
        sa = self._analytics.get(symbol)
        if sa is None:
            sa = self._analytics[symbol] = SymbolAnalytics()
        return sa

    def process(
        self, snap: OrderBookSnapshot
    ) -> tuple[dict[str, Any], list[Anomaly]]:
        """Process a single snapshot and return (metrics, anomalies)."""
        return _run_modules(self._get(snap.symbol), snap)

    def volume_profile(self, symbol: str) -> dict[float, int]:
        """Return the current volume profile without creating symbol state."""
        analytics = self._analytics.get(symbol)
        return analytics.volume_profile.profile if analytics else {}
