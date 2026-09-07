# API Reference and Quick Start

The FastAPI service publishes the current synthetic or configured market-data
stream through REST endpoints and server-to-client WebSocket channels. This
guide documents the public interface implemented in `backend/api/main.py`.

The examples assume the service is running locally at `http://localhost:8000`
with the default mock data source. Response values shown below are illustrative;
prices, timestamps, quantities, metrics, and alerts change as new snapshots are
processed.

## Five-minute walkthrough

### 1. Install and start the service

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate          # Linux or macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell

pip install -r requirements.txt
cp .env.example .env               # use Copy-Item on Windows PowerShell
uvicorn backend.api.main:app --reload
```

The service starts at `http://localhost:8000`. Interactive OpenAPI
documentation is available at `http://localhost:8000/docs`, with ReDoc at
`http://localhost:8000/redoc` and the raw schema at
`http://localhost:8000/openapi.json`.

### 2. Check the service and symbols

In a second terminal:

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/symbols
```

With the default configuration, the responses have this shape:

```json
{
  "status": "ok",
  "source": "mock",
  "symbols": ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]
}
```

```json
["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]
```

### 3. Read one analytics message

The installed `websockets` package can be used as a small client. Save this as
`stream_once.py` outside the repository, or run it in a Python session:

```python
import asyncio
import json

import websockets


async def main() -> None:
    uri = "ws://localhost:8000/ws/analytics/RELIANCE"
    async with websockets.connect(uri) as websocket:
        message = json.loads(await websocket.recv())
        print(json.dumps(message, indent=2))


asyncio.run(main())
```

The connection receives analytics continuously. This example prints the first
message and closes. When `WS_AUTH_TOKEN` is configured, append
`?token=<configured-token>` to the WebSocket URL.

### 4. Retrieve accumulated state

After the streamer has processed a few snapshots:

```bash
curl http://localhost:8000/api/volume-profile/RELIANCE
curl "http://localhost:8000/api/alerts/recent?limit=10"
```

The first response maps stringified price buckets to accumulated traded volume.
The alerts endpoint may return an empty list until a rolling metric crosses its
configured anomaly threshold.

## REST API

Application REST responses use JSON. The `/api/*` endpoints have no identity
authentication, but they share a fixed-window, per-client-IP rate limiter
(default: 120 requests per minute). Cross-origin access is controlled by
`CORS_ALLOW_ORIGINS`, which defaults to `*` for local development.

### `GET /healthz`

Reports liveness and readiness. It returns `200 OK` while the ingestion task is
alive and ticks are fresh (or the service is within its 30-second startup grace
period), and `503 Service Unavailable` if the task is dead or ticks are stale.

The JSON response includes `ok`, `task_alive`, `ticks_ingested`,
`source_restarts`, `seconds_since_last_tick`, and `warming_up`.

### `GET /metrics`

Returns Prometheus text format for ingestion, persistence failures, data-quality
rejections and repairs, quarantine-write failures, source restarts, anomalies,
last-tick lag, connected WebSocket clients, and dropped subscriber messages.
This endpoint is not rate-limited by the application; restrict it at the reverse
proxy in an internet-facing deployment.

### `GET /api/health`

Provides the compatibility health response and identifies the selected data
source and configured symbols. Prefer `GET /healthz` for deployment probes.
This endpoint returns HTTP 200 with `status` set to `ok` or `degraded`.

**Parameters:** none

**Success response:** `200 OK`

```json
{
  "status": "ok",
  "source": "mock",
  "symbols": ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]
}
```

This is an application-level health check. It does not prove that an external
brokerage feed, Redis server, or individual WebSocket subscriber is healthy.

### `GET /api/symbols`

Returns the uppercase symbols parsed from the `SYMBOLS` environment variable.

**Parameters:** none

**Success response:** `200 OK`

```json
["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]
```

### `GET /api/data-quality`

Returns current-process validation and quarantine counters. `repaired` only
counts accepted snapshots whose symbol was trimmed or uppercased; other defects
are not silently altered.

```json
{
  "received": 1200,
  "accepted": 1197,
  "rejected": 3,
  "repaired": 4,
  "quarantine_writes": 3,
  "quarantine_write_failures": 0,
  "rejected_by_reason": {
    "crossed_or_locked_book": 2,
    "non_monotonic_timestamp": 1
  },
  "repair_policy": "symbol_trim_and_uppercase_only"
}
```

Rejected snapshots are stopped before persistence, analytics, cache updates,
or WebSocket broadcast. They are appended to `DATA_QUARANTINE_PATH` as JSONL.

### `GET /api/volume-profile/{symbol}`

Returns the in-memory traded-volume profile accumulated for a symbol since the
current process started. The path value is normalised to uppercase.

| Parameter | Location | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `symbol` | path | string | yes | A symbol included in the configured stream, such as `RELIANCE` |

**Success response:** `200 OK`

```json
{
  "2899.5": 37,
  "2900.0": -12,
  "2900.5": 24
}
```

Price-bucket keys are strings because JSON object keys must be strings. Values
are signed volume changes assigned to each price bucket, so negative values are
valid.

**No data response:** `404 Not Found`

```json
{
  "detail": "no data yet for this symbol"
}
```

A `404` can mean that the symbol is not configured or that no snapshot has been
processed for it yet.

### `GET /api/alerts/recent`

Returns the most recent anomalies retained by the current process, ordered from
oldest to newest within the requested slice.

| Parameter | Location | Type | Required | Default | Description |
| --- | --- | --- | --- | --- | --- |
| `limit` | query | integer | no | `100` | Maximum recent slice requested from the in-memory alert history |

**Success response:** `200 OK`

```json
[
  {
    "symbol": "RELIANCE",
    "ts": "2026-08-22T12:00:00.000000+00:00",
    "kind": "spread_blowup",
    "severity": "warn",
    "detail": {
      "spread": 0.15,
      "zscore": 3.2
    }
  }
]
```

The exact `kind` and `detail` fields depend on the triggered detector. An empty
list is a valid response. If `limit` cannot be parsed as an integer, FastAPI
returns `422 Unprocessable Entity`.

## WebSocket API

WebSocket endpoints send JSON text messages from the server to the client. A
client does not need to send subscription messages after connecting. When
`WS_AUTH_TOKEN` is non-empty, clients must pass the matching shared secret as
the `token` query parameter. Use `ws://` for local HTTP deployments and
`wss://` when the service is hosted behind HTTPS.

Connections are process-local and are not replayed. Slow subscribers use a
bounded queue; when it fills, the oldest queued message is discarded before a
new one is added.

### `WS /ws/orderbook/{symbol}`

Streams normalised five-level order-book snapshots for the requested configured
symbol. The path value is normalised to uppercase.

```json
{
  "symbol": "RELIANCE",
  "ts": "2026-08-22T12:00:00.000000+00:00",
  "bids": [
    {"price": 2899.95, "qty": 210, "orders": 4}
  ],
  "asks": [
    {"price": 2900.05, "qty": 185, "orders": 3}
  ],
  "ltp": 2900.05,
  "ltq": 18,
  "volume": 1250
}
```

`bids` and `asks` contain up to five levels in the validated workflow. The
example abbreviates each side to one level for readability.

### `WS /ws/analytics/{symbol}`

Streams the analytics record produced for every snapshot of the requested
configured symbol.

```json
{
  "symbol": "RELIANCE",
  "timestamp": "2026-08-22T12:00:00.000000+00:00",
  "ltp": 2900.05,
  "midprice": 2900.0,
  "spread": 0.1,
  "relative_spread": 0.00003448,
  "weighted_spread": 0.08,
  "ofi_60s": 120.0,
  "ofi_300s": 120.0,
  "ofi_900s": 120.0,
  "vwap": 2900.05,
  "vwap_dev": 0.0,
  "vwap_stdev": 0.0,
  "cum_delta": 18,
  "kyle_lambda": null,
  "kyle_r2": null,
  "kyle_t_stat": null,
  "amihud_illiq": null,
  "roll_spread_bps": null,
  "roll_serial_cov": null,
  "trade_variance_share": null,
  "avg_signed_trade_return_bps": null
}
```

Advanced rolling estimators return `null` while their warm-up or data-quality
requirements are not satisfied. Field names and meanings are summarised below.

| Field | Description |
| --- | --- |
| `symbol`, `timestamp` | Record identity and ISO 8601 event timestamp |
| `ltp`, `midprice` | Last traded price and best-bid/best-ask midpoint |
| `spread` | Best-ask price minus best-bid price |
| `relative_spread` | Quoted spread divided by midprice |
| `weighted_spread` | Depth-weighted spread across the available book levels |
| `ofi_60s`, `ofi_300s`, `ofi_900s` | Rolling order-flow imbalance over each horizon |
| `vwap`, `vwap_dev`, `vwap_stdev` | Session VWAP, relative LTP deviation, and weighted standard deviation |
| `cum_delta` | Cumulative signed volume |
| `kyle_lambda`, `kyle_r2`, `kyle_t_stat` | Rolling price-impact estimate and regression diagnostics |
| `amihud_illiq` | Rolling Amihud illiquidity estimate |
| `roll_spread_bps`, `roll_serial_cov` | Roll implied spread and supporting serial covariance |
| `trade_variance_share`, `avg_signed_trade_return_bps` | Descriptive trade/quote variance diagnostic outputs |

### `WS /ws/alerts`

Streams every anomaly emitted by the analytics engine across all configured
symbols.

```json
{
  "symbol": "RELIANCE",
  "ts": "2026-08-22T12:00:00.000000+00:00",
  "kind": "spread_blowup",
  "severity": "warn",
  "detail": {
    "spread": 0.15,
    "zscore": 3.2
  }
}
```

Unlike the symbol-specific channels, this endpoint has no path parameter. Use
`GET /api/alerts/recent` when a bounded slice of retained alert history is more
appropriate than a continuous stream.

### Symbol and connection behaviour

Symbol-specific WebSocket endpoints normalise symbols to uppercase and reject
unknown symbols before allocating subscription state (application close code
`4404`). When WebSocket authentication is enabled, a missing or incorrect token
is rejected before subscription registration (application close code `4401`).
Obtain valid symbols from `GET /api/symbols`. If the server restarts, clients
must reconnect and accumulated process-local analytics state begins again.

## Configuration

Settings are loaded from environment variables and an optional root-level
`.env` file when the application starts. Environment variable names are the
uppercase forms shown below.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATA_SOURCE` | `mock` | Selects `mock` or the experimental `angel` source |
| `SYMBOLS` | `RELIANCE,TCS,HDFCBANK,INFY,ICICIBANK` | Comma-separated symbols, normalised to uppercase |
| `ANGEL_API_KEY` | empty | Angel One API key; required only for the experimental source |
| `ANGEL_CLIENT_CODE` | empty | Angel One client code; required only for the experimental source |
| `ANGEL_MPIN` | empty | Angel One MPIN; required only for the experimental source |
| `ANGEL_TOTP_SECRET` | empty | Angel One TOTP secret; required only for the experimental source |
| `BACKEND_HOST` | `0.0.0.0` | Intended backend bind host |
| `BACKEND_PORT` | `8000` | Intended backend bind port |
| `CORS_ALLOW_ORIGINS` | `*` | Comma-separated allowed browser origins; replace `*` in production |
| `LOG_LEVEL` | `INFO` | Application log level |
| `LOG_JSON` | `false` | Emit one-line JSON logs when enabled |
| `WS_AUTH_TOKEN` | empty | Shared secret required by WebSocket clients when configured |
| `HTTP_RATE_LIMIT_PER_MINUTE` | `120` | Per-client-IP limit for `/api/*`; `0` disables it |
| `TICK_STORE_DIR` | `./data/ticks` | Directory for rolled tick data |
| `PARQUET_ROLL_MINUTES` | `15` | Tick-store roll interval in minutes |
| `DATA_QUARANTINE_PATH` | `./data/quarantine/rejected_ticks.jsonl` | Append-only JSONL file for rejected snapshots |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection; falls back to in-memory state when unavailable |

`BACKEND_HOST` and `BACKEND_PORT` are application settings; the documented
`uvicorn backend.api.main:app --reload` command uses Uvicorn's own defaults. Pass
`--host` and `--port` explicitly when binding Uvicorn differently.

Keep `.env` local and never commit brokerage credentials or TOTP secrets. The
Angel One adapter is an explicit experimental stub and is not a validated live
market-data connector.

## Error handling and limitations

- REST validation errors use FastAPI's standard `422` response format.
- The volume-profile endpoint returns `404` until state exists for the symbol.
- WebSocket payloads are not versioned and currently have no envelope or
  sequence number.
- WebSocket clients must implement their own reconnect and resubscription logic.
- The built-in controls are deliberately minimal: `/api/*` uses a per-process
  IP rate limiter, WebSockets use an optional shared secret, and `/healthz`
  and `/metrics` remain unauthenticated. Use a reverse proxy, WAF, OAuth2
  proxy, or mTLS as appropriate before exposing the service publicly.
- Mock or replayed output validates the software pipeline, not exchange-feed
  correctness, predictive power, or profitability.

For the project's validation boundary, see [`VALIDATION.md`](VALIDATION.md).
