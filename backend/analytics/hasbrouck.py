"""Trade/quote variance diagnostic inspired by price-discovery literature.

This module is deliberately *not* labelled a Hasbrouck information-share
estimator. Classical Hasbrouck information share uses cointegrated price series
and a VECM innovation decomposition across venues. The single-venue diagnostic
implemented here only tracks:

1. Trade-return variance (price moves on trade ticks)
2. Quote-return variance (midprice moves between ticks)
3. Descriptive variance share = trade_var / (trade_var + quote_var)

Reference:
    Hasbrouck, J. (1991). "Measuring the Information Content of
    Stock Trades." The Journal of Finance, 46(1), 179–207.

    Hasbrouck, J. (1995). "One Security, Many Markets: Determining
    the Contributions to Price Discovery." The Journal of Finance,
    50(4), 1175–1199.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from backend.analytics.volume import TickRuleClassifier
from backend.models import OrderBookSnapshot


@dataclass(slots=True)
class TradeQuoteVarianceResult:
    """Output of the descriptive trade/quote variance diagnostic."""
    trade_variance_share: float  # descriptive share in [0, 1]
    quote_variance_share: float  # descriptive share in [0, 1]
    trade_var: float             # variance of trade returns
    quote_var: float             # variance of quote (mid) returns
    avg_signed_return_bps: float # average tick-rule-signed trade return
    n_obs: int


class TradeQuoteVarianceEstimator:
    """Rolling descriptive variance diagnostic for a single price stream."""

    def __init__(self, window: int = 500) -> None:
        self._window = window
        self._classifier = TickRuleClassifier()
        self._prev_mid: float | None = None
        self._prev_ltp: float | None = None

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

    def update(self, snap: OrderBookSnapshot) -> TradeQuoteVarianceResult | None:
        """Process a tick and return descriptive variance statistics.

        Returns None until at least 50 observations are collected.
        """
        mid = snap.midprice
        ltp = snap.ltp
        if mid is None or mid <= 0 or ltp is None or ltp <= 0:
            return None
        sign = self._classifier.classify(ltp)

        if self._prev_mid is None:
            self._prev_mid = mid
            self._prev_ltp = ltp
            return None

        # Trade return: change in transaction price (in bps)
        trade_ret = (ltp - self._prev_ltp) / self._prev_ltp * 10_000 if self._prev_ltp else 0.0
        # Quote return: change in midprice (in bps)
        quote_ret = (mid - self._prev_mid) / self._prev_mid * 10_000 if self._prev_mid else 0.0
        # Signed impact
        signed_impact = sign * trade_ret

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

        return TradeQuoteVarianceResult(
            trade_variance_share=trade_share,
            quote_variance_share=quote_share,
            trade_var=trade_var,
            quote_var=quote_var,
            avg_signed_return_bps=perm_impact,
            n_obs=n,
        )


# Compatibility aliases for callers of the original prototype. New code should
# use the accurate names above.
HasbrouckResult = TradeQuoteVarianceResult
HasbrouckEstimator = TradeQuoteVarianceEstimator
