"""FastAPI entrypoint. Run with: uvicorn backend.api.main:app --reload"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from ..config import settings
from .streamer import streamer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await streamer.start()
    log.info("Streamer started")
    try:
        yield
    finally:
        await streamer.stop()
        log.info("Streamer stopped")


app = FastAPI(title="Market Microstructure Analyzer", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "source": settings.data_source, "symbols": settings.symbol_list}


@app.get("/api/symbols")
def symbols() -> list[str]:
    return settings.symbol_list


@app.get("/api/volume-profile/{symbol}")
def volume_profile(symbol: str) -> dict:
    prof = streamer.volume_profile(symbol.upper())
    if not prof:
        raise HTTPException(404, "no data yet for this symbol")
    return prof


@app.get("/api/alerts/recent")
def recent_alerts(limit: int = 100) -> list[dict]:
    return streamer.recent_alerts(limit=limit)


async def _pump(ws: WebSocket, queue: asyncio.Queue) -> None:
    try:
        while True:
            msg = await queue.get()
            await ws.send_text(msg)
    except WebSocketDisconnect:
        return


@app.websocket("/ws/orderbook/{symbol}")
async def ws_orderbook(ws: WebSocket, symbol: str) -> None:
    await ws.accept()
    symbol = symbol.upper()
    q = streamer.subscribe_book(symbol)
    try:
        await _pump(ws, q)
    finally:
        streamer.unsubscribe_book(symbol, q)


@app.websocket("/ws/analytics/{symbol}")
async def ws_metrics(ws: WebSocket, symbol: str) -> None:
    await ws.accept()
    symbol = symbol.upper()
    q = streamer.subscribe_metrics(symbol)
    try:
        await _pump(ws, q)
    finally:
        streamer.unsubscribe_metrics(symbol, q)


@app.websocket("/ws/alerts")
async def ws_alerts(ws: WebSocket) -> None:
    await ws.accept()
    q = streamer.subscribe_alerts()
    try:
        await _pump(ws, q)
    finally:
        streamer.unsubscribe_alerts(q)
