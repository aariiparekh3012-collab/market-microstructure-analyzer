"""Statistical + rule-based anomaly detection on order-book streams."""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timezone

from ..models import Anomaly, OrderBookSnapshot
from .spread import quoted_spread


def _as_datetime(ts: float | datetime) -> datetime:
    if isinstance(ts, datetime):
        return ts
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


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

    def _record_anomaly(
        self,
        out: list[Anomaly],
        *,
        symbol: str,
        ts: float | datetime,
        kind: str,
        value: float,
        stats: _RollingStats,
        detail_key: str,
        abs_mode: bool = False,
    ) -> None:
        z = stats.zscore(value)
        stats.push(value)
        if z is None:
            return

        trigger = abs(z) if abs_mode else z
        if (abs_mode and abs(z) > self.z_threshold) or (not abs_mode and z > self.z_threshold):
            ts_value = _as_datetime(ts)
            out.append(Anomaly(
                symbol=symbol,
                ts=ts_value,
                kind=kind,
                severity="warn" if trigger < self.z_threshold * 1.5 else "critical",
                detail={detail_key: round(value, 2) if detail_key == "ofi" else value, "zscore": round(z, 2)},
            ))

    def check(self, s: OrderBookSnapshot, ofi_rolling: float | None = None) -> list[Anomaly]:
        out: list[Anomaly] = []

        sp = quoted_spread(s)
        if sp is not None:
            self._record_anomaly(
                out,
                symbol=s.symbol,
                ts=s.ts,
                kind="spread_blowup",
                value=sp,
                stats=self.spread_stats,
                detail_key="spread",
            )

        if s.volume is not None:
            dvol = 0 if self._prev_vol is None else max(0, s.volume - self._prev_vol)
            self._prev_vol = s.volume
            self._record_anomaly(
                out,
                symbol=s.symbol,
                ts=s.ts,
                kind="volume_spike",
                value=float(dvol),
                stats=self.vol_stats,
                detail_key="tick_volume",
            )

        if ofi_rolling is not None:
            self._record_anomaly(
                out,
                symbol=s.symbol,
                ts=s.ts,
                kind="ofi_extreme",
                value=ofi_rolling,
                stats=self.ofi_stats,
                detail_key="ofi",
                abs_mode=True,
            )

        return out
