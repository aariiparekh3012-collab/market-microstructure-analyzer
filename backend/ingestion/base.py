"""Base interface every data source must implement."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from ..models import OrderBookSnapshot


class DataSource(ABC):
    name: str = "base"

    @abstractmethod
    async def stream(self, symbols: list[str]) -> AsyncIterator[OrderBookSnapshot]:
        raise NotImplementedError
        if False:
            yield  # type: ignore
