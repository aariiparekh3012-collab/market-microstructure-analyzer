"""Session VWAP + deviation bands."""
from __future__ import annotations

import math
from datetime import date

from ..models import OrderBookSnapshot


class SessionVWAP:
    def __init__(self) -> None:
        self._session_date: date | None = None
        self._pv: float = 0.0
        self._vol: float = 0.0
        self._pv2: float = 0.0
        self._prev_vol: int | None = None
        self.vwap: float | None = None
        self.deviation: float | None = None
        self.stdev: float | None = None

    def reset(self) -> None:
        self._pv = self._vol = self._pv2 = 0.0
        self._prev_vol = None
        self.vwap = self.deviation = self.stdev = None

    def update(self, s: OrderBookSnapshot) -> None:
        today = s.ts.date()
        if self._session_date != today:
            self._session_date = today
            self.reset()

        if s.ltp is None or s.volume is None:
            return

        dvol = s.volume - self._prev_vol if self._prev_vol is not None else 0
        self._prev_vol = s.volume
        if dvol < 0:
            dvol = 0

        if dvol > 0:
            self._pv += s.ltp * dvol
            self._pv2 += (s.ltp ** 2) * dvol
            self._vol += dvol

        if self._vol > 0:
            self.vwap = self._pv / self._vol
            variance = max(0.0, self._pv2 / self._vol - self.vwap ** 2)
            self.stdev = math.sqrt(variance)
            self.deviation = s.ltp - self.vwap
