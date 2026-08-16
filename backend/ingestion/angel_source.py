"""Angel One SmartAPI WebSocket source — STUB.

Fill this in after your demat account is approved and you have API credentials.

Setup steps:
  1. Open a demat account at https://www.angelone.in/
  2. Register at https://smartapi.angelbroking.com/ and create an app to get:
        - API Key, Client Code, MPIN, TOTP secret
  3. Fill the corresponding fields in .env
  4. Set DATA_SOURCE=angel and restart the backend

Reference: https://smartapi.angelbroking.com/docs/WebSocket2
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import AsyncIterator

from ..config import settings
from ..models import BookLevel, OrderBookSnapshot
from .base import DataSource

log = logging.getLogger(__name__)


class AngelSource(DataSource):
    name = "angel"

    def __init__(self) -> None:
        missing = [
            k for k, v in {
                "ANGEL_API_KEY": settings.angel_api_key,
                "ANGEL_CLIENT_CODE": settings.angel_client_code,
                "ANGEL_MPIN": settings.angel_mpin,
                "ANGEL_TOTP_SECRET": settings.angel_totp_secret,
            }.items() if not v
        ]
        if missing:
            raise RuntimeError(
                f"AngelSource: missing credentials in .env: {', '.join(missing)}"
            )

    async def stream(self, symbols: list[str]) -> AsyncIterator[OrderBookSnapshot]:
        # TODO: implement Angel One WebSocket login + subscribe
        raise NotImplementedError(
            "AngelSource.stream() not yet implemented — see comments and Angel One docs."
        )
        if False:
            yield  # type: ignore


def _demo_normalize(raw: dict, symbol: str) -> OrderBookSnapshot:
    """Reference for translating a raw Angel depth message to our model."""
    def _lvls(rows: list[dict]) -> list[BookLevel]:
        return [
            BookLevel(price=r["price"] / 100, qty=int(r["quantity"]), orders=int(r.get("no_of_orders", 0)))
            for r in rows[:5]
        ]
    return OrderBookSnapshot(
        symbol=symbol,
        ts=datetime.now(timezone.utc),
        bids=_lvls(raw.get("best_5_buy_data", [])),
        asks=_lvls(raw.get("best_5_sell_data", [])),
        ltp=raw.get("last_traded_price", 0) / 100 if raw.get("last_traded_price") else None,
        ltq=raw.get("last_traded_quantity"),
        volume=raw.get("volume_trade_for_the_day"),
    )
