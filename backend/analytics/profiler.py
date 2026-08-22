"""Latency profiler for the analytics pipeline.

Instruments each analytics module (spread, OFI, VWAP, volume, anomaly detection)
at the per-tick level, collecting high-resolution timing data for performance
analysis and reporting.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

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


@dataclass
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
    overhead_us: float  # total - sum(modules)


class ProfiledSymbolAnalytics:
    """Same as SymbolAnalytics but returns per-module timings."""

    def __init__(self) -> None:
        self.ofi = OFICalculator()
        self.vwap = SessionVWAP()
        self.volume_profile = VolumeProfile(bucket_size=0.5)
        self.kyle_lambda = KyleLambdaEstimator(window=300)
        self.amihud = AmihudEstimator(window=300)
        self.roll_spread = RollSpreadEstimator(window=200)
        self.trade_quote_variance = TradeQuoteVarianceEstimator(window=500)
        self.anomaly_detector = AnomalyDetector(
            spread_window=300,
            volume_window=300,
            ofi_window=300,
            z_threshold=3.0,
        )


class ProfiledEngine:
    """Drop-in replacement for Engine that collects per-tick latency data."""

    def __init__(self) -> None:
        self._analytics: dict[str, ProfiledSymbolAnalytics] = {}
        self.timings: list[TimingRecord] = []
        self._tick_counter = 0

    def _get(self, symbol: str) -> ProfiledSymbolAnalytics:
        if symbol not in self._analytics:
            self._analytics[symbol] = ProfiledSymbolAnalytics()
        return self._analytics[symbol]

    def process(
        self, snap: OrderBookSnapshot
    ) -> tuple[dict[str, Any], list[Anomaly]]:
        self._tick_counter += 1
        sa = self._get(snap.symbol)

        t_total_start = time.perf_counter_ns()

        # --- spreads ---
        t0 = time.perf_counter_ns()
        qs = quoted_spread(snap)
        rs = relative_spread(snap)
        ws = weighted_spread(snap, depth=5)
        t_spread = time.perf_counter_ns() - t0

        # --- OFI ---
        t0 = time.perf_counter_ns()
        ofi_event = sa.ofi.update(snap)
        ofi_rolling = sa.ofi.rolling(snap.ts)
        t_ofi = time.perf_counter_ns() - t0

        # --- VWAP ---
        t0 = time.perf_counter_ns()
        sa.vwap.update(snap)
        vwap_val = sa.vwap.vwap
        vwap_stdev = sa.vwap.stdev
        vwap_dev = (snap.ltp - vwap_val) / vwap_val if vwap_val else 0.0
        t_vwap = time.perf_counter_ns() - t0

        # --- Volume profile ---
        t0 = time.perf_counter_ns()
        sa.volume_profile.update(snap)
        cum_delta = sa.volume_profile.cumulative_delta
        t_volume = time.perf_counter_ns() - t0

        # --- Advanced diagnostics ---
        t0 = time.perf_counter_ns()
        kyle_res = sa.kyle_lambda.update(snap)
        t_kyle = time.perf_counter_ns() - t0

        t0 = time.perf_counter_ns()
        amihud_res = sa.amihud.update(snap)
        t_amihud = time.perf_counter_ns() - t0

        t0 = time.perf_counter_ns()
        roll_res = sa.roll_spread.update(snap)
        t_roll = time.perf_counter_ns() - t0

        t0 = time.perf_counter_ns()
        variance_res = sa.trade_quote_variance.update(snap)
        t_trade_quote = time.perf_counter_ns() - t0

        # --- Anomaly detection ---
        t0 = time.perf_counter_ns()
        anomalies = sa.anomaly_detector.check(snap, ofi_event)
        t_anomaly = time.perf_counter_ns() - t0

        t_total = time.perf_counter_ns() - t_total_start

        # Convert ns -> us
        module_sum = (
            t_spread
            + t_ofi
            + t_vwap
            + t_volume
            + t_kyle
            + t_amihud
            + t_roll
            + t_trade_quote
            + t_anomaly
        )
        self.timings.append(TimingRecord(
            tick_id=self._tick_counter,
            symbol=snap.symbol,
            total_us=t_total / 1000,
            spread_us=t_spread / 1000,
            ofi_us=t_ofi / 1000,
            vwap_us=t_vwap / 1000,
            volume_us=t_volume / 1000,
            kyle_us=t_kyle / 1000,
            amihud_us=t_amihud / 1000,
            roll_us=t_roll / 1000,
            trade_quote_us=t_trade_quote / 1000,
            anomaly_us=t_anomaly / 1000,
            overhead_us=(t_total - module_sum) / 1000,
        ))

        metrics: dict[str, Any] = {
            "symbol": snap.symbol,
            "timestamp": snap.ts.isoformat(),
            "ltp": snap.ltp,
            "midprice": snap.midprice,
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

    def summary(self) -> dict[str, Any]:
        """Generate a summary report of all collected timings."""
        if not self.timings:
            return {}

        totals = np.array([t.total_us for t in self.timings])
        spreads = np.array([t.spread_us for t in self.timings])
        ofis = np.array([t.ofi_us for t in self.timings])
        vwaps = np.array([t.vwap_us for t in self.timings])
        volumes = np.array([t.volume_us for t in self.timings])
        kyles = np.array([t.kyle_us for t in self.timings])
        amihuds = np.array([t.amihud_us for t in self.timings])
        rolls = np.array([t.roll_us for t in self.timings])
        trade_quotes = np.array([t.trade_quote_us for t in self.timings])
        anomalies = np.array([t.anomaly_us for t in self.timings])
        overheads = np.array([t.overhead_us for t in self.timings])

        def stats(arr):
            return {
                "mean_us": float(np.mean(arr)),
                "median_us": float(np.median(arr)),
                "p95_us": float(np.percentile(arr, 95)),
                "p99_us": float(np.percentile(arr, 99)),
                "max_us": float(np.max(arr)),
                "min_us": float(np.min(arr)),
                "std_us": float(np.std(arr)),
            }

        return {
            "total_ticks": len(self.timings),
            "total": stats(totals),
            "modules": {
                "spread": stats(spreads),
                "ofi": stats(ofis),
                "vwap": stats(vwaps),
                "volume": stats(volumes),
                "kyle": stats(kyles),
                "amihud": stats(amihuds),
                "roll": stats(rolls),
                "trade_quote": stats(trade_quotes),
                "anomaly_detection": stats(anomalies),
            },
            "overhead": stats(overheads),
            "mean_breakdown_pct": {
                "spread": float(np.mean(spreads) / np.mean(totals) * 100),
                "ofi": float(np.mean(ofis) / np.mean(totals) * 100),
                "vwap": float(np.mean(vwaps) / np.mean(totals) * 100),
                "volume": float(np.mean(volumes) / np.mean(totals) * 100),
                "kyle": float(np.mean(kyles) / np.mean(totals) * 100),
                "amihud": float(np.mean(amihuds) / np.mean(totals) * 100),
                "roll": float(np.mean(rolls) / np.mean(totals) * 100),
                "trade_quote": float(np.mean(trade_quotes) / np.mean(totals) * 100),
                "anomaly_detection": float(np.mean(anomalies) / np.mean(totals) * 100),
                "overhead": float(np.mean(overheads) / np.mean(totals) * 100),
            },
        }
