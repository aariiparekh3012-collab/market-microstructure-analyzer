"""Per-symbol analytics pipeline: spread, OFI, VWAP, volume, anomalies."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Anomaly, OrderBookSnapshot
from .anomaly_detector import AnomalyDetector
from .order_flow import OFICalculator
from .spread import quoted_spread, relative_spread, weighted_spread
from .volume import VolumeProfile
from .vwap import SessionVWAP


@dataclass
class SymbolAnalytics:
    ofi: OFICalculator = field(default_factory=OFICalculator)
    vwap: SessionVWAP = field(default_factory=SessionVWAP)
    volprof: VolumeProfile = field(default_factory=VolumeProfile)
    detector: AnomalyDetector = field(default_factory=AnomalyDetector)


class Engine:
    def __init__(self) -> None:
        self._per: dict[str, SymbolAnalytics] = {}

    def _ensure(self, symbol: str) -> SymbolAnalytics:
        if symbol not in self._per:
            self._per[symbol] = SymbolAnalytics()
        return self._per[symbol]

    def process(self, s: OrderBookSnapshot) -> tuple[dict, list[Anomaly]]:
        a = self._ensure(s.symbol)
        a.ofi.update(s)
        a.vwap.update(s)
        a.volprof.update(s)
        rolling = a.ofi.rolling(s.ts)
        anomalies = a.detector.check(s, ofi_rolling=rolling.get("ofi_60s"))

        metrics = {
            "symbol": s.symbol,
            "ts": s.ts.isoformat(),
            "ltp": s.ltp,
            "midprice": s.midprice,
            "spread": quoted_spread(s),
            "relative_spread": relative_spread(s),
            "weighted_spread": weighted_spread(s),
            **rolling,
            "vwap": a.vwap.vwap,
            "vwap_dev": a.vwap.deviation,
            "vwap_stdev": a.vwap.stdev,
            "cum_delta": a.volprof.cumulative_delta,
        }
        return metrics, anomalies

    def volume_profile(self, symbol: str) -> dict[float, int]:
        return self._ensure(symbol).volprof.profile
