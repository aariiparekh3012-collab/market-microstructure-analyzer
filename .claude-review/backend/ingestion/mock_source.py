"""Deterministic-capable synthetic order-book source for development.

The generator uses a simple mean-reverting additive price process. It is useful
for software tests and demonstrations; it is not a calibrated market model.
"""
from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from ..models import BookLevel, OrderBookSnapshot
from .base import DataSource


class _SymbolState:
    __slots__ = ("anchor", "mid", "vol_cum", "sigma", "imbalance_bias", "rng")

    def __init__(self, start_mid: float, rng: random.Random) -> None:
        self.anchor = start_mid
        self.mid = start_mid
        self.vol_cum = 0
        self.sigma = start_mid * 0.0004
        self.imbalance_bias = 0.0
        self.rng = rng

    def step(self, tick_size: float) -> None:
        if self.rng.random() < 0.02:
            self.imbalance_bias = self.rng.uniform(-0.6, 0.6)
        self.imbalance_bias *= 0.98
        reversion = 0.015 * (self.anchor - self.mid)
        shock = (
            reversion
            + self.rng.gauss(0.0, self.sigma)
            + self.imbalance_bias * self.sigma * 2
        )
        self.mid = max(1.0, self.mid + shock)
        self.mid = round(self.mid / tick_size) * tick_size


class MockSource(DataSource):
    name = "mock"

    def __init__(
        self,
        tick_ms: int = 200,
        tick_size: float = 0.05,
        *,
        symbols: list[str] | None = None,
        max_ticks: int | None = None,
        realtime: bool = True,
        seed: int | None = None,
        start_time: datetime | None = None,
    ) -> None:
        self.tick_ms = tick_ms
        self.tick_size = tick_size
        self.symbols = symbols
        self.max_ticks = max_ticks
        self.realtime = realtime
        self._rng = random.Random(seed)
        self.start_time = start_time or datetime.now(UTC)
        self._starts = {
            "RELIANCE": 2900.0,
            "TCS": 4100.0,
            "HDFCBANK": 1650.0,
            "INFY": 1800.0,
            "ICICIBANK": 1250.0,
        }

    def _initial_mid(self, symbol: str) -> float:
        return self._starts.get(symbol, 1000.0 + self._rng.uniform(-100, 100))

    def _build_book(
        self, symbol: str, st: _SymbolState, timestamp: datetime
    ) -> OrderBookSnapshot:
        half = self.tick_size * self._rng.choice([1, 1, 1, 2, 2, 3])
        best_bid = round((st.mid - half) / self.tick_size) * self.tick_size
        best_ask = round((st.mid + half) / self.tick_size) * self.tick_size
        if best_ask <= best_bid:
            best_ask = best_bid + self.tick_size

        def _levels(side: str) -> list[BookLevel]:
            out: list[BookLevel] = []
            base_qty = self._rng.randint(80, 400)
            bias = 1.0 + st.imbalance_bias if side == "bid" else 1.0 - st.imbalance_bias
            bias = max(0.2, bias)
            for i in range(5):
                px = (
                    best_bid - i * self.tick_size
                    if side == "bid"
                    else best_ask + i * self.tick_size
                )
                qty = int(base_qty * (0.9 ** i) * bias * self._rng.uniform(0.6, 1.4))
                out.append(
                    BookLevel(
                        price=round(px, 2),
                        qty=max(1, qty),
                        orders=self._rng.randint(1, 8),
                    )
                )
            return out

        ltq = self._rng.randint(1, 50)
        if self._rng.random() < 0.01:
            ltq = self._rng.randint(500, 2000)
        st.vol_cum += ltq

        # Print at one side of the spread so tick-rule classification sees a
        # transaction-like price rather than a permanent midpoint print.
        buy_probability = min(0.9, max(0.1, 0.5 + st.imbalance_bias / 2))
        ltp = best_ask if self._rng.random() < buy_probability else best_bid

        return OrderBookSnapshot(
            symbol=symbol,
            ts=timestamp,
            bids=_levels("bid"),
            asks=_levels("ask"),
            ltp=round(ltp, 2),
            ltq=ltq,
            volume=st.vol_cum,
        )

    async def stream(
        self, symbols: list[str] | None = None
    ) -> AsyncIterator[OrderBookSnapshot]:
        selected = symbols or self.symbols
        if not selected:
            raise ValueError("MockSource.stream requires at least one symbol")
        states = {
            symbol: _SymbolState(self._initial_mid(symbol), self._rng)
            for symbol in selected
        }
        interval = self.tick_ms / 1000.0
        emitted = 0
        while True:
            for sym, st in states.items():
                st.step(self.tick_size)
                timestamp = self.start_time + timedelta(
                    milliseconds=self.tick_ms * emitted
                )
                yield self._build_book(sym, st, timestamp)
                emitted += 1
                if self.max_ticks is not None and emitted >= self.max_ticks:
                    return
            if self.realtime:
                await asyncio.sleep(interval)
