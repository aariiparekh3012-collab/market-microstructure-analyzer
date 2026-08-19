"""Central analytics engine — aggregates all per-symbol analytics."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from backend.models import Anomaly, OrderBookSnapshot
from backend.analytics.spread import quoted_spread, relative_spread, weighted_spread
from backend.analytics.order_flow import OFICalculator
from backend.analytics.vwap import SessionVWAP
from backend.analytics.volume import VolumeProfile
from backend.analytics.anomaly_detector import AnomalyDetector
from backend.analytics.kyle_lambda import KyleLambdaEstimator
from backend.analytics.amihud import AmihudEstimator
from backend.analytics.roll_spread import RollSpreadEstimator
from backend.analytics.hasbrouck import HasbrouckEstimator


class SymbolAnalytics:
    """Holds all analytics state for a single symbol."""

    def __init__(self) -> None:
        self.ofi = OFICalculator()
        self.vwap = SessionVWAP()
        self.volume_profile = VolumeProfile(bucket_size=0.5)
        self.anomaly_detector = AnomalyDetector(z_threshold=3.0, window=300)
        # Advanced microstructure estimators
        self.kyle_lambda = KyleLambdaEstimator(window=300)
        self.amihud = AmihudEstimator(window=300)
        self.roll_spread = RollSpreadEstimator(window=200)
        self.hasbrouck = HasbrouckEstimator(window=500)


class Engine:
    """Process order-book snapshots and return metrics + anomalies."""

    def __init__(self) -> None:
        self._analytics: Dict[str, SymbolAnalytics] = {}

    def _get(self, symbol: str) -> SymbolAnalytics:
        if symbol not in self._analytics:
            self._analytics[symbol] = SymbolAnalytics()
        return self._analytics[symbol]

    def process(
        self, snap: OrderBookSnapshot
    ) -> Tuple[Dict[str, Any], List[Anomaly]]:
        """Process a single snapshot.

        Returns:
            (metrics_dict, anomalies_list)
        """
        sa = self._get(snap.symbol)

        # --- spreads ---
        qs = quoted_spread(snap)
        rs = relative_spread(snap)
        ws = weighted_spread(snap, depth=5)

        # --- OFI ---
        ofi_event = sa.ofi.update(snap)
        ofi_rolling = sa.ofi.rolling(snap.timestamp)

        # --- VWAP ---
        sa.vwap.update(snap)
        vwap_val = sa.vwap.vwap
        vwap_stdev = sa.vwap.stdev
        vwap_dev = (snap.ltp - vwap_val) / vwap_val if vwap_val else 0.0

        # --- Volume profile ---
        sa.volume_profile.update(snap)
        cum_delta = sa.volume_profile.cum_delta

        # --- Kyle's Lambda (price impact) ---
        kyle_res = sa.kyle_lambda.update(snap)
        kyle_lambda = kyle_res.lambda_val if kyle_res else None
        kyle_r2 = kyle_res.r_squared if kyle_res else None
        kyle_t = kyle_res.t_statistic if kyle_res else None

        # --- Amihud Illiquidity ---
        amihud_res = sa.amihud.update(snap)
        amihud_illiq = amihud_res.illiq_avg if amihud_res else None

        # --- Roll's Implied Spread ---
        roll_res = sa.roll_spread.update(snap)
        roll_spread_val = roll_res.implied_spread_bps if roll_res else None
        roll_cov = roll_res.serial_cov if roll_res else None

        # --- Hasbrouck Information Share ---
        hasb_res = sa.hasbrouck.update(snap)
        trade_info_share = hasb_res.trade_info_share if hasb_res else None
        perm_impact = hasb_res.permanent_impact_bps if hasb_res else None

        # --- Anomaly detection ---
        anomalies = sa.anomaly_detector.check(snap, ofi_event)

        metrics: Dict[str, Any] = {
            "symbol": snap.symbol,
            "timestamp": snap.timestamp.isoformat(),
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
            # Advanced microstructure metrics
            "kyle_lambda": kyle_lambda,
            "kyle_r2": kyle_r2,
            "kyle_t_stat": kyle_t,
            "amihud_illiq": amihud_illiq,
            "roll_spread_bps": roll_spread_val,
            "roll_serial_cov": roll_cov,
            "trade_info_share": trade_info_share,
            "permanent_impact_bps": perm_impact,
        }

        return metrics, anomalies
