import React from 'react';

export default function SymbolPicker({ symbols, value, onChange, status }) {
  const dot = status === 'live' ? '#3ddc84' : status === 'connecting' ? '#f5a623' : '#e5484d';
  return (
    <div style={{ display: 'flex', gap: 16, alignItems: 'center', padding: '10px 16px', borderBottom: '1px solid #1c2230' }}>
      <strong style={{ fontSize: 18 }}>Market Microstructure Analyzer</strong>
      <label style={{ marginLeft: 24, opacity: 0.7 }}>Symbol</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ background: '#141a24', color: '#e6e9ef', border: '1px solid #2a3345', padding: '4px 8px', borderRadius: 4 }}
      >
        {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
      <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, opacity: 0.85 }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: dot }} />
        {status}
      </span>
    </div>
  );
}
