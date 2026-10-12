"""Volume profile, buy/sell classification (tick rule), cumulative delta."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from ..models import OrderBookSnapshot


class TickRuleClassifier:
    def __init__(self) -> None:
        self._prev_price: float | None = None
        self._prev_sign: int = 0

    def classify(self, price: float | None) -> int:
        """Return +1 for buyer-initiated, -1 for seller-initiated, else 0.

        Unchanged prices inherit the most recent non-zero classification.
        """
        if price is None:
            return 0
        if self._prev_price is None:
            self._prev_price = price
            return 0
        if price > self._prev_price:
            self._prev_sign = 1
        elif price < self._prev_price:
            self._prev_sign = -1
        self._prev_price = price
        return self._prev_sign


class VolumeProfile:
    def __init__(self, bucket_size: float = 0.5) -> None:
        self.bucket_size = bucket_size
        self._profile: dict[float, int] = defaultdict(int)
        self._delta: int = 0
        self._prev_cum_vol: int | None = None
        self._clf = TickRuleClassifier()
        self._session_date: date | None = None

    def _bucket(self, price: float) -> float:
        return round((price // self.bucket_size) * self.bucket_size, 4)

    def reset(self) -> None:
        self._profile.clear()
        self._delta = 0
        self._prev_cum_vol = None
        self._clf = TickRuleClassifier()

    def update(self, s: OrderBookSnapshot) -> None:
        today = s.ts.date()
        if self._session_date != today:
            self._session_date = today
            self.reset()
        if s.ltp is None or s.volume is None:
            return
        if self._prev_cum_vol is not None and s.volume < self._prev_cum_vol:
            self.reset()
        dvol = s.volume - self._prev_cum_vol if self._prev_cum_vol is not None else 0
        self._prev_cum_vol = s.volume
        if dvol <= 0:
            return
        side = self._clf.classify(s.ltp)
        self._profile[self._bucket(s.ltp)] += dvol
        if side > 0:
            self._delta += dvol
        elif side < 0:
            self._delta -= dvol

    @property
    def profile(self) -> dict[float, int]:
        return dict(sorted(self._profile.items()))

    @property
    def cumulative_delta(self) -> int:
        return self._delta
