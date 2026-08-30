"""Data source factory — pick between mock and live feeds via env."""
from __future__ import annotations

from ..config import settings
from .base import DataSource
from .mock_source import MockSource


def make_source() -> DataSource:
    if settings.data_source == "angel":
        from .angel_source import AngelSource
        return AngelSource()
    return MockSource()
