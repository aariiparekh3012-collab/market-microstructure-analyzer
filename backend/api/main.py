"""FastAPI entrypoint.

Run locally with: ``uvicorn backend.api.main:app --reload``

Deployment endpoints
--------------------
* ``GET /healthz`` — liveness + readiness. Returns 503 if the ingestion task
  is dead or ticks have gone stale.
* ``GET /metrics`` — Prometheus-format counters and gauges scraped from the
  streamer. No external client dependency.
* ``GET /api/health`` — kept as a compat alias for the old endpoint.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from ..config import settings
from .auth import rate_limit, require_ws_auth
from .streamer import streamer

# --- structured-ish logging: JSON when LOG_JSON=1, otherwise human-readable. --
if getattr(settings, "log_json", False):
    class _JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload = {
                "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            if record.exc_info:
                payload["exc"] = self.formatException(record.exc_info)
            return json.dumps(payload, default=str)

    root_handler = logging.StreamHandler()
    root_handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=settings.log_level, handlers=[root_handler], force=True)
else:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.ws_auth_token:
        log.warning(
            "WS auth is DISABLED (WS_AUTH_TOKEN empty). "
            "Set WS_AUTH_TOKEN in the environment before deploying publicly."
        )
    if settings.http_rate_limit_per_minute <= 0:
        log.warning("HTTP rate limiting is DISABLED (HTTP_RATE_LIMIT_PER_MINUTE=0).")
    await streamer.start()
    log.info("Streamer started")
    try:
        yield
    finally:
        await streamer.stop()
        log.info("Streamer stopped")


app = FastAPI(title="Market Microstructure Analyzer", lifespan=lifespan)

_allowed = settings.cors_allow_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed,
    allow_credentials=False,     # keep off — ["*"] with credentials is unsafe
    allow_methods=["GET"],       # this API is read-only over HTTP
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Deployment endpoints
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz() -> Response:
    """Liveness + readiness. 200 when the ingestion task is alive and
    producing ticks (or still warming up), 503 otherwise."""
    ok, details = streamer.is_healthy()
    body = json.dumps({"ok": ok, **details})
    return Response(
        body,
        status_code=200 if ok else 503,
        media_type="application/json",
    )


@app.get("/metrics")
def metrics() -> Response:
    """Prometheus text-format metrics. No client library dependency."""
    m = streamer.metrics
    now = time.perf_counter()
    lag = (now - m.last_tick_ts) if m.last_tick_ts else -1.0

    lines: list[str] = [
        "# HELP mma_ticks_ingested_total Ticks successfully processed by the engine.",
        "# TYPE mma_ticks_ingested_total counter",
        f"mma_ticks_ingested_total {m.ticks_ingested}",
        "# HELP mma_ticks_persist_failed_total Ticks whose parquet append raised.",
        "# TYPE mma_ticks_persist_failed_total counter",
        f"mma_ticks_persist_failed_total {m.ticks_persist_failed}",
        "# HELP mma_source_restarts_total Number of times the ingest source has restarted after an error.",
        "# TYPE mma_source_restarts_total counter",
        f"mma_source_restarts_total {m.source_restarts}",
        "# HELP mma_anomalies_emitted_total Anomalies produced by the analytics engine.",
        "# TYPE mma_anomalies_emitted_total counter",
        f"mma_anomalies_emitted_total {m.anomalies_emitted}",
        "# HELP mma_last_tick_lag_seconds Seconds since the most recent ingested tick (-1 if none yet).",
        "# TYPE mma_last_tick_lag_seconds gauge",
        f"mma_last_tick_lag_seconds {lag:.3f}",
        "# HELP mma_ws_clients WebSocket clients currently subscribed, by topic.",
        "# TYPE mma_ws_clients gauge",
    ]
    for topic, n in sorted(m.ws_clients.items()):
        lines.append(f'mma_ws_clients{{topic="{topic}"}} {n}')

    lines += [
        "# HELP mma_messages_dropped_total Messages dropped because a subscriber's queue was full.",
        "# TYPE mma_messages_dropped_total counter",
    ]
    for topic, n in sorted(m.messages_dropped.items()):
        lines.append(f'mma_messages_dropped_total{{topic="{topic}"}} {n}')

    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


# ---------------------------------------------------------------------------
# Application endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health", dependencies=[Depends(rate_limit)])
def health() -> dict:
    """Kept for backwards compatibility. Prefer /healthz."""
    ok, details = streamer.is_healthy()
    return {"status": "ok" if ok else "degraded",
            "source": settings.data_source,
            "symbols": settings.symbol_list,
            **details}


@app.get("/api/symbols", dependencies=[Depends(rate_limit)])
def symbols() -> list[str]:
    return settings.symbol_list


@app.get("/api/volume-profile/{symbol}", dependencies=[Depends(rate_limit)])
def volume_profile(symbol: str) -> dict:
    prof = streamer.volume_profile(symbol.upper())
    if not prof:
        raise HTTPException(404, "no data yet for this symbol")
    return prof


@app.get("/api/alerts/recent", dependencies=[Depends(rate_limit)])
def recent_alerts(limit: int = 100) -> list[dict]:
    return streamer.recent_alerts(limit=limit)


# ---------------------------------------------------------------------------
# WebSocket endpoints
# ---------------------------------------------------------------------------

async def _pump(ws: WebSocket, queue: asyncio.Queue) -> None:
    try:
        while True:
            msg = await queue.get()
            await ws.send_text(msg)
    except WebSocketDisconnect:
        return
    except Exception:
        # Any other transport error (RuntimeError from a half-closed socket,
        # ConnectionClosed from the underlying protocol lib) is logged and
        # ends this pump — the caller's `finally` handles cleanup.
        log.exception("ws pump: transport error")


@app.websocket("/ws/orderbook/{symbol}")
async def ws_orderbook(ws: WebSocket, symbol: str) -> None:
    if not await require_ws_auth(ws):
        return
    await ws.accept()
    symbol = symbol.upper()
    q = streamer.subscribe_book(symbol)
    try:
        await _pump(ws, q)
    finally:
        streamer.unsubscribe_book(symbol, q)


@app.websocket("/ws/analytics/{symbol}")
async def ws_metrics(ws: WebSocket, symbol: str) -> None:
    if not await require_ws_auth(ws):
        return
    await ws.accept()
    symbol = symbol.upper()
    q = streamer.subscribe_metrics(symbol)
    try:
        await _pump(ws, q)
    finally:
        streamer.unsubscribe_metrics(symbol, q)


@app.websocket("/ws/alerts")
async def ws_alerts(ws: WebSocket) -> None:
    if not await require_ws_auth(ws):
        return
    await ws.accept()
    q = streamer.subscribe_alerts()
    try:
        await _pump(ws, q)
    finally:
        streamer.unsubscribe_alerts(q)
