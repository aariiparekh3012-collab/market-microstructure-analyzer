"""Realistic mock order book generator for development without a live feed.

Simulates a mean-reverting mid-price with GBM-style noise, builds a 5-level
book with plausible bid/ask liquidity, occasional imbalance shifts, and volume
spikes so the analytics + anomaly detection modules have interesting input.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import AsyncIterator

from ..models import BookLevel, OrderBookSnapshot
from .base import DataSource


class _SymbolState:
    __slots__ = ("mid", "vol_cum", "drift", "sigma", "imbalance_bias")

    def __init__(self, start_mid: float) -> None:
        self.mid = start_mid
        self.vol_cum = 0
        self.drift = 0.0
        self.sigma = start_mid * 0.0004
        self.imbalance_bias = 0.0

    def step(self, tick_size: float) -> None:
        if random.random() < 0.02:
            self.imbalance_bias = random.uniform(-0.6, 0.6)
        self.imbalance_bias *= 0.98
        shock = random.gauss(0.0, self.sigma) + self.imbalance_bias * self.sigma * 2
        self.mid = max(1.0, self.mid + shock)
        self.mid = round(self.mid / tick_size) * tick_size


class MockSource(DataSource):
    name = "mock"

    def __init__(self, tick_ms: int = 200, tick_size: float = 0.05) -> None:
        self.tick_ms = tick_ms
        self.tick_size = tick_size
        self._starts = {
            "RELIANCE": 2900.0,
            "TCS": 4100.0,
            "HDFCBANK": 1650.0,
            "INFY": 1800.0,
            "ICICIBANK": 1250.0,
        }

    def _initial_mid(self, symbol: str) -> float:
        return self._starts.get(symbol, 1000.0 + random.uniform(-100, 100))

    def _build_book(self, symbol: str, st: _SymbolState) -> OrderBookSnapshot:
        half = self.tick_size * random.choice([1, 1, 1, 2, 2, 3])
        best_bid = round((st.mid - half) / self.tick_size) * self.tick_size
        best_ask = round((st.mid + half) / self.tick_size) * self.tick_size
        if best_ask <= best_bid:
            best_ask = best_bid + self.tick_size

        def _levels(side: str) -> list[BookLevel]:
            out: list[BookLevel] = []
            base_qty = random.randint(80, 400)
            bias = 1.0 + st.imbalance_bias if side == "bid" else 1.0 - st.imbalance_bias
            bias = max(0.2, bias)
            for i in range(5):
                px = (
                    best_bid - i * self.tick_size
                    if side == "bid"
                    else best_ask + i * self.tick_size
                )
                qty = int(base_qty * (0.9 ** i) * bias * random.uniform(0.6, 1.4))
                out.append(BookLevel(price=round(px, 2), qty=max(1, qty), orders=random.randint(1, 8)))
            return out

        ltq = random.randint(1, 50)
        if random.random() < 0.01:
            ltq = random.randint(500, 2000)
        st.vol_cum += ltq

        return OrderBookSnapshot(
            symbol=symbol,
            ts=datetime.now(timezone.utc),
            bids=_levels("bid"),
            asks=_levels("ask"),
            ltp=round((best_bid + best_ask) / 2, 2),
            ltq=ltq,
            volume=st.vol_cum,
        )

    async def stream(self, symbols: list[str]) -> AsyncIterator[OrderBookSnapshot]:
        states = {s: _SymbolState(self._initial_mid(s)) for s in symbols}
        interval = self.tick_ms / 1000.0
        while True:
            for sym, st in states.items():
                st.step(self.tick_size)
                yield self._build_book(sym, st)
            await asyncio.sleep(interval)
