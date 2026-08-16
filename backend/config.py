"""Application configuration loaded from environment / .env."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Data source
    data_source: Literal["mock", "angel"] = "mock"
    symbols: str = "RELIANCE,TCS,HDFCBANK,INFY,ICICIBANK"

    # Angel One creds (only required when data_source == "angel")
    angel_api_key: str = ""
    angel_client_code: str = ""
    angel_mpin: str = ""
    angel_totp_secret: str = ""

    # Backend
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000

    # Storage
    tick_store_dir: Path = Path("./data/ticks")
    parquet_roll_minutes: int = 15

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.symbols.split(",") if s.strip()]


settings = Settings()
