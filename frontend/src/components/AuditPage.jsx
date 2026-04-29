// AuditPage — diagnostic complet "TITAN bat-il le marché ?"
//
// Architecture :
//   1. <VerdictHero>          — verdict global + 4 chiffres clés (alpha, ...)
//   2. <DiagnosticChecklist>  — 11 checks groupés par catégorie (Perf/Data/Modèle)
//   3. <BacktestPanel>        — equity curve TITAN vs SPY + détail périodes
//   4. <DelistedSection>      — détail registry survivorship
//   5. <WfoWeightsSection>    — détail prod vs OOS poids piliers
//   6. <WfoHistorySection>    — détail évolution IC dans le temps
//
// Le payload unique vient de /api/audit/full (cache backend 24h).

import { useMemo } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { useAuditFull, useRefreshAuditFull } from '../hooks/useApi';
import ApiErrorBanner from './common/ApiErrorBanner';

const PILLAR_LABELS = {
  quality_score:    'Quality',
  value_score:      'Value',
  risk_score:       'Risk',
  sentiment_score:  'Sentiment',
  momentum_score:   'Momentum',
  piotroski_score:  'Piotroski',
  growth_score:     'Growth',
};

// Couleurs status (alignées avec le backend `color` field).
const STATUS_COLORS = {
  ok:   { fg: '#4ade80', bg: 'rgba(74,222,128,0.10)',  bd: '#4ade80' },
  warn: { fg: '#fbbf24', bg: 'rgba(251,191,36,0.10)',  bd: '#fbbf24' },
  fail: { fg: '#f87171', bg: 'rgba(248,113,113,0.10)', bd: '#f87171' },
  na:   { fg: '#9ca3af', bg: 'rgba(156,163,175,0.10)', bd: '#9ca3af' },
};
const STATUS_ICONS = { ok: '✅', warn: '⚠️', fail: '❌', na: '➖' };

const VERDICT_THEME = {
  BAT_LE_MARCHÉ:         { fg: '#4ade80', bg: 'rgba(74,222,128,0.12)',  icon: '🏆', sub: 'Tu peux croire ton backtest.' },
  INCERTAIN:              { fg: '#fbbf24', bg: 'rgba(251,191,36,0.12)',  icon: '⚠️',  sub: 'Verdict pas tranché — voir les checks fail/warn ci-dessous.' },
  NE_BAT_PAS:             { fg: '#f87171', bg: 'rgba(248,113,113,0.12)', icon: '🔴', sub: 'TITAN sous-performe le benchmark — investiguer avant de risquer du capital.' },
  DONNÉES_INSUFFISANTES:  { fg: '#9ca3af', bg: 'rgba(156,163,175,0.10)', icon: '⏳', sub: 'Le backtest n\'a pas pu tourner. L\'historique se densifie chaque jour.' },
};

const CATEGORY_LABELS = {
  performance: 'Performance — TITAN bat-il SPY ?',
  data:        'Données — Inputs fiables ?',
  model:       'Modèle — Signal et poids calibrés ?',
};
const CATEGORY_ICONS = { performance: '📊', data: '🗄️', model: '🧠' };

// ─────────────────────────────────────────────────────────────────
// Helpers UI
// ─────────────────────────────────────────────────────────────────

function Card({ title, subtitle, children, style }) {
  return (
    <div className="card"
          style={{ padding: 16, marginBottom: 16, ...style }}>
      {title && (
        <div style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0, fontSize: '0.95rem' }}>{title}</h3>
          {subtitle && (
            <div style={{ color: 'var(--text-muted)', fontSize: '0.78rem',
                          marginTop: 2 }}>{subtitle}</div>
          )}
        </div>
      )}
      {children}
    </div>
  );
}

function fmtPct(v, digits = 2) {
  if (v == null || !Number.isFinite(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${(v * 100).toFixed(digits)}%`;
}

function fmtNum(v, digits = 2) {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toFixed(digits);
}

// ─────────────────────────────────────────────────────────────────
// Section 1 — Verdict global + chiffres clés
// ─────────────────────────────────────────────────────────────────

function VerdictHero({ verdict, backtest }) {
  const theme = VERDICT_THEME[verdict?.verdict] || VERDICT_THEME.INCERTAIN;
  const bt = backtest || {};
  const isOk = bt._status === 'ok';

  return (
    <div style={{ marginBottom: 16, padding: 18, borderRadius: 10,
                   background: theme.bg,
                   borderLeft: `4px solid ${theme.fg}`,
                   border: `1px solid ${theme.fg}33` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14,
                     marginBottom: 8 }}>
        <div style={{ fontSize: '2.4rem' }}>{theme.icon}</div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: '0.78rem', textTransform: 'uppercase',
                          color: 'var(--text-muted)', letterSpacing: 1.5,
                          marginBottom: 2 }}>
            TITAN bat-il le marché ?
          </div>
          <div style={{ fontSize: '1.6rem', fontWeight: 700,
                          color: theme.fg, lineHeight: 1.1 }}>
            {verdict?.label || '—'}
          </div>
        </div>
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)',
                       textAlign: 'right', lineHeight: 1.4,
                       padding: '4px 8px', borderRadius: 4,
                       background: 'rgba(255,255,255,0.05)',
                       border: '1px solid rgba(255,255,255,0.08)' }}>
          📝 Simulation rétro<br />
          sur scores historiques<br />
          <span style={{ opacity: 0.7 }}>≠ tes positions réelles</span>
        </div>
      </div>

      <div style={{ fontSize: '0.88rem', color: 'var(--text)',
                     marginBottom: 12, lineHeight: 1.5 }}>
        {verdict?.summary || ''}
      </div>

      <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)',
                     marginBottom: 14, fontStyle: 'italic' }}>
        {theme.sub}
      </div>

      {isOk && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <KpiTile label="Alpha vs SPY"
                    value={fmtPct(bt.alpha)}
                    color={bt.alpha > 0 ? '#4ade80' : '#f87171'} />
          <KpiTile label="TITAN return"
                    value={fmtPct(bt.total_return)} />
          <KpiTile label="SPY return"
                    value={fmtPct(bt.benchmark_return)} />
          <KpiTile label="Sharpe annualisé"
                    value={fmtNum(bt.sharpe_annual)}
                    hint={`hit ${(bt.hit_rate * 100).toFixed(0)}%`} />
          <KpiTile label="Drawdown max"
                    value={`-${(bt.max_drawdown * 100).toFixed(2)}%`} />
          <KpiTile label="Périodes"
                    value={bt.n_periods}
                    hint={bt.diagnostics?.date_range
                      ? `${bt.diagnostics.date_range.start} → ${bt.diagnostics.date_range.end}`
                      : null} />
        </div>
      )}
    </div>
  );
}

function KpiTile({ label, value, color, hint }) {
  return (
    <div style={{ minWidth: 130, padding: '10px 14px', borderRadius: 8,
                   background: 'rgba(255,255,255,0.04)' }}>
      <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)',
                     textTransform: 'uppercase', letterSpacing: 1 }}>
        {label}
      </div>
      <div style={{ fontSize: '1.35rem', fontWeight: 700,
                     color: color || 'var(--text)', marginTop: 2 }}>
        {value}
      </div>
      {hint && (
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
          {hint}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Section 2 — Diagnostic checklist
// ─────────────────────────────────────────────────────────────────

function DiagnosticChecklist({ checks }) {
  // Group by category
  const grouped = useMemo(() => {
    const acc = { performance: [], data: [], model: [] };
    (checks || []).forEach((c) => {
      if (acc[c.category]) acc[c.category].push(c);
    });
    return acc;
  }, [checks]);

  // Compteurs globaux
  const counts = useMemo(() => {
    const out = { ok: 0, warn: 0, fail: 0, na: 0 };
    (checks || []).forEach((c) => { out[c.status] = (out[c.status] || 0) + 1; });
    return out;
  }, [checks]);

  return (
    <Card title="🩺 Checklist diagnostic"
          subtitle={`${counts.ok} OK · ${counts.warn} warn · ${counts.fail} fail · ${counts.na} n/a — clique sur un check pour voir l'explication`}>
      {Object.entries(grouped).map(([cat, items]) => {
        if (!items.length) return null;
        return (
          <div key={cat} style={{ marginBottom: 14 }}>
            <div style={{ fontSize: '0.82rem', fontWeight: 600,
                           color: 'var(--text)', marginBottom: 6 }}>
              {CATEGORY_ICONS[cat]} {CATEGORY_LABELS[cat] || cat}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {items.map((c) => <CheckRow key={c.name} check={c} />)}
            </div>
          </div>
        );
      })}
    </Card>
  );
}

function CheckRow({ check }) {
  const colors = STATUS_COLORS[check.status] || STATUS_COLORS.na;
  const icon = STATUS_ICONS[check.status] || '?';
  const hasDetail = !!check.hint;
  const valueStr = check.value === null || check.value === undefined
    ? '—'
    : (typeof check.value === 'number'
        ? (Math.abs(check.value) < 1
            ? check.value.toFixed(4)
            : check.value.toFixed(2))
        : String(check.value));
  const thStr = check.threshold === null || check.threshold === undefined
    ? null
    : (typeof check.threshold === 'number'
        ? (Math.abs(check.threshold) < 1
            ? check.threshold.toFixed(2)
            : check.threshold.toFixed(0))
        : String(check.threshold));

  return (
    <details style={{ padding: '8px 10px', borderRadius: 6,
                       background: colors.bg,
                       borderLeft: `3px solid ${colors.bd}` }}>
      <summary style={{ cursor: hasDetail ? 'pointer' : 'default',
                         display: 'grid',
                         gridTemplateColumns: '24px 1fr auto auto',
                         gap: 10, alignItems: 'center',
                         listStyle: hasDetail ? undefined : 'none' }}>
        <span style={{ fontSize: '1rem' }}>{icon}</span>
        <span style={{ fontSize: '0.85rem', fontWeight: 600,
                         color: 'var(--text)' }}>
          {check.label}
        </span>
        <span style={{ fontFamily: 'monospace', fontSize: '0.82rem',
                         color: colors.fg, fontWeight: 600 }}>
          {valueStr}
          {thStr && (
            <span style={{ color: 'var(--text-muted)', fontWeight: 400,
                            marginLeft: 6, fontSize: '0.75rem' }}>
              / seuil {thStr}
            </span>
          )}
        </span>
        <span style={{ fontSize: '0.78rem', color: colors.fg,
                         textTransform: 'uppercase', letterSpacing: 1,
                         padding: '1px 6px', borderRadius: 3,
                         background: 'rgba(0,0,0,0.2)' }}>
          {check.status}
        </span>
      </summary>
      <div style={{ marginTop: 8, padding: '6px 0 2px 34px',
                     fontSize: '0.82rem', color: 'var(--text)',
                     lineHeight: 1.55 }}>
        {check.message}
      </div>
      {check.hint && (
        <div style={{ marginTop: 4, padding: '0 0 0 34px',
                       fontSize: '0.76rem', color: 'var(--text-muted)',
                       fontStyle: 'italic', lineHeight: 1.5 }}>
          ℹ️ {check.hint}
        </div>
      )}
    </details>
  );
}

// ─────────────────────────────────────────────────────────────────
// Section 3 — Backtest detail (equity curve TITAN vs SPY)
// ─────────────────────────────────────────────────────────────────

function BacktestPanel({ backtest }) {
  if (!backtest || backtest._status !== 'ok') {
    return (
      <Card title="📊 Détail backtest TITAN vs SPY"
            subtitle="Cache 24h · top-N 20 · slippage 5bps · point-in-time">
        <div style={{ padding: 16, color: 'var(--text-muted)',
                       fontSize: '0.85rem' }}>
          {backtest?._error || 'Backtest non disponible.'}
        </div>
      </Card>
    );
  }

  // Vraies courbes TITAN + SPY (le backend fetch les closes journaliers
  // SPY normalisés à 1.0 au 1er point — `benchmark_curve`). Si le fetch a
  // échoué, on retombe sur la courbe TITAN seule.
  const equity = backtest.equity_curve || [];
  const spyByDate = new Map(
    (backtest.benchmark_curve || []).map((p) => [p.date, p.spy]),
  );
  const hasRealSpy = spyByDate.size > 0;
  const chartData = equity.map((row) => {
    const [date, eq] = row;
    const spy = spyByDate.get(date);
    return { date, titan: eq, spy: spy ?? null };
  });

  const diag = backtest.diagnostics || {};

  return (
    <Card title="📊 Détail backtest TITAN vs SPY"
          subtitle={`${backtest.n_periods} période(s) · ${diag.date_range?.start ?? '?'} → ${diag.date_range?.end ?? '?'} · top-${backtest.top_n} ${diag.weighting || 'equal'} · slippage ${diag.slippage_bps?.toFixed(1) ?? '?'}bps · turnover ${(diag.avg_turnover * 100).toFixed(1)}%`}>
      <div style={{ height: 240 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
            <CartesianGrid stroke="rgba(255,255,255,0.06)" />
            <XAxis dataKey="date" stroke="var(--text-muted)"
                    tick={{ fontSize: 11 }} interval={0}
                    minTickGap={0} />
            <YAxis stroke="var(--text-muted)" tick={{ fontSize: 11 }}
                    tickFormatter={(v) => `${((v - 1) * 100).toFixed(1)}%`} />
            <Tooltip contentStyle={{ background: '#0d0d1a',
                                       border: '1px solid rgba(255,255,255,0.1)' }}
                      formatter={(v) => `${((v - 1) * 100).toFixed(2)}%`} />
            <Legend wrapperStyle={{ fontSize: '0.8rem' }} />
            <ReferenceLine y={1} stroke="rgba(255,255,255,0.2)" />
            <Line type="monotone" dataKey="titan" stroke="#60a5fa"
                  strokeWidth={2} dot={{ r: 2 }} name="TITAN" />
            {hasRealSpy && (
              <Line type="monotone" dataKey="spy" stroke="#fbbf24"
                    strokeWidth={2} dot={false}
                    connectNulls name="SPY (close réel)" />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>

      {(backtest.periods || []).length > 0 && (
        <div style={{ marginTop: 12, overflowX: 'auto' }}>
          <table style={{ width: '100%', fontSize: '0.78rem',
                          borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
                <th style={{ textAlign: 'left',  padding: '4px 6px' }}
                    title="Date du snapshot utilisé pour ranker le top-N">Snapshot</th>
                <th style={{ textAlign: 'left',  padding: '4px 6px' }}
                    title="Date du close suivant utilisé pour mesurer le forward return">Sortie</th>
                <th style={{ textAlign: 'right', padding: '4px 6px' }}>Net</th>
                <th style={{ textAlign: 'right', padding: '4px 6px' }}>Brut</th>
                <th style={{ textAlign: 'right', padding: '4px 6px' }}>Coût</th>
                <th style={{ textAlign: 'right', padding: '4px 6px' }}>Turn.</th>
                <th style={{ textAlign: 'left',  padding: '4px 6px' }}>Top 5</th>
              </tr>
            </thead>
            <tbody>
              {backtest.periods.map((p, i) => {
                const ret = p.portfolio_return ?? 0;
                const color = ret >= 0 ? '#4ade80' : '#f87171';
                return (
                  <tr key={i}
                      style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                    <td style={{ padding: '4px 6px',
                                  fontFamily: 'monospace' }}>{p.signal_date}</td>
                    <td style={{ padding: '4px 6px',
                                  fontFamily: 'monospace' }}>{p.next_date}</td>
                    <td style={{ padding: '4px 6px', textAlign: 'right',
                                  fontFamily: 'monospace', color, fontWeight: 600 }}>
                      {fmtPct(ret)}
                    </td>
                    <td style={{ padding: '4px 6px', textAlign: 'right',
                                  fontFamily: 'monospace',
                                  color: 'var(--text-muted)' }}>
                      {fmtPct(p.portfolio_return_gross)}
                    </td>
                    <td style={{ padding: '4px 6px', textAlign: 'right',
                                  fontFamily: 'monospace',
                                  color: 'var(--text-muted)' }}>
                      {fmtPct(p.cost_pct, 3)}
                    </td>
                    <td style={{ padding: '4px 6px', textAlign: 'right',
                                  fontFamily: 'monospace',
                                  color: 'var(--text-muted)' }}>
                      {(p.turnover * 100).toFixed(0)}%
                    </td>
                    <td style={{ padding: '4px 6px',
                                  fontFamily: 'monospace',
                                  color: 'var(--text-muted)',
                                  fontSize: '0.74rem' }}>
                      {(p.top_tickers || []).slice(0, 5).join(', ')}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────
// Section 4 — Delisted registry table
// ─────────────────────────────────────────────────────────────────

function DelistedSection({ data }) {
  if (!data) return null;
  const rows = data.delisted || [];

  return (
    <Card title="📉 Registry delisted (Survivorship)"
          subtitle="Tickers retirés de l'univers · consommés par le filtre point-in-time du backtest">
      {rows.length === 0 ? (
        <div style={{ padding: 12, color: 'var(--text-muted)',
                       fontSize: '0.83rem' }}>
          Aucun ticker delisted enregistré pour le moment. Le registre s'enrichit
          automatiquement à chaque rebuild d'univers.
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', fontSize: '0.85rem',
                          borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
                <th style={{ textAlign: 'left',  padding: '6px 8px' }}>Ticker</th>
                <th style={{ textAlign: 'left',  padding: '6px 8px' }}>Secteur</th>
                <th style={{ textAlign: 'left',  padding: '6px 8px' }}>First seen</th>
                <th style={{ textAlign: 'left',  padding: '6px 8px' }}>Removed at</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.ticker}
                    style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                  <td style={{ padding: '6px 8px', fontFamily: 'monospace',
                                fontWeight: 600 }}>{r.ticker}</td>
                  <td style={{ padding: '6px 8px',
                                color: 'var(--text-muted)' }}>
                    {r.sector || '—'}
                  </td>
                  <td style={{ padding: '6px 8px',
                                color: 'var(--text-muted)' }}>
                    {r.first_seen || '—'}
                  </td>
                  <td style={{ padding: '6px 8px', fontFamily: 'monospace' }}>
                    {r.removed_at || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────
// Section 5 — WFO weights : prod vs OOS
// ─────────────────────────────────────────────────────────────────

function WfoWeightsSection({ data }) {
  const chartData = useMemo(() => {
    if (!data) return [];
    const prodW   = data.prod_weights   || {};
    const oosW    = data.avg_weights    || {};
    const deltas  = data.weight_deltas  || {};
    const all = new Set([...Object.keys(prodW), ...Object.keys(oosW)]);
    return Array.from(all).map((p) => ({
      pillar: PILLAR_LABELS[p] || p,
      prod:   typeof prodW[p]  === 'number' ? prodW[p]  : 0,
      oos:    typeof oosW[p]   === 'number' ? oosW[p]   : 0,
      delta:  typeof deltas[p] === 'number' ? deltas[p] : 0,
    })).sort((a, b) => b.prod - a.prod);
  }, [data]);

  if (!data) {
    return (
      <Card title="⚖️ WFO weights — prod vs OOS"
            subtitle="Comparaison poids hardcodés vs poids optimaux walk-forward">
        <div style={{ padding: 12, color: 'var(--text-muted)',
                       fontSize: '0.85rem', lineHeight: 1.6 }}>
          <strong>Aucune calibration WFO n'a encore été lancée.</strong>
          {' '}La calibration roule des fenêtres
          (<code>train_days=60</code>, <code>test_days=20</code>) et trouve
          les poids piliers qui maximisent l'IC composite OOS.
          <div style={{ marginTop: 6 }}>
            Lancer manuellement :{' '}
            <code style={{ background: 'rgba(255,255,255,0.05)',
                            padding: '1px 6px', borderRadius: 4 }}>
              python -m modules.wfo_calibration --train-days 60 --test-days 20
            </code>
          </div>
        </div>
      </Card>
    );
  }

  return (
    <Card title="⚖️ WFO weights — prod vs OOS"
          subtitle={`${data.n_folds || 0} folds · IC test moyen : ${typeof data.avg_ic_test === 'number' ? data.avg_ic_test.toFixed(4) : '—'} · Δ > 0.05 = drift à investiguer`}>
      <div style={{ height: 260 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
            <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
            <XAxis dataKey="pillar" stroke="var(--text-muted)"
                    tick={{ fontSize: 11 }} />
            <YAxis stroke="var(--text-muted)" tick={{ fontSize: 11 }}
                    domain={[0, 0.4]} tickFormatter={(v) => v.toFixed(2)} />
            <Tooltip contentStyle={{ background: '#0d0d1a',
                                       border: '1px solid rgba(255,255,255,0.1)' }}
                      formatter={(v) => v.toFixed(3)} />
            <Legend wrapperStyle={{ fontSize: '0.8rem' }} />
            <Bar dataKey="prod" fill="#60a5fa" name="Prod (hardcodé)" />
            <Bar dataKey="oos"  fill="#fbbf24" name="OOS (WFO)" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div style={{ marginTop: 14, overflowX: 'auto' }}>
        <table style={{ width: '100%', fontSize: '0.82rem', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
              <th style={{ textAlign: 'left',  padding: '6px 8px' }}>Pilier</th>
              <th style={{ textAlign: 'right', padding: '6px 8px' }}>Prod</th>
              <th style={{ textAlign: 'right', padding: '6px 8px' }}>OOS</th>
              <th style={{ textAlign: 'right', padding: '6px 8px' }}>Δ</th>
            </tr>
          </thead>
          <tbody>
            {chartData.map((r) => {
              const dColor = Math.abs(r.delta) < 0.02
                ? 'var(--text-muted)'
                : r.delta > 0 ? 'var(--success)' : 'var(--danger)';
              return (
                <tr key={r.pillar}
                    style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                  <td style={{ padding: '6px 8px', fontWeight: 600 }}>{r.pillar}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right',
                                fontFamily: 'monospace' }}>
                    {r.prod.toFixed(3)}
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right',
                                fontFamily: 'monospace' }}>
                    {r.oos.toFixed(3)}
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right',
                                fontFamily: 'monospace', color: dColor,
                                fontWeight: 600 }}>
                    {r.delta >= 0 ? '+' : ''}{r.delta.toFixed(3)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────
// Section 6 — IC history line chart
// ─────────────────────────────────────────────────────────────────

function WfoHistorySection({ data }) {
  if (!data) return null;
  const threshold = data.threshold_ic ?? 0.02;
  const allEntries = data.history || [data.latest].filter(Boolean);
  const okEntries = allEntries.filter((e) => e?.status === 'ok');

  if (okEntries.length === 0) {
    return (
      <Card title="📈 IC history (santé du signal dans le temps)"
            subtitle={`Zone danger sous ${threshold.toFixed(2)} (= bruit). Cron mensuel step 3b de run_titan.sh.`}>
        <div style={{ padding: 12, color: 'var(--text-muted)',
                       fontSize: '0.85rem' }}>
          <strong>Aucun run WFO réussi enregistré.</strong>{' '}
          Le monitor mensuel append à <code>data/wfo_history.jsonl</code> ;
          tant qu'aucun run n'a tourné, ce graphique reste vide.
        </div>
      </Card>
    );
  }

  const chartData = okEntries.map((e) => ({
    timestamp: e.timestamp ? e.timestamp.slice(0, 10) : '',
    ic: typeof e.avg_ic_test === 'number' ? e.avg_ic_test : 0,
  }));

  return (
    <Card title="📈 IC history (santé du signal dans le temps)"
          subtitle={`${data.n_total ?? okEntries.length} runs · zone danger sous ${threshold.toFixed(2)}`}>
      <div style={{ height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
            <CartesianGrid stroke="rgba(255,255,255,0.06)" />
            <XAxis dataKey="timestamp" stroke="var(--text-muted)"
                    tick={{ fontSize: 11 }} />
            <YAxis stroke="var(--text-muted)" tick={{ fontSize: 11 }}
                    domain={[(dataMin) => Math.min(-0.05, dataMin),
                             (dataMax) => Math.max(0.10, dataMax)]} />
            <Tooltip contentStyle={{ background: '#0d0d1a',
                                       border: '1px solid rgba(255,255,255,0.1)' }} />
            <ReferenceLine y={threshold} stroke="var(--danger)"
                            strokeDasharray="4 4"
                            label={{ value: 'seuil', fill: 'var(--danger)',
                                      fontSize: 10, position: 'right' }} />
            <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" />
            <Line type="monotone" dataKey="ic" stroke="#fbbf24"
                  strokeWidth={2} dot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────
// Page principale
// ─────────────────────────────────────────────────────────────────

export default function AuditPage() {
  const auditQ = useAuditFull();
  const refreshM = useRefreshAuditFull();

  if (auditQ.isError) {
    return <ApiErrorBanner error={auditQ.error}
                            onRetry={() => auditQ.refetch()} />;
  }
  if (auditQ.isLoading || !auditQ.data) {
    return (
      <div style={{ padding: 20, color: 'var(--text-muted)' }}>
        Chargement diagnostic complet… (cache backend 24h, le 1er run peut
        prendre 10-30s)
      </div>
    );
  }

  const a = auditQ.data;
  const cachedAt = a.backtest?._cached_at_iso;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end',
                     alignItems: 'center', gap: 10, marginBottom: 8,
                     fontSize: '0.78rem', color: 'var(--text-muted)' }}>
        {cachedAt && (
          <span>Cache backend : {new Date(cachedAt).toLocaleString()}</span>
        )}
        <button type="button"
                disabled={refreshM.isPending}
                onClick={() => refreshM.mutate()}
                style={{ padding: '4px 10px', fontSize: '0.78rem',
                         borderRadius: 4, border: '1px solid rgba(255,255,255,0.15)',
                         background: 'rgba(255,255,255,0.05)',
                         color: 'var(--text)', cursor: 'pointer' }}>
          {refreshM.isPending ? '⏳ Recompute…' : '🔄 Refresh'}
        </button>
      </div>
      <VerdictHero verdict={a.verdict} backtest={a.backtest} />
      <DiagnosticChecklist checks={a.checks} />
      <BacktestPanel backtest={a.backtest} />
      <DelistedSection data={a.delisted} />
      <WfoWeightsSection data={a.wfo} />
      <WfoHistorySection data={a.ic_history} />
    </div>
  );
}
