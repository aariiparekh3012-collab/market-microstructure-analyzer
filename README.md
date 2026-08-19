<![CDATA[<div align="center">

# Real-Time Market Microstructure Analyzer

**A production-grade system for computing, backtesting, and visualising market microstructure signals on Indian equities**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-00a393.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18+-61dafb.svg)](https://react.dev)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Architecture](#architecture) · [Modules](#analytics-modules) · [Notebooks](#research-notebooks) · [Demo](#interactive-demo) · [Setup](#quick-start)

</div>

---

## What This Does

Ingests real-time order-book snapshots (5-level depth, 5 ticks/sec), computes **22 microstructure metrics** per tick across 9 analytics modules, detects statistical anomalies, and streams everything to an interactive React dashboard over WebSocket.

The analytics pipeline processes each tick in **~1.2 ms** (0.6% of the 200ms budget), leaving 99%+ headroom for additional modules or higher tick rates.

### Key Metrics

| Category | Metrics | Reference |
|----------|---------|-----------|
| **Spread Analysis** | Quoted, relative, volume-weighted spread | — |
| **Order Flow** | Cont-Kukanov-Stoikov OFI at 60s/300s/900s windows | Cont et al. (2014) |
| **Price Discovery** | Hasbrouck information share, permanent impact | Hasbrouck (1991, 1995) |
| **Price Impact** | Kyle's Lambda via rolling OLS regression | Kyle (1985) |
| **Liquidity** | Amihud illiquidity ratio, Roll's implied spread | Amihud (2002), Roll (1984) |
| **Volume** | Tick-rule classification, volume profile, cumulative delta | — |
| **VWAP** | Session VWAP with ±σ deviation bands | — |
| **Anomaly Detection** | Z-score detectors for spread blow-ups, volume spikes, OFI extremes | — |

---

## Architecture

```mermaid
graph TB
    subgraph Ingestion
        MS[MockSource<br/>GBM + mean-reversion]
        AS[AngelSource<br/>SmartAPI WebSocket]
    end

    subgraph Analytics Engine
        SP[Spread<br/>quoted · relative · weighted]
        OFI[OFI Calculator<br/>Cont-Kukanov-Stoikov]
        VW[Session VWAP<br/>± σ bands]
        VP[Volume Profile<br/>tick-rule classifier]
        KL[Kyle's Lambda<br/>rolling OLS]
        AM[Amihud Illiquidity<br/>rolling average]
        RS[Roll's Spread<br/>serial covariance]
        HB[Hasbrouck<br/>information share]
        AD[Anomaly Detector<br/>rolling z-score]
    end

    subgraph API Layer
        FA[FastAPI Server]
        WS1[/ws/orderbook/:symbol]
        WS2[/ws/analytics/:symbol]
        WS3[/ws/alerts]
    end

    subgraph Frontend
        RC[React + Recharts<br/>Live Dashboard]
    end

    subgraph Research
        NB1[Microstructure Analysis<br/>18 charts]
        NB2[Latency Report<br/>8 charts]
        BT[OFI Signal Backtester]
        EX[Execution Simulator<br/>TWAP / VWAP]
        ST[Stress Test<br/>concurrent WebSocket]
    end

    MS & AS --> SP & OFI & VW & VP & KL & AM & RS & HB & AD
    SP & OFI & VW & VP & KL & AM & RS & HB & AD --> FA
    FA --> WS1 & WS2 & WS3
    WS1 & WS2 & WS3 --> RC
```

---

## Analytics Modules

### 1. Spread Analytics (`backend/analytics/spread.py`)

Computes three flavours of bid-ask spread per tick. The **weighted spread** uses volume across all 5 levels, giving a more realistic cost-of-execution estimate than the quoted spread alone.

### 2. Order Flow Imbalance (`backend/analytics/order_flow.py`)

Implements the **Cont-Kukanov-Stoikov (2014)** event-level OFI: tracks changes in best bid/ask price and quantity to compute a signed flow signal, then aggregates over 60s, 300s, and 900s rolling windows. The OFI signal is predictive of short-term price direction (see backtest results below).

### 3. Kyle's Lambda (`backend/analytics/kyle_lambda.py`)

Estimates the **price impact coefficient** from Kyle (1985) via rolling OLS regression of midprice changes on signed order flow. A higher λ means less liquidity — large orders move the price more. Reports λ, R², and t-statistic per tick.

### 4. Amihud Illiquidity (`backend/analytics/amihud.py`)

Rolling **Amihud (2002)** illiquidity ratio: average absolute return per unit of dollar volume. Captures how much prices move for a given amount of trading activity. Complements Kyle's Lambda with a model-free approach.

### 5. Roll's Implied Spread (`backend/analytics/roll_spread.py`)

Estimates the **effective bid-ask spread** from transaction prices alone using **Roll (1984)**. Exploits the negative serial covariance in price changes induced by bid-ask bounce: `spread = 2√(-Cov(ΔPₜ, ΔPₜ₋₁))`. Useful for markets where order book data is unavailable.

### 6. Hasbrouck Information Share (`backend/analytics/hasbrouck.py`)

Decomposes price variance into **trade-induced** vs **quote-induced** components following **Hasbrouck (1991, 1995)**. A high trade information share means informed traders are active and the market is discovering price through order flow rather than quote adjustments.

### 7. VWAP Tracker (`backend/analytics/vwap.py`)

Session VWAP with online running sums (O(1) per tick, auto-resets on day boundary). Computes volume-weighted standard deviation for ±σ bands — useful for detecting when the last traded price deviates significantly from the session's fair value.

### 8. Volume Profile (`backend/analytics/volume.py`)

Tick-rule trade classifier (+1 buy / -1 sell) and price-bucketed volume profile. Tracks cumulative signed delta — a running measure of net buying vs. selling pressure.

### 9. Anomaly Detector (`backend/analytics/anomaly_detector.py`)

Rolling z-score monitors on spread, volume, and OFI. Fires alerts when any metric exceeds ±3σ from its rolling mean. Uses online Welford-style variance for O(1) updates.

---

## Research Notebooks

### Microstructure Analysis (`notebooks/microstructure_analysis.ipynb`)

18 publication-quality charts across 10 sections: price dynamics and return distributions, spread analysis (time series, boxplots, quoted vs. weighted), OFI signal analysis (time series with directional fill, predictive correlation across 3 horizons, distributions), VWAP with ±1σ bands, volume profiling, anomaly detection breakdown, cross-metric correlation heatmaps, and OFI autocorrelation structure (ACF).

### Latency Report (`notebooks/latency_report.ipynb`)

8 charts profiling per-tick computation latency: stat tiles, per-module horizontal bar breakdown, total latency histogram with P50/P95/P99 markers, per-module percentile comparison, latency-over-time trend analysis, per-symbol boxplots, and OFI optimisation impact projection.

---

## Backtesting & Simulation

### OFI Signal Backtester (`backend/analytics/backtester.py`)

Z-score entry/exit strategy on the OFI signal. Configurable parameters: entry threshold (default 1.5σ), exit threshold (0.3σ), lookback window, transaction costs. Outputs: Sharpe ratio, max drawdown, profit factor, win rate, trade-level P&L.

Run: `python run_backtest.py`

### Execution Simulator (`backend/analytics/execution_sim.py`)

TWAP and VWAP execution strategies with realistic order-book walking (fills across ask levels 1-5). Measures arrival slippage, implementation shortfall, and fill rates across order sizes from 100 to 2,000 shares.

Run: `python run_execution_sim.py`

### WebSocket Stress Test (`tests/stress_test.py`)

Concurrent client load testing: ramps up N WebSocket connections, measures connection success rate, message throughput, and first-message latency. Default: 50 clients × 15 seconds.

Run: `python tests/stress_test.py --clients 50 --duration 15`

---

## Interactive Demo

Open `demo/index.html` in any browser — no server required. A self-contained 1MB HTML file with embedded tick data for all 5 symbols. Features:

- Dark trading-terminal theme
- 5-level order book with quantity bars
- Live-updating Price + VWAP, Spread, OFI, and Cumulative Delta charts
- Symbol selector tabs (RELIANCE, TCS, HDFCBANK, INFY, ICICIBANK)
- Playback speed controls (1×, 2×, 5×, 10×)

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend)

### Backend

```bash
# Clone and setup
git clone https://github.com/aariiparekh3012-collab/quantproject2.git
cd quantproject2

python -m venv .venv
source .venv/bin/activate      # Linux/Mac
# .venv\Scripts\activate       # Windows

pip install -r requirements.txt
cp .env.example .env

# Run the analytics server
uvicorn backend.api.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# Dashboard at http://localhost:5173
```

### Generate Research Data

```bash
python run_profiler.py          # Latency profiling (15,000 ticks)
python run_backtest.py          # OFI signal backtest
python run_execution_sim.py     # TWAP/VWAP execution sim
python tests/stress_test.py     # WebSocket stress test
```

---

## Project Structure

```
├── backend/
│   ├── analytics/
│   │   ├── spread.py              # Quoted, relative, weighted spread
│   │   ├── order_flow.py          # OFI (Cont-Kukanov-Stoikov 2014)
│   │   ├── kyle_lambda.py         # Kyle's Lambda (price impact)
│   │   ├── amihud.py              # Amihud Illiquidity Ratio
│   │   ├── roll_spread.py         # Roll's Implied Spread
│   │   ├── hasbrouck.py           # Hasbrouck Information Share
│   │   ├── vwap.py                # Session VWAP + deviation bands
│   │   ├── volume.py              # Tick-rule classifier + volume profile
│   │   ├── anomaly_detector.py    # Rolling z-score anomaly detection
│   │   ├── engine.py              # Central engine (22 metrics/tick)
│   │   ├── profiler.py            # Latency profiler wrapper
│   │   ├── backtester.py          # OFI signal backtester
│   │   └── execution_sim.py       # TWAP/VWAP execution simulator
│   ├── ingestion/
│   │   ├── mock_source.py         # GBM + mean-reversion mock generator
│   │   └── angel_source.py        # Angel One SmartAPI (live data)
│   ├── api/
│   │   ├── main.py                # FastAPI app + WebSocket endpoints
│   │   └── streamer.py            # Pub/sub broadcaster
│   └── storage/
│       ├── tick_store.py          # Parquet writer
│       └── state_cache.py         # Redis / in-memory cache
├── frontend/                      # React + Vite + Recharts dashboard
├── notebooks/
│   ├── microstructure_analysis.ipynb  # 18-chart research notebook
│   └── latency_report.ipynb           # 8-chart performance report
├── demo/
│   └── index.html                 # Self-contained interactive dashboard
├── tests/
│   └── stress_test.py             # WebSocket load test
├── run_profiler.py                # Latency profiler runner
├── run_backtest.py                # Backtester runner
└── run_execution_sim.py           # Execution sim runner
```

---

## Switching to Live Data

1. Open an Angel One demat account
2. Register at [SmartAPI](https://smartapi.angelbroking.com/)
3. Fill credentials in `.env`
4. Set `DATA_SOURCE=angel` in `.env`
5. Complete the TODOs in `backend/ingestion/angel_source.py`
6. Restart the backend

---

## References

1. Cont, R., Kukanov, A., & Stoikov, S. (2014). *The Price Impact of Order Book Events.* Journal of Financial Econometrics, 12(1), 47–88.
2. Kyle, A. S. (1985). *Continuous Auctions and Insider Trading.* Econometrica, 53(6), 1315–1335.
3. Amihud, Y. (2002). *Illiquidity and Stock Returns: Cross-Section and Time-Series Effects.* Journal of Financial Markets, 5(1), 31–56.
4. Roll, R. (1984). *A Simple Implicit Measure of the Effective Bid-Ask Spread in an Efficient Market.* The Journal of Finance, 39(4), 1127–1139.
5. Hasbrouck, J. (1991). *Measuring the Information Content of Stock Trades.* The Journal of Finance, 46(1), 179–207.
6. Hasbrouck, J. (1995). *One Security, Many Markets: Determining the Contributions to Price Discovery.* The Journal of Finance, 50(4), 1175–1199.

---

<div align="center">

**Aarya Parekh** · IIT Bombay · 2026

</div>
]]>