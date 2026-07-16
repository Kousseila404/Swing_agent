// TickerDetailPage — visualisation historique d'un ticker via Lot 6 storage.
//
// Consomme GET /api/history/ticker/{ticker} (champs par défaut = scores TITAN
// principaux + price + sector). Trace 2 charts :
//   1. Composite + 5 piliers en lignes (échelle 0-100)
//   2. Current price overlay séparé (échelle USD)
// Plus un récap des dernières valeurs + breakdown F-Score Piotroski.

import { useMemo, useState } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useSnapshotsList, useTickerHistory } from '../hooks/useApi';
import { fmtNum, fmtPctRaw, fmtPrice } from '../utils/format';
import ApiErrorBanner from './common/ApiErrorBanner';

const PILLAR_FIELDS = [
  { id: 'titan_composite_score', label: 'TITAN', color: '#fbbf24', stroke: 3 },
  { id: 'quality_score',         label: 'Quality',   color: '#60a5fa' },
  { id: 'value_score',           label: 'Value',     color: '#34d399' },
  { id: 'risk_score',            label: 'Risk',      color: '#f87171' },
  { id: 'sentiment_score',       label: 'Sentiment', color: '#c084fc' },
  { id: 'momentum_score',        label: 'Momentum',  color: '#22d3ee' },
  { id: 'piotroski_score',       label: 'Piotroski', color: '#a78bfa' },
];

const FIELDS_QS = [
  ...PILLAR_FIELDS.map(f => f.id),
  'titan_composite_raw', 'data_quality',
  'current_price', 'momentum_return_pct', 'sector',
  'f_score', 'f_score_max',
].join(',');

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: 'rgba(13, 13, 26, 0.95)',
      border: '1px solid var(--border)',
      padding: '8px 12px', borderRadius: 6, fontSize: '0.78rem',
    }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
      {payload.map(p => (
        <div key={p.dataKey} style={{ color: p.color, display: 'flex',
                                       justifyContent: 'space-between', gap: 12 }}>
          <span>{p.name}</span>
          <span style={{ fontFamily: 'monospace' }}>
            {p.value !== null && p.value !== undefined ? fmtNum(p.value, 2) : '—'}
          </span>
        </div>
      ))}
    </div>
  );
}

function FScoreBadge({ history }) {
  const last = history?.[history.length - 1];
  if (!last) return null;
  const score = last.f_score;
  const maxScore = last.f_score_max;
  if (score === null || score === undefined) {
    return (
      <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>
        F-Score : data manquante
      </span>
    );
  }
  const ratio = maxScore > 0 ? score / maxScore : 0;
  const tone = ratio >= 0.75 ? '#4ade80' : ratio >= 0.5 ? '#fbbf24' : '#f87171';
  return (
    <span style={{
      background: `${tone}25`, color: tone, padding: '4px 10px',
      borderRadius: 6, fontSize: '0.78rem', fontWeight: 600,
    }}>
      F-Score {score}/{maxScore} (Piotroski absolu)
    </span>
  );
}

export default function TickerDetailPage() {
  const [tickerInput, setTickerInput] = useState('NVDA');
  const [activeTicker, setActiveTicker] = useState('NVDA');

  const snapshotsQ = useSnapshotsList();
  const histQ = useTickerHistory(activeTicker, { fields: FIELDS_QS });

  const data = useMemo(() => {
    const hist = histQ.data?.history || [];
    return hist.map(r => {
      // Recharts attend des numeric, pas null pour rien tracer.
      // Garder les nulls comme nulls — Recharts gère via connectNulls par défaut.
      const out = { date: r.date };
      for (const f of [...PILLAR_FIELDS.map(p => p.id),
                       'titan_composite_raw', 'current_price',
                       'data_quality', 'momentum_return_pct',
                       'f_score', 'f_score_max']) {
        out[f] = r[f] ?? null;
      }
      return out;
    });
  }, [histQ.data]);

  const last = data[data.length - 1];
  const first = data[0];
  const priceChangePct = (last && first
                          && Number.isFinite(last.current_price)
                          && Number.isFinite(first.current_price)
                          && first.current_price > 0)
    ? ((last.current_price / first.current_price - 1) * 100)
    : null;

  const handleSubmit = (e) => {
    e.preventDefault();
    const t = tickerInput.trim().toUpperCase();
    if (t) setActiveTicker(t);
  };

  const noData = !histQ.isLoading && !histQ.isError
                  && (histQ.data?.n_points ?? 0) === 0;

  const totalSnapshots = (snapshotsQ.data?.history_dates?.length ?? 0)
                        + (snapshotsQ.data?.legacy_dates?.length ?? 0);

  return (
    <div className="page-content">
      {/* ── Search bar ── */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <form onSubmit={handleSubmit}
              style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <h2 style={{ margin: 0, fontSize: '1.05rem' }}>📈 Score history par ticker</h2>
          <input
            type="text"
            value={tickerInput}
            onChange={e => setTickerInput(e.target.value)}
            placeholder="AAPL, NVDA, …"
            aria-label="Ticker à analyser"
            style={{
              padding: '6px 12px', borderRadius: 6,
              background: 'rgba(255,255,255,0.04)',
              border: '1px solid var(--border)', color: 'var(--text)',
              fontSize: '0.9rem', textTransform: 'uppercase',
              fontFamily: 'monospace', width: 120,
            }}
          />
          <button className="action-btn" type="submit"
                  aria-label="Charger l'historique du ticker">
            Charger
          </button>
          <span style={{ marginLeft: 'auto', fontSize: '0.78rem',
                          color: 'var(--text-muted)' }}>
            {totalSnapshots > 0
              ? `${totalSnapshots} snapshot(s) historique(s) disponibles`
              : 'Aucun snapshot encore collecté.'}
          </span>
        </form>
      </div>

      {histQ.isError && (
        <ApiErrorBanner msg={histQ.error?.message || 'Erreur chargement historique'}
                        onRetry={() => histQ.refetch()} />
      )}

      {noData && (
        <div className="card" style={{ padding: 24, textAlign: 'center',
                                        color: 'var(--text-muted)' }}>
          Aucun historique pour <strong>{activeTicker}</strong>. Le ticker n'a pas encore
          été snapshoté ou n'existe pas dans l'univers.
        </div>
      )}

      {/* ── Récap dernier point ── */}
      {last && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'baseline',
                         justifyContent: 'space-between', marginBottom: 12 }}>
            <h3 style={{ margin: 0, fontSize: '1.1rem' }}>
              {activeTicker}
              <span style={{ marginLeft: 12, fontSize: '0.78rem',
                              color: 'var(--text-muted)', fontWeight: 400 }}>
                {(histQ.data?.history?.[histQ.data.history.length - 1] || {}).sector}
                {' · '}
                Dernier snapshot {last.date}
              </span>
            </h3>
            <FScoreBadge history={data} />
          </div>

          <div style={{ display: 'grid',
                        gridTemplateColumns: 'repeat(auto-fit, minmax(80px, 1fr))', gap: 12,
                        fontSize: '0.85rem' }}>
            {PILLAR_FIELDS.map(f => (
              <div key={f.id}>
                <div style={{ fontSize: '0.7rem',
                              color: 'var(--text-muted)',
                              textTransform: 'uppercase' }}>{f.label}</div>
                <div style={{ fontWeight: 600, color: f.color,
                              fontFamily: 'monospace' }}>
                  {fmtNum(last[f.id], 1)}
                </div>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 12, padding: '8px 10px',
                        background: 'rgba(255,255,255,0.02)',
                        borderRadius: 6, fontSize: '0.78rem',
                        color: 'var(--text-muted)' }}>
            Prix : <strong style={{ color: 'var(--text)' }}>
              ${fmtPrice(last.current_price)}
            </strong>
            {priceChangePct !== null && data.length > 1 && (
              <>
                {' · variation depuis '}<strong>{first.date}</strong>{' : '}
                <strong style={{ color: priceChangePct >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                  {priceChangePct >= 0 ? '+' : ''}{fmtNum(priceChangePct, 2)}%
                </strong>
              </>
            )}
            {' · momentum 6M : '}
            <strong>{fmtPctRaw(last.momentum_return_pct, 1)}</strong>
            {' · DQ : '}<strong>{fmtNum(last.data_quality, 2)}</strong>
          </div>
        </div>
      )}

      {/* ── Chart scores 0-100 ── */}
      {data.length > 1 && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <h3 style={{ margin: '0 0 12px 0', fontSize: '1rem' }}>
            Évolution des scores TITAN
          </h3>
          <ResponsiveContainer width="100%" height={320}>
            <LineChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="date" tick={{ fontSize: 11, fill: 'var(--text-muted)' }} />
              <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: 'var(--text-muted)' }} />
              <Tooltip content={<ChartTooltip />} />
              {PILLAR_FIELDS.map(f => (
                <Line
                  key={f.id}
                  type="monotone"
                  dataKey={f.id}
                  name={f.label}
                  stroke={f.color}
                  strokeWidth={f.stroke || 1.5}
                  dot={false}
                  connectNulls
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12,
                         marginTop: 8, fontSize: '0.78rem',
                         color: 'var(--text-muted)' }}>
            {PILLAR_FIELDS.map(f => (
              <span key={f.id} style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                <span style={{ display: 'inline-block', width: 10, height: 2,
                                background: f.color }} />
                {f.label}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* ── Chart prix ── */}
      {data.length > 1 && data.some(r => Number.isFinite(r.current_price)) && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <h3 style={{ margin: '0 0 12px 0', fontSize: '1rem' }}>
            Évolution du prix (snapshots)
          </h3>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="date" tick={{ fontSize: 11, fill: 'var(--text-muted)' }} />
              <YAxis tick={{ fontSize: 11, fill: 'var(--text-muted)' }}
                     tickFormatter={v => `$${v}`} />
              <Tooltip content={<ChartTooltip />} />
              <Line type="monotone" dataKey="current_price" name="Prix"
                    stroke="#fbbf24" strokeWidth={2}
                    dot={false} connectNulls isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
          <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)',
                      margin: '8px 0 0 0' }}>
            Prix instantanés au moment de chaque snapshot — ne pas confondre avec
            une chart OHLC continue.
          </p>
        </div>
      )}
    </div>
  );
}
