// Sparkline — mini-chart SVG inline, zéro dépendance.
//
// Utilisable dans une cellule de table (60×20 px par défaut). Trace une
// polyline + un point de fin. Couleur auto basée sur le delta (vert si fin
// > début, rouge sinon) — surchargeable via la prop `color`.
//
// Props :
//   data       : array<number> ou array<{value: number}> — accepte les nulls
//   width/height : px (défaut 60×20)
//   color      : optionnel — sinon auto vert/rouge selon delta
//   strokeWidth: défaut 1.25
//   showDot    : montre un point sur la dernière valeur (défaut true)

import { useMemo } from 'react';

export default function Sparkline({
  data,
  width = 60,
  height = 20,
  color,
  strokeWidth = 1.25,
  showDot = true,
}) {
  const series = useMemo(() => {
    if (!Array.isArray(data) || data.length < 2) return [];
    return data
      .map(d => (typeof d === 'number' ? d : (d?.value ?? d?.y ?? null)))
      .filter(v => v != null && Number.isFinite(v));
  }, [data]);

  if (series.length < 2) {
    return (
      <span style={{
        display: 'inline-block', width, height,
        color: 'var(--text-muted)', fontSize: '0.65rem',
        fontFamily: 'monospace', opacity: 0.5,
        textAlign: 'center', lineHeight: `${height}px`,
      }}>—</span>
    );
  }

  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = max - min || 1;
  const stepX = width / (series.length - 1);
  const pad = strokeWidth + 0.5;
  const innerH = height - 2 * pad;

  const points = series.map((v, i) => {
    const x = i * stepX;
    const y = pad + innerH * (1 - (v - min) / range);
    return [x, y];
  });

  const path = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const last = points[points.length - 1];

  const delta = series[series.length - 1] - series[0];
  const autoColor = delta >= 0 ? 'var(--success)' : 'var(--danger)';
  const stroke = color || autoColor;

  return (
    <svg width={width} height={height} style={{ display: 'block', overflow: 'visible' }}
         aria-hidden="true">
      <polyline
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
        points={path}
      />
      {showDot && (
        <circle cx={last[0]} cy={last[1]} r={1.6} fill={stroke} />
      )}
    </svg>
  );
}
