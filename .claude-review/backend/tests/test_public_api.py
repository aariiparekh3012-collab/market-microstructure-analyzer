from unittest import TestCase

from market_microstructure import (
    Anomaly,
    BookLevel,
    Engine,
    OrderBookSnapshot,
)


class TestPublicAPI(TestCase):
    def test_public_api_exports(self) -> None:
        self.assertIsNotNone(Engine)
        self.assertIsNotNone(BookLevel)
        self.assertIsNotNone(OrderBookSnapshot)
        self.assertIsNotNone(Anomaly)