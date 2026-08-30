from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.analytics.anomaly_detector import AnomalyDetector
from backend.analytics.order_flow import OFICalculator
from backend.analytics.volume import TickRuleClassifier
from backend.models import BookLevel, OrderBookSnapshot


def _snapshot(
    ts: datetime,
    bid: float = 100.0,
    bid_qty: int = 100,
    ask: float = 100.5,
    ask_qty: int = 100,
    volume: int = 100,
) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="TEST",
        ts=ts,
        bids=[BookLevel(bid, bid_qty)],
        asks=[BookLevel(ask, ask_qty)],
        ltp=ask,
        ltq=10,
        volume=volume,
    )


def test_tick_rule_returns_numeric_signs():
    classifier = TickRuleClassifier()
    assert classifier.classify(100.0) == 0
    assert classifier.classify(100.1) == 1
    assert classifier.classify(100.1) == 1
    assert classifier.classify(99.9) == -1


def test_ofi_expires_events_outside_window():
    calculator = OFICalculator(windows=(60,))
    start = datetime(2026, 1, 2, tzinfo=UTC)
    calculator.update(_snapshot(start))
    calculator.update(_snapshot(start + timedelta(seconds=1), bid=100.1))
    assert calculator.rolling(start + timedelta(seconds=1))["ofi_60s"] > 0
    assert calculator.rolling(start + timedelta(seconds=62))["ofi_60s"] == 0


def test_ofi_outlier_generates_zscore_alert():
    detector = AnomalyDetector(ofi_window=10, z_threshold=2.0)
    start = datetime(2026, 1, 2, tzinfo=UTC)
    for index in range(10):
        detector.check(_snapshot(start + timedelta(seconds=index)), 1.0 + index % 2)
    alerts = detector.check(_snapshot(start + timedelta(seconds=11)), 100.0)
    assert any(alert.kind == "ofi_extreme" for alert in alerts)
