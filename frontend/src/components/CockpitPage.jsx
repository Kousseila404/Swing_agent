// CockpitPage — page d'atterrissage (audit 2026-09-17, Lot 6).
//
// Répond à une seule question : « est-ce que ça gagne de l'argent, et
// est-ce que c'est sous contrôle ? »
//   1. Compte réel vs SPY vs panier TITAN théorique (rebasés à 100).
//   2. Chaque position : stop broker présent ? distance ? décision LT ?
//   3. Ce qui attend une décision (propositions qualifiées).
//   4. Santé système : tracker, killswitch, circuit breaker, crons.
//
// Styles : src/styles/cockpit.css (aucun style inline).

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';

import { fetchLtDecision } from '../api/client';
import {
  useMarketStatus,
  usePerformanceBenchmark,
  usePortfolio,
  useProposals,
  useProtection,
  useSystemHealth,
} from '../hooks/useApi';
import { fmtNum, fmtPrice } from '../utils/format';
import { PageSkeleton } from './common/Skeleton';

const SERIES = [
  { key: 'account', label: 'Compte (Alpaca)', color: '#3b82f6' },
  { key: 'spy',     label: 'SPY',             color: '#94a3b8' },
  { key: 'basket',  label: 'Panier TITAN top-20 (théorique)', color: '#8b5cf6' },
];

const LT_TONE = {
  HOLD: 'muted', ADD_ON: 'info', TRIM: 'warn', EXIT_THESIS: 'danger',
  EXIT_VALUATION: 'warn', EXIT_CATASTROPHE: 'danger', NO_DATA: 'muted',
};

const VERDICT_TONE = { STRONG_BUY: 'ok', BUY: 'ok', WATCH: 'warn', SKIP: 'muted' };

function signed(v, digits = 2, suffix = '%') {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v > 0 ? '+' : ''}${fmtNum(v, digits)}${suffix}`;
}

function toneOf(v) {
  if (v == null) return '';
  return v >= 0 ? 'pos' : 'neg';
}

function ageLabel(sec) {
  if (sec == null) return 'jamais';
  if (sec < 90) return `${Math.round(sec)} s`;
  if (sec < 5400) return `${Math.round(sec / 60)} min`;
  if (sec < 172800) return `${Math.round(sec / 3600)} h`;
  return `${Math.round(sec / 86400)} j`;
}

function Kpi({ label, value, sub, tone, valueTone, onClick }) {
  return (
    <div
      className={`kpi${tone ? ` kpi--${tone}` : ''}${onClick ? ' kpi--clickable' : ''}`}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
    >
      <div className="kpi-label">{label}</div>
      <div className={`kpi-value ${valueTone || ''}`}>{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

function Pill({ tone = 'muted', children, title }) {
  return <span className={`pill pill--${tone}`} title={title}>{children}</span>;
}

function Card({ title, icon, action, children, flush, note }) {
  return (
    <section className="cockpit-card">
      <div className="cockpit-card-head">
        <h3><span aria-hidden="true">{icon}</span>{title}</h3>
        {action}
      </div>
      <div className={`cockpit-card-body${flush ? ' cockpit-card-body--flush' : ''}`}>{children}</div>
      {note && <div className="cockpit-card-body cockpit-note">{note}</div>}
    </section>
  );
}

function BenchmarkChart({ bench }) {
  const data = useMemo(() => {
    if (!bench?.dates?.length) return [];
    return bench.dates.map((d, i) => ({
      date: d.slice(5),
      account: bench.series.account?.[i] ?? null,
      spy: bench.series.spy?.[i] ?? null,
      basket: bench.series.basket?.[i] ?? null,
    }));
  }, [bench]);
  if (!data.length) return <div className="cockpit-empty">Séries indisponibles (broker / yfinance / backtest).</div>;
  return (
    <>
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--panel-border)" />
          <XAxis dataKey="date" tick={{ fontSize: 11, fill: 'var(--text-muted)' }} minTickGap={28} />
          <YAxis domain={['auto', 'auto']} tick={{ fontSize: 11, fill: 'var(--text-muted)' }} width={44}
                 tickFormatter={(v) => v.toFixed(0)} />
          <Tooltip
            contentStyle={{ background: 'var(--panel-bg)', border: '1px solid var(--panel-border)', borderRadius: 8, fontSize: 12 }}
            formatter={(v, name) => [v == null ? '—' : v.toFixed(2), SERIES.find((s) => s.key === name)?.label || name]}
          />
          <ReferenceLine y={100} stroke="var(--text-muted)" strokeDasharray="4 4" />
          {SERIES.map((s) => (
            <Line key={s.key} type="monotone" dataKey={s.key} stroke={s.color} dot={false}
                  strokeWidth={s.key === 'account' ? 2.4 : 1.6} connectNulls isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <div className="chart-legend">
        {SERIES.map((s) => <span key={s.key} style={{ '--swatch': s.color }}>{s.label}</span>)}
      </div>
    </>
  );
}

export default function CockpitPage({ onNavigate }) {
  const marketQ     = useMarketStatus();
  const portfolioQ  = usePortfolio({ refetchInterval: 30_000 });
  const proposalsQ  = useProposals({ status: 'pending', limit: 200 });
  const benchQ      = usePerformanceBenchmark(6, 20);
  const protectQ    = useProtection();
  const healthQ     = useSystemHealth();
  const ltQ         = useQuery({ queryKey: ['lt_decision'], queryFn: fetchLtDecision, staleTime: 5 * 60_000 });

  const equity    = portfolioQ.data?.equity || {};
  const positions = equity.open_positions || [];
  const bench     = benchQ.data;
  const summary   = bench?.summary || {};
  const protect   = protectQ.data;
  const health    = healthQ.data;
  const ltByTicker = useMemo(() => {
    const m = {};
    for (const it of ltQ.data?.items || []) m[it.ticker] = it;
    return m;
  }, [ltQ.data]);
  const protByTicker = useMemo(() => {
    const m = {};
    for (const it of protect?.items || []) m[it.ticker] = it;
    return m;
  }, [protect]);

  const invested = useMemo(() => positions.reduce((acc, p) => acc + (Number(p.current_price || p.entry) || 0) * (Number(p.size) || 0), 0), [positions]);
  const current  = Number(equity.current_equity) || 0;
  const investedPct = current > 0 ? (invested / current) * 100 : null;

  const qualifying = useMemo(() => {
    const items = proposalsQ.data?.items || proposalsQ.data?.proposals || [];
    return [...items]
      .filter((p) => p.status === 'pending')
      .map((p) => ({ ...p, _titan: p.context?.titan_score ?? 0, _verdict: p.context?.buy_signal?.verdict || '—' }))
      .sort((a, b) => b._titan - a._titan)
      .slice(0, 6);
  }, [proposalsQ.data]);

  if (portfolioQ.isLoading && benchQ.isLoading) return <PageSkeleton tiles={6} blockHeight={280} rows={4} />;

  const marketOpen = marketQ.data?.is_open === true;
  const nUnprotected = protect?.n_unprotected ?? null;
  const allProtected = protect?.all_protected;
  const ksBlocked = health?.killswitch?.blocked;
  const cbPaused  = health?.circuit_breaker?.is_paused;
  const trackerAlive = health?.tracker?.alive;
  const today = new Date().toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' });

  return (
    <div className="cockpit animate-fade-in">
      {/* ── Bandeau ── */}
      <header className="cockpit-head">
        <div>
          <div className="cockpit-kicker">Cockpit · {today}</div>
          <h2>Est-ce que ça gagne de l'argent, et est-ce sous contrôle ?</h2>
          <div className="cockpit-sub">
            Compte {health?.broker?.mode === 'alpaca' ? 'Alpaca' : 'paper'}
            {health?.broker?.base_url?.includes('paper') ? ' (paper)' : ''} · fenêtre live depuis {bench?.params?.live_start || '—'}
          </div>
        </div>
        <div className="pill-row">
          <Pill tone={marketOpen ? 'ok' : 'muted'}>{marketOpen ? '🟢 NYSE ouverte' : '⚫ NYSE fermée'}</Pill>
          <Pill tone={trackerAlive ? 'ok' : 'danger'} title={`Dernier heartbeat : ${ageLabel(health?.tracker?.age_sec)}`}>
            {trackerAlive ? '🫀 Tracker OK' : '💀 Tracker silencieux'}
          </Pill>
          <Pill tone={ksBlocked ? 'danger' : 'ok'}>{ksBlocked ? '🧊 Killswitch : entrées gelées' : '🛡️ Killswitch inactif'}</Pill>
          <Pill tone={cbPaused ? 'warn' : 'ok'}>{cbPaused ? '⏸ Circuit breaker en pause' : `CB ×${fmtNum(health?.circuit_breaker?.size_multiplier ?? 1, 2)}`}</Pill>
          <Pill tone={health?.macro?.regime === 'BULL_MARKET' ? 'ok' : 'warn'}>
            {health?.macro?.regime || '—'} · VIX {health?.macro?.vix != null ? fmtNum(health.macro.vix, 1) : '—'}
          </Pill>
        </div>
      </header>

      {/* ── KPI ── */}
      <div className="kpi-grid">
        <Kpi label="Equity" value={current ? `$${fmtNum(current, 0)}` : '—'}
             sub={`PnL latent ${signed(equity.unrealized_pnl, 0, ' $')} · réalisé ${signed(equity.realized_pnl, 0, ' $')}`}
             tone={(equity.unrealized_pnl ?? 0) >= 0 ? 'ok' : 'danger'} />
        <Kpi label="Compte · fenêtre live" value={signed(summary.account_return_pct)} valueTone={toneOf(summary.account_return_pct)}
             sub={`Max DD ${summary.account_max_drawdown_pct != null ? fmtNum(summary.account_max_drawdown_pct, 1) + '%' : '—'} · ${summary.n_days || 0} j`} />
        <Kpi label="vs SPY" value={signed(summary.alpha_vs_spy_pct)} valueTone={toneOf(summary.alpha_vs_spy_pct)}
             sub={`SPY ${signed(summary.spy_return_pct)} sur la même fenêtre`}
             tone={summary.alpha_vs_spy_pct == null ? undefined : summary.alpha_vs_spy_pct >= 0 ? 'ok' : 'danger'} />
        <Kpi label="vs panier TITAN top-20" value={signed(summary.alpha_vs_basket_pct)} valueTone={toneOf(summary.alpha_vs_basket_pct)}
             sub={`Panier ${signed(summary.basket_return_pct)} · hit ${summary.basket_hit_rate != null ? fmtNum(summary.basket_hit_rate * 100, 0) + '%' : '—'} · ${summary.basket_periods || 0} sem.`}
             tone={summary.alpha_vs_basket_pct == null ? undefined : summary.alpha_vs_basket_pct >= 0 ? 'ok' : 'warn'} />
        <Kpi label="Capital investi" value={investedPct != null ? `${fmtNum(investedPct, 0)}%` : '—'}
             sub={`${positions.length} position${positions.length > 1 ? 's' : ''} · cible ≥ 80 % en BULL`}
             tone={investedPct == null ? undefined : investedPct >= 80 ? 'ok' : investedPct >= 50 ? 'warn' : 'danger'} />
        <Kpi label="Positions protégées" value={protect ? `${(protect.n_positions ?? 0) - (nUnprotected ?? 0)} / ${protect.n_positions ?? 0}` : '—'}
             sub={protect ? (allProtected ? 'Stop broker actif sur chaque ligne' : `${nUnprotected} sans stop broker`) : 'chargement…'}
             tone={protect ? (allProtected ? 'ok' : 'danger') : undefined}
             onClick={onNavigate ? () => onNavigate('portfolio') : undefined} />
      </div>

      {/* ── Graphique + actions ── */}
      <div className="cockpit-grid-2">
        <Card title="Compte vs SPY vs panier TITAN (base 100)" icon="📈"
              note={(summary.notes || []).join(' · ') || 'Panier : top-20 TITAN équipondéré, rebalance hebdo, 10 bps de frais, snapshots live uniquement (sans look-ahead).'}
              action={<span className="cockpit-note">{bench?.basket_meta?.computed_at ? `calculé ${bench.basket_meta.computed_at.slice(0, 16).replace('T', ' ')}` : ''}</span>}>
          {benchQ.isLoading ? <div className="cockpit-empty">Calcul du panier (≈ 20 s la première fois)…</div> : <BenchmarkChart bench={bench} />}
        </Card>

        <Card title="À décider" icon="📬" flush
              action={onNavigate && (
                <button type="button" className="btn btn-primary btn-sm" onClick={() => onNavigate('proposals')}>
                  Propositions ({proposalsQ.data?.n_pending ?? qualifying.length}) →
                </button>
              )}>
          {qualifying.length === 0 ? (
            <div className="cockpit-empty">Aucune proposition en attente.</div>
          ) : (
            <table className="cockpit-table">
              <thead><tr><th>Ticker</th><th className="right">TITAN</th><th>Verdict</th><th className="right">Taille</th><th>Support</th></tr></thead>
              <tbody>
                {qualifying.map((p) => (
                  <tr key={p.id}>
                    <td className="ticker">{p.ticker}</td>
                    <td className="num right">{fmtNum(p._titan, 1)}</td>
                    <td><Pill tone={VERDICT_TONE[p._verdict] || 'muted'}>{p._verdict}</Pill></td>
                    <td className="num right">{p.size} × ${fmtPrice(p.entry)}</td>
                    <td className="cockpit-note">{p.context?.support?.level?.replace('_', ' ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      {/* ── Positions ── */}
      <Card title="Positions · protection & décision LT" icon="🛡️" flush
            action={<Pill tone={protect ? (allProtected ? 'ok' : 'danger') : 'muted'}>
              {protect ? (allProtected ? 'toutes protégées' : `${nUnprotected} non protégée(s)`) : '…'}
            </Pill>}>
        {positions.length === 0 ? (
          <div className="cockpit-empty">Aucune position ouverte.</div>
        ) : (
          <table className="cockpit-table">
            <thead>
              <tr>
                <th>Ticker</th><th className="right">Qté</th><th className="right">Entrée</th><th className="right">Live</th>
                <th className="right">PnL</th><th>Stop broker</th><th className="right">Marge au stop</th><th>Décision LT</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => {
                const t = p.ticker;
                const pr = protByTicker[t];
                const lt = ltByTicker[t];
                const pnl = p.pct_from_entry;
                const stopTone = !pr ? 'muted' : pr.status === 'protected' ? 'ok' : pr.status === 'drift' ? 'warn' : 'danger';
                const stopLabel = !pr ? '…'
                  : pr.broker_stop ? `${fmtPrice(pr.broker_stop)}${pr.broker_order_class === 'oco' ? ' · OCO' : ''}`
                  : pr.status === 'journal_only' ? `journal ${fmtPrice(pr.sl_journal)}`
                  : 'AUCUN';
                return (
                  <tr key={t}>
                    <td className="ticker">{t}</td>
                    <td className="num right">{p.size}</td>
                    <td className="num right">${fmtPrice(p.entry)}</td>
                    <td className="num right">{p.current_price != null ? `$${fmtPrice(p.current_price)}` : '—'}</td>
                    <td className={`num right ${toneOf(pnl)}`}>{signed(pnl, 1)}</td>
                    <td><Pill tone={stopTone} title={pr?.drift_pct != null ? `écart journal ${signed(pr.drift_pct, 1)}` : undefined}>{stopLabel}</Pill></td>
                    <td className="num right">{pr?.pct_to_stop != null ? `${fmtNum(pr.pct_to_stop, 1)}%` : '—'}</td>
                    <td>
                      {lt ? (
                        <Pill tone={LT_TONE[lt.action] || 'muted'} title={(lt.reasons || []).join(' · ')}>
                          {lt.action}{lt.thesis_status ? ` · ${lt.thesis_status}` : ''}
                        </Pill>
                      ) : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      {/* ── Santé système ── */}
      <Card title="Santé système" icon="🩺"
            action={<Pill tone={health ? (health.ok ? 'ok' : 'danger') : 'muted'}>{health ? (health.ok ? 'OK' : 'ATTENTION') : '…'}</Pill>}>
        {health?.checks?.length ? (
          <ul className="check-list">
            {health.checks.map((c, i) => (
              <li key={i}><Pill tone={c.level === 'danger' ? 'danger' : 'warn'}>{c.level}</Pill><span>{c.msg}</span></li>
            ))}
          </ul>
        ) : <div className="cockpit-note">Aucune alerte système.</div>}
        <div className="health-strip" style={undefined}>
          {Object.entries(health?.crons || {}).map(([name, c]) => {
            const age = c.log_age_sec;
            const cls = age == null ? 'health-item--dead' : age > 86400 * 2 ? 'health-item--stale' : '';
            return (
              <div key={name} className={`health-item ${cls}`} title={c.last_line || ''}>
                <b>{name}</b><span>il y a {ageLabel(age)}</span>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}
