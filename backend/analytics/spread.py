"""Bid-ask spread metrics."""
from __future__ import annotations

from ..models import OrderBookSnapshot


def quoted_spread(s: OrderBookSnapshot) -> float | None:
    if not s.bids or not s.asks:
        return None
    return s.asks[0].price - s.bids[0].price


def relative_spread(s: OrderBookSnapshot) -> float | None:
    mid = s.midprice
    if not mid:
        return None
    sp = quoted_spread(s)
    return None if sp is None else sp / mid


def weighted_spread(s: OrderBookSnapshot, depth: int = 5) -> float | None:
    """Depth-weighted spread across the top `depth` levels.

    Single pass per side (previous impl iterated three times over the same
    5-item slice for sum(qty), sum(px·qty), etc.).
    """
    bids = s.bids[:depth]
    asks = s.asks[:depth]
    if not bids or not asks:
        return None

    bq_sum = 0
    bpv_sum = 0.0
    for lvl in bids:
        bq_sum += lvl.qty
        bpv_sum += lvl.price * lvl.qty

    aq_sum = 0
    apv_sum = 0.0
    for lvl in asks:
        aq_sum += lvl.qty
        apv_sum += lvl.price * lvl.qty

    if bq_sum == 0 or aq_sum == 0:
        return None
    return apv_sum / aq_sum - bpv_sum / bq_sum
