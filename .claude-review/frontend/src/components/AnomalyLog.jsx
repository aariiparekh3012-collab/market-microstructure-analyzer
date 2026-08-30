import React from 'react';

export default function AnomalyLog({ alerts }) {
  return (
    <div style={{ padding: 12, border: '1px solid #1c2230', borderRadius: 8, background: '#0f131b', maxHeight: 320, overflow: 'auto' }}>
      <div style={{ fontSize: 12, letterSpacing: 0.5, textTransform: 'uppercase', opacity: 0.7, marginBottom: 8 }}>Anomaly Log</div>
      {!alerts?.length && <div style={{ opacity: 0.4 }}>No anomalies detected yet.</div>}
      {alerts?.slice().reverse().map((a, i) => {
        let severityColor = '#5b8def';
        if (a.severity === 'critical') {
          severityColor = '#e5484d';
        } else if (a.severity === 'warn') {
          severityColor = '#f5a623';
        }

        return (
        <div key={`${a.ts}-${a.kind}-${i}`} style={{ padding: '6px 0', borderTop: i ? '1px solid #1c2230' : 'none', display: 'flex', gap: 12, fontSize: 12 }}>
          <span style={{ opacity: 0.6, minWidth: 90 }}>{new Date(a.ts).toLocaleTimeString('en-IN', { hour12: false })}</span>
          <span style={{ minWidth: 70, color: severityColor }}>{a.severity}</span>
          <span style={{ minWidth: 130 }}>{a.kind}</span>
          <span style={{ opacity: 0.85 }}>{a.symbol} — {JSON.stringify(a.detail)}</span>
        </div>
        );
      })}
    </div>
  );
}
