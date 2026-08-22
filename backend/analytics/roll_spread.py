"""Roll's Implied Spread Estimator (Roll, 1984).

Estimates the effective bid-ask spread from transaction prices alone,
without needing order book data. Based on the insight that in a
market-maker model, consecutive price changes are negatively
autocorrelated, and the spread can be recovered from this covariance:

    Spread = 2 × √(-Cov(ΔP_t, ΔP_{t-1}))

When the covariance is positive (no market-maker friction), the
estimator returns 0. We compute this over a rolling window.

Reference:
    Roll, R. (1984). "A Simple Implicit Measure of the Effective
    Bid-Ask Spread in an Efficient Market." The Journal of Finance,
    39(4), 1127–1139.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from backend.models import OrderBookSnapshot


@dataclass(slots=True)
class RollSpreadResult:
    """Output of a single Roll spread estimation."""
    implied_spread: float      # estimated effective spread
    implied_spread_bps: float  # spread in basis points
    serial_cov: float          # Cov(ΔP_t, ΔP_{t-1})
    n_obs: int


class RollSpreadEstimator:
    """Rolling Roll's implied spread estimator.

    Uses a circular buffer of price changes and computes the
    first-order autocovariance to estimate the effective spread.
    """

    def __init__(self, window: int = 200) -> None:
        self._window = window
        self._prev_price: float | None = None
        self._prev_dp: float | None = None

        # Store consecutive (ΔP_t, ΔP_{t-1}) pairs
        self._dp_curr: deque[float] = deque(maxlen=window)
        self._dp_prev: deque[float] = deque(maxlen=window)

        # Running sums for covariance: Cov(X,Y) = E[XY] - E[X]E[Y]
        self._sx: float = 0.0    # Σ dp_prev
        self._sy: float = 0.0    # Σ dp_curr
        self._sxy: float = 0.0   # Σ dp_prev * dp_curr

    def update(self, snap: OrderBookSnapshot) -> RollSpreadResult | None:
        """Process a tick and return the implied spread estimate.

        Returns None until at least 30 pairs are collected.
        """
        price = snap.ltp
        if price is None or price <= 0:
            return None

        if self._prev_price is None:
            self._prev_price = price
            return None

        dp = price - self._prev_price
        self._prev_price = price

        if self._prev_dp is None:
            self._prev_dp = dp
            return None

        # Evict oldest pair if at capacity
        if len(self._dp_curr) == self._window:
            old_x = self._dp_prev[0]
            old_y = self._dp_curr[0]
            self._sx -= old_x
            self._sy -= old_y
            self._sxy -= old_x * old_y

        self._dp_prev.append(self._prev_dp)
        self._dp_curr.append(dp)
        self._sx += self._prev_dp
        self._sy += dp
        self._sxy += self._prev_dp * dp

        self._prev_dp = dp

        n = len(self._dp_curr)
        if n < 30:
            return None

        # Serial covariance: Cov(ΔP_t, ΔP_{t-1})
        cov = self._sxy / n - (self._sx / n) * (self._sy / n)

        # Roll's formula: spread = 2 * sqrt(-cov)  [only if cov < 0]
        implied = 2.0 * math.sqrt(-cov) if cov < 0 else 0.0

        # Convert to bps using midprice
        mid = snap.midprice
        bps = implied / mid * 10_000 if mid is not None and mid > 0 else 0.0

        return RollSpreadResult(
            implied_spread=implied,
            implied_spread_bps=bps,
            serial_cov=cov,
            n_obs=n,
        )
