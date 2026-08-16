import React, { useEffect, useMemo, useState } from 'react';
import SymbolPicker from './components/SymbolPicker.jsx';
import OrderBook from './components/OrderBook.jsx';
import AnomalyLog from './components/AnomalyLog.jsx';
import MetricLineChart from './charts/MetricLineChart.jsx';
import { useJsonWebSocket } from './hooks/useJsonWebSocket.js';

const DEFAULT_SYMBOLS = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK'];

export default function App() {
  const [symbols, setSymbols] = useState(DEFAULT_SYMBOLS);
  const [symbol, setSymbol] = useState(DEFAULT_SYMBOLS[0]);

  useEffect(() => {
    fetch('/api/symbols').then((r) => r.json()).then((s) => {
      if (Array.isArray(s) && s.length) {
        setSymbols(s);
        setSymbol(s[0]);
      }
    }).catch(() => {});
  }, []);

  const book = useJsonWebSocket(`ws://${location.hostname}:8000/ws/orderbook/${symbol}`, { historyLimit: 1 });
  const metrics = useJsonWebSocket(`ws://${location.hostname}:8000/ws/analytics/${symbol}`, { historyLimit: 300 });
  const alerts = useJsonWebSocket(`ws://${location.hostname}:8000/ws/alerts`, { historyLimit: 200 });

  const spreadPts   = useMemo(() => metrics.history.map((m) => ({ t: m.ts, v: m.spread })), [metrics.history]);
  const ofiPts      = useMemo(() => metrics.history.map((m) => ({ t: m.ts, v: m.ofi_60s })), [metrics.history]);
  const pricePts    = useMemo(() => metrics.history.map((m) => ({ t: m.ts, v: m.ltp })), [metrics.history]);
  const vwapPts     = useMemo(() => metrics.history.map((m) => ({ t: m.ts, v: m.vwap })).filter((p) => p.v != null), [metrics.history]);

  const status = book.status;

  return (
    <div style={{ minHeight: '100vh' }}>
      <SymbolPicker symbols={symbols} value={symbol} onChange={setSymbol} status={status} />

      <div style={{ display: 'grid', gridTemplateColumns: '380px 1fr', gap: 12, padding: 12 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <OrderBook snapshot={book.latest} />
          <StatCard latest={metrics.latest} />
        </div>

        <div style={{ display: 'grid', gap: 12 }}>
          <MetricLineChart title={`${symbol} — Last Traded Price`} points={pricePts} color="#e6e9ef" />
          <MetricLineChart title="VWAP (session)" points={vwapPts} color="#5b8def" />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <MetricLineChart title="Quoted spread" points={spreadPts} color="#f5a623" />
            <MetricLineChart title="Order flow imbalance (60s)" points={ofiPts} color="#a879ff" baseline={0} />
          </div>
          <AnomalyLog alerts={alerts.history} />
        </div>
      </div>
    </div>
  );
}

function StatCard({ latest }) {
  const rows = latest
    ? [
        ['LTP', latest.ltp?.toFixed(2)],
        ['Mid', latest.midprice?.toFixed(2)],
        ['Spread', latest.spread?.toFixed(2)],
        ['Rel. spread (bps)', latest.relative_spread ? (latest.relative_spread * 1e4).toFixed(2) : '—'],
        ['OFI 60s', latest.ofi_60s?.toFixed(0)],
        ['VWAP', latest.vwap ? latest.vwap.toFixed(2) : '—'],
        ['VWAP dev.', latest.vwap_dev != null ? latest.vwap_dev.toFixed(2) : '—'],
        ['Cum. delta', latest.cum_delta],
      ]
    : [];
  return (
    <div style={{ padding: 12, border: '1px solid #1c2230', borderRadius: 8, background: '#0f131b' }}>
      <div style={{ fontSize: 12, letterSpacing: 0.5, textTransform: 'uppercase', opacity: 0.7, marginBottom: 8 }}>Live metrics</div>
      {!rows.length && <div style={{ opacity: 0.4 }}>Waiting for data...</div>}
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', fontSize: 13, borderTop: '1px solid #1c2230' }}>
          <span style={{ opacity: 0.7 }}>{k}</span>
          <span style={{ fontFamily: 'ui-monospace,SFMono-Regular,Menlo,monospace' }}>{v ?? '—'}</span>
        </div>
      ))}
    </div>
  );
}
