"""Tests for the deployment surface: /healthz, /metrics, WS auth, rate limit.

Uses `fastapi.testclient.TestClient`, which runs the app in-process via
Starlette's synchronous WSGI adapter. WebSocket tests use the same client.
Nothing here touches Redis or a real disk.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import backend.api.auth as auth_mod
import backend.api.main as api_main
import backend.api.streamer as sm
from backend.api.auth import RateLimiter, _limiter


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


class _NoopSource:
    """Source that stays alive but never yields a tick.

    We must NOT return immediately — the streamer's supervisor treats a
    clean end as "source finished" and exits the task, which would make
    every /healthz check return 503 (task_alive=False). Instead we sit
    on an interruptible sleep so the task stays alive and warming_up
    remains true for the duration of the test.
    """
    name = "noop"

    async def stream(self, symbols):
        import asyncio
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            return
        if False:  # pragma: no cover — makes this an async generator
            yield


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient wired to a Streamer with an in-memory cache and a temp
    tick-store directory. WS auth and rate limit start OFF; individual
    tests re-enable them via monkeypatch."""
    # Deterministic, self-contained streamer.
    monkeypatch.setattr(sm.settings, "redis_url", "")
    monkeypatch.setattr(sm.settings, "tick_store_dir", tmp_path)
    monkeypatch.setattr(sm, "make_source", lambda: _NoopSource())

    # Start disabled — WS-auth / rate-limit tests below re-enable per case.
    monkeypatch.setattr(api_main.settings, "ws_auth_token", "")
    monkeypatch.setattr(api_main.settings, "http_rate_limit_per_minute", 0)

    # Fresh Streamer, replacing the module-level singleton the app references.
    fresh = sm.Streamer()
    monkeypatch.setattr(api_main, "streamer", fresh)
    monkeypatch.setattr(sm, "streamer", fresh)

    # Rate-limiter counters are process-level — reset before each test.
    _limiter._buckets.clear()

    with TestClient(api_main.app) as c:
        yield c


# --------------------------------------------------------------------------
# /healthz
# --------------------------------------------------------------------------


def test_healthz_returns_200_during_warmup(client):
    """Right after start, task alive but no ticks yet → 200 (warming up)."""
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["warming_up"] is True
    assert body["task_alive"] is True


def test_healthz_returns_503_when_ticks_stale(client, monkeypatch):
    s = api_main.streamer
    metrics = getattr(s, "metrics")
    # Simulate: warm-up long since elapsed, one tick was ingested a minute ago.
    monkeypatch.setattr(s, "started_at", time.perf_counter() - 3600)
    metrics.ticks_ingested = 1
    metrics.last_tick_ts = time.perf_counter() - 60

    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json()["ok"] is False


def test_healthz_returns_503_when_task_dead(client, monkeypatch):
    monkeypatch.setattr(api_main.streamer, "_task", None)
    r = client.get("/healthz")
    assert r.status_code == 503


# --------------------------------------------------------------------------
# /metrics
# --------------------------------------------------------------------------


def test_metrics_prometheus_shape(client, monkeypatch):
    s = api_main.streamer
    metrics = getattr(s, "metrics")
    monkeypatch.setattr(metrics, "ticks_ingested", 42)
    monkeypatch.setattr(metrics, "anomalies_emitted", 3)
    metrics.messages_dropped["metrics:AAA"] = 7
    metrics.ws_clients["book:AAA"] = 2

    r = client.get("/metrics")
    assert r.status_code == 200
    ctype = r.headers["content-type"]
    assert ctype.startswith("text/plain")

    body = r.text
    # Every metric must have a HELP + TYPE preamble.
    for name in [
        "mma_ticks_ingested_total",
        "mma_ticks_persist_failed_total",
        "mma_source_restarts_total",
        "mma_anomalies_emitted_total",
        "mma_last_tick_lag_seconds",
        "mma_ws_clients",
        "mma_messages_dropped_total",
    ]:
        assert f"# HELP {name}" in body, f"missing HELP for {name}"
        assert f"# TYPE {name}" in body, f"missing TYPE for {name}"

    # Values landed
    assert "mma_ticks_ingested_total 42" in body
    assert "mma_anomalies_emitted_total 3" in body
    assert 'mma_ws_clients{topic="book:AAA"} 2' in body
    assert 'mma_messages_dropped_total{topic="metrics:AAA"} 7' in body


# --------------------------------------------------------------------------
# /api endpoints — rate limit
# --------------------------------------------------------------------------


def test_api_endpoints_serve_when_rate_limit_disabled(client):
    """per_minute=0 means no throttling."""
    for _ in range(30):
        r = client.get("/api/symbols")
        assert r.status_code == 200


def test_rate_limit_returns_429_and_retry_after(client, monkeypatch):
    """Enable a 3/min cap and verify the 4th request is 429 with Retry-After."""
    monkeypatch.setattr(api_main.settings, "http_rate_limit_per_minute", 3)
    # Reinstall the process-level limiter with the new cap.
    # Use the module imported at top of file to avoid re-import issues.
    monkeypatch.setattr(auth_mod, "_limiter", RateLimiter(per_minute=3))

    # 3 allowed
    for _ in range(3):
        assert client.get("/api/symbols").status_code == 200
    r = client.get("/api/symbols")
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) >= 0


def test_rate_limiter_isolates_keys():
    """RateLimiter itself: two different keys keep independent buckets."""
    rl = RateLimiter(per_minute=2)
    a_ok = [rl.check("a")[0] for _ in range(3)]
    b_ok = [rl.check("b")[0] for _ in range(3)]
    assert a_ok == [True, True, False]
    assert b_ok == [True, True, False]


def test_rate_limiter_disabled_by_zero():
    rl = RateLimiter(per_minute=0)
    assert all(rl.check("anything")[0] for _ in range(100))


# --------------------------------------------------------------------------
# WebSocket auth
# --------------------------------------------------------------------------


def test_ws_no_token_dev_mode_allows(client):
    """Empty WS_AUTH_TOKEN = dev mode: connection accepted."""
    with client.websocket_connect("/ws/orderbook/AAA") as ws:
        # Accepted; we don't wait for a message (there's no source producing).
        ws.close()


def test_ws_wrong_token_closes_with_4401(client, monkeypatch):
    monkeypatch.setattr(api_main.settings, "ws_auth_token", "correct-secret")

    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as excinfo:
        # The connect attempt should raise; avoid calling other methods
        # inside the context to ensure only one invocation may throw.
        with client.websocket_connect("/ws/orderbook/AAA?token=wrong"):
            pass
    assert excinfo.value.code == 4401


def test_ws_correct_token_accepts(client, monkeypatch):
    monkeypatch.setattr(api_main.settings, "ws_auth_token", "correct-secret")
    with client.websocket_connect(
        "/ws/orderbook/AAA?token=correct-secret"
    ) as ws:
        ws.close()  # accepted OK


def test_ws_missing_token_when_required_closes_4401(client, monkeypatch):
    monkeypatch.setattr(api_main.settings, "ws_auth_token", "correct-secret")
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as excinfo:
        # Only the connect should raise the disconnect; don't call receive_text().
        with client.websocket_connect("/ws/alerts"):
            pass
    assert excinfo.value.code == 4401
