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
message and closes.

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

All REST responses use JSON. The service currently has no authentication layer.
Cross-origin requests are allowed from any origin by the application-level CORS
configuration.

### `GET /api/health`

Reports whether the application is serving requests and identifies the selected
data source and configured symbols.

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
are non-negative changes in cumulative volume assigned to each price bucket.

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
client does not need to send subscription messages after connecting. Use `ws://`
for local HTTP deployments and `wss://` when the service is hosted behind HTTPS.

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

The service does not currently reject an unknown WebSocket symbol. Such a
connection remains open but receives no messages because the streamer only
publishes configured symbols. Obtain valid values from `GET /api/symbols` before
subscribing. If the server restarts, clients must reconnect and accumulated
process-local analytics state begins again.

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
| `TICK_STORE_DIR` | `./data/ticks` | Directory for rolled tick data |
| `PARQUET_ROLL_MINUTES` | `15` | Tick-store roll interval in minutes |
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
- REST and WebSocket interfaces currently have no authentication or rate
  limiting; do not expose the development service directly to an untrusted
  network.
- Mock or replayed output validates the software pipeline, not exchange-feed
  correctness, predictive power, or profitability.

For the project's validation boundary, see [`VALIDATION.md`](VALIDATION.md).
