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


def test_vwap_resets_on_same_day_volume_restart():
    v = SessionVWAP()
    t = datetime(2026, 10, 12, 10, tzinfo=UTC)
    v.update(_snap(99, 10, 102, 10, ltp=100, vol=100, ts=t))
    v.update(_snap(99, 10, 102, 10, ltp=101, vol=120, ts=t + timedelta(seconds=1)))
    assert v.vwap == 101
    v.update(_snap(99, 10, 102, 10, ltp=110, vol=0, ts=t + timedelta(seconds=2)))
    assert v.vwap is None
    v.update(_snap(99, 10, 102, 10, ltp=105, vol=10, ts=t + timedelta(seconds=3)))
    assert v.vwap == 105


def test_volume_profile_resets_on_volume_restart_and_new_day():
    from backend.analytics.volume import VolumeProfile
    profile = VolumeProfile()
    t = datetime(2026, 10, 12, 10, tzinfo=UTC)
    profile.update(_snap(99, 10, 102, 10, ltp=100, vol=100, ts=t))
    profile.update(_snap(99, 10, 102, 10, ltp=101, vol=120, ts=t + timedelta(seconds=1)))
    assert sum(profile.profile.values()) == 20
    profile.update(_snap(99, 10, 102, 10, ltp=110, vol=0, ts=t + timedelta(seconds=2)))
    assert profile.profile == {}
    assert profile.cumulative_delta == 0
    profile.update(_snap(99, 10, 102, 10, ltp=105, vol=10, ts=t + timedelta(seconds=3)))
    assert sum(profile.profile.values()) == 10
    profile.update(_snap(99, 10, 102, 10, ltp=106, vol=100, ts=t + timedelta(days=1)))
    assert profile.profile == {}
