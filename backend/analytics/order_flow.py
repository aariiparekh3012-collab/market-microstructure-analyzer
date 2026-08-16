"""Order Flow Imbalance (OFI).

Reference: Cont, Kukanov & Stoikov (2014), "The Price Impact of Order Book Events".
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from ..models import OrderBookSnapshot


class OFICalculator:
    def __init__(self, windows: tuple[int, ...] = (60, 300, 900)) -> None:
        self.windows = windows
        self._history: deque[tuple[datetime, float]] = deque(maxlen=100_000)
        self._prev_bid_px: float | None = None
        self._prev_bid_qty: int | None = None
        self._prev_ask_px: float | None = None
        self._prev_ask_qty: int | None = None

    def update(self, s: OrderBookSnapshot) -> float:
        if not s.bids or not s.asks:
            return 0.0

        bpx, bq = s.bids[0].price, s.bids[0].qty
        apx, aq = s.asks[0].price, s.asks[0].qty

        if self._prev_bid_px is None:
            self._prev_bid_px, self._prev_bid_qty = bpx, bq
            self._prev_ask_px, self._prev_ask_qty = apx, aq
            return 0.0

        if bpx > self._prev_bid_px:
            e_bid = float(bq)
        elif bpx == self._prev_bid_px:
            e_bid = float(bq - (self._prev_bid_qty or 0))
        else:
            e_bid = -float(self._prev_bid_qty or 0)

        if apx < self._prev_ask_px:
            e_ask = -float(aq)
        elif apx == self._prev_ask_px:
            e_ask = -float(aq - (self._prev_ask_qty or 0))
        else:
            e_ask = float(self._prev_ask_qty or 0)

        ofi = e_bid + e_ask
        self._history.append((s.ts, ofi))
        self._prev_bid_px, self._prev_bid_qty = bpx, bq
        self._prev_ask_px, self._prev_ask_qty = apx, aq
        return ofi

    def rolling(self, now: datetime) -> dict[str, float]:
        out = {}
        for w in self.windows:
            cutoff = now - timedelta(seconds=w)
            out[f"ofi_{w}s"] = sum(v for t, v in self._history if t >= cutoff)
        return out
