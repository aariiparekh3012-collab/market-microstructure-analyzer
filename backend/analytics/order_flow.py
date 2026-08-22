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
        self._history: dict[int, deque[tuple[datetime, float]]] = {
            window: deque() for window in windows
        }
        self._sums: dict[int, float] = {window: 0.0 for window in windows}
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
        for window in self.windows:
            self._history[window].append((s.ts, ofi))
            self._sums[window] += ofi
        self._prev_bid_px, self._prev_bid_qty = bpx, bq
        self._prev_ask_px, self._prev_ask_qty = apx, aq
        return ofi

    def rolling(self, now: datetime) -> dict[str, float]:
        out: dict[str, float] = {}
        for w in self.windows:
            cutoff = now - timedelta(seconds=w)
            history = self._history[w]
            while history and history[0][0] < cutoff:
                _, expired = history.popleft()
                self._sums[w] -= expired
            out[f"ofi_{w}s"] = self._sums[w]
        return out
