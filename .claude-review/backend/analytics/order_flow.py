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
        self._sums: dict[int, float] = dict.fromkeys(windows, 0.0)
        self._prev_bid_px: float | None = None
        self._prev_bid_qty: int | None = None
        self._prev_ask_px: float | None = None
        self._prev_ask_qty: int | None = None

    def update(self, s: OrderBookSnapshot) -> float:
        if not s.bids or not s.asks:
            return 0.0

        bpx, bq = s.bids[0].price, s.bids[0].qty
        apx, aq = s.asks[0].price, s.asks[0].qty

        if self._prev_bid_px is None or self._prev_ask_px is None:
            self._prev_bid_px, self._prev_bid_qty = bpx, bq
            self._prev_ask_px, self._prev_ask_qty = apx, aq
            return 0.0

        prev_bid_px = self._prev_bid_px
        prev_bid_qty = self._prev_bid_qty or 0
        prev_ask_px = self._prev_ask_px
        prev_ask_qty = self._prev_ask_qty or 0

        if bpx > prev_bid_px:
            e_bid = float(bq)
        elif bpx == prev_bid_px:
            e_bid = float(bq - prev_bid_qty)
        else:
            e_bid = -float(prev_bid_qty)

        if apx < prev_ask_px:
            e_ask = -float(aq)
        elif apx == prev_ask_px:
            e_ask = -float(aq - prev_ask_qty)
        else:
            e_ask = float(prev_ask_qty)

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
