import { useMemo } from 'react';
import {
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts';
import { useMacro, usePortfolio, useStatus } from '../hooks/useApi';
import { PageSkeleton } from './common/Skeleton';

const KILLSWITCH_PCT   = 4.0;
const RISK_PER_TRADE   = 0.25;
const INITIAL_CAPITAL  = 100_000;

const REGIME_STYLES = {
  BULL_MARKET: { label: '🟢 BULL MARKET', bg: 'rgba(16,185,129,0.12)', color: 'var(--success)', border: 'rgba(16,185,129,0.4)' },
  BEAR_MARKET: { label: '🔴 BEAR MARKET', bg: 'rgba(239,68,68,0.12)',  color: 'var(--danger)',  border: 'rgba(239,68,68,0.4)'  },
  CRASH_PANIC: { label: '🚨 CRASH/PANIC',  bg: 'rgba(239,68,68,0.2)',   color: 'var(--danger)',  border: 'rgba(239,68,68,0.6)'  },
  FEAR:        { label: '⚠️ FEAR',         bg: 'rgba(245,158,11,0.12)', color: 'var(--warning)', border: 'rgba(245,158,11,0.4)' },
  UNKNOWN:     { label: '⚫ UNKNOWN',       bg: 'rgba(148,163,184,0.08)', color: 'var(--text-muted)', border: 'var(--panel-border)' },
};

const REGIME_FACTOR = { BULL_MARKET: 1.0, BEAR_MARKET: 0.5, CRASH_PANIC: 0.0 };

const PIE_COLORS = ['#f59e0b', '#10b981', '#8b5cf6', '#ef4444', '#06b6d4', '#ec4899'];

function vixColor(v) {
  if (!v) return 'var(--text-muted)';
  if (v < 20) return 'var(--success)';
  if (v < 30) return 'var(--warning)';
  return 'var(--danger)';
}

function Kpi({ label, value, sub, subColor }) {
  return (
    <div className="status-chip" style={{ minWidth: 160 }}>
      <span className="sc-lbl">{label}</span>
      <span className="sc-val">{value}</span>
      {sub && <span style={{ fontSize: '0.72rem', color: subColor || 'var(--text-muted)' }}>{sub}</span>}
    </div>
  );
}

function IndicatorRow({ name, val, unit, hint, color }) {
  if (val == null || val === 0) return null;
  const valFmt = typeof val === 'number'
    ? (Number.isInteger(val) ? val : val.toFixed(1))
    : val;
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      fontFamily: 'monospace', fontSize: '0.82rem',
      padding: '0.5rem 0', borderBottom: '1px solid var(--panel-border)',
    }}>
      <span style={{ color: 'var(--text-muted)' }}>{name}</span>
      <span style={{ color: color || 'var(--text-main)', fontWeight: 700 }}>{unit}{valFmt}</span>
      <span style={{ color: 'var(--text-muted)', fontSize: '0.7rem', marginLeft: 8, flex: 1, textAlign: 'right' }}>{hint}</span>
    </div>
  );
}

export default function RiskMonitorPage() {
  const macroQ  = useMacro({ refetchInterval: 30_000 });
  const portQ   = usePortfolio({ refetchInterval: 15_000 });
  const statusQ = useStatus({ refetchInterval: 15_000 });

  const macro   = macroQ.data || {};
  const port    = portQ.data;
  const equity  = useMemo(() => port?.equity || {}, [port]);
  const openPositions = useMemo(() => equity.open_positions || [], [equity]);

  const current = equity.current_equity ?? statusQ.data?.account_equity ?? INITIAL_CAPITAL;
  const start   = equity.starting_equity ?? INITIAL_CAPITAL;
  const dailyDd = start > 0 ? ((current - start) / start) * 100 : 0;
  const budgetUsed = Math.max(0, -dailyDd);
  const budgetLeft = Math.max(0, KILLSWITCH_PCT - budgetUsed);
  const maxMoreTrades = RISK_PER_TRADE > 0 ? Math.floor(budgetLeft / RISK_PER_TRADE) : 0;

  const vix = macro.vix ?? statusQ.data?.vix ?? 0;
  const regime = macro.confirmed_regime || macro.regime || 'UNKNOWN';
  const regimeStyle = REGIME_STYLES[regime] || REGIME_STYLES.UNKNOWN;
  const factor = REGIME_FACTOR[regime] ?? 1.0;
  const vixFactor = vix >= 25 ? 0.75 : vix >= 20 ? 0.9 : 1.0;
  const effectiveRisk = RISK_PER_TRADE * factor * vixFactor;
  const effectiveUsd  = INITIAL_CAPITAL * effectiveRisk / 100;

  const concentration = useMemo(() => openPositions.map(p => {
    const cur = p.current_price ?? p.entry ?? 0;
    const notional = (p.size || 0) * cur;
    return {
      ticker:       p.ticker,
      direction:    p.direction,
      notional,
      exposurePct:  notional / INITIAL_CAPITAL * 100,
      unrealized:   p.unrealized_pnl ?? 0,
      pctToSl:      p.pct_to_sl,
      pctToTp:      p.pct_to_tp,
    };
  }), [openPositions]);

  if (portQ.isLoading || macroQ.isLoading) {
    return <PageSkeleton tiles={5} blockHeight={220} rows={3} />;
  }

  return (
    <div className="control-panel animate-fade-in">
      {/* ── KPIs budget ── */}
      <div className="cp-status-bar">
        <Kpi
          label="Budget journalier utilisé"
          value={`${budgetUsed.toFixed(2)}%`}
          sub={`${budgetLeft.toFixed(2)}% restant`}
          subColor={budgetLeft > 2 ? 'var(--success)' : 'var(--danger)'}
        />
        <Kpi
          label="Trades restants avant KS"
          value={String(maxMoreTrades)}
          sub={`@${RISK_PER_TRADE}% risque/trade`}
        />
        <Kpi
          label="Positions ouvertes"
          value={`${openPositions.length} / 5`}
          sub={openPositions.length >= 5 ? 'Plein' : `${5 - openPositions.length} slot(s)`}
          subColor={openPositions.length >= 5 ? 'var(--danger)' : 'var(--text-muted)'}
        />
        <Kpi
          label="VIX"
          value={vix ? vix.toFixed(1) : 'N/A'}
          sub={vix < 20 ? '< 20 calme' : vix < 30 ? '20-30 vigilance' : '> 30 stress'}
          subColor={vixColor(vix)}
        />
      </div>

      {/* ── VIX gauge ── */}
      {vix > 0 && (
        <div className="card cp-section-card">
          <div className="card-title">📊 VIX — CBOE Volatility Index</div>
          <div style={{ position: 'relative', height: 34, background: 'rgba(0,0,0,0.3)', borderRadius: 8, overflow: 'hidden', border: '1px solid var(--panel-border)' }}>
            <div style={{
              position: 'absolute', top: 0, left: 0, height: '100%',
              width: `${Math.min(vix / 50 * 100, 100)}%`,
              background: `linear-gradient(90deg, ${vixColor(vix)}66, ${vixColor(vix)})`,
              transition: 'width 0.4s ease',
            }} />
            {[20, 30, 35].map(m => (
              <div key={m} style={{
                position: 'absolute', top: 0, left: `${m / 50 * 100}%`,
                width: 1, height: '100%', background: 'rgba(255,255,255,0.15)',
              }} />
            ))}
            <div style={{
              position: 'absolute', top: 0, left: 0, right: 0, height: '100%',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontWeight: 700, fontFamily: 'monospace',
              color: 'var(--text-main)', fontSize: '0.95rem',
              textShadow: '0 1px 2px rgba(0,0,0,0.5)',
            }}>
              {vix.toFixed(1)}
            </div>
          </div>
          <div style={{
            display: 'flex', justifyContent: 'space-between',
            fontSize: '0.7rem', color: 'var(--text-muted)',
            fontFamily: 'monospace', marginTop: 6,
          }}>
            <span>0</span>
            <span>20 vigilance</span>
            <span>30 stress</span>
            <span>35 PANIC</span>
            <span>50+</span>
          </div>
        </div>
      )}

      {/* ── Regime + Indicators ── */}
      <div className="cp-layout">
        <div className="cp-left">
          <div className="card cp-section-card">
            <div className="card-title">🎯 Régime confirmé</div>
            <div style={{
              background: regimeStyle.bg,
              color: regimeStyle.color,
              border: `1px solid ${regimeStyle.border}`,
              borderRadius: 10,
              padding: '0.75rem 1rem',
              textAlign: 'center',
              fontWeight: 700,
              fontFamily: 'monospace',
              fontSize: '1rem',
            }}>
              {regimeStyle.label}
            </div>
            {macro.candidate_regime && macro.candidate_regime !== regime && (
              <div style={{ marginTop: '0.5rem', fontSize: '0.78rem', color: 'var(--text-muted)', textAlign: 'center' }}>
                ⏳ Candidat <strong>{macro.candidate_regime}</strong> depuis {macro.candidate_since || '?'}
              </div>
            )}
          </div>

          <div className="card cp-section-card">
            <div className="card-title">📐 Sizing recommandé (Kelly-ajusté)</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              <div>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>Risque base</div>
                <div style={{ fontSize: '1.1rem', fontWeight: 700 }}>{RISK_PER_TRADE.toFixed(2)}%</div>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>0.25% = $250 sur 100k</div>
              </div>
              <div>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>Facteur régime × VIX</div>
                <div style={{ fontSize: '1.1rem', fontWeight: 700, color: factor >= 1 ? 'var(--success)' : 'var(--danger)' }}>
                  ×{(factor * vixFactor).toFixed(2)}
                </div>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                  Régime {regime} · VIX {vix ? vix.toFixed(0) : '—'}
                </div>
              </div>
              <div>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>Risque effectif</div>
                <div style={{ fontSize: '1.1rem', fontWeight: 700, color: effectiveRisk >= RISK_PER_TRADE * 0.9 ? 'var(--success)' : 'var(--warning)' }}>
                  {effectiveRisk.toFixed(3)}% (${effectiveUsd.toLocaleString(undefined, { maximumFractionDigits: 0 })})
                </div>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                  {effectiveRisk >= RISK_PER_TRADE * 0.9 ? '✅ Normal' : '⬇️ Réduit (régime défavorable)'}
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="cp-right">
          <div className="card cp-section-card">
            <div className="card-title">📊 Indicateurs secondaires</div>
            <IndicatorRow name="VIX"       val={macro.vix}        unit=""  hint="< 20 = calme"                  color={vixColor(vix)} />
            <IndicatorRow name="SP500"     val={macro.sp500}      unit="$" hint="vs EMA200" />
            <IndicatorRow name="EMA200"    val={macro.ema200}     unit="$" hint="support structurel" />
            <IndicatorRow name="HYG Score" val={macro.hyg_score}  unit=""  hint="< 50 = crédit baissier"        color={(macro.hyg_score ?? 50) >= 50 ? 'var(--success)' : 'var(--danger)'} />
            <IndicatorRow name="RSP Score" val={macro.rsp_score}  unit=""  hint="< 50 = breadth baissier"       color={(macro.rsp_score ?? 50) >= 50 ? 'var(--success)' : 'var(--danger)'} />
            <IndicatorRow name="LQD Score" val={macro.lqd_score}  unit=""  hint="< 50 = IG baissier"            color={(macro.lqd_score ?? 50) >= 50 ? 'var(--success)' : 'var(--danger)'} />
          </div>

          {openPositions.length > 0 ? (
            <div className="card cp-section-card">
              <div className="card-title">🎯 Concentration positions ouvertes</div>
              <table className="scan-table" style={{ marginBottom: '1rem' }}>
                <thead>
                  <tr>
                    <th>Ticker</th><th>Dir</th><th>Notionnel</th><th>Exposition</th>
                    <th>PnL latent</th><th>→SL</th><th>→TP</th>
                  </tr>
                </thead>
                <tbody>
                  {concentration.map((p) => (
                    <tr key={p.ticker} className="scan-row">
                      <td><strong>{p.ticker}</strong></td>
                      <td>
                        <span className="scan-signal-badge" style={{
                          color: p.direction === 'LONG' ? 'var(--success)' : 'var(--danger)',
                          borderColor: (p.direction === 'LONG' ? 'var(--success)' : 'var(--danger)') + '50',
                        }}>
                          {p.direction === 'LONG' ? '▲' : '▼'} {p.direction}
                        </span>
                      </td>
                      <td>${p.notional.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                      <td>{p.exposurePct.toFixed(1)}%</td>
                      <td className={p.unrealized >= 0 ? 'pos' : 'neg'} style={{ fontWeight: 600 }}>
                        {p.unrealized >= 0 ? '+' : ''}${p.unrealized.toFixed(2)}
                      </td>
                      <td style={{ color: 'var(--text-muted)' }}>{p.pctToSl != null ? `${p.pctToSl.toFixed(1)}%` : '—'}</td>
                      <td style={{ color: 'var(--text-muted)' }}>{p.pctToTp != null ? `${p.pctToTp.toFixed(1)}%` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <ResponsiveContainer width="100%" height={220}>
                <PieChart>
                  <Pie
                    data={concentration}
                    dataKey="notional"
                    nameKey="ticker"
                    innerRadius={50}
                    outerRadius={90}
                    paddingAngle={2}
                    label={e => `${e.ticker} ${e.exposurePct.toFixed(0)}%`}
                  >
                    {concentration.map((p, i) => (
                      <Cell key={p.ticker} fill={PIE_COLORS[i % PIE_COLORS.length]} stroke="#0d0d1a" strokeWidth={2} />
                    ))}
                  </Pie>
                  <Tooltip
                    formatter={(v, n) => [`$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`, n]}
                    contentStyle={{ background: 'rgba(15,23,42,0.95)', border: '1px solid rgba(148,163,184,0.3)', borderRadius: 6, fontSize: 12 }}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="card cp-section-card">
              <div style={{ color: 'var(--success)', textAlign: 'center', padding: '2rem', fontFamily: 'monospace' }}>
                ✅ Aucune position ouverte — risk budget entièrement disponible.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
