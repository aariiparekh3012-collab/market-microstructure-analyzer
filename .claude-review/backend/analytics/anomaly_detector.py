"""Statistical + rule-based anomaly detection on order-book streams."""
from __future__ import annotations

import math
from collections import deque

from ..models import Anomaly, OrderBookSnapshot
from .spread import quoted_spread


class _RollingStats:
    def __init__(self, maxlen: int) -> None:
        self._buf: deque[float] = deque(maxlen=maxlen)
        self._sum = 0.0
        self._sum2 = 0.0

    def push(self, x: float) -> None:
        if math.isfinite(x):
            if len(self._buf) == self._buf.maxlen:
                expired = self._buf[0]
                self._sum -= expired
                self._sum2 -= expired * expired
            self._buf.append(x)
            self._sum += x
            self._sum2 += x * x

    def mean(self) -> float | None:
        return self._sum / len(self._buf) if self._buf else None

    def std(self) -> float | None:
        n = len(self._buf)
        if n < 5:
            return None
        m = self._sum / n
        var = max(0.0, self._sum2 / n - m * m)
        return math.sqrt(var)

    def zscore(self, x: float) -> float | None:
        s = self.std()
        m = self.mean()
        if s is None or m is None or s == 0:
            return None
        return (x - m) / s


class AnomalyDetector:
    def __init__(
        self,
        spread_window: int = 300,
        volume_window: int = 300,
        ofi_window: int = 300,
        z_threshold: float = 3.0,
    ) -> None:
        self.spread_stats = _RollingStats(spread_window)
        self.vol_stats = _RollingStats(volume_window)
        self.ofi_stats = _RollingStats(ofi_window)
        self.z_threshold = z_threshold
        self._prev_vol: int | None = None

    def check(self, s: OrderBookSnapshot, ofi_rolling: float | None = None) -> list[Anomaly]:
        out: list[Anomaly] = []

        sp = quoted_spread(s)
        if sp is not None:
            z = self.spread_stats.zscore(sp)
            self.spread_stats.push(sp)
            if z is not None and z > self.z_threshold:
                out.append(Anomaly(
                    symbol=s.symbol, ts=s.ts, kind="spread_blowup",
                    severity="warn" if z < self.z_threshold * 1.5 else "critical",
                    detail={"spread": sp, "zscore": round(z, 2)},
                ))

        if s.volume is not None:
            dvol = 0 if self._prev_vol is None else max(0, s.volume - self._prev_vol)
            self._prev_vol = s.volume
            z = self.vol_stats.zscore(dvol)
            self.vol_stats.push(float(dvol))
            if z is not None and z > self.z_threshold:
                out.append(Anomaly(
                    symbol=s.symbol, ts=s.ts, kind="volume_spike",
                    severity="warn" if z < self.z_threshold * 1.5 else "critical",
                    detail={"tick_volume": dvol, "zscore": round(z, 2)},
                ))

        if ofi_rolling is not None:
            z = self.ofi_stats.zscore(ofi_rolling)
            self.ofi_stats.push(ofi_rolling)
            if z is not None and abs(z) > self.z_threshold:
                out.append(Anomaly(
                    symbol=s.symbol, ts=s.ts, kind="ofi_extreme",
                    severity="warn" if abs(z) < self.z_threshold * 1.5 else "critical",
                    detail={"ofi": round(ofi_rolling, 2), "zscore": round(z, 2)},
                ))

        return out
