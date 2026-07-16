/**
 * TickerAnalysisModal — Factsheet structurée d'un ticker.
 *
 * Click sur un ticker dans ProposalsPage → modal centré avec sections :
 * Identité / TITAN / Valuation / Quality / Health / Growth / Analystes /
 * Price action / Support / Drift / Flags / Meta.
 *
 * 100 % données existantes (no IA, no API externe). Endpoint /api/ticker_analysis/{ticker}.
 */
import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { addPriceAlert, fetchTickerAnalysis } from '../api/client.js';
import { fmtMarketCap, fmtNum, fmtPct } from '../utils/format.js';
import PeerComparison from './PeerComparison.jsx';
import TickerPriceChart from './common/TickerPriceChart.jsx';
import TitanScoreChart from './common/TitanScoreChart.jsx';
import TradingViewWidget from './common/TradingViewWidget.jsx';

// Sections extraites dans tickerAnalysis/* pour découper ce fichier.
import NewsSection from './tickerAnalysis/NewsSection.jsx';
import NotesSection from './tickerAnalysis/NotesSection.jsx';
import SecFilingsSection from './tickerAnalysis/SecFilingsSection.jsx';

// Constantes (palettes, mappings) — source unique tickerAnalysis/constants.js
import {
  BUY_SIGNAL_PALETTE,
  DIVIDEND_LEVEL_TONE,
  ENTRY_RECO_PALETTE,
  FLAG_PALETTE,
  GRADE_PALETTE,
  MODAL_TABS,
  RATING_LABEL,
  SURPRISE_LEVEL_TONE,
  THESIS_PALETTE,
} from './tickerAnalysis/constants';
import { Grid, KV, Section } from './tickerAnalysis/Layout';
import { pctToneSigned } from './tickerAnalysis/helpers';

function GradeBadge({ grade }) {
  const p = GRADE_PALETTE[grade] || GRADE_PALETTE['N/A'];
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      minWidth: 28, height: 22, padding: '0 0.4rem',
      borderRadius: 4, fontSize: '0.72rem', fontWeight: 800,
      fontFamily: 'monospace', background: p.bg, color: p.fg,
    }}>{grade}</span>
  );
}

function FactorGradesGrid({ grades }) {
  if (!grades) return null;
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
      gap: '0.4rem 0.7rem',
    }}>
      {Object.entries(grades).map(([label, info]) => (
        <div key={label} style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          background: 'var(--bg-tertiary)', padding: '0.35rem 0.5rem',
          borderRadius: 5, border: '1px solid var(--border)',
        }}>
          <span style={{ fontSize: '0.7rem', fontWeight: 600 }}>{label}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
              {info.score != null ? fmtNum(info.score, 0) : '—'}
            </span>
            <GradeBadge grade={info.grade} />
          </div>
        </div>
      ))}
    </div>
  );
}

function QuantRatingBadge({ rating, composite }) {
  const r = RATING_LABEL[rating] || RATING_LABEL['N/A'];
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 8,
      padding: '0.5rem 0.8rem', borderRadius: 8,
      background: r.bg, border: `1px solid ${r.fg}`,
    }}>
      <span style={{ fontSize: '0.85rem', fontWeight: 800, letterSpacing: '0.04em', color: r.fg }}>
        {r.label}
      </span>
      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
        TITAN {composite != null ? fmtNum(composite, 1) : '—'}
      </span>
    </div>
  );
}

function BullBearCases({ cases }) {
  if (!cases) return null;
  const block = (title, list, color, icon) => (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{
        fontSize: '0.65rem', fontWeight: 800, letterSpacing: '0.06em',
        color, marginBottom: 4, textTransform: 'uppercase',
      }}>
        {icon} {title} ({list.length})
      </div>
      {list.length === 0 ? (
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>—</div>
      ) : (
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: '0.72rem', lineHeight: 1.5 }}>
          {list.map((s, i) => <li key={i}>{s}</li>)}
        </ul>
      )}
    </div>
  );
  return (
    <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
      {block('Bullish case', cases.bull || [], '#4ade80', '🟢')}
      {block('Bearish case', cases.bear || [], '#f87171', '🔴')}
    </div>
  );
}

// Lot 18 — Big BUY badge prominent (couleur + icône + sizing).
function BuySignalBadge({ signal }) {
  if (!signal || !signal.verdict) return null;
  const p = BUY_SIGNAL_PALETTE[signal.verdict] || BUY_SIGNAL_PALETTE.SKIP;
  const sizing = signal.sizing_pct;
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 10,
      padding: p.big ? '0.7rem 1.1rem' : '0.45rem 0.85rem',
      borderRadius: 10,
      background: p.bg, border: `2px solid ${p.fg}`,
      boxShadow: p.big ? `0 0 14px ${p.fg}33` : 'none',
    }}>
      <span style={{ fontSize: p.big ? '1.6rem' : '1.05rem' }}>{p.icon}</span>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{
          fontSize: p.big ? '0.95rem' : '0.78rem',
          fontWeight: 800, letterSpacing: '0.04em', color: p.fg,
        }}>
          {signal.label}
        </span>
        {sizing > 0 && (
          <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>
            Sizing recommandé : {(sizing * 100).toFixed(0)}% du budget TITAN
          </span>
        )}
      </div>
    </div>
  );
}

function InsiderBadge({ insider }) {
  if (!insider) return null;
  const filings = insider.n_filings || 0;
  const cluster = insider.cluster_buying;
  const buy30 = insider.buy_count_30d || 0;
  if (filings === 0 && !cluster) return null;
  let bg = 'rgba(148,163,184,0.10)', fg = 'var(--text-muted)', label = `Insider ${filings} filings`;
  if (cluster) {
    bg = 'rgba(34,197,94,0.18)'; fg = '#22c55e';
    label = `🟢 Cluster buying (3+ insiders / 7j)`;
  } else if (buy30 >= 3) {
    bg = 'rgba(132,204,22,0.16)'; fg = '#a3e635';
    label = `Insider activity ${buy30}× sur 30j`;
  } else if (buy30 > 0) {
    label = `Insider ${buy30}× sur 30j`;
  }
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      fontSize: '0.7rem', fontWeight: 700, padding: '0.25rem 0.6rem',
      borderRadius: 5, background: bg, color: fg,
    }}>
      🏛️ {label}
    </span>
  );
}

function EarningsBadge({ days, date }) {
  if (date == null) return null;
  let bg = 'rgba(148,163,184,0.10)', fg = 'var(--text-muted)', label = `Earnings ${date}`;
  if (Number.isFinite(days)) {
    if (days >= 0 && days < 7) { bg = 'rgba(248,113,113,0.18)'; fg = '#f87171'; label = `Earnings dans ${days}j (BLACKOUT)`; }
    else if (days >= 0 && days < 30) { bg = 'rgba(251,191,36,0.18)'; fg = '#fbbf24'; label = `Earnings dans ${days}j`; }
    else if (days < 0) { label = `Earnings passés (${-days}j)`; }
    else { label = `Earnings dans ${days}j`; }
  }
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      fontSize: '0.7rem', fontWeight: 700, padding: '0.25rem 0.6rem',
      borderRadius: 5, background: bg, color: fg,
    }}>
      📅 {label}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────
// EntryPlan — plan d'achat fractionné (issu de /api/ticker_analysis)
// ─────────────────────────────────────────────────────────────────
function EntryPlanSection({ plan, ticker }) {
  const [pendingTier, setPendingTier] = useState(null);
  const [feedback, setFeedback] = useState(null);

  if (!plan || plan.available === false) {
    return (
      <Section title="🎯 Plan d'entrée">
        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
          Plan désactivé{plan?.reason ? ` — ${plan.reason}` : ''}.
        </div>
      </Section>
    );
  }

  const reco = ENTRY_RECO_PALETTE[plan.recommendation] || {
    bg: 'rgba(148,163,184,0.10)', fg: 'var(--text-muted)', icon: '—',
    label: plan.recommendation || '—',
  };

  const watchTier = async (tier) => {
    if (!tier.limit_price) return;
    setPendingTier(tier);
    setFeedback(null);
    try {
      const res = await addPriceAlert({
        ticker,
        target_price: tier.limit_price,
        direction: 'below',
        weight_pct: tier.weight_pct,
        note: `entry_plan ${tier.label}`,
        ttl_days: 90,
      });
      setFeedback({ ok: true, msg: `Alerte créée (${res?.alert?.id || 'OK'})` });
    } catch (e) {
      setFeedback({ ok: false, msg: e?.message || String(e) });
    } finally {
      setPendingTier(null);
    }
  };

  return (
    <Section title="🎯 Plan d'entrée recommandé">
      <div style={{
        padding: '0.7rem 0.9rem', borderRadius: 8,
        background: reco.bg, border: `1px solid ${reco.fg}`,
        marginBottom: 10,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontSize: '1.2rem' }}>{reco.icon}</span>
          <span style={{ fontSize: '0.85rem', fontWeight: 800, color: reco.fg, letterSpacing: '0.04em' }}>
            {reco.label}
          </span>
          {plan.is_ath_extended && (
            <span style={{
              fontSize: '0.65rem', fontWeight: 700,
              padding: '0.15rem 0.5rem', borderRadius: 4,
              background: 'rgba(248,113,113,0.18)', color: '#f87171',
            }}>
              ATH-EXTENDED
            </span>
          )}
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginLeft: 'auto', fontFamily: 'monospace' }}>
            Anchor : ${fmtNum(plan.anchor_price, 2)} · Support : {plan.support_level || '—'}
          </span>
        </div>
        {plan.rationale && (
          <div style={{ marginTop: 6, fontSize: '0.72rem', color: 'var(--text-muted)' }}>
            {plan.rationale}
          </div>
        )}
      </div>

      {/* overflowX : les 3 colonnes fixes (80+110+90px) + auto ne tiennent pas
          dans un modal mobile (~340px de large) — on scrolle plutôt que de
          les écraser illisibles. */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, overflowX: 'auto' }}>
        {(plan.tiers || []).map((tier, idx) => {
          const canWatch = tier.limit_price != null && tier.weight_pct > 0
                           && (tier.discount_pct ?? 0) < 0;
          return (
            <div key={idx} style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(0,1.8fr) 80px 110px 90px auto',
              gap: 10, alignItems: 'center',
              padding: '0.5rem 0.7rem', borderRadius: 6,
              background: 'var(--bg-tertiary)', border: '1px solid var(--border)',
              fontSize: '0.78rem',
              minWidth: 380,
            }}>
              <span style={{ fontWeight: 600 }}>{tier.label}</span>
              <span style={{
                fontFamily: 'monospace', fontWeight: 700,
                color: tier.weight_pct > 0 ? 'var(--text-main)' : 'var(--text-muted)',
              }}>
                {fmtNum(tier.weight_pct, 0)} %
              </span>
              <span style={{ fontFamily: 'monospace', color: 'var(--text-main)' }}>
                {tier.limit_price != null ? `$${fmtNum(tier.limit_price, 2)}` : tier.note || '—'}
              </span>
              <span style={{
                fontFamily: 'monospace', fontSize: '0.72rem',
                color: tier.discount_pct != null && tier.discount_pct < 0
                       ? '#fbbf24' : 'var(--text-muted)',
              }}>
                {tier.discount_pct != null ? `${tier.discount_pct.toFixed(1)} %` : '—'}
              </span>
              <button
                type="button"
                className="scan-filter-btn"
                disabled={!canWatch || pendingTier === tier}
                onClick={() => watchTier(tier)}
                title={canWatch ? `Crée une alerte prix à ${tier.limit_price}` : 'Pas de seuil watchable'}
                style={{
                  fontSize: '0.7rem', padding: '0.25rem 0.6rem',
                  opacity: canWatch ? 1 : 0.4,
                }}
              >
                {pendingTier === tier ? '⏳' : '🔔 Watch'}
              </button>
            </div>
          );
        })}
      </div>

      {feedback && (
        <div style={{
          marginTop: 8, fontSize: '0.72rem',
          color: feedback.ok ? '#4ade80' : '#fb7185',
        }}>
          {feedback.ok ? '✓' : '⚠️'} {feedback.msg}
        </div>
      )}
    </Section>
  );
}


// Phase 2 SL/TP — thesis_stop fondamental.
function ThesisStatusPanel({ thesis }) {
  if (!thesis || !thesis.status) return null;
  const p = THESIS_PALETTE[thesis.status] || THESIS_PALETTE.NO_DATA;
  const breaks = thesis.reasons_break || [];
  const warns = thesis.reasons_warn || [];
  return (
    <div style={{
      marginTop: 10, padding: '0.7rem 0.9rem',
      background: p.bg, border: `1px solid ${p.fg}`, borderRadius: 8,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: '1.1rem' }}>{p.icon}</span>
        <span style={{ fontSize: '0.85rem', fontWeight: 800,
                       color: p.fg, letterSpacing: '0.04em' }}>
          {p.label}
        </span>
        {thesis.drift?.titan != null && (
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)',
                         marginLeft: 'auto', fontFamily: 'monospace' }}>
            Drift TITAN : {thesis.drift.titan > 0 ? '+' : ''}{thesis.drift.titan} pts
            {thesis.drift.f_score != null && ` · F-Score ${thesis.drift.f_score > 0 ? '+' : ''}${thesis.drift.f_score}`}
          </span>
        )}
      </div>
      {(breaks.length > 0 || warns.length > 0) && (
        <ul style={{ margin: '8px 0 0 0', paddingLeft: 18,
                     fontSize: '0.72rem', lineHeight: 1.55 }}>
          {breaks.map((r, i) => (
            <li key={`b${i}`} style={{ color: '#f87171' }}>{r}</li>
          ))}
          {warns.map((r, i) => (
            <li key={`w${i}`} style={{ color: '#fbbf24' }}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  );
}


function FlagBadge({ flag }) {
  const p = FLAG_PALETTE[flag.level] || FLAG_PALETTE.info;
  return (
    <div title={flag.detail} style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      fontSize: '0.7rem', fontWeight: 700,
      padding: '0.2rem 0.55rem', borderRadius: 5,
      background: p.bg, color: p.fg, border: `1px solid ${p.border}`,
    }}>
      <span>{flag.label}</span>
      <span style={{ opacity: 0.7, fontWeight: 500 }}>· {flag.detail}</span>
    </div>
  );
}

// Section / KV / Grid / pctToneSigned déplacés dans tickerAnalysis/Layout.jsx
// FORM_TONE déplacé dans tickerAnalysis/constants.js


// ─── Onglets ─────────────────────────────────────────────────────
// MODAL_TABS déplacé dans tickerAnalysis/constants.js
function TabButton({ tab, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: '0.55rem 0.95rem',
        border: 'none',
        background: 'none',
        color: active ? 'var(--text-main)' : 'var(--text-muted)',
        fontWeight: active ? 700 : 500,
        fontSize: '0.82rem',
        fontFamily: 'inherit',
        cursor: 'pointer',
        borderBottom: active ? '2px solid var(--accent-primary)' : '2px solid transparent',
        transition: 'color 0.15s, border-color 0.15s',
        whiteSpace: 'nowrap',
      }}
    >
      <span style={{ marginRight: 6 }}>{tab.icon}</span>{tab.label}
    </button>
  );
}

export default function TickerAnalysisModal({ ticker, onClose }) {
  // Hack idiomatique : on stocke le ticker précédent dans un state, et on
  // détecte le changement *pendant le render* pour reset le tab — sans
  // useEffect (évite cascading render). Pattern recommandé par la doc React.
  const [tab, setTab] = useState('overview');
  const [prevTicker, setPrevTicker] = useState(ticker);
  if (prevTicker !== ticker) {
    setPrevTicker(ticker);
    setTab('overview');
  }
  const [chartMode, setChartMode] = useState('compact'); // 'compact' | 'tradingview'

  // Migration vers React Query : annule le fetch précédent au changement de
  // ticker, pas de cascade setState dans useEffect.
  const analysisQ = useQuery({
    queryKey: ['ticker_analysis', ticker],
    queryFn: () => fetchTickerAnalysis(ticker),
    enabled: !!ticker,
    staleTime: 60_000,
  });
  const data = analysisQ.data;
  const loading = analysisQ.isLoading;
  const error = analysisQ.isError ? (analysisQ.error?.message || String(analysisQ.error)) : null;

  // ESC pour fermer
  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose?.(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!ticker) return null;

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.72)',
      backdropFilter: 'blur(3px)',
      display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
      zIndex: 1000, padding: '2.5rem 1rem', overflow: 'auto',
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        width: '100%', maxWidth: 1080,
        background: 'var(--panel-bg)',
        backdropFilter: 'blur(20px)',
        borderRadius: 14,
        border: '1px solid var(--panel-border)',
        boxShadow: '0 24px 70px rgba(0,0,0,0.5)',
        overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{
          padding: '1.1rem 1.4rem',
          borderBottom: '1px solid var(--panel-border)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          position: 'sticky', top: 0,
          background: 'linear-gradient(135deg, rgba(59,130,246,0.07), rgba(139,92,246,0.05))',
          backdropFilter: 'blur(20px)',
          borderRadius: '14px 14px 0 0', zIndex: 2,
        }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap' }}>
            <div style={{
              fontSize: '1.85rem', fontWeight: 900,
              fontFamily: 'monospace', letterSpacing: '0.02em',
              background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))',
              WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
            }}>{ticker.toUpperCase()}</div>
            {data?.identity?.name && (
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: '0.95rem', fontWeight: 600, color: 'var(--text-main)' }}>
                  {data.identity.name}
                </div>
                {data.identity.sector && (
                  <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>
                    {data.identity.sector}{data.identity.industry ? ` · ${data.identity.industry}` : ''}
                  </div>
                )}
              </div>
            )}
          </div>
          <button onClick={onClose} className="scan-filter-btn"
                  style={{ padding: '0.4rem 0.85rem', fontSize: '0.85rem' }}>
            ✕ Fermer (Esc)
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: '1rem 1.25rem' }}>
          {loading && <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>
            Chargement…
          </div>}

          {error && <div style={{
            padding: '1rem', borderRadius: 6,
            background: 'rgba(248,113,113,0.1)', color: '#f87171',
            border: '1px solid rgba(248,113,113,0.4)',
          }}>
            ❌ {error}
          </div>}

          {data && <>
            {/* ─────── HERO (toujours visible) ─────── */}
            <div style={{
              padding: '1rem 1.1rem 0.6rem',
              background: 'var(--bg-tertiary)',
              borderRadius: 10,
              marginBottom: '1rem',
              border: '1px solid var(--border)',
            }}>
              {/* Chart prix — toggle compact (universe_history) / TradingView */}
              <div style={{ marginBottom: '0.85rem' }}>
                <div style={{
                  display: 'flex', justifyContent: 'flex-end', gap: 4,
                  marginBottom: 6,
                }}>
                  <button type="button"
                          onClick={() => setChartMode('compact')}
                          className={`scan-filter-btn ${chartMode === 'compact' ? 'active' : ''}`}
                          style={{ fontSize: '0.7rem', padding: '0.2rem 0.55rem' }}
                          title="Chart compact (universe_history, rapide)">
                    📊 Compact
                  </button>
                  <button type="button"
                          onClick={() => setChartMode('tradingview')}
                          className={`scan-filter-btn ${chartMode === 'tradingview' ? 'active' : ''}`}
                          style={{ fontSize: '0.7rem', padding: '0.2rem 0.55rem' }}
                          title="TradingView Advanced Chart (zoom, indicateurs, dessins)">
                    📈 TradingView
                  </button>
                </div>
                {chartMode === 'compact' ? (
                  <TickerPriceChart ticker={ticker.toUpperCase()} height={130} />
                ) : (
                  <TradingViewWidget ticker={ticker.toUpperCase()} height={420} />
                )}
              </div>

              {/* Buy Signal — verdict prominent */}
              {data.buy_signal && (
                <div style={{ marginBottom: '0.7rem' }}>
                  <BuySignalBadge signal={data.buy_signal} />
                  {data.buy_signal.reasons_pos?.length > 0 && data.buy_signal.verdict !== 'SKIP' && (
                    <details style={{ marginTop: 6, fontSize: '0.7rem' }}>
                      <summary style={{ cursor: 'pointer', color: 'var(--text-muted)' }}>
                        Pourquoi ? ({data.buy_signal.reasons_pos.length} raisons positives,
                        {' '}{data.buy_signal.reasons_neg?.length || 0} négatives)
                      </summary>
                      <ul style={{ margin: '4px 0 0 0', paddingLeft: 18, lineHeight: 1.5 }}>
                        {data.buy_signal.reasons_pos.map((r, i) =>
                          <li key={`p${i}`} style={{ color: '#4ade80' }}>{r}</li>
                        )}
                        {(data.buy_signal.reasons_neg || []).map((r, i) =>
                          <li key={`n${i}`} style={{ color: '#fb7185' }}>{r}</li>
                        )}
                      </ul>
                    </details>
                  )}
                </div>
              )}

              {/* Headline ratings : QuantRating + TITAN history + Earnings + Insider + Dividend */}
              <div style={{
                display: 'flex', flexWrap: 'wrap', gap: '0.55rem',
                alignItems: 'center',
              }}>
                <QuantRatingBadge rating={data.quant_rating} composite={data.titan?.composite} />
                <TitanScoreChart ticker={ticker.toUpperCase()} />
                {data.next_earnings_date && (
                  <EarningsBadge
                    date={data.next_earnings_date}
                    days={(() => {
                      try {
                        const d = new Date(data.next_earnings_date);
                        const now = new Date();
                        return Math.round((d - now) / (1000 * 60 * 60 * 24));
                      } catch { return null; }
                    })()}
                  />
                )}
                <InsiderBadge insider={data.insider} />
                {data.dividend_safety && data.dividend_safety.level !== 'NO_DIVIDEND' && (
                  <span style={{
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                    fontSize: '0.7rem', fontWeight: 700, padding: '0.25rem 0.6rem',
                    borderRadius: 5,
                    background: 'rgba(96,165,250,0.10)',
                    color: DIVIDEND_LEVEL_TONE[data.dividend_safety.level] || 'var(--text-muted)',
                  }}>
                    💰 Dividend {data.dividend_safety.level?.replace('_', ' ')}
                    {data.dividend_safety.score != null && ` · ${fmtNum(data.dividend_safety.score, 0)}/100`}
                  </span>
                )}
              </div>
            </div>

            {/* ─────── ONGLETS ─────── */}
            <div style={{
              display: 'flex', gap: 4,
              borderBottom: '1px solid var(--border)',
              marginBottom: '1.1rem',
              overflowX: 'auto',
              position: 'sticky', top: 70,
              background: 'var(--panel-bg)',
              backdropFilter: 'blur(12px)',
              zIndex: 1,
            }}>
              {MODAL_TABS.map(t => (
                <TabButton
                  key={t.id} tab={t} active={tab === t.id}
                  onClick={() => setTab(t.id)}
                />
              ))}
            </div>

            {/* ─────── ONGLET APERÇU ─────── */}
            {tab === 'overview' && <>
            {/* Bull/Bear cases (auto-generated) */}
            {data.bull_bear_cases && (data.bull_bear_cases.bull?.length || data.bull_bear_cases.bear?.length) ? (
              <Section title="🎯 Bull case vs Bear case (auto-derived)">
                <BullBearCases cases={data.bull_bear_cases} />
              </Section>
            ) : null}

            {/* Factor Grades — A+/F par pilier */}
            {data.factor_grades && (
              <Section title="Factor Grades — note lettrée par pilier">
                <FactorGradesGrid grades={data.factor_grades} />
              </Section>
            )}

            {/* TITAN snapshot */}
            <Section title="TITAN — composite & piliers">
              <Grid cols={4}>
                <KV label="Composite" value={fmtNum(data.titan.composite, 1)}
                    tone={data.titan.composite >= 80 ? '#4ade80' : data.titan.composite >= 70 ? '#fbbf24' : 'var(--text-muted)'} />
                <KV label="F-Score" value={`${data.titan.f_score ?? '—'} / ${data.titan.f_score_max ?? 9}`}
                    tone={data.titan.f_score >= 7 ? '#4ade80' : data.titan.f_score <= 3 ? '#f87171' : undefined} />
                <KV label="Tilts" value={data.titan.tilt_flags.length ? data.titan.tilt_flags.join(', ') : '—'}
                    tone={data.titan.tilt_adjust > 0 ? '#4ade80' : data.titan.tilt_adjust < 0 ? '#f87171' : undefined}
                    hint={`Adjust ${data.titan.tilt_adjust ?? 0}`} />
                <KV label="Market Cap" value={fmtMarketCap(data.identity.market_cap)} />

                <KV label="Quality" value={fmtNum(data.titan.quality, 1)} />
                <KV label="Value" value={fmtNum(data.titan.value, 1)} />
                <KV label="Risk" value={fmtNum(data.titan.risk, 1)} />
                <KV label="Momentum" value={fmtNum(data.titan.momentum, 1)} />

                <KV label="Growth" value={fmtNum(data.titan.growth, 1)} />
                <KV label="Piotroski" value={fmtNum(data.titan.piotroski, 1)} />
                <KV label="Revisions" value={fmtNum(data.revisions?.score, 1)}
                    tone={data.revisions?.score >= 70 ? '#4ade80' :
                          data.revisions?.score < 30 ? '#f87171' : undefined} />
                <KV label="Insider" value={fmtNum(data.insider?.score, 1)}
                    tone={data.insider?.score >= 70 ? '#4ade80' :
                          data.insider?.score < 40 ? '#f87171' : undefined} />
              </Grid>
            </Section>

            {/* Flags */}
            {data.flags.length > 0 && (
              <Section title="Points d'attention (règles statiques, no IA)">
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {data.flags.map((f, i) => <FlagBadge key={i} flag={f} />)}
                </div>
              </Section>
            )}

            {/* Drift (position OPEN) */}
            {data.drift?.is_open && (
              <Section title="📊 Position OPEN — drift TITAN entry → maintenant">
                <Grid cols={4}>
                  <KV label="Date entry" value={data.drift.entry_date || '—'} />
                  <KV label="Prix entry" value={data.drift.entry_price ? `$${fmtNum(data.drift.entry_price, 2)}` : '—'} />
                  <KV label="TITAN entry" value={fmtNum(data.drift.entry_titan, 1)}
                      hint={data.drift.warning} />
                  <KV label="TITAN now" value={fmtNum(data.titan.composite, 1)}
                      tone={data.drift.entry_titan && data.titan.composite < data.drift.entry_titan - 10 ? '#f87171' : undefined} />
                  {data.drift.entry_f_score != null && (
                    <KV label="F-Score entry" value={`${data.drift.entry_f_score} / 9`} />
                  )}
                  {data.drift.entry_titan != null && (
                    <KV label="Drift TITAN" value={`${data.titan.composite >= data.drift.entry_titan ? '+' : ''}${fmtNum(data.titan.composite - data.drift.entry_titan, 1)}`}
                        tone={pctToneSigned(data.titan.composite - data.drift.entry_titan)} />
                  )}
                </Grid>
                {data.drift.warning && (
                  <div style={{ marginTop: 8, fontSize: '0.7rem', color: '#fbbf24' }}>
                    ⚠️ {data.drift.warning}
                  </div>
                )}
                <ThesisStatusPanel thesis={data.drift.thesis} />
              </Section>
            )}
            </>}

            {/* ─────── ONGLET ACTION & CATALYSEURS ─────── */}
            {tab === 'action' && <>
            {data.entry_plan && (
              <EntryPlanSection plan={data.entry_plan} ticker={ticker.toUpperCase()} />
            )}

            {/* Price action + Support */}
            {data.price_action.available && (
              <Section title="Price action & Support">
                <Grid cols={4}>
                  <KV label="Cours" value={`$${fmtNum(data.price_action.current_price, 2)}`} />
                  <KV label="MA50" value={data.price_action.ma50 ? `$${fmtNum(data.price_action.ma50, 2)}` : '—'} />
                  <KV label="MA200" value={data.price_action.ma200 ? `$${fmtNum(data.price_action.ma200, 2)}` : '—'} />
                  <KV label="Beta" value={fmtNum(data.risk.beta, 2)} />

                  <KV label="52w high" value={`$${fmtNum(data.price_action.high_52w, 2)}`} />
                  <KV label="52w low" value={`$${fmtNum(data.price_action.low_52w, 2)}`} />
                  <KV label="Drawdown / high" value={`${fmtNum(data.price_action.drawdown_from_high_pct, 1)}%`}
                      tone={pctToneSigned(data.price_action.drawdown_from_high_pct)} />
                  <KV label="Momentum 6M" value={`${fmtNum(data.price_action.momentum_6m_pct, 1)}%`}
                      tone={pctToneSigned(data.price_action.momentum_6m_pct)} />

                  {data.support?.level && data.support.level !== 'INSUFFICIENT_DATA' && <>
                    <KV label="Support level" value={data.support.level.replace('_SUPPORT', '')}
                        tone={data.support.level === 'ON_SUPPORT' ? '#4ade80' : data.support.level === 'NEAR_SUPPORT' ? '#fbbf24' : 'var(--text-muted)'} />
                    <KV label="Score support" value={`${fmtNum(data.support.score, 0)} / 100`} />
                    <KV label="Swing low" value={data.support.nearest_swing_low ? `$${fmtNum(data.support.nearest_swing_low, 2)}` : '—'} />
                    <KV label="Volatility" value={`${fmtNum(data.risk.volatility_pct, 1)}%`} />
                  </>}
                </Grid>
              </Section>
            )}

            {/* Lot 16 — Revisions analystes */}
            {data.revisions && (data.revisions.score != null || data.revisions.upgrades_90d != null) && (
              <Section title="Révisions analystes (Seeking Alpha-style)">
                <Grid cols={4}>
                  <KV label="Score (rank)" value={fmtNum(data.revisions.score, 1)}
                      tone={data.revisions.score >= 70 ? '#4ade80' :
                            data.revisions.score < 30 ? '#f87171' : undefined} />
                  <KV label="Net score 90j" value={fmtNum(data.revisions.revisions_net_score, 2)}
                      hint="(up - down) / (up + down) sur 90 jours"
                      tone={pctToneSigned(data.revisions.revisions_net_score)} />
                  <KV label="Upgrades 30j" value={fmtNum(data.revisions.upgrades_30d, 0)}
                      tone={data.revisions.upgrades_30d > 0 ? '#4ade80' : undefined} />
                  <KV label="Downgrades 30j" value={fmtNum(data.revisions.downgrades_30d, 0)}
                      tone={data.revisions.downgrades_30d > 0 ? '#f87171' : undefined} />

                  <KV label="Upgrades 90j" value={fmtNum(data.revisions.upgrades_90d, 0)} />
                  <KV label="Downgrades 90j" value={fmtNum(data.revisions.downgrades_90d, 0)} />
                  <KV label="Data quality" value={fmtPct(data.revisions.data_quality, 0)} />
                </Grid>
              </Section>
            )}

            {/* Lot 17 — Insider Activity (SEC EDGAR Form 4) */}
            {data.insider && data.insider.n_filings != null && (
              <Section title="🏛️ Insider Activity (SEC EDGAR Form 4)">
                <Grid cols={4}>
                  <KV label="Score" value={fmtNum(data.insider.score, 1)}
                      tone={data.insider.score >= 70 ? '#4ade80' :
                            data.insider.score < 40 ? '#f87171' : undefined} />
                  <KV label="Filings 30j" value={fmtNum(data.insider.buy_count_30d, 0)}
                      tone={data.insider.buy_count_30d >= 3 ? '#4ade80' : undefined} />
                  <KV label="Insiders distincts 30j" value={fmtNum(data.insider.distinct_30d, 0)}
                      tone={data.insider.distinct_30d >= 3 ? '#4ade80' : undefined} />
                  <KV label="Cluster buying" value={data.insider.cluster_buying ? '✅ OUI (3+/7j)' : '—'}
                      tone={data.insider.cluster_buying ? '#22c55e' : undefined} />

                  <KV label="Filings 90j (total)" value={fmtNum(data.insider.n_filings, 0)} />
                  <KV label="Dernier filing" value={data.insider.most_recent || '—'} />
                  <KV label="Data quality" value={fmtPct(data.insider.data_quality, 0)} />
                </Grid>
                <div style={{ marginTop: 8, fontSize: '0.62rem', color: 'var(--text-muted)' }}>
                  Source : SEC EDGAR (data.sec.gov) — Form 4 transactions C-level/director, gratuit illimité.
                  Les filings sont publics sous 2 jours ouvrés. Cluster = 3+ filings dans une fenêtre 7j (signal smart-money fort).
                </div>
              </Section>
            )}

            {/* SEC EDGAR filings (10-K, 10-Q, 8-K, Form 4, 13F-HR…) */}
            <SecFilingsSection ticker={ticker.toUpperCase()} />

            {/* Lot 16 — Earnings Surprise (PEAD) */}
            {data.earnings_surprise && data.earnings_surprise.level !== 'INSUFFICIENT_DATA' && (
              <Section title="Earnings Surprise (PEAD signal)">
                <Grid cols={4}>
                  <KV label="Niveau" value={data.earnings_surprise.level?.replace('_', ' ') || '—'}
                      tone={SURPRISE_LEVEL_TONE[data.earnings_surprise.level]} />
                  <KV label="Score" value={fmtNum(data.earnings_surprise.score, 0)} />
                  <KV label="Beat rate 8Q" value={fmtPct(data.earnings_surprise.beat_rate_8q, 0)}
                      tone={data.earnings_surprise.beat_rate_8q > 0.6 ? '#4ade80' :
                            data.earnings_surprise.beat_rate_8q < 0.4 ? '#f87171' : undefined} />
                  <KV label="Surprise avg 4Q" value={fmtPct(data.earnings_surprise.surprise_avg_4q / 100, 1)}
                      tone={pctToneSigned(data.earnings_surprise.surprise_avg_4q)}
                      hint="Surprise moyenne sur les 4 derniers trimestres" />
                  <KV label="Surprise dernier Q" value={fmtPct(data.earnings_surprise.surprise_pct_last / 100, 1)}
                      tone={pctToneSigned(data.earnings_surprise.surprise_pct_last)} />
                </Grid>
              </Section>
            )}

            {/* Lot 16 — Dividend Safety scorecard */}
            {data.dividend_safety && data.dividend_safety.level !== 'NO_DIVIDEND' && data.dividend_safety.score != null && (
              <Section title="Dividend Safety — scorecard 4-axis">
                <Grid cols={4}>
                  <KV label="Score" value={`${fmtNum(data.dividend_safety.score, 0)} / 100`}
                      tone={DIVIDEND_LEVEL_TONE[data.dividend_safety.level]} />
                  <KV label="Niveau" value={data.dividend_safety.level?.replace('_', ' ') || '—'}
                      tone={DIVIDEND_LEVEL_TONE[data.dividend_safety.level]} />
                  <KV label="Payout ratio" value={fmtPct(data.dividend_safety.raw?.payout_ratio, 0)}
                      tone={data.dividend_safety.raw?.payout_ratio > 0.8 ? '#fbbf24' :
                            data.dividend_safety.raw?.payout_ratio < 0.5 ? '#4ade80' : undefined} />
                  <KV label="Yield 5Y avg" value={fmtPct(data.dividend_safety.raw?.five_year_avg_dividend_yield, 2)} />

                  <KV label="Axe payout" value={fmtNum(data.dividend_safety.components?.payout, 0)} />
                  <KV label="Axe FCF cover" value={fmtNum(data.dividend_safety.components?.fcf_cover, 0)} />
                  <KV label="Axe yield stab." value={fmtNum(data.dividend_safety.components?.yield_stability, 0)} />
                  <KV label="Axe history" value={fmtNum(data.dividend_safety.components?.history, 0)} />
                </Grid>
              </Section>
            )}
            </>}

            {/* ─────── ONGLET FONDAMENTAUX ─────── */}
            {tab === 'fundamentals' && <>
            {/* Valuation */}
            <Section title="Valuation">
              <Grid cols={4}>
                <KV label="P/E TTM" value={fmtNum(data.valuation.trailing_pe, 1)}
                    tone={data.valuation.trailing_pe > 30 ? '#fbbf24' : data.valuation.trailing_pe < 0 ? '#f87171' : undefined} />
                <KV label="Fwd P/E" value={fmtNum(data.valuation.forward_pe, 1)} />
                <KV label="EV/EBITDA" value={fmtNum(data.valuation.ev_to_ebitda, 1)} />
                <KV label="EV/Revenue" value={fmtNum(data.valuation.ev_to_revenue, 1)} />

                <KV label="P/B" value={fmtNum(data.valuation.price_to_book, 2)} />
                <KV label="PEG" value={fmtNum(data.valuation.peg_ratio, 2)}
                    tone={data.valuation.peg_ratio > 0 && data.valuation.peg_ratio < 1 ? '#4ade80' : undefined}
                    hint="PEG < 1 = potentiellement undervalued vs growth" />
                <KV label="Div yield" value={fmtPct(data.valuation.dividend_yield, 2)} />
              </Grid>
            </Section>

            {/* Quality */}
            <Section title="Profitabilité & Qualité comptable">
              <Grid cols={4}>
                <KV label="ROE" value={fmtPct(data.quality.return_on_equity, 1)}
                    tone={data.quality.return_on_equity > 0.15 ? '#4ade80' : data.quality.return_on_equity < 0 ? '#f87171' : undefined} />
                <KV label="ROA" value={fmtPct(data.quality.return_on_assets, 1)}
                    tone={data.quality.return_on_assets > 0.10 ? '#4ade80' : data.quality.return_on_assets < 0 ? '#f87171' : undefined} />
                <KV label="ROA Y-1" value={fmtPct(data.quality.return_on_assets_prev_year, 1)} />
                <KV label="Net margin" value={fmtPct(data.quality.profit_margin, 1)}
                    tone={data.quality.profit_margin < 0 ? '#f87171' : undefined} />

                <KV label="Op margin" value={fmtPct(data.quality.operating_margin, 1)} />
                <KV label="Gross margin" value={fmtPct(data.quality.gross_margin, 1)} />
                <KV label="GM Y-1" value={fmtPct(data.quality.gross_margin_prev_year, 1)} />
                <KV label="FCF" value={fmtMarketCap(data.quality.free_cash_flow)} />
              </Grid>
            </Section>

            {/* Health */}
            <Section title="Bilan & santé financière">
              <Grid cols={4}>
                <KV label="Debt/Equity" value={fmtNum(data.health.debt_to_equity, 1)}
                    tone={data.health.debt_to_equity > 200 ? '#f87171' : data.health.debt_to_equity < 50 ? '#4ade80' : undefined} />
                <KV label="D/E Y-1" value={fmtNum(data.health.debt_to_equity_prev_year, 1)} />
                <KV label="Current ratio" value={fmtNum(data.health.current_ratio, 2)}
                    tone={data.health.current_ratio < 1 ? '#f87171' : data.health.current_ratio > 2 ? '#4ade80' : undefined} />
                <KV label="Quick ratio" value={fmtNum(data.health.quick_ratio, 2)} />
              </Grid>
            </Section>

            {/* Growth */}
            <Section title="Croissance">
              <Grid cols={4}>
                <KV label="Revenue growth" value={fmtPct(data.growth.revenue_growth, 1)}
                    tone={pctToneSigned(data.growth.revenue_growth)} />
                <KV label="Earnings growth" value={fmtPct(data.growth.earnings_growth, 1)}
                    tone={pctToneSigned(data.growth.earnings_growth)} />
                <KV label="EPS Q/Q" value={fmtPct(data.growth.earnings_quarterly_growth, 1)}
                    tone={pctToneSigned(data.growth.earnings_quarterly_growth)} />
                <KV label="Shares O/S Y-1" value={fmtNum(data.growth.shares_outstanding_prev_year, 0)} />
              </Grid>
            </Section>
            </>}

            {/* ─────── ONGLET PEERS & ANALYSTES ─────── */}
            {tab === 'peers' && <>
            {/* Analystes */}
            {data.analysts.num_analysts > 0 && (
              <Section title="Consensus analystes">
                <Grid cols={4}>
                  <KV label="N analystes" value={fmtNum(data.analysts.num_analysts, 0)} />
                  <KV label="Reco" value={data.analysts.recommendation_key?.replace?.('_', ' ') || '—'}
                      tone={['strong_buy','buy'].includes(data.analysts.recommendation_key) ? '#4ade80' :
                            ['sell','strong_sell'].includes(data.analysts.recommendation_key) ? '#f87171' : undefined} />
                  <KV label="Reco mean" value={fmtNum(data.analysts.recommendation_mean, 2)}
                      hint="1=strong buy, 5=strong sell" />
                  <KV label="Target mean" value={data.analysts.price_target_mean ? `$${fmtNum(data.analysts.price_target_mean, 2)}` : '—'} />

                  <KV label="Target high" value={data.analysts.price_target_high ? `$${fmtNum(data.analysts.price_target_high, 2)}` : '—'} />
                  <KV label="Target low" value={data.analysts.price_target_low ? `$${fmtNum(data.analysts.price_target_low, 2)}` : '—'} />
                </Grid>
              </Section>
            )}

            {/* Lot 16 — Peer Comparison */}
            <Section title="Peer Comparison — comparaison sectorielle">
              <PeerComparison ticker={ticker} />
            </Section>
            </>}

            {/* ─────── ONGLET NEWS ─────── */}
            {tab === 'news' && <>
            <NewsSection ticker={ticker.toUpperCase()} />
            </>}

            {/* ─────── ONGLET NOTES ─────── */}
            {tab === 'notes' && <>
            <NotesSection ticker={ticker.toUpperCase()} />

            {/* Meta */}
            <Section title="Source des données">
              <Grid cols={4}>
                <KV label="Provider" value={data.meta.source_provider || '—'} />
                <KV label="Fetched at" value={data.meta.fetched_at?.slice?.(0, 16) || '—'} />
                <KV label="Data quality" value={fmtPct(data.meta.data_quality, 0)} />
                <KV label="DQ coef" value={fmtNum(data.meta.data_quality_coef, 2)} />
              </Grid>
              {data.meta.backfill_fields && Object.keys(data.meta.backfill_fields || {}).length > 0 && (
                <div style={{ marginTop: 6, fontSize: '0.65rem', color: 'var(--text-muted)' }}>
                  Backfill : {Object.keys(data.meta.backfill_fields).join(', ')}
                </div>
              )}
            </Section>
            </>}
          </>}
        </div>
      </div>
    </div>
  );
}
