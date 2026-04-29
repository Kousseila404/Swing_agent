/**
 * TickerAnalysisModal — Factsheet structurée d'un ticker.
 *
 * Click sur un ticker dans ProposalsPage → modal centré avec sections :
 * Identité / TITAN / Valuation / Quality / Health / Growth / Analystes /
 * Price action / Support / Drift / Flags / Meta.
 *
 * 100 % données existantes (no IA, no API externe). Endpoint /api/ticker_analysis/{ticker}.
 */
import { useEffect, useMemo, useState } from 'react';

import { addPriceAlert, fetchTickerAnalysis } from '../api/client.js';
import {
  useAddNote,
  useAddToWatchlist,
  useDeleteNote,
  useNews,
  useNotes,
  useSecFilings,
  useUpdateNote,
} from '../hooks/useApi.js';
import { fmtMarketCap, fmtNum, fmtPct } from '../utils/format.js';
import PeerComparison from './PeerComparison.jsx';
import TickerPriceChart from './common/TickerPriceChart.jsx';
import TitanScoreChart from './common/TitanScoreChart.jsx';
import TradingViewWidget from './common/TradingViewWidget.jsx';

const FLAG_PALETTE = {
  ok:     { bg: 'rgba(34,197,94,0.15)',  fg: '#4ade80', border: 'rgba(34,197,94,0.5)'  },
  warn:   { bg: 'rgba(251,191,36,0.15)', fg: '#fbbf24', border: 'rgba(251,191,36,0.5)' },
  danger: { bg: 'rgba(248,113,113,0.15)',fg: '#f87171', border: 'rgba(248,113,113,0.5)' },
  info:   { bg: 'rgba(96,165,250,0.12)', fg: '#60a5fa', border: 'rgba(96,165,250,0.45)' },
};

// Lot 16 — palette pour les Factor Grades (lettres A+/A/B+/B/C+/C/D/F).
const GRADE_PALETTE = {
  'A+': { bg: 'rgba(34,197,94,0.20)',  fg: '#22c55e' },
  'A':  { bg: 'rgba(34,197,94,0.15)',  fg: '#4ade80' },
  'B+': { bg: 'rgba(132,204,22,0.18)', fg: '#a3e635' },
  'B':  { bg: 'rgba(132,204,22,0.12)', fg: '#bef264' },
  'C+': { bg: 'rgba(251,191,36,0.18)', fg: '#fbbf24' },
  'C':  { bg: 'rgba(251,191,36,0.12)', fg: '#fcd34d' },
  'D':  { bg: 'rgba(248,113,113,0.15)',fg: '#fb7185' },
  'F':  { bg: 'rgba(248,113,113,0.20)',fg: '#f87171' },
  'N/A':{ bg: 'rgba(148,163,184,0.10)',fg: 'var(--text-muted)' },
};

// Quant Rating (TITAN composite mapping).
const RATING_LABEL = {
  STRONG_BUY:   { label: 'STRONG BUY',   bg: 'rgba(34,197,94,0.20)',  fg: '#22c55e' },
  BUY:          { label: 'BUY',          bg: 'rgba(132,204,22,0.18)', fg: '#a3e635' },
  HOLD:         { label: 'HOLD',         bg: 'rgba(251,191,36,0.16)', fg: '#fbbf24' },
  SELL:         { label: 'SELL',         bg: 'rgba(251,113,113,0.18)',fg: '#fb7185' },
  STRONG_SELL:  { label: 'STRONG SELL',  bg: 'rgba(248,113,113,0.22)',fg: '#f87171' },
  'N/A':        { label: 'N/A',          bg: 'rgba(148,163,184,0.12)',fg: 'var(--text-muted)' },
};

const DIVIDEND_LEVEL_TONE = {
  VERY_SAFE: '#22c55e', SAFE: '#4ade80', MODERATE: '#fbbf24',
  RISKY: '#fb7185', UNSAFE: '#f87171',
  NO_DIVIDEND: 'var(--text-muted)', INSUFFICIENT_DATA: 'var(--text-muted)',
};

const SURPRISE_LEVEL_TONE = {
  STRONG_BEAT: '#22c55e', BEAT: '#4ade80', INLINE: 'var(--text-muted)',
  MISS: '#fb7185', STRONG_MISS: '#f87171', INSUFFICIENT_DATA: 'var(--text-muted)',
};

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
      display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
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
const BUY_SIGNAL_PALETTE = {
  STRONG_BUY:        { bg: 'rgba(34,197,94,0.22)',  fg: '#22c55e', icon: '🟢🟢', big: true },
  BUY:               { bg: 'rgba(132,204,22,0.20)', fg: '#84cc16', icon: '🟢',   big: true },
  WATCH:             { bg: 'rgba(251,191,36,0.18)', fg: '#fbbf24', icon: '👁️',   big: false },
  EARNINGS_BLACKOUT: { bg: 'rgba(248,113,113,0.20)',fg: '#f87171', icon: '⏸️',   big: false },
  CHEAP_JUNK:        { bg: 'rgba(248,113,113,0.20)',fg: '#f87171', icon: '⚠️',   big: false },
  FALLING_KNIFE:     { bg: 'rgba(248,113,113,0.20)',fg: '#f87171', icon: '🔻',   big: false },
  SKIP:              { bg: 'rgba(148,163,184,0.10)',fg: 'var(--text-muted)', icon: '—', big: false },
  NO_DATA:           { bg: 'rgba(148,163,184,0.08)',fg: 'var(--text-muted)', icon: '?', big: false },
};

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
const ENTRY_RECO_PALETTE = {
  WAIT_PULLBACK: { bg: 'rgba(248,113,113,0.18)', fg: '#f87171', icon: '⏸️',
                   label: 'Wait pullback' },
  SPLIT_3:       { bg: 'rgba(251,191,36,0.18)',  fg: '#fbbf24', icon: '📊',
                   label: 'Entry fractionnée (3 tranches)' },
  SPLIT_2:       { bg: 'rgba(132,204,22,0.16)',  fg: '#a3e635', icon: '⚖️',
                   label: 'Entry fractionnée (2 tranches)' },
  MARKET_FULL:   { bg: 'rgba(34,197,94,0.18)',   fg: '#22c55e', icon: '🟢',
                   label: 'Market — full size' },
};

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

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
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
const THESIS_PALETTE = {
  INTACT:  { bg: 'rgba(34,197,94,0.16)',   fg: '#22c55e', icon: '🟢',
             label: 'Thèse intacte' },
  WARN:    { bg: 'rgba(251,191,36,0.18)',  fg: '#fbbf24', icon: '⚠️',
             label: 'Thèse en alerte' },
  BROKEN:  { bg: 'rgba(248,113,113,0.20)', fg: '#f87171', icon: '🔴',
             label: 'Thèse cassée — exit recommandé' },
  NO_DATA: { bg: 'rgba(148,163,184,0.10)', fg: 'var(--text-muted)', icon: '?',
             label: 'Pas d\'entry scores' },
};

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

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: '0.8rem' }}>
      <h3 style={{
        fontSize: '0.7rem', fontWeight: 800, letterSpacing: '0.08em',
        textTransform: 'uppercase', color: 'var(--text-muted)',
        marginBottom: '0.4rem', borderBottom: '1px solid var(--border)',
        paddingBottom: '0.25rem',
      }}>{title}</h3>
      {children}
    </div>
  );
}

function KV({ label, value, hint, tone }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0 }}>
      <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)',
                     letterSpacing: '0.03em' }}>{label}</span>
      <span style={{
        fontSize: '0.85rem', fontFamily: 'monospace', fontWeight: 600,
        color: tone || 'var(--text-primary)',
      }} title={hint || ''}>{value}</span>
    </div>
  );
}

function Grid({ children, cols = 4 }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
      gap: '0.6rem 1rem',
    }}>{children}</div>
  );
}

function pctToneSigned(v) {
  if (v == null || !Number.isFinite(v)) return 'var(--text-muted)';
  return v >= 0 ? 'var(--success)' : 'var(--danger)';
}

function pctToneFromGoodness(v, goodHigh = true) {
  if (v == null || !Number.isFinite(v)) return 'var(--text-muted)';
  return goodHigh ? (v >= 0 ? '#4ade80' : '#f87171') : (v <= 0 ? '#4ade80' : '#f87171');
}

// Form palette pour les filings SEC.
const FORM_TONE = {
  '10-K':   { bg: 'rgba(34,197,94,0.18)',  fg: '#4ade80', icon: '📘' },
  '10-K/A': { bg: 'rgba(34,197,94,0.12)',  fg: '#4ade80', icon: '📘' },
  '10-Q':   { bg: 'rgba(132,204,22,0.16)', fg: '#a3e635', icon: '📗' },
  '10-Q/A': { bg: 'rgba(132,204,22,0.12)', fg: '#a3e635', icon: '📗' },
  '8-K':    { bg: 'rgba(251,191,36,0.18)', fg: '#fbbf24', icon: '⚡' },
  '8-K/A':  { bg: 'rgba(251,191,36,0.12)', fg: '#fbbf24', icon: '⚡' },
  '4':      { bg: 'rgba(168,85,247,0.16)', fg: '#c084fc', icon: '🏛️' },
  '4/A':    { bg: 'rgba(168,85,247,0.12)', fg: '#c084fc', icon: '🏛️' },
  '13F-HR': { bg: 'rgba(96,165,250,0.16)', fg: '#60a5fa', icon: '🐋' },
  'DEF 14A':{ bg: 'rgba(148,163,184,0.16)', fg: 'var(--text-muted)', icon: '🗳️' },
  'S-1':    { bg: 'rgba(248,113,113,0.16)', fg: '#fb7185', icon: '🚀' },
  '20-F':   { bg: 'rgba(34,197,94,0.16)',   fg: '#4ade80', icon: '🌐' },
};

function SecFilingsSection({ ticker }) {
  const [formFilter, setFormFilter] = useState('all');
  const filingsQ = useSecFilings(ticker, 50);
  const data = filingsQ.data || {};
  const filings = data.filings || [];

  const filtered = useMemo(() => {
    if (formFilter === 'all') return filings;
    if (formFilter === 'reports') {
      return filings.filter(f => /^10-[KQ]/.test(f.form));
    }
    if (formFilter === 'events') {
      return filings.filter(f => /^8-K/.test(f.form));
    }
    if (formFilter === 'insider') {
      return filings.filter(f => /^4/.test(f.form));
    }
    return filings;
  }, [filings, formFilter]);

  return (
    <Section title={`📂 Filings SEC EDGAR (${data.n_filings ?? 0})`}>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap',
                    marginBottom: 8, alignItems: 'center' }}>
        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
          Filtre :
        </span>
        {[
          { id: 'all',     label: 'Tout' },
          { id: 'reports', label: '10-K/Q' },
          { id: 'events',  label: '8-K' },
          { id: 'insider', label: 'Insider' },
        ].map(f => (
          <button key={f.id}
                  type="button"
                  onClick={() => setFormFilter(f.id)}
                  className={`scan-filter-btn ${formFilter === f.id ? 'active' : ''}`}
                  style={{ fontSize: '0.7rem', padding: '0.2rem 0.55rem' }}>
            {f.label}
          </button>
        ))}
        {data.cik && (
          <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)',
                         marginLeft: 'auto', fontFamily: 'monospace' }}>
            CIK {data.cik}
          </span>
        )}
      </div>

      {filingsQ.isLoading && (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)',
                      padding: '0.5rem' }}>
          Chargement filings…
        </div>
      )}

      {data.error && (
        <div style={{
          padding: '0.6rem', borderRadius: 6, fontSize: '0.78rem',
          background: 'rgba(248,113,113,0.08)', color: '#fb7185',
          border: '1px solid rgba(248,113,113,0.3)',
        }}>
          ⚠️ {data.error === 'cik_unknown'
            ? "Ticker introuvable dans l'index SEC EDGAR."
            : data.error}
        </div>
      )}

      {!filingsQ.isLoading && filtered.length === 0 && !data.error && (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)',
                      padding: '0.5rem' }}>
          📭 Aucun filing pour ce filtre.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {filtered.map(f => {
          const meta = FORM_TONE[f.form] || {
            bg: 'rgba(148,163,184,0.10)', fg: 'var(--text-muted)', icon: '📄',
          };
          return (
            <a key={f.accession + f.form}
               href={f.url} target="_blank" rel="noopener noreferrer"
               style={{
                 display: 'flex', alignItems: 'center', gap: 10,
                 padding: '0.4rem 0.7rem',
                 background: 'var(--bg-tertiary)',
                 borderRadius: 5,
                 border: '1px solid var(--border)',
                 textDecoration: 'none', color: 'inherit',
                 transition: 'border-color 0.15s, background 0.15s',
               }}
               onMouseEnter={e => {
                 e.currentTarget.style.borderColor = 'var(--accent-primary)';
               }}
               onMouseLeave={e => {
                 e.currentTarget.style.borderColor = 'var(--border)';
               }}>
              <span style={{
                fontSize: '0.65rem', fontWeight: 800, letterSpacing: '0.04em',
                padding: '0.2rem 0.5rem', borderRadius: 4,
                background: meta.bg, color: meta.fg,
                minWidth: 78, textAlign: 'center', fontFamily: 'monospace',
              }}>
                {meta.icon} {f.form}
              </span>
              <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                {f.form_label}
              </span>
              <span style={{ marginLeft: 'auto', display: 'flex', gap: 12,
                             alignItems: 'center', fontSize: '0.74rem' }}>
                <span style={{ fontFamily: 'monospace', color: 'var(--text-main)' }}>
                  {f.date}
                </span>
                <span style={{ color: 'var(--text-muted)', minWidth: 60,
                               textAlign: 'right' }}>
                  {f.days_ago === 0 ? "auj." : `il y a ${f.days_ago}j`}
                </span>
                <span style={{ color: 'var(--accent-primary)', fontSize: '0.85rem' }}>
                  ↗
                </span>
              </span>
            </a>
          );
        })}
      </div>
    </Section>
  );
}

function relTime(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    const diffMs = Date.now() - d.getTime();
    const m = Math.round(diffMs / 60_000);
    if (m < 60)  return `il y a ${m}min`;
    const h = Math.round(m / 60);
    if (h < 24)  return `il y a ${h}h`;
    const j = Math.round(h / 24);
    return `il y a ${j}j`;
  } catch { return iso; }
}

function NewsSection({ ticker }) {
  const [days, setDays] = useState(14);
  const newsQ = useNews(ticker, days);
  const data = newsQ.data || {};
  const articles = data.articles || [];

  return (
    <Section title={`📰 News (${data.n_articles ?? 0})`}>
      <div style={{ display: 'flex', gap: 6, marginBottom: 10,
                    justifyContent: 'flex-end', alignItems: 'center' }}>
        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
          Fenêtre :
        </span>
        {[7, 14, 30].map(d => (
          <button key={d} type="button"
                  onClick={() => setDays(d)}
                  className={`scan-filter-btn ${days === d ? 'active' : ''}`}
                  style={{ fontSize: '0.7rem', padding: '0.2rem 0.55rem' }}>
            {d}j
          </button>
        ))}
        {data.cached && (
          <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)',
                         marginLeft: 6 }} title="Servi depuis le cache 1h">
            ⚡ cache
          </span>
        )}
      </div>

      {newsQ.isLoading && (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)',
                      padding: '1rem', textAlign: 'center' }}>
          Chargement news…
        </div>
      )}

      {data.error && (
        <div style={{
          padding: '0.7rem', borderRadius: 6, fontSize: '0.78rem',
          background: 'rgba(248,113,113,0.08)', color: '#fb7185',
          border: '1px solid rgba(248,113,113,0.3)',
        }}>
          ⚠️ {data.error}
          {data.error.includes('FINNHUB_API_KEY') && (
            <div style={{ marginTop: 4, fontSize: '0.7rem',
                          color: 'var(--text-muted)' }}>
              Configure <code>FINNHUB_API_KEY</code> dans <code>backend/.env</code> puis
              redémarre <code>swing-api.service</code>.
            </div>
          )}
        </div>
      )}

      {!newsQ.isLoading && articles.length === 0 && !data.error && (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)',
                      padding: '1rem', textAlign: 'center' }}>
          📭 Aucune news sur les {days} derniers jours.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {articles.map(a => (
          <a key={a.id || a.url}
             href={a.url} target="_blank" rel="noopener noreferrer"
             style={{
               display: 'flex', gap: 10, padding: '0.65rem 0.8rem',
               background: 'var(--bg-tertiary)', borderRadius: 6,
               border: '1px solid var(--border)',
               textDecoration: 'none', color: 'inherit',
               transition: 'background 0.15s, border-color 0.15s',
             }}
             onMouseEnter={e => {
               e.currentTarget.style.borderColor = 'var(--accent-primary)';
             }}
             onMouseLeave={e => {
               e.currentTarget.style.borderColor = 'var(--border)';
             }}>
            {a.image && (
              <img src={a.image} alt=""
                   loading="lazy"
                   onError={(e) => { e.currentTarget.style.display = 'none'; }}
                   style={{
                     width: 64, height: 48, objectFit: 'cover',
                     borderRadius: 4, flexShrink: 0,
                   }} />
            )}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{
                fontSize: '0.85rem', fontWeight: 600, lineHeight: 1.35,
                color: 'var(--text-main)',
              }}>
                {a.headline}
              </div>
              {a.summary && (
                <div style={{
                  fontSize: '0.74rem', color: 'var(--text-muted)',
                  marginTop: 3, lineHeight: 1.4,
                  display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                }}>
                  {a.summary}
                </div>
              )}
              <div style={{
                fontSize: '0.66rem', color: 'var(--text-muted)',
                marginTop: 4, display: 'flex', gap: 8, alignItems: 'center',
              }}>
                {a.source && (
                  <span style={{
                    fontWeight: 700, color: 'var(--accent-primary)',
                  }}>
                    {a.source}
                  </span>
                )}
                {a.datetime && <span>· {relTime(a.datetime)}</span>}
                {a.category && (
                  <span style={{
                    border: '1px solid var(--border)', borderRadius: 3,
                    padding: '0 5px', textTransform: 'capitalize',
                  }}>
                    {a.category}
                  </span>
                )}
              </div>
            </div>
          </a>
        ))}
      </div>
    </Section>
  );
}

function NotesSection({ ticker }) {
  const notesQ    = useNotes(ticker);
  const addMut    = useAddNote();
  const updateMut = useUpdateNote();
  const deleteMut = useDeleteNote();
  const watchMut  = useAddToWatchlist();

  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState(null); // { id, body } | null

  const notes = notesQ.data?.notes || [];

  const submitNew = (e) => {
    e.preventDefault();
    const body = draft.trim();
    if (!body) return;
    addMut.mutate({ ticker, body }, {
      onSuccess: (res) => { if (res?.ok !== false) setDraft(''); },
    });
  };

  const saveEdit = () => {
    if (!editing) return;
    const body = (editing.body || '').trim();
    if (!body) return;
    updateMut.mutate({ id: editing.id, body }, {
      onSuccess: (res) => { if (res?.ok !== false) setEditing(null); },
    });
  };

  const remove = (note) => {
    if (!window.confirm('Supprimer cette note ?')) return;
    deleteMut.mutate({ id: note.id, ticker });
  };

  const addToWatch = () => {
    watchMut.mutate({ ticker });
  };

  return (
    <Section title={`📝 Notes (${notes.length})`}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 6 }}>
        <button
          type="button"
          className="scan-filter-btn"
          onClick={addToWatch}
          disabled={watchMut.isPending}
          style={{ fontSize: '0.7rem' }}
          title="Ajouter ce ticker à la watchlist"
        >
          {watchMut.isPending ? '⏳' : '👁'} Watchlist
        </button>
      </div>

      <form onSubmit={submitNew} style={{ marginBottom: 10 }}>
        <textarea
          value={draft}
          onChange={e => setDraft(e.target.value)}
          placeholder="Thèse, observations, rappel earnings… (markdown libre)"
          rows={3}
          style={{
            width: '100%', resize: 'vertical', fontFamily: 'inherit',
            padding: '0.55rem 0.7rem', fontSize: '0.82rem',
            background: 'var(--bg-tertiary)', color: 'var(--text-main)',
            border: '1px solid var(--border)', borderRadius: 6,
          }}
        />
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 6 }}>
          <button
            type="submit"
            className="action-btn"
            disabled={addMut.isPending || !draft.trim()}
            style={{ padding: '0.4rem 0.85rem', fontSize: '0.78rem' }}
          >
            {addMut.isPending ? '⏳' : '+ Ajouter note'}
          </button>
        </div>
      </form>

      {notesQ.isLoading && (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
          Chargement notes…
        </div>
      )}

      {notes.length === 0 && !notesQ.isLoading && (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)',
                      textAlign: 'center', padding: '1rem' }}>
          📭 Aucune note pour ce ticker.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {notes.map(n => (
          <div key={n.id} style={{
            background: 'var(--bg-tertiary)', padding: '0.55rem 0.7rem',
            borderRadius: 6, border: '1px solid var(--border)',
            fontSize: '0.82rem',
          }}>
            {editing?.id === n.id ? (
              <>
                <textarea
                  value={editing.body}
                  onChange={e => setEditing(s => ({ ...s, body: e.target.value }))}
                  rows={3}
                  style={{
                    width: '100%', resize: 'vertical', fontFamily: 'inherit',
                    padding: '0.4rem 0.55rem', fontSize: '0.82rem',
                    background: 'var(--panel-bg)', color: 'var(--text-main)',
                    border: '1px solid var(--border)', borderRadius: 4,
                  }}
                />
                <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end',
                              marginTop: 6 }}>
                  <button type="button" className="scan-filter-btn"
                          onClick={() => setEditing(null)}
                          style={{ fontSize: '0.7rem' }}>
                    Annuler
                  </button>
                  <button type="button" className="action-btn"
                          onClick={saveEdit}
                          disabled={updateMut.isPending || !editing.body.trim()}
                          style={{ fontSize: '0.7rem', padding: '0.3rem 0.7rem' }}>
                    {updateMut.isPending ? '⏳' : '💾 Enregistrer'}
                  </button>
                </div>
              </>
            ) : (
              <>
                <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                              lineHeight: 1.45, marginBottom: 6 }}>
                  {n.body}
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between',
                              alignItems: 'center', fontSize: '0.66rem',
                              color: 'var(--text-muted)' }}>
                  <span>
                    {n.updated_at?.slice(0, 16)?.replace('T', ' ') || '—'}
                    {n.updated_at !== n.created_at && ' (modifiée)'}
                  </span>
                  <span style={{ display: 'flex', gap: 4 }}>
                    <button type="button"
                            className="scan-filter-btn"
                            onClick={() => setEditing({ id: n.id, body: n.body })}
                            style={{ fontSize: '0.65rem', padding: '0.15rem 0.45rem' }}>
                      ✎
                    </button>
                    <button type="button"
                            className="scan-filter-btn"
                            onClick={() => remove(n)}
                            style={{ fontSize: '0.65rem', padding: '0.15rem 0.45rem',
                                     color: 'var(--danger)',
                                     borderColor: 'rgba(239,68,68,0.3)' }}>
                      🗑
                    </button>
                  </span>
                </div>
              </>
            )}
          </div>
        ))}
      </div>
    </Section>
  );
}

// ─── Onglets ─────────────────────────────────────────────────────
const MODAL_TABS = [
  { id: 'overview',     icon: '🎯', label: 'Aperçu' },
  { id: 'fundamentals', icon: '💰', label: 'Fondamentaux' },
  { id: 'action',       icon: '📈', label: 'Action & catalyseurs' },
  { id: 'peers',        icon: '🤝', label: 'Peers & analystes' },
  { id: 'news',         icon: '📰', label: 'News' },
  { id: 'notes',        icon: '📝', label: 'Notes' },
];

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
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState('overview');
  const [chartMode, setChartMode] = useState('compact'); // 'compact' | 'tradingview'

  useEffect(() => {
    if (!ticker) return;
    setLoading(true); setError(null); setData(null); setTab('overview');
    fetchTickerAnalysis(ticker)
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e?.message || String(e)); setLoading(false); });
  }, [ticker]);

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
