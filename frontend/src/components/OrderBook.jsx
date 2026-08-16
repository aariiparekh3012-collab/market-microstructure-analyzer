import React from 'react';

export default function OrderBook({ snapshot }) {
  if (!snapshot) return <Panel title="Order book"><div style={{ opacity: 0.4, padding: 12 }}>Waiting for data...</div></Panel>;
  const bids = snapshot.bids || [];
  const asks = snapshot.asks || [];
  const maxQty = Math.max(...bids.map((l) => l.qty || 0), ...asks.map((l) => l.qty || 0), 1);
  return (
    <Panel title="Order Book (5-depth)">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, fontFamily: 'ui-monospace,SFMono-Regular,Menlo,monospace', fontSize: 13 }}>
        <div>
          <Header left="Bid px" right="Qty" />
          {bids.slice(0, 5).map((l, i) => (
            <Row key={i} px={l.price} qty={l.qty} pct={l.qty / maxQty} color="#3ddc84" align="left" />
          ))}
        </div>
        <div>
          <Header left="Ask px" right="Qty" />
          {asks.slice(0, 5).map((l, i) => (
            <Row key={i} px={l.price} qty={l.qty} pct={l.qty / maxQty} color="#e5484d" align="right" />
          ))}
        </div>
      </div>
    </Panel>
  );
}

function Header({ left, right }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 8px', opacity: 0.6, fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase' }}>
      <span>{left}</span><span>{right}</span>
    </div>
  );
}

function Row({ px, qty, pct, color, align }) {
  const bar = { position: 'absolute', top: 0, bottom: 0, width: `${Math.min(100, pct * 100)}%`, background: color, opacity: 0.15 };
  bar[align] = 0;
  return (
    <div style={{ position: 'relative', padding: '4px 8px', borderTop: '1px solid #1c2230', display: 'flex', justifyContent: 'space-between' }}>
      <span style={bar} />
      <span style={{ zIndex: 1 }}>{px?.toFixed(2)}</span>
      <span style={{ zIndex: 1, opacity: 0.85 }}>{qty}</span>
    </div>
  );
}

function Panel({ title, children }) {
  return (
    <div style={{ padding: 12, border: '1px solid #1c2230', borderRadius: 8, background: '#0f131b' }}>
      <div style={{ fontSize: 12, letterSpacing: 0.5, textTransform: 'uppercase', opacity: 0.7, marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  );
}
