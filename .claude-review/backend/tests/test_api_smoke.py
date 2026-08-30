from __future__ import annotations

import os

os.environ["REDIS_URL"] = ""
os.environ["TICK_STORE_DIR"] = "/tmp/mma-api-test-ticks"

from fastapi.testclient import TestClient  # noqa: E402

from backend.api.main import app  # noqa: E402


def test_health_websocket_and_volume_profile():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        with client.websocket_connect("/ws/analytics/RELIANCE") as websocket:
            payload = websocket.receive_json()

        assert payload["symbol"] == "RELIANCE"
        assert "timestamp" in payload
        assert "ofi_60s" in payload

        profile = client.get("/api/volume-profile/RELIANCE")
        assert profile.status_code == 200
        assert profile.json()
