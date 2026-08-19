"""Hasbrouck Information Share (Hasbrouck, 1991/1995).

Decomposes price variance into contributions from trades vs. quote
revisions, measuring how much "private information" is revealed by
trades. A high trade information share means informed traders are
active — the market is discovering price through order flow rather
than quote adjustments.

We implement a simplified single-venue version that tracks:
1. Trade-return variance (price moves on trade ticks)
2. Quote-return variance (midprice moves between ticks)
3. Information share = trade_var / (trade_var + quote_var)

Reference:
    Hasbrouck, J. (1991). "Measuring the Information Content of
    Stock Trades." The Journal of Finance, 46(1), 179–207.

    Hasbrouck, J. (1995). "One Security, Many Markets: Determining
    the Contributions to Price Discovery." The Journal of Finance,
    50(4), 1175–1199.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional

from backend.models import OrderBookSnapshot
from backend.analytics.volume import TickRuleClassifier


@dataclass(slots=True)
class HasbrouckResult:
    """Output of Hasbrouck information share estimation."""
    trade_info_share: float      # fraction of variance from trades [0, 1]
    quote_info_share: float      # fraction of variance from quotes [0, 1]
    trade_var: float             # variance of trade returns
    quote_var: float             # variance of quote (mid) returns
    permanent_impact_bps: float  # avg permanent price impact of trades
    n_obs: int


class HasbrouckEstimator:
    """Rolling Hasbrouck information share estimator.

    Decomposes tick-level price changes into trade-induced and
    quote-induced components, estimating how much price discovery
    comes from order flow vs. market-maker quote adjustments.
    """

    def __init__(self, window: int = 500) -> None:
        self._window = window
        self._classifier = TickRuleClassifier()
        self._prev_mid: Optional[float] = None
        self._prev_ltp: Optional[float] = None

        # Trade returns: ΔP when a trade occurs (all ticks have trades here)
        self._trade_returns: deque[float] = deque(maxlen=window)
        # Quote returns: ΔMid independent of trades
        self._quote_returns: deque[float] = deque(maxlen=window)
        # Signed trade impacts (for permanent impact estimation)
        self._signed_impacts: deque[float] = deque(maxlen=window)

        # Running sums for variance (Welford not needed — window is bounded)
        self._tr_sum: float = 0.0
        self._tr_sum2: float = 0.0
        self._qr_sum: float = 0.0
        self._qr_sum2: float = 0.0
        self._si_sum: float = 0.0

    def update(self, snap: OrderBookSnapshot) -> Optional[HasbrouckResult]:
        """Process a tick and return information share estimates.

        Returns None until at least 50 observations are collected.
        """
        mid = snap.midprice
        ltp = snap.ltp
        sign = self._classifier.classify(snap)

        if self._prev_mid is None:
            self._prev_mid = mid
            self._prev_ltp = ltp
            return None

        # Trade return: change in transaction price (in bps)
        trade_ret = (ltp - self._prev_ltp) / self._prev_ltp * 10_000 if self._prev_ltp else 0.0
        # Quote return: change in midprice (in bps)
        quote_ret = (mid - self._prev_mid) / self._prev_mid * 10_000 if self._prev_mid else 0.0
        # Signed impact
        signed_impact = sign * abs(trade_ret)

        self._prev_mid = mid
        self._prev_ltp = ltp

        # Evict oldest if at capacity
        if len(self._trade_returns) == self._window:
            old_tr = self._trade_returns[0]
            old_qr = self._quote_returns[0]
            old_si = self._signed_impacts[0]
            self._tr_sum -= old_tr
            self._tr_sum2 -= old_tr * old_tr
            self._qr_sum -= old_qr
            self._qr_sum2 -= old_qr * old_qr
            self._si_sum -= old_si

        self._trade_returns.append(trade_ret)
        self._quote_returns.append(quote_ret)
        self._signed_impacts.append(signed_impact)

        self._tr_sum += trade_ret
        self._tr_sum2 += trade_ret * trade_ret
        self._qr_sum += quote_ret
        self._qr_sum2 += quote_ret * quote_ret
        self._si_sum += signed_impact

        n = len(self._trade_returns)
        if n < 50:
            return None

        # Variance of trade and quote returns
        trade_var = self._tr_sum2 / n - (self._tr_sum / n) ** 2
        quote_var = self._qr_sum2 / n - (self._qr_sum / n) ** 2
        trade_var = max(0.0, trade_var)
        quote_var = max(0.0, quote_var)

        total_var = trade_var + quote_var
        if total_var > 0:
            trade_share = trade_var / total_var
            quote_share = quote_var / total_var
        else:
            trade_share = 0.5
            quote_share = 0.5

        # Average permanent impact
        perm_impact = self._si_sum / n

        return HasbrouckResult(
            trade_info_share=trade_share,
            quote_info_share=quote_share,
            trade_var=trade_var,
            quote_var=quote_var,
            permanent_impact_bps=perm_impact,
            n_obs=n,
        )
