"""Bid-ask spread metrics."""
from __future__ import annotations
from ..models import OrderBookSnapshot


def quoted_spread(s: OrderBookSnapshot) -> float | None:
    if not s.bids or not s.asks:
        return None
    return s.asks[0].price - s.bids[0].price


def relative_spread(s: OrderBookSnapshot) -> float | None:
    mid = s.midprice
    if mid is None or mid == 0:
        return None
    sp = quoted_spread(s)
    return None if sp is None else sp / mid


def weighted_spread(s: OrderBookSnapshot, depth: int = 5) -> float | None:
    bids = s.bids[:depth]
    asks = s.asks[:depth]
    if not bids or not asks:
        return None
    bq = sum(b.qty for b in bids) or 1
    aq = sum(a.qty for a in asks) or 1
    vwap_bid = sum(b.price * b.qty for b in bids) / bq
    vwap_ask = sum(a.price * a.qty for a in asks) / aq
    return vwap_ask - vwap_bid
