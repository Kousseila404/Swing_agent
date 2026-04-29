// TickerPriceChart — mini-chart prix via universe_history pour le modal.
//
// Consomme /api/history/ticker/{ticker}?fields=current_price.
// Affiche un area-chart simple avec gradient + tooltip et delta period en
// header. Réutilise Recharts (déjà bundlé pour Performance/Portfolio).

import { useMemo } from 'react';
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useTickerHistory } from '../../hooks/useApi';
import { fmtNum, fmtSignedPct } from '../../utils/format';

function PriceTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const v = payload[0].value;
  return (
    <div style={{
      background: 'rgba(13, 13, 26, 0.95)',
      border: '1px solid var(--border)',
      padding: '6px 10px', borderRadius: 6,
      fontSize: '0.74rem', fontFamily: 'monospace',
    }}>
      <div style={{ fontWeight: 600, marginBottom: 2 }}>{label}</div>
      <div style={{ color: 'var(--accent-primary)' }}>
        ${v != null ? fmtNum(v, 2) : '—'}
      </div>
    </div>
  );
}

export default function TickerPriceChart({ ticker, height = 140 }) {
  const histQ = useTickerHistory(ticker, { fields: 'current_price' });

  const series = useMemo(() => {
    const rows = histQ.data?.history || [];
    return rows
      .map(r => ({ date: String(r.date).slice(0, 10), price: r.current_price }))
      .filter(r => Number.isFinite(r.price));
  }, [histQ.data]);

  if (histQ.isLoading) {
    return (
      <div style={{
        height, display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: 'var(--text-muted)', fontSize: '0.78rem',
      }}>
        Chargement chart…
      </div>
    );
  }

  if (histQ.isError || series.length < 2) {
    return (
      <div style={{
        height, display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: 'var(--text-muted)', fontSize: '0.78rem',
        background: 'var(--bg-tertiary)', borderRadius: 6,
        border: '1px dashed var(--border)',
      }}>
        📉 Pas d'historique prix disponible (universe_history vide)
      </div>
    );
  }

  const first = series[0].price;
  const last  = series[series.length - 1].price;
  const delta = ((last - first) / first) * 100;
  const tone  = delta >= 0 ? 'var(--success)' : 'var(--danger)';

  return (
    <div>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
        marginBottom: 4, fontSize: '0.78rem',
      }}>
        <span style={{ color: 'var(--text-muted)' }}>
          {series.length} points · {series[0].date} → {series[series.length - 1].date}
        </span>
        <span style={{ fontFamily: 'monospace', fontWeight: 700 }}>
          ${fmtNum(last, 2)}{' '}
          <span style={{ color: tone }}>{fmtSignedPct(delta / 100)}</span>
        </span>
      </div>
      <div style={{ width: '100%', height }}>
        <ResponsiveContainer>
          <AreaChart data={series} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="tickerPriceGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%"   stopColor={tone} stopOpacity={0.35} />
                <stop offset="100%" stopColor={tone} stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="date" hide />
            <YAxis domain={['auto', 'auto']} hide />
            <Tooltip content={<PriceTooltip />} cursor={{ stroke: 'var(--border)' }} />
            <Area
              type="monotone"
              dataKey="price"
              stroke={tone}
              strokeWidth={1.5}
              fill="url(#tickerPriceGrad)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
