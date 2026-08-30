"""Application configuration loaded from environment / .env.

All deployment-tunable values live here — the runtime should never read the
environment directly. Tests can override the whole `settings` singleton by
constructing a fresh `Settings(...)` or setting env vars before import.
"""
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

    # --- data source --------------------------------------------------
    data_source: Literal["mock", "angel"] = "mock"
    symbols: str = "RELIANCE,TCS,HDFCBANK,INFY,ICICIBANK"

    # Angel One creds (required only when data_source == "angel")
    angel_api_key: str = ""
    angel_client_code: str = ""
    angel_mpin: str = ""
    angel_totp_secret: str = ""

    # --- HTTP ---------------------------------------------------------
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    # Comma-separated list of allowed origins, or "*" for allow-all. Never
    # use "*" in production — set this to the dashboard's origin.
    cors_allow_origins: str = "*"

    # --- observability ------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = False   # LOG_JSON=1 -> one-line JSON per log record

    # --- auth & rate limiting ----------------------------------------
    # Shared-secret token required to open a WebSocket. Empty = disabled
    # (dev only). Clients pass it as ?token=... on the WS URL. This is
    # deliberately minimal; front a real auth layer at your reverse
    # proxy for anything production-serious.
    ws_auth_token: str = ""
    # HTTP requests per client per minute. 0 = disabled.
    http_rate_limit_per_minute: int = 120

    # --- storage ------------------------------------------------------
    tick_store_dir: Path = Path("./data/ticks")
    parquet_roll_minutes: int = 15

    # --- redis --------------------------------------------------------
    # Set to empty string to skip Redis entirely and use the in-memory cache.
    redis_url: str = "redis://localhost:6379/0"

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.symbols.split(",") if s.strip()]

    @property
    def cors_allow_origins_list(self) -> list[str]:
        raw = [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]
        return raw or ["*"]


settings = Settings()
