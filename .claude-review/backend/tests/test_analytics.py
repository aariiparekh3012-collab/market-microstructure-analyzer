"""Basic sanity tests for spread + OFI + VWAP."""
from datetime import UTC, datetime, timedelta

from backend.analytics.order_flow import OFICalculator
from backend.analytics.spread import quoted_spread, relative_spread, weighted_spread
from backend.analytics.vwap import SessionVWAP
from backend.models import BookLevel, OrderBookSnapshot


def _snap(bid_px, bid_qty, ask_px, ask_qty, ltp=None, vol=None, ts=None):
    ts = ts or datetime.now(UTC)
    return OrderBookSnapshot(
        symbol="TEST", ts=ts,
        bids=[BookLevel(bid_px, bid_qty)] + [BookLevel(bid_px - 0.5 * i, 50) for i in range(1, 5)],
        asks=[BookLevel(ask_px, ask_qty)] + [BookLevel(ask_px + 0.5 * i, 50) for i in range(1, 5)],
        ltp=ltp or (bid_px + ask_px) / 2, ltq=1, volume=vol or 0,
    )


def test_spread_basic():
    s = _snap(100.0, 200, 100.5, 200)
    assert quoted_spread(s) == 0.5
    assert relative_spread(s) is not None
    assert weighted_spread(s) is not None


def test_ofi_bid_price_up_positive_contribution():
    calc = OFICalculator()
    t0 = datetime.now(UTC)
    calc.update(_snap(100.0, 100, 100.5, 100, ts=t0))
    contrib = calc.update(_snap(100.1, 80, 100.5, 100, ts=t0 + timedelta(seconds=1)))
    assert contrib > 0


def test_vwap_progresses_with_volume():
    v = SessionVWAP()
    t0 = datetime.now(UTC)
    v.update(_snap(100.0, 100, 100.5, 100, ltp=100.25, vol=0, ts=t0))
    v.update(_snap(100.0, 100, 100.5, 100, ltp=101.0, vol=1000, ts=t0 + timedelta(seconds=1)))
    v.update(_snap(100.0, 100, 100.5, 100, ltp=99.0, vol=3000, ts=t0 + timedelta(seconds=2)))
    assert v.vwap is not None
    assert 99.0 <= v.vwap <= 101.0
