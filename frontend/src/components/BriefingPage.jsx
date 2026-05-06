// BriefingPage — point d'atterrissage matinal.
//
// Compose les hooks existants (status, macro, macro_calendar, portfolio,
// proposals, market_status) pour donner un "30 secondes" de prise de
// décision LT : régime, blackouts, action requise, positions à surveiller,
// budget jour. Aucun appel backend nouveau — pure agrégation côté client.

import { useMemo } from 'react';
import {
  useMacro,
  useMacroCalendar,
  useMarketStatus,
  usePortfolio,
  useProposals,
  useStatus,
} from '../hooks/useApi';
import { fmtNum, fmtPrice, fmtSignedPct } from '../utils/format';
import { PageSkeleton } from './common/Skeleton';
import TickerSpark from './common/TickerSpark';

const REGIME_STYLES = {
  BULL_MARKET: { label: '🟢 BULL MARKET', tone: 'var(--success)' },
  BEAR_MARKET: { label: '🔴 BEAR MARKET', tone: 'var(--danger)'  },
  CRASH_PANIC: { label: '🚨 CRASH/PANIC',  tone: 'var(--danger)'  },
  FEAR:        { label: '⚠️ FEAR',         tone: 'var(--warning)' },
  UNKNOWN:     { label: '⚫ UNKNOWN',       tone: 'var(--text-muted)' },
};

const TYPE_TONE = {
  FOMC: 'var(--accent-secondary)',
  CPI:  'var(--warning)',
  NFP:  'var(--accent-primary)',
};

function vixTone(v) {
  if (v == null) return 'var(--text-muted)';
  if (v < 20) return 'var(--success)';
  if (v < 30) return 'var(--warning)';
  return 'var(--danger)';
}

function todayLabel() {
  return new Date().toLocaleDateString('fr-FR', {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  });
}

function Tile({ label, value, sub, tone, onClick, icon }) {
  return (
    <div
      className="card"
      style={{
        padding: '14px 16px', minWidth: 180, flex: '1 1 200px',
        cursor: onClick ? 'pointer' : 'default',
        borderLeft: tone ? `3px solid ${tone}` : undefined,
      }}
      onClick={onClick}
    >
      <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)',
                    textTransform: 'uppercase', letterSpacing: '0.06em',
                    marginBottom: 4 }}>
        {icon ? `${icon}  ` : ''}{label}
      </div>
      <div style={{ fontSize: '1.45rem', fontWeight: 700, color: tone || 'var(--text)',
                    fontFamily: 'monospace', lineHeight: 1.1 }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 4 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

function SectionTitle({ icon, children, action }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between',
                  alignItems: 'baseline', marginBottom: 10, marginTop: 18 }}>
      <h3 style={{ margin: 0, fontSize: '0.95rem', fontWeight: 700 }}>
        {icon} {children}
      </h3>
      {action}
    </div>
  );
}

export default function BriefingPage({ onNavigate }) {
  const statusQ        = useStatus({ refetchInterval: 30_000 });
  const marketQ        = useMarketStatus();
  const macroQ         = useMacro({ refetchInterval: 30_000 });
  const macroCalendarQ = useMacroCalendar(14);
  const portfolioQ     = usePortfolio({ refetchInterval: 30_000 });
  const proposalsQ     = useProposals({ status: 'pending', limit: 200 });

  const macro          = macroQ.data || {};
  const status         = statusQ.data || {};
  const market         = marketQ.data || {};
  const portfolio      = portfolioQ.data || {};
  const equity         = portfolio.equity || {};
  const proposalsData  = proposalsQ.data || {};
  const inBlackout     = macroCalendarQ.data?.in_blackout || false;
  // openPositions et calendarEvents ne sont PAS extraits ici car les
  // useMemo en aval lisent directement portfolioQ.data / macroCalendarQ.data
  // pour éviter les deps "|| []" qui changent à chaque render.

  const regime      = macro.confirmed_regime || macro.regime || 'UNKNOWN';
  const regimeStyle = REGIME_STYLES[regime] || REGIME_STYLES.UNKNOWN;
  const vix         = macro.vix ?? status.vix;

  const current     = equity.current_equity ?? status.account_equity ?? 100_000;
  const start       = equity.starting_equity ?? 100_000;
  const dailyDdPct  = start > 0 ? Math.max(0, (start - current) / start * 100) : 0;
  const unrealized  = equity.unrealized_pnl ?? 0;

  // Positions à surveiller : <5% du SL OU <3% du TP (très proche d'un trigger).
  const positionsAtRisk = useMemo(() => {
    const list = portfolioQ.data?.equity?.open_positions || [];
    return list
      .map((p) => {
        const toSl = p.pct_to_sl;
        const toTp = p.pct_to_tp;
        let level = null;
        let detail = null;
        if (Number.isFinite(toSl) && toSl <= 5) {
          level = 'sl'; detail = `À ${toSl.toFixed(1)}% du SL`;
        } else if (Number.isFinite(toTp) && toTp <= 3) {
          level = 'tp'; detail = `À ${toTp.toFixed(1)}% du TP`;
        }
        return level ? { ...p, _level: level, _detail: detail } : null;
      })
      .filter(Boolean)
      .sort((a, b) => {
        const ai = a._level === 'sl' ? 0 : 1;
        const bi = b._level === 'sl' ? 0 : 1;
        if (ai !== bi) return ai - bi;
        return (a.pct_to_sl ?? a.pct_to_tp ?? 999) - (b.pct_to_sl ?? b.pct_to_tp ?? 999);
      });
  }, [portfolioQ.data]);

  // Macro J+0 → J+7 (filtre upcoming + horizon 7j).
  const upcomingMacro = useMemo(() => {
    const events = macroCalendarQ.data?.events || [];
    return events
      .filter(e => e.days_delta >= 0 && e.days_delta <= 7)
      .sort((a, b) => a.days_delta - b.days_delta);
  }, [macroCalendarQ.data]);

  // Top P&L latent positif et négatif.
  const topMovers = useMemo(() => {
    const list = portfolioQ.data?.equity?.open_positions || [];
    return [...list]
      .filter(p => Number.isFinite(p.unrealized_pnl))
      .sort((a, b) => Math.abs(b.unrealized_pnl) - Math.abs(a.unrealized_pnl))
      .slice(0, 5);
  }, [portfolioQ.data]);

  // Pour l'affichage hors memo (KPIs, listes simples).
  const openPositions = portfolioQ.data?.equity?.open_positions || [];

  const isLoading = statusQ.isLoading || macroQ.isLoading || portfolioQ.isLoading;
  if (isLoading) {
    return <PageSkeleton tiles={6} blockHeight={160} rows={5} />;
  }

  const nPending = proposalsData.n_pending ?? 0;
  const slotsUsed = openPositions.length;
  const slotsMax  = 20;

  const ksTriggered = status.killswitch_triggered || dailyDdPct >= 4;
  const marketOpen  = market.is_open !== false;

  return (
    <div className="control-panel animate-fade-in">
      {/* ── HEADER : date + alertes critiques ── */}
      <div className="card" style={{ padding: 16, marginBottom: 14,
            background: 'linear-gradient(135deg, rgba(59,130,246,0.06), rgba(168,85,247,0.04))' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between',
                      alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
          <div>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)',
                          textTransform: 'uppercase', letterSpacing: '0.1em' }}>
              Briefing du jour
            </div>
            <h2 style={{ margin: '4px 0 0', fontSize: '1.4rem',
                         textTransform: 'capitalize' }}>
              {todayLabel()}
            </h2>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {ksTriggered && (
              <span style={{
                padding: '6px 12px', borderRadius: 8, fontWeight: 700,
                background: 'rgba(239,68,68,0.18)', color: 'var(--danger)',
                border: '1px solid var(--danger)',
              }}>⛔ KILLSWITCH ACTIF</span>
            )}
            {inBlackout && (
              <span style={{
                padding: '6px 12px', borderRadius: 8, fontWeight: 700,
                background: 'rgba(239,68,68,0.15)', color: 'var(--danger)',
                border: '1px solid rgba(239,68,68,0.4)',
              }}>🚫 BLACKOUT MACRO</span>
            )}
            <span style={{
              padding: '6px 12px', borderRadius: 8, fontWeight: 600,
              background: marketOpen ? 'rgba(34,197,94,0.12)' : 'rgba(148,163,184,0.12)',
              color: marketOpen ? 'var(--success)' : 'var(--text-muted)',
              border: `1px solid ${marketOpen ? 'rgba(34,197,94,0.4)' : 'var(--border)'}`,
            }}>
              {marketOpen ? '🟢 NYSE ouverte' : '⚫ NYSE fermée'}
            </span>
          </div>
        </div>
      </div>

      {/* ── TUILES KPI ── */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 6 }}>
        <Tile
          icon="🌊"
          label="Régime macro"
          value={regimeStyle.label}
          sub={macro.spy_above_ma200 != null ? `SPY ${macro.spy_above_ma200 ? '> ' : '< '}MA200` : undefined}
          tone={regimeStyle.tone}
        />
        <Tile
          icon="📊"
          label="VIX"
          value={vix != null ? fmtNum(vix, 1) : '—'}
          sub={vix != null ? (vix < 20 ? 'Calme' : vix < 30 ? 'Élevé' : 'Stress') : undefined}
          tone={vixTone(vix)}
        />
        <Tile
          icon="💼"
          label="Capital"
          value={`$${fmtNum(current, 0)}`}
          sub={`P&L latent ${unrealized >= 0 ? '+' : ''}$${fmtNum(unrealized, 0)}`}
          tone={unrealized >= 0 ? 'var(--success)' : 'var(--danger)'}
        />
        <Tile
          icon="📉"
          label="Drawdown jour"
          value={`${dailyDdPct.toFixed(2)}%`}
          sub={dailyDdPct >= 4 ? '⛔ Killswitch' : dailyDdPct >= 2 ? '⚠️ Vigilance' : '✅ OK'}
          tone={dailyDdPct >= 4 ? 'var(--danger)' : dailyDdPct >= 2 ? 'var(--warning)' : 'var(--success)'}
        />
        <Tile
          icon="🎯"
          label="Slots"
          value={`${slotsUsed} / ${slotsMax}`}
          sub={`${Math.max(0, slotsMax - slotsUsed)} libres`}
        />
      </div>

      {/* ── ACTION REQUISE ── */}
      <SectionTitle
        icon="📬"
        action={nPending > 0 && onNavigate ? (
          <button className="action-btn" onClick={() => onNavigate('proposals')}
                  style={{ padding: '0.4rem 0.85rem', fontSize: '0.8rem' }}>
            → Décider ({nPending})
          </button>
        ) : null}
      >
        Action requise
      </SectionTitle>
      <div className="card" style={{ padding: 16 }}>
        {nPending === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
            ✅ Aucune proposition en attente. Génère un plan depuis l'onglet Propositions.
          </div>
        ) : (
          <div style={{ display: 'flex', justifyContent: 'space-between',
                        alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
            <div>
              <div style={{ fontSize: '1.6rem', fontWeight: 700,
                            color: 'var(--accent-primary)', fontFamily: 'monospace' }}>
                {nPending}
              </div>
              <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                proposition{nPending > 1 ? 's' : ''} à décider (TTL 36h)
              </div>
            </div>
            {onNavigate && (
              <button className="action-btn" onClick={() => onNavigate('proposals')}>
                Ouvrir Propositions →
              </button>
            )}
          </div>
        )}
      </div>

      {/* ── POSITIONS À SURVEILLER ── */}
      {openPositions.length > 0 && (
        <>
          <SectionTitle
            icon="⚠️"
            action={onNavigate ? (
              <button className="scan-filter-btn" onClick={() => onNavigate('portfolio')}
                      style={{ fontSize: '0.72rem' }}>
                Voir portfolio →
              </button>
            ) : null}
          >
            Positions à surveiller
            {positionsAtRisk.length > 0 && (
              <span style={{ marginLeft: 8, fontSize: '0.78rem',
                             color: 'var(--warning)', fontWeight: 500 }}>
                ({positionsAtRisk.length})
              </span>
            )}
          </SectionTitle>
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            {positionsAtRisk.length === 0 ? (
              <div style={{ padding: 16, fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                ✅ Aucune position proche d'un SL ou TP — surveillance routine.
              </div>
            ) : (
              <table className="scan-table" style={{ margin: 0 }}>
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>Tendance</th>
                    <th>Entrée</th>
                    <th>Live</th>
                    <th>P&L latent</th>
                    <th>Distance</th>
                    <th>Niveau</th>
                  </tr>
                </thead>
                <tbody>
                  {positionsAtRisk.map(p => {
                    const upnl = p.unrealized_pnl;
                    const pct  = p.pct_from_entry;
                    const isSl = p._level === 'sl';
                    return (
                      <tr key={p.Ticker || p.ticker} className="scan-row"
                          data-ticker={p.Ticker || p.ticker}>
                        <td><strong>{p.Ticker || p.ticker}</strong></td>
                        <td><TickerSpark ticker={p.Ticker || p.ticker} /></td>
                        <td style={{ fontFamily: 'monospace' }}>${fmtPrice(p.Entry || p.entry)}</td>
                        <td style={{ fontFamily: 'monospace' }}>
                          {p.current_price != null ? `$${fmtPrice(p.current_price)}` : '—'}
                        </td>
                        <td>
                          {upnl != null && (
                            <span className={upnl >= 0 ? 'pos' : 'neg'} style={{ fontWeight: 600 }}>
                              {upnl >= 0 ? '+' : ''}${fmtNum(upnl, 2)}
                              {pct != null && <small style={{ marginLeft: 4, opacity: 0.7 }}>
                                ({pct >= 0 ? '+' : ''}{pct.toFixed(1)}%)
                              </small>}
                            </span>
                          )}
                        </td>
                        <td style={{
                          color: isSl ? 'var(--danger)' : 'var(--success)',
                          fontWeight: 700, fontFamily: 'monospace',
                        }}>
                          {p._detail}
                        </td>
                        <td>
                          <span style={{
                            padding: '2px 8px', borderRadius: 4, fontSize: '0.7rem',
                            fontWeight: 700, letterSpacing: '0.04em',
                            background: isSl ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)',
                            color: isSl ? 'var(--danger)' : 'var(--success)',
                          }}>
                            {isSl ? '⚠️ Proche SL' : '🎯 Proche TP'}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

      {/* ── MACRO 7J ── */}
      <SectionTitle
        icon="📅"
        action={onNavigate ? (
          <button className="scan-filter-btn" onClick={() => onNavigate('macro')}
                  style={{ fontSize: '0.72rem' }}>
            Voir calendrier →
          </button>
        ) : null}
      >
        Macro · 7 prochains jours
      </SectionTitle>
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {upcomingMacro.length === 0 ? (
          <div style={{ padding: 16, fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            ✅ Aucun événement macro majeur sur les 7 prochains jours.
          </div>
        ) : (
          <table className="scan-table" style={{ margin: 0 }}>
            <thead>
              <tr>
                <th>Date</th>
                <th>Type</th>
                <th>Événement</th>
                <th>Quand</th>
                <th>Statut</th>
              </tr>
            </thead>
            <tbody>
              {upcomingMacro.map((e, i) => (
                <tr key={`${e.date}-${i}`} className="scan-row">
                  <td style={{ fontFamily: 'monospace', fontSize: '0.85rem' }}>{e.date}</td>
                  <td>
                    <span className="scan-signal-badge" style={{
                      color: TYPE_TONE[e.type] || 'var(--text-main)',
                      borderColor: (TYPE_TONE[e.type] || 'var(--text-muted)') + '50',
                    }}>
                      {e.type}
                    </span>
                  </td>
                  <td>{e.label}</td>
                  <td style={{ fontFamily: 'monospace', fontSize: '0.82rem',
                               color: 'var(--text-muted)' }}>
                    {e.days_delta === 0 ? "Aujourd'hui"
                     : e.days_delta === 1 ? 'Demain'
                     : `Dans ${e.days_delta}j`}
                  </td>
                  <td>
                    {e.blackout ? (
                      <span className="result-badge loss">🔴 BLACKOUT</span>
                    ) : e.upcoming ? (
                      <span className="result-badge" style={{
                        background: 'rgba(245,158,11,0.15)',
                        color: 'var(--warning)',
                        border: '1px solid rgba(245,158,11,0.3)',
                      }}>🟡 À venir</span>
                    ) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* ── TOP MOVERS ── */}
      {topMovers.length > 0 && (
        <>
          <SectionTitle icon="🚀">Top movers (P&L latent absolu)</SectionTitle>
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="scan-table" style={{ margin: 0 }}>
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Tendance</th>
                  <th>Entrée</th>
                  <th>Live</th>
                  <th>P&L latent</th>
                  <th>%</th>
                  <th>Détenu depuis</th>
                </tr>
              </thead>
              <tbody>
                {topMovers.map(p => {
                  const upnl = p.unrealized_pnl;
                  const pct  = p.pct_from_entry;
                  return (
                    <tr key={p.Ticker || p.ticker} className="scan-row"
                        data-ticker={p.Ticker || p.ticker}>
                      <td><strong>{p.Ticker || p.ticker}</strong></td>
                      <td><TickerSpark ticker={p.Ticker || p.ticker} /></td>
                      <td style={{ fontFamily: 'monospace' }}>${fmtPrice(p.Entry || p.entry)}</td>
                      <td style={{ fontFamily: 'monospace' }}>
                        {p.current_price != null ? `$${fmtPrice(p.current_price)}` : '—'}
                      </td>
                      <td>
                        <span className={upnl >= 0 ? 'pos' : 'neg'} style={{ fontWeight: 600 }}>
                          {upnl >= 0 ? '+' : ''}${fmtNum(upnl, 2)}
                        </span>
                      </td>
                      <td>
                        {pct != null && (
                          <span className={pct >= 0 ? 'pos' : 'neg'} style={{ fontFamily: 'monospace' }}>
                            {fmtSignedPct(pct / 100)}
                          </span>
                        )}
                      </td>
                      <td style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                        {(p.Date || p.date || '').slice(0, 10) || '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* ── FOOTER QUICK ACTIONS ── */}
      {onNavigate && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 16 }}>
          <button className="scan-filter-btn" onClick={() => onNavigate('universe')}>
            🌐 Univers
          </button>
          <button className="scan-filter-btn" onClick={() => onNavigate('sectors')}>
            🏛 Secteurs
          </button>
          <button className="scan-filter-btn" onClick={() => onNavigate('risk')}>
            ⚠️ Risk Monitor
          </button>
          <button className="scan-filter-btn" onClick={() => onNavigate('performance')}>
            📈 Performance
          </button>
          <button className="scan-filter-btn" onClick={() => onNavigate('datahealth')}>
            🩺 Data Health
          </button>
        </div>
      )}
    </div>
  );
}
