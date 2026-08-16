# Real-Time Market Microstructure Analyzer

A web-based system that ingests live and historical Indian equity market data,
computes microstructure signals (bid-ask spreads, order flow imbalance, VWAP,
volume profile), detects statistical anomalies, and streams everything to an
interactive React dashboard.

## Quick Start

### 1. Backend

```powershell
cd "C:\Users\AARYA\OneDrive\Documents\semester 5 iitb year3\quant project aarya parekh"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn backend.api.main:app --reload
```

Check: http://localhost:8000/api/health

### 2. Frontend

```powershell
cd frontend
npm install
npm run dev
```

Dashboard: http://localhost:5173

### 3. Tests

```powershell
pytest
```

## Architecture

```
Frontend (React + Vite + Recharts)
        │ WebSocket + REST
Backend (FastAPI)
  ├── Ingestion (Mock / Angel One SmartAPI)
  ├── Analytics Engine (Spread, OFI, VWAP, Volume, Anomalies)
  └── Storage (Parquet + Redis/in-memory cache)
```

## Metrics

| Metric | Module |
|--------|--------|
| Quoted / relative / weighted spread | analytics/spread.py |
| Order Flow Imbalance (Cont-Kukanov-Stoikov 2014) | analytics/order_flow.py |
| Session VWAP + deviation bands | analytics/vwap.py |
| Volume profile + tick-rule classifier + cum. delta | analytics/volume.py |
| Kyle's lambda, Amihud illiquidity | analytics/impact.py |
| Spread blow-ups, volume spikes, OFI extremes | analytics/anomaly_detector.py |

## Switching to Live Data

1. Open Angel One demat account
2. Register at https://smartapi.angelbroking.com/
3. Fill credentials in `.env`
4. Set `DATA_SOURCE=angel`
5. Implement TODOs in `backend/ingestion/angel_source.py`
6. Restart backend
