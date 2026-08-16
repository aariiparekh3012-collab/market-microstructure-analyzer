"""Canonical data models for order book snapshots, ticks, and analytics output."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(slots=True)
class BookLevel:
    price: float
    qty: int
    orders: int = 0


@dataclass(slots=True)
class OrderBookSnapshot:
    """Normalized 5-level order book snapshot."""

    symbol: str
    ts: datetime
    bids: list[BookLevel]
    asks: list[BookLevel]
    ltp: float | None = None
    ltq: int | None = None
    volume: int | None = None

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def midprice(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.asks[0].price - self.bids[0].price


@dataclass(slots=True)
class Trade:
    symbol: str
    ts: datetime
    price: float
    qty: int
    side: Literal["buy", "sell", "unknown"] = "unknown"


@dataclass(slots=True)
class Anomaly:
    symbol: str
    ts: datetime
    kind: str
    severity: Literal["info", "warn", "critical"] = "warn"
    detail: dict = field(default_factory=dict)
