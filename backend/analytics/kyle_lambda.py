"""Kyle's Lambda — price impact coefficient (Kyle, 1985).

Measures how much the price moves per unit of signed order flow.
A higher lambda means the market is less liquid — large orders move
the price more. Estimated via rolling OLS regression:

    ΔP_t = α + λ · SignedVolume_t + ε_t

where SignedVolume is classified by the tick rule.

Reference:
    Kyle, A. S. (1985). "Continuous Auctions and Insider Trading."
    Econometrica, 53(6), 1315–1335.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from backend.analytics.volume import TickRuleClassifier
from backend.models import OrderBookSnapshot


@dataclass(slots=True)
class KyleLambdaResult:
    """Output of a single Kyle's Lambda estimation."""
    lambda_val: float     # price impact coefficient (bps per unit flow)
    r_squared: float      # goodness of fit
    t_statistic: float    # statistical significance of lambda
    n_obs: int            # observations in the window


class KyleLambdaEstimator:
    """Rolling Kyle's Lambda via OLS regression.

    Maintains a sliding window of (ΔP, SignedVolume) pairs and
    re-estimates λ on each tick using rolling sufficient statistics
    for O(1) per-tick updates.
    """

    def __init__(self, window: int = 300) -> None:
        self._window = window
        self._classifier = TickRuleClassifier()
        self._prev_mid: float | None = None

        # Circular buffers for ΔP and signed volume
        self._dp: deque[float] = deque(maxlen=window)
        self._sv: deque[float] = deque(maxlen=window)

        # Running sums for OLS  (Σx, Σy, Σxy, Σx², Σy²)
        self._sx: float = 0.0
        self._sy: float = 0.0
        self._sxy: float = 0.0
        self._sx2: float = 0.0
        self._sy2: float = 0.0

    def _add(self, x: float, y: float) -> None:
        """Add a new observation, evicting the oldest if at capacity."""
        if len(self._dp) == self._window:
            old_x = self._dp[0]
            old_y = self._sv[0]
            self._sx -= old_x
            self._sy -= old_y
            self._sxy -= old_x * old_y
            self._sx2 -= old_x * old_x
            self._sy2 -= old_y * old_y

        self._dp.append(y)
        self._sv.append(x)

        self._sx += x
        self._sy += y
        self._sxy += x * y
        self._sx2 += x * x
        self._sy2 += y * y

    def update(self, snap: OrderBookSnapshot) -> KyleLambdaResult | None:
        """Process a tick and return the current Kyle's Lambda estimate.

        Returns None until at least 30 observations are collected.
        """
        mid = snap.midprice
        if mid is None or mid <= 0 or snap.ltp is None or snap.ltq is None:
            return None

        sign = self._classifier.classify(snap.ltp)

        if self._prev_mid is None:
            self._prev_mid = mid
            return None

        # ΔP in basis points relative to previous mid (prev_mid > 0 guaranteed
        # above, so no zero-guard needed on the divisor).
        dp = (mid - self._prev_mid) / self._prev_mid * 10_000
        signed_vol = float(sign * snap.ltq)
        self._prev_mid = mid

        self._add(signed_vol, dp)

        n = len(self._dp)
        if n < 30:
            return None

        # OLS on centered sums:
        #   Sxx = Σx² − (Σx)²/n,  Syy = Σy² − (Σy)²/n,  Sxy = Σxy − ΣxΣy/n
        # λ = Sxy / Sxx,  ss_res = Syy − λ · Sxy = Syy − λ² · Sxx
        # All O(1) — no rescan of the window buffers.
        sxx = self._sx2 - self._sx * self._sx / n
        if sxx < 1e-12:
            return KyleLambdaResult(lambda_val=0.0, r_squared=0.0,
                                    t_statistic=0.0, n_obs=n)

        syy = self._sy2 - self._sy * self._sy / n
        sxy_c = self._sxy - self._sx * self._sy / n

        lam = sxy_c / sxx
        ss_res = max(0.0, syy - lam * sxy_c)
        r_sq = max(0.0, 1.0 - ss_res / syy) if syy > 0 else 0.0

        # Standard error of lambda: se(λ) = sqrt(MSE / Sxx)
        mse = ss_res / (n - 2) if n > 2 else 0.0
        se_lam = math.sqrt(mse / sxx) if mse > 0 else 0.0
        t_stat = lam / se_lam if se_lam > 0 else 0.0

        return KyleLambdaResult(
            lambda_val=lam,
            r_squared=r_sq,
            t_statistic=t_stat,
            n_obs=n,
        )
