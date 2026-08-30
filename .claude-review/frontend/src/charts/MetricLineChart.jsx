import React from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts';

export default function MetricLineChart({ points, color = '#5b8def', title, baseline }) {
  const data = (points || []).map((p) => ({ t: fmt(p.t), v: p.v }));
  return (
    <div style={{ padding: 12, border: '1px solid #1c2230', borderRadius: 8, background: '#0f131b' }}>
      <div style={{ fontSize: 12, letterSpacing: 0.5, textTransform: 'uppercase', opacity: 0.7, marginBottom: 8 }}>{title}</div>
      <div style={{ width: '100%', height: 180 }}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
            <XAxis dataKey="t" tick={{ fill: '#8891a4', fontSize: 10 }} minTickGap={40} />
            <YAxis tick={{ fill: '#8891a4', fontSize: 10 }} width={54} domain={['auto', 'auto']} />
            <Tooltip contentStyle={{ background: '#141a24', border: '1px solid #2a3345', fontSize: 12 }} />
            {baseline !== undefined && <ReferenceLine y={baseline} stroke="#4a5468" strokeDasharray="3 3" />}
            <Line type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function fmt(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString('en-IN', { hour12: false });
}
