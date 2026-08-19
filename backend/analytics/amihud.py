"""Amihud Illiquidity Ratio (Amihud, 2002).

Measures the price impact of trading volume — the average absolute return
per unit of dollar volume. High Amihud values indicate illiquid markets
where even small trades move prices significantly.

    ILLIQ_t = |r_t| / DollarVolume_t

We compute a rolling average over a configurable window for smoothing.

Reference:
    Amihud, Y. (2002). "Illiquidity and Stock Returns: Cross-Section
    and Time-Series Effects." Journal of Financial Markets, 5(1), 31–56.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional

from backend.models import OrderBookSnapshot


@dataclass(slots=True)
class AmihudResult:
    """Output of a single Amihud illiquidity measurement."""
    illiq: float           # current-tick illiquidity ratio
    illiq_avg: float       # rolling average over the window
    abs_return: float      # |r_t| in basis points
    dollar_volume: float   # price × quantity for this tick
    n_obs: int


class AmihudEstimator:
    """Rolling Amihud Illiquidity Ratio.

    Maintains a sliding window of per-tick illiquidity ratios and
    reports both the instantaneous value and the rolling average.
    """

    def __init__(self, window: int = 300) -> None:
        self._window = window
        self._prev_mid: Optional[float] = None
        self._ratios: deque[float] = deque(maxlen=window)
        self._sum: float = 0.0

    def update(self, snap: OrderBookSnapshot) -> Optional[AmihudResult]:
        """Process a tick and return the Amihud illiquidity ratio.

        Returns None until the second observation (need a return).
        """
        mid = snap.midprice

        if self._prev_mid is None or self._prev_mid == 0:
            self._prev_mid = mid
            return None

        # Absolute return in basis points
        ret = abs(mid - self._prev_mid) / self._prev_mid * 10_000
        self._prev_mid = mid

        # Dollar volume for this tick
        dollar_vol = snap.ltp * snap.ltq
        if dollar_vol == 0:
            return None

        # Amihud ratio: |return| / dollar_volume  (×10^6 for scaling)
        illiq = ret / dollar_vol * 1e6

        # Rolling average
        if len(self._ratios) == self._window:
            self._sum -= self._ratios[0]
        self._ratios.append(illiq)
        self._sum += illiq

        n = len(self._ratios)
        avg = self._sum / n if n else 0.0

        return AmihudResult(
            illiq=illiq,
            illiq_avg=avg,
            abs_return=ret,
            dollar_volume=dollar_vol,
            n_obs=n,
        )
