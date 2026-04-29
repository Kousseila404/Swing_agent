// ProposalsPage v2 — surface unique d'achat (fusion Propositions + Recommandations).
//
// Flow utilisateur :
//   1. Cron ou bouton "Générer un plan" → POST /proposals/refresh
//      → propositions pending arrivent dans la file (TTL 36h).
//   2. Revue ligne à ligne dans la table (Entry/SL/TP/Size éditables),
//      cocher les positions à approuver, acquitter les warnings over-cap
//      + top-up selon besoin.
//   3. Sticky ActionBar :
//        ✓ Approuver sélection → POST /proposals/approve_batch
//        ✕ Rejeter sélection  → POST /proposals/reject_batch
//   4. Les 7 gates fail-closed (killswitch/CB/régime/universe/slots/cash)
//      s'affichent en permanence en haut : toute gate rouge disable Approuver.

import { useMemo, useRef, useState } from 'react';
import {
  useApproveProposalsBatch,
  useMarketStatus,
  useProposals,
  useRefreshProposals,
  useRegenerateProposals,
  useRejectProposalsBatch,
} from '../hooks/useApi';
import { fmtNum, fmtPctRaw, fmtPrice, fmtSignedPct } from '../utils/format';
import { factorColor } from '../utils/colors';
import ApiErrorBanner from './common/ApiErrorBanner';
import PresetBar from './common/PresetBar';
import TickerAnalysisModal from './TickerAnalysisModal';
import TickerSpark from './common/TickerSpark';
import { loadProposalDefaults } from '../utils/preferences';

// Lot 14 — palette tilt flags (qarp/garp/consistent/cheap-junk/falling-knife).
const TILT_PALETTE = {
  qarp:          { bg: 'rgba(34,197,94,0.18)',  fg: '#22c55e', label: 'QARP',
                   title: 'Quality At Reasonable Price (boost composite)' },
  garp:          { bg: 'rgba(132,204,22,0.16)', fg: '#a3e635', label: 'GARP',
                   title: 'Growth At Reasonable Price (boost composite)' },
  consistent:    { bg: 'rgba(96,165,250,0.16)', fg: '#60a5fa', label: 'CONSISTENT',
                   title: 'Métriques fondamentales consistantes (boost)' },
  cheap_junk:    { bg: 'rgba(248,113,113,0.18)',fg: '#f87171', label: 'CHEAP JUNK',
                   title: 'Bas prix mais qualité dégradée (pénalité)' },
  falling_knife: { bg: 'rgba(248,113,113,0.18)',fg: '#fb7185', label: 'KNIFE',
                   title: 'Falling knife : dump récent + tendance neg (pénalité)' },
};

function TiltBadges({ flags }) {
  if (!Array.isArray(flags) || flags.length === 0) return null;
  return (
    <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
      {flags.map(f => {
        const p = TILT_PALETTE[f] || {
          bg: 'rgba(148,163,184,0.10)', fg: 'var(--text-muted)',
          label: f.toUpperCase(), title: f,
        };
        return (
          <span key={f} title={p.title} style={{
            fontSize: '0.6rem', fontWeight: 800, letterSpacing: '0.04em',
            padding: '0.08rem 0.4rem', borderRadius: 4,
            background: p.bg, color: p.fg,
          }}>{p.label}</span>
        );
      })}
    </span>
  );
}

const STATUS_FILTERS = [
  { id: 'pending',  label: 'À décider',   color: 'var(--accent)'     },
  { id: 'executed', label: 'Exécutées',   color: 'var(--success)'    },
  { id: 'rejected', label: 'Rejetées',    color: 'var(--danger)'     },
  { id: 'expired',  label: 'Expirées',    color: 'var(--text-muted)' },
  { id: null,       label: 'Toutes',      color: 'var(--text)'       },
];

const CAPITAL_PRESETS = [25_000, 50_000, 100_000, 250_000];

// ─────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────
function StatusBadge({ status }) {
  const palette = {
    pending:  { bg: 'rgba(59,130,246,0.15)',  fg: '#60a5fa' },
    approved: { bg: 'rgba(168,85,247,0.15)',  fg: '#c084fc' },
    executed: { bg: 'rgba(34,197,94,0.15)',   fg: '#4ade80' },
    rejected: { bg: 'rgba(239,68,68,0.15)',   fg: '#f87171' },
    expired:  { bg: 'rgba(148,163,184,0.15)', fg: '#94a3b8' },
  };
  const c = palette[status] || palette.expired;
  return (
    <span style={{
      background: c.bg, color: c.fg,
      padding: '2px 8px', borderRadius: 6,
      fontSize: '0.7rem', fontWeight: 600,
      textTransform: 'uppercase', letterSpacing: '0.5px',
    }}>{status}</span>
  );
}

function Pill({ ok, label, detail, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '4px 10px', borderRadius: 999,
        border: `1px solid ${ok ? 'rgba(34,197,94,0.4)' : 'rgba(239,68,68,0.5)'}`,
        background: ok ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)',
        color: ok ? '#4ade80' : '#f87171',
        fontSize: '0.75rem', fontWeight: 600, cursor: onClick ? 'pointer' : 'default',
      }}
      title={detail}
    >
      <span>{ok ? '✅' : '🔴'}</span>
      <span>{label}</span>
    </button>
  );
}

function StatusStrip({ lastRefresh, marketStatus, proposalsData }) {
  const gates  = lastRefresh?.gates || [];
  const diag   = lastRefresh?.diagnostics || {};
  const params = diag?.params || {};
  const portfolio = diag?.portfolio || {};

  const ran = lastRefresh?.ran_at;
  const ranDate = ran ? new Date(ran).toLocaleString() : '—';

  return (
    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12,
                    justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '1rem' }}>
            📬 {proposalsData?.n_pending ?? 0} à décider · {proposalsData?.n_total ?? 0} total
          </h2>
          <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 4 }}>
            Dernier refresh : {ranDate}
          </div>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
          <Pill
            ok={!marketStatus || marketStatus.is_open !== false}
            label={marketStatus?.is_open === false ? 'NYSE fermée' : 'NYSE ouverte'}
            detail={marketStatus?.next_open ? `Prochaine ouverture : ${marketStatus.next_open}` : ''}
          />
          {gates.map(g => (
            <Pill key={g.name} ok={g.ok} label={g.name} detail={g.detail} />
          ))}
        </div>
      </div>
      {(portfolio.n_open_positions !== undefined || params.total_capital) && (
        <div style={{
          marginTop: 12, display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
          gap: 10, fontSize: '0.78rem',
        }}>
          {params.total_capital != null && (
            <KpiCell label="Capital cible" value={`$${fmtNum(params.total_capital, 0)}`} />
          )}
          {portfolio.already_invested_usd != null && (
            <KpiCell
              label="Déjà investi"
              value={`$${fmtNum(portfolio.already_invested_usd, 0)}`}
              sub={`${portfolio.n_open_positions ?? 0} OPEN`}
            />
          )}
          {portfolio.available_budget_usd != null && (
            <KpiCell label="Budget dispo"
                     value={`$${fmtNum(portfolio.available_budget_usd, 0)}`} tone="pos" />
          )}
          {diag?.effective_multiplier != null && (
            <KpiCell
              label="Multiplicateur"
              value={fmtNum(diag.effective_multiplier, 2)}
              tone={diag.effective_multiplier === 0 ? 'neg' :
                    diag.effective_multiplier < 0.5 ? 'warn' : 'pos'}
            />
          )}
          {diag?.allocation?.n_candidates != null && (
            <KpiCell
              label="Candidats"
              value={diag.allocation.n_candidates}
              sub={`${diag.allocation.n_bullish ?? 0} bullish → ${diag.allocation.n_kept ?? 0} retenus`}
            />
          )}
        </div>
      )}
    </div>
  );
}

function KpiCell({ label, value, sub, tone }) {
  const color = tone === 'pos'  ? 'var(--success)' :
                tone === 'neg'  ? 'var(--danger)'  :
                tone === 'warn' ? 'var(--warning)' : 'var(--text)';
  return (
    <div style={{
      padding: '8px 10px', borderRadius: 8,
      background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border)',
    }}>
      <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)',
                    textTransform: 'uppercase', letterSpacing: '0.4px' }}>{label}</div>
      <div style={{ fontWeight: 700, color, fontFamily: 'monospace' }}>{value}</div>
      {sub && <small style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}>{sub}</small>}
    </div>
  );
}

function RefreshPanel({ onRun, onRegenerate, isPending, isRegenerating, nPending, defaults }) {
  // Priorité : params du dernier refresh (defaults props) > settings utilisateur > hard-coded.
  const userPrefs = loadProposalDefaults();
  const [capital, setCapital] = useState(defaults?.total_capital ?? userPrefs.total_capital);
  const [maxHoldings, setMaxHoldings] = useState(defaults?.max_holdings ?? userPrefs.max_holdings);
  const [mode, setMode] = useState(defaults?.top_n_mode ?? userPrefs.top_n_mode);
  const [allowFractional, setAllowFractional] = useState(
    defaults?.allow_fractional_shares ?? userPrefs.allow_fractional_shares,
  );
  const [includeHeld, setIncludeHeld] = useState(false);

  return (
    <div className="card" style={{ padding: 12, marginBottom: 12 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'flex-end' }}>
        <div>
          <label style={{ display: 'block', fontSize: '0.7rem',
                          color: 'var(--text-muted)', marginBottom: 3 }}>
            Capital cible
          </label>
          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            <span style={{ color: 'var(--text-muted)', fontFamily: 'monospace' }}>$</span>
            <input type="number" min="1000" step="1000"
                   value={capital}
                   onChange={e => setCapital(parseFloat(e.target.value) || 0)}
                   className="mini-input"
                   style={{ width: 110, fontFamily: 'monospace' }} />
            <div style={{ display: 'flex', gap: 3 }}>
              {CAPITAL_PRESETS.map(v => (
                <button key={v} type="button"
                        onClick={() => setCapital(v)}
                        className="scan-filter-btn"
                        style={{
                          padding: '0.2rem 0.45rem', fontSize: '0.68rem',
                          opacity: capital === v ? 1 : 0.7,
                          borderColor: capital === v ? 'var(--accent-primary)' : undefined,
                        }}>
                  {v / 1000}k
                </button>
              ))}
            </div>
          </div>
        </div>

        <div>
          <label style={{ display: 'block', fontSize: '0.7rem',
                          color: 'var(--text-muted)', marginBottom: 3 }}>
            Max holdings
          </label>
          <input type="number" min="1" max="100" step="1"
                 value={maxHoldings}
                 onChange={e => setMaxHoldings(
                   Math.max(1, Math.min(100, parseInt(e.target.value, 10) || 1)))}
                 className="mini-input"
                 style={{ width: 64, fontFamily: 'monospace', textAlign: 'center' }} />
        </div>

        <div>
          <label style={{ display: 'block', fontSize: '0.7rem',
                          color: 'var(--text-muted)', marginBottom: 3 }}>
            Mode
          </label>
          <div style={{
            display: 'inline-flex', border: '1px solid var(--border)',
            borderRadius: 6, overflow: 'hidden', fontSize: '0.72rem',
          }}>
            <button type="button"
                    onClick={() => setMode('free_slots')}
                    style={{
                      padding: '0.3rem 0.7rem', border: 'none',
                      background: mode === 'free_slots' ? 'var(--accent-primary)' : 'transparent',
                      color: mode === 'free_slots' ? '#0b1220' : 'var(--text-muted)',
                      fontWeight: 600, cursor: 'pointer',
                    }}
                    title="Combler les slots libres uniquement (comportement cron)">
              Combler
            </button>
            <button type="button"
                    onClick={() => setMode('max_holdings')}
                    style={{
                      padding: '0.3rem 0.7rem', border: 'none',
                      borderLeft: '1px solid var(--border)',
                      background: mode === 'max_holdings' ? 'var(--accent-primary)' : 'transparent',
                      color: mode === 'max_holdings' ? '#0b1220' : 'var(--text-muted)',
                      fontWeight: 600, cursor: 'pointer',
                    }}
                    title="Rebalance complet : jusqu'à max_holdings candidats, incluant les top-ups">
              Rebalance
            </button>
          </div>
        </div>

        <label style={{ display: 'inline-flex', alignItems: 'center',
                        gap: 6, fontSize: '0.78rem', color: 'var(--text-muted)',
                        cursor: 'pointer' }}>
          <input type="checkbox" checked={allowFractional}
                 onChange={e => setAllowFractional(e.target.checked)}
                 style={{ width: 14, height: 14 }} />
          Shares fractionnaires
        </label>

        <label style={{ display: 'inline-flex', alignItems: 'center',
                        gap: 6, fontSize: '0.78rem', color: 'var(--text-muted)',
                        cursor: 'pointer' }}
               title="Inclure les tickers déjà OPEN dans le plan (pour top-ups)">
          <input type="checkbox" checked={includeHeld}
                 onChange={e => setIncludeHeld(e.target.checked)}
                 style={{ width: 14, height: 14 }} />
          Inclure les HELD
        </label>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {nPending > 0 && (
            <button type="button"
                    disabled={isPending || isRegenerating}
                    onClick={() => {
                      const ok = window.confirm(
                        `Purger les ${nPending} proposition${nPending > 1 ? 's' : ''} pending ` +
                        `(via expired — pas de cooldown veto) et régénérer un plan avec ` +
                        `les paramètres actuels ?`,
                      );
                      if (!ok) return;
                      onRegenerate({
                        total_capital: capital,
                        max_holdings: maxHoldings,
                        top_n_mode: mode,
                        allow_fractional_shares: allowFractional,
                        include_held: includeHeld,
                        notify_telegram: false,
                      });
                    }}
                    className="action-btn"
                    title="Expire les pending existants (sans cooldown veto) puis relance le proposer"
                    style={{
                      background: 'transparent',
                      border: '1px solid var(--warning)',
                      color: 'var(--warning)',
                    }}>
              {isRegenerating ? '⏳ Régénération…' : '🗑️ Purger + régénérer'}
            </button>
          )}
          <button type="button"
                  disabled={isPending || isRegenerating}
                  onClick={() => onRun({
                    total_capital: capital,
                    max_holdings: maxHoldings,
                    top_n_mode: mode,
                    allow_fractional_shares: allowFractional,
                    include_held: includeHeld,
                    notify_telegram: false,
                  })}
                  className="action-btn">
            {isPending ? '⏳ Génération…' : '🔄 Générer un plan'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Badge Support — affiche le PRIX du support détecté + niveau qualitatif.
// Le score 0-100 reste accessible via tooltip (power-users).
// ─────────────────────────────────────────────────────────────────
function SupportBadge({ support }) {
  if (!support || !support.level || support.level === 'INSUFFICIENT_DATA') {
    return (
      <div style={{
        display: 'inline-flex', flexDirection: 'column', alignItems: 'center',
        fontSize: '0.62rem', color: 'var(--text-muted)', lineHeight: 1.1,
      }}>
        <span style={{ fontSize: '0.95rem', fontWeight: 700 }}>—</span>
        <span style={{ fontSize: '0.55rem', opacity: 0.7 }}>no data</span>
      </div>
    );
  }
  const palette = {
    ON_SUPPORT:   { bg: 'rgba(34,197,94,0.18)',  fg: '#4ade80', border: 'rgba(34,197,94,0.6)',  label: 'SUPPORT' },
    NEAR_SUPPORT: { bg: 'rgba(251,191,36,0.15)', fg: '#fbbf24', border: 'rgba(251,191,36,0.5)', label: 'PROCHE'  },
    OFF_SUPPORT:  { bg: 'rgba(148,163,184,0.08)',fg: 'var(--text-muted)', border: 'var(--border)', label: 'HORS' },
  }[support.level] || { bg: 'transparent', fg: 'var(--text-muted)', border: 'var(--border)', label: '?' };

  // Prix de support à afficher : swing low (priorité = vrai support technique)
  // sinon MA200 (support de tendance long-terme), sinon "—" avec score en fallback.
  const priceLevel = Number.isFinite(support.nearest_swing_low)
    ? support.nearest_swing_low
    : (Number.isFinite(support.ma200_value) ? support.ma200_value : null);
  const priceSource = Number.isFinite(support.nearest_swing_low)
    ? 'swing'
    : (Number.isFinite(support.ma200_value) ? 'MA200' : null);

  const mainDisplay = priceLevel != null
    ? `$${priceLevel.toFixed(2)}`
    : `${support.score?.toFixed?.(0) ?? '?'}/100`;

  const tooltip = [
    `Niveau : ${support.level}`,
    `Score qualité signal : ${support.score?.toFixed?.(1) ?? '—'} / 100`,
    '',
    priceLevel != null
      ? `Prix support affiché : $${priceLevel.toFixed(2)} (source: ${priceSource})`
      : 'Aucun niveau de support technique détecté',
    '',
    support.ma200_proximity != null
      ? `MA200 proximity : ${support.ma200_proximity.toFixed(0)} / 100${support.ma200_value ? ` (MA200 = $${support.ma200_value.toFixed(2)})` : ''}`
      : 'MA200 : N/A (historique < 200j)',
    `Swing low proximity : ${support.swing_low_proximity?.toFixed?.(0) ?? '—'} / 100${support.nearest_swing_low ? ` (swing = $${support.nearest_swing_low.toFixed(2)})` : ''}`,
    `Pullback depth : ${support.pullback_depth?.toFixed?.(0) ?? '—'} / 100`,
    support.pct_from_52w_high != null ? `52w drawdown : ${support.pct_from_52w_high.toFixed(1)}%` : '',
    support.method !== 'full' ? `(method: ${support.method})` : '',
  ].filter(Boolean).join('\n');

  return (
    <div title={tooltip} style={{
      display: 'inline-flex', flexDirection: 'column', alignItems: 'center',
      padding: '0.18rem 0.5rem', borderRadius: 5,
      background: palette.bg, color: palette.fg,
      border: `1px solid ${palette.border}`,
      lineHeight: 1.1, minWidth: 64,
    }}>
      <span style={{ fontFamily: 'monospace', fontWeight: 800, fontSize: '0.85rem' }}>
        {mainDisplay}
      </span>
      <span style={{ fontSize: '0.55rem', fontWeight: 700, letterSpacing: '0.04em', opacity: 0.95 }}>
        {palette.label}
      </span>
    </div>
  );
}

// Lot 18 — Buy Signal verdict palette (synchronisé avec backend buy_signal.py).
const BUY_VERDICT_STYLE = {
  STRONG_BUY:        { bg: 'rgba(34,197,94,0.22)',  fg: '#22c55e', label: '🟢 STRONG BUY', glow: true },
  BUY:               { bg: 'rgba(132,204,22,0.20)', fg: '#84cc16', label: '🟢 BUY', glow: true },
  WATCH:             { bg: 'rgba(251,191,36,0.18)', fg: '#fbbf24', label: '👁 WATCH', glow: false },
  EARNINGS_BLACKOUT: { bg: 'rgba(248,113,113,0.18)',fg: '#f87171', label: '⏸ EARNINGS', glow: false },
  CHEAP_JUNK:        { bg: 'rgba(248,113,113,0.18)',fg: '#f87171', label: '⚠ TRAP', glow: false },
  FALLING_KNIFE:     { bg: 'rgba(248,113,113,0.18)',fg: '#f87171', label: '🔻 KNIFE', glow: false },
  SKIP:              { bg: 'rgba(148,163,184,0.10)',fg: 'var(--text-muted)', label: '— SKIP', glow: false },
  NO_DATA:           { bg: 'rgba(148,163,184,0.08)',fg: 'var(--text-muted)', label: '? N/A', glow: false },
};

function BuySignalChip({ signal }) {
  if (!signal || !signal.verdict) return null;
  const s = BUY_VERDICT_STYLE[signal.verdict] || BUY_VERDICT_STYLE.SKIP;
  const tooltip = signal.label || signal.verdict;
  return (
    <span title={tooltip} style={{
      display: 'inline-flex', alignItems: 'center',
      fontSize: '0.62rem', fontWeight: 800, letterSpacing: '0.04em',
      padding: '0.15rem 0.5rem', borderRadius: 4,
      background: s.bg, color: s.fg,
      border: `1px solid ${s.fg}`,
      textShadow: s.glow ? `0 0 8px ${s.fg}66` : 'none',
      whiteSpace: 'nowrap',
    }}>
      {s.label}
    </span>
  );
}

// Garde isInBuyZone basé sur le NOUVEAU verdict buy_signal pour le row highlighting.
function isInBuyZone(ctx) {
  const v = ctx?.buy_signal?.verdict;
  return v === 'STRONG_BUY' || v === 'BUY';
}


// ─────────────────────────────────────────────────────────────────
// Ligne de proposition — table éditable
// ─────────────────────────────────────────────────────────────────
function ProposalRow({
  p, rank, selected, onToggle, edits, setEdit, sectorAck, onToggleSectorAck,
  topUpAck, onToggleTopUpAck, onOpenAnalysis,
}) {
  const ctx = p.context || {};
  const secExp = ctx.sector_exposure || {};
  const overCap = !!secExp.over_cap;
  const alreadyHeld = !!ctx.already_held;
  const isPending = p.status === 'pending';

  const titanCol = factorColor(ctx.titan_score);
  const momCol   = ctx.momentum_pct == null
                 ? 'var(--text-muted)'
                 : ctx.momentum_pct >= 0 ? 'var(--success)' : 'var(--danger)';

  const getVal = (field, def) => {
    const v = edits[p.id]?.[field];
    return v != null && v !== '' ? v : def;
  };

  const inBuyZone = isInBuyZone(ctx);
  const rowBg = !isPending
    ? 'rgba(148,163,184,0.04)'
    : inBuyZone
      ? 'rgba(34,197,94,0.08)'
      : selected ? 'rgba(34,197,94,0.06)' : undefined;
  const rowStyle = {
    ...(rowBg ? { background: rowBg } : {}),
    ...(inBuyZone && isPending ? { boxShadow: 'inset 3px 0 0 0 #4ade80' } : {}),
  };

  return (
    <tr className="scan-row" data-ticker={p.ticker}
        style={Object.keys(rowStyle).length ? rowStyle : undefined}>
      <td style={{ textAlign: 'center' }}>
        <input type="checkbox" disabled={!isPending}
               checked={selected} onChange={onToggle}
               style={{ width: 16, height: 16, cursor: isPending ? 'pointer' : 'not-allowed' }} />
      </td>

      <td style={{ textAlign: 'right', color: 'var(--text-muted)',
                   fontFamily: 'monospace', paddingRight: 6 }}>
        {rank}
      </td>

      <td>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button type="button" onClick={() => onOpenAnalysis?.(p.ticker)}
                  title="Cliquer pour ouvrir l'analyse complète"
                  style={{
                    background: 'transparent', border: 'none', padding: 0,
                    margin: 0, cursor: 'pointer',
                    fontSize: '0.95rem', fontWeight: 800,
                    color: 'var(--accent-primary)',
                    textDecoration: 'underline dotted',
                    textUnderlineOffset: 3,
                  }}>
            {p.ticker}
          </button>
          <StatusBadge status={p.status} />
          {ctx.buy_signal && isPending && <BuySignalChip signal={ctx.buy_signal} />}
          {alreadyHeld && (
            <span style={{
              fontSize: '0.6rem', padding: '0.08rem 0.4rem', borderRadius: 4,
              background: 'rgba(59,130,246,0.15)', color: '#60a5fa',
              border: '1px solid rgba(59,130,246,0.4)', fontWeight: 700,
            }} title={`Position déjà OPEN (${ctx.current_shares} shares)`}>
              HELD × {ctx.current_shares || 0}
            </span>
          )}
        </div>
        <div style={{ marginTop: 2, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <span style={{
            fontSize: '0.68rem', color: 'var(--text-muted)',
            border: '1px solid var(--border)', borderRadius: 4,
            padding: '0.08rem 0.4rem',
          }}>
            {p.sector || '?'}
            {secExp.projected_pct != null && (
              <span style={{ marginLeft: 4,
                             color: overCap ? '#f87171' : 'var(--text-muted)' }}>
                · {fmtPctRaw(secExp.projected_pct, 1)}
              </span>
            )}
          </span>
          {overCap && isPending && (
            <label style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              fontSize: '0.65rem', padding: '0.08rem 0.4rem', borderRadius: 4,
              background: sectorAck ? 'rgba(251,146,60,0.08)' : 'rgba(248,113,113,0.12)',
              border: `1px solid ${sectorAck ? 'rgba(251,146,60,0.5)' : 'rgba(248,113,113,0.5)'}`,
              color: sectorAck ? '#fb923c' : '#f87171',
              cursor: 'pointer', fontWeight: 600,
            }}
            title={`${p.sector} > ${secExp.cap_pct}% après achat`}>
              <input type="checkbox" checked={sectorAck}
                     onChange={onToggleSectorAck}
                     style={{ width: 12, height: 12 }} />
              {sectorAck ? 'override ✓' : 'over-exposed'}
            </label>
          )}
          {alreadyHeld && isPending && (
            <label style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              fontSize: '0.65rem', padding: '0.08rem 0.4rem', borderRadius: 4,
              background: topUpAck ? 'rgba(59,130,246,0.1)' : 'rgba(148,163,184,0.12)',
              border: `1px solid ${topUpAck ? 'rgba(59,130,246,0.5)' : 'var(--border)'}`,
              color: topUpAck ? '#60a5fa' : 'var(--text-muted)',
              cursor: 'pointer', fontWeight: 600,
            }}
            title="Autoriser le top-up (ajout à la position existante)">
              <input type="checkbox" checked={topUpAck}
                     onChange={onToggleTopUpAck}
                     style={{ width: 12, height: 12 }} />
              {topUpAck ? 'top-up ✓' : 'top-up'}
            </label>
          )}
          <TiltBadges flags={ctx.titan_tilt_flags} />
          {Number.isFinite(ctx.days_until_earnings) && ctx.days_until_earnings >= 0 && ctx.days_until_earnings <= 14 && (
            <span title={`Earnings ${ctx.next_earnings_date || ''}`} style={{
              fontSize: '0.6rem', fontWeight: 800,
              padding: '0.08rem 0.4rem', borderRadius: 4,
              background: ctx.days_until_earnings < 7
                ? 'rgba(248,113,113,0.18)' : 'rgba(251,191,36,0.18)',
              color: ctx.days_until_earnings < 7 ? '#f87171' : '#fbbf24',
            }}>
              📅 EARN {ctx.days_until_earnings}j
            </span>
          )}
          {Number.isFinite(ctx.revisions_score) && (
            <span
              title={`Earnings revisions score : ${fmtNum(ctx.revisions_score, 1)} / 100`}
              style={{
                fontSize: '0.6rem', fontWeight: 800,
                padding: '0.08rem 0.4rem', borderRadius: 4,
                background: ctx.revisions_score >= 70
                  ? 'rgba(34,197,94,0.16)'
                  : ctx.revisions_score >= 50
                    ? 'rgba(251,191,36,0.14)'
                    : 'rgba(148,163,184,0.10)',
                color: ctx.revisions_score >= 70
                  ? '#22c55e'
                  : ctx.revisions_score >= 50
                    ? '#fbbf24'
                    : 'var(--text-muted)',
              }}>
              ↗ REV {fmtNum(ctx.revisions_score, 0)}
            </span>
          )}
        </div>
      </td>

      <td style={{ textAlign: 'center' }}>
        {Number.isFinite(ctx.titan_score) && (
          <span style={{
            fontFamily: 'monospace', fontWeight: 800,
            fontSize: '0.95rem', color: titanCol,
          }}>
            {fmtNum(ctx.titan_score, 1)}
          </span>
        )}
      </td>

      <td style={{ textAlign: 'center' }}>
        <SupportBadge support={ctx.support} />
      </td>

      <td style={{ textAlign: 'right', fontFamily: 'monospace', fontSize: '0.78rem',
                   color: 'var(--text-muted)' }}>
        {fmtPctRaw(ctx.weight_pct, 2)}
      </td>

      <MiniCell editable={isPending}
                value={getVal('entry', p.entry)}
                defaultValue={p.entry}
                onChange={v => setEdit(p.id, 'entry', v)} />
      <MiniCell editable={isPending}
                value={getVal('stop_loss', p.stop_loss)}
                defaultValue={p.stop_loss}
                tone="neg"
                onChange={v => setEdit(p.id, 'stop_loss', v)}
                title={ctx.suggested_sl_pct ? `−${ctx.suggested_sl_pct}%` : ''} />
      <MiniCell editable={isPending}
                value={getVal('take_profit', p.take_profit)}
                defaultValue={p.take_profit}
                tone="pos"
                onChange={v => setEdit(p.id, 'take_profit', v)}
                title={ctx.suggested_tp_pct ? `+${ctx.suggested_tp_pct}%` : ''} />
      <MiniCell editable={isPending}
                value={getVal('size', p.size)}
                defaultValue={p.size}
                step="1"
                onChange={v => setEdit(p.id, 'size', v)} />

      <td style={{ textAlign: 'right', fontFamily: 'monospace', fontWeight: 700 }}>
        {fmtPrice(ctx.amount_usd, 0)}
      </td>

      <td style={{ textAlign: 'center' }}>
        <TickerSpark ticker={p.ticker} width={70} height={20} />
      </td>

      <td style={{ textAlign: 'right', fontFamily: 'monospace',
                   color: momCol, fontWeight: 600, fontSize: '0.78rem' }}>
        {fmtSignedPct(ctx.momentum_pct, 1)}
      </td>

      <td style={{ fontSize: '0.68rem', color: 'var(--text-muted)',
                   fontFamily: 'monospace', whiteSpace: 'nowrap' }}>
        {isPending && p.expires_at ? `exp ${p.expires_at.slice(5, 16)}` :
         p.decided_at ? `décidée ${p.decided_at.slice(5, 16)}` : ''}
        {p.order_id && (
          <div style={{ color: 'var(--success)' }}>{p.order_id}</div>
        )}
      </td>
    </tr>
  );
}

function MiniCell({ editable, value, defaultValue, onChange, step = '0.01', tone, title }) {
  const color = tone === 'pos' ? 'var(--success)' : tone === 'neg' ? 'var(--danger)' : undefined;
  if (!editable) {
    return (
      <td style={{ textAlign: 'right', fontFamily: 'monospace', color,
                   fontSize: '0.82rem' }}>
        {fmtPrice(defaultValue)}
      </td>
    );
  }
  return (
    <td style={{ padding: 4 }}>
      <input
        type="number" value={value ?? ''} step={step}
        onChange={e => onChange(e.target.value)}
        placeholder={defaultValue != null ? String(defaultValue) : ''}
        title={title}
        style={{
          width: '100%', fontFamily: 'monospace', fontSize: '0.85rem',
          fontWeight: 700, textAlign: 'right',
          padding: '0.4rem 0.5rem', color,
          background: 'var(--bg-dark-secondary)', border: '1px solid var(--border)',
          borderRadius: 6, boxSizing: 'border-box',
        }}
      />
    </td>
  );
}

function ActionBar({
  nSelected, errors, canApprove, isApproving, isRejecting,
  onApprove, onReject,
}) {
  return (
    <div style={{
      position: 'sticky', bottom: 0, zIndex: 10, marginTop: 12,
      background: 'rgba(15,23,42,0.95)', backdropFilter: 'blur(8px)',
      border: '1px solid var(--border)', borderRadius: 12,
      padding: '0.85rem 1rem',
      display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
    }}>
      <div style={{ fontSize: '0.9rem' }}>
        <strong>{nSelected}</strong> sélectionnée{nSelected > 1 ? 's' : ''}
      </div>
      {errors.length > 0 && (
        <div style={{ fontSize: '0.75rem', color: '#f87171', flex: 1 }}>
          {errors.slice(0, 2).join(' · ')}
          {errors.length > 2 && ` · +${errors.length - 2} autre${errors.length > 3 ? 's' : ''}`}
        </div>
      )}
      <button type="button"
              disabled={nSelected === 0 || isRejecting || isApproving}
              onClick={onReject}
              className="scan-filter-btn"
              style={{
                marginLeft: 'auto', padding: '0.5rem 1.1rem',
                background: 'rgba(239,68,68,0.15)',
                borderColor: 'rgba(239,68,68,0.5)',
                color: '#f87171',
                opacity: nSelected === 0 ? 0.4 : 1,
              }}>
        {isRejecting ? '⏳…' : `✕ Rejeter${nSelected > 0 ? ` (${nSelected})` : ''}`}
      </button>
      <button type="button"
              disabled={!canApprove}
              onClick={onApprove}
              className="scan-filter-btn"
              style={{
                padding: '0.5rem 1.25rem', fontSize: '0.85rem',
                fontWeight: 700,
                background: canApprove ? 'rgba(34,197,94,0.18)' : undefined,
                borderColor: canApprove ? 'rgba(34,197,94,0.6)' : undefined,
                color: canApprove ? '#22c55e' : undefined,
                cursor: canApprove ? 'pointer' : 'not-allowed',
                opacity: canApprove ? 1 : 0.5,
              }}>
        {isApproving ? '⏳ Exécution…' :
          `✓ Approuver${nSelected > 0 ? ` (${nSelected})` : ''}`}
      </button>
    </div>
  );
}

function ConfirmModal({ items, isPending, onConfirm, onCancel }) {
  const total = items.reduce((s, t) => s + (t.entry * t.size || 0), 0);
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 100, padding: 20,
    }} onClick={onCancel}>
      <div onClick={e => e.stopPropagation()}
           style={{
             background: 'var(--bg-card)', borderRadius: 12, padding: 24,
             maxWidth: 680, width: '100%', maxHeight: '85vh', overflow: 'auto',
             border: '1px solid var(--border)',
           }}>
        <h3 style={{ margin: '0 0 12px', fontSize: '1.05rem' }}>
          Confirmer l'ouverture de {items.length} position{items.length > 1 ? 's' : ''}
        </h3>
        <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: 12 }}>
          Total engagé : <strong style={{ color: 'var(--text)' }}>{fmtPrice(total, 2)}</strong>
          {' '}(prix d'entrée × quantité, hors slippage).
        </div>
        <table className="scan-table" style={{ fontSize: '0.8rem', marginBottom: 16 }}>
          <thead>
            <tr>
              <th>Ticker</th>
              <th style={{ textAlign: 'right' }}>Entry</th>
              <th style={{ textAlign: 'right' }}>SL</th>
              <th style={{ textAlign: 'right' }}>TP</th>
              <th style={{ textAlign: 'right' }}>Size</th>
              <th style={{ textAlign: 'right' }}>Notional</th>
            </tr>
          </thead>
          <tbody>
            {items.map(t => (
              <tr key={t.id}>
                <td style={{ fontWeight: 700 }}>
                  {t.ticker}
                  {t.alreadyHeld && (
                    <span style={{ marginLeft: 6, fontSize: '0.6rem', color: '#60a5fa' }}>+held</span>
                  )}
                  {t.overCap && (
                    <span style={{ marginLeft: 6, fontSize: '0.6rem', color: '#fb923c' }}>
                      over-cap ✓
                    </span>
                  )}
                </td>
                <td style={{ textAlign: 'right', fontFamily: 'monospace' }}>{fmtPrice(t.entry)}</td>
                <td style={{ textAlign: 'right', fontFamily: 'monospace', color: 'var(--danger)' }}>
                  {fmtPrice(t.sl)}
                </td>
                <td style={{ textAlign: 'right', fontFamily: 'monospace', color: 'var(--success)' }}>
                  {fmtPrice(t.tp)}
                </td>
                <td style={{ textAlign: 'right', fontFamily: 'monospace' }}>{t.size}</td>
                <td style={{ textAlign: 'right', fontFamily: 'monospace', fontWeight: 700 }}>
                  {fmtPrice(t.entry * t.size, 0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button type="button" onClick={onCancel} disabled={isPending}
                  className="scan-filter-btn" style={{ padding: '0.5rem 1rem' }}>
            Annuler
          </button>
          <button type="button" onClick={onConfirm} disabled={isPending}
                  className="scan-filter-btn"
                  style={{
                    padding: '0.5rem 1.25rem',
                    background: 'rgba(34,197,94,0.18)',
                    borderColor: 'rgba(34,197,94,0.6)',
                    color: '#22c55e', fontWeight: 700,
                  }}>
            {isPending ? '⏳…' : '✓ Confirmer'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Page principale
// ─────────────────────────────────────────────────────────────────
export default function ProposalsPage() {
  const [statusFilter, setStatusFilter] = useState('pending');
  const [sortMode, setSortMode] = useState('score'); // 'score' | 'weight'
  const [edits, setEdits] = useState({});            // {id: {entry, stop_loss, take_profit, size}}
  const [selected, setSelected] = useState(() => new Set());
  const [sectorAck, setSectorAck] = useState(() => new Set());
  const [topUpAck, setTopUpAck] = useState(() => new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [analysisTicker, setAnalysisTicker] = useState(null);
  const toastIdRef = useRef(0);
  const toast = (text, type = 'ok') => {
    const id = ++toastIdRef.current;
    setToasts(t => [...t, { id, text, type }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 4500);
  };

  const proposalsQ   = useProposals({ status: statusFilter });
  const refreshM     = useRefreshProposals();
  const regenerateM  = useRegenerateProposals();
  const approveM     = useApproveProposalsBatch();
  const rejectM      = useRejectProposalsBatch();
  const marketQ      = useMarketStatus();
  const marketClosed = marketQ.data?.is_open === false;

  const lastRefresh = proposalsQ.data?.last_refresh;

  // Tri stable (par score desc par défaut, sinon par poids). On lit
  // `proposals` directement de `proposalsQ.data` pour éviter qu'un fallback
  // `|| []` en dehors du useMemo ne change la référence à chaque render.
  const sortedItems = useMemo(() => {
    const source = proposalsQ.data?.proposals || [];
    const copy = [...source];
    const titanKey  = p => p.context?.titan_score ?? 0;
    const weightKey = p => p.context?.weight_pct ?? 0;
    if (sortMode === 'buy_zone') {
      // Tri composite : buy-zone d'abord (TITAN ≥80 + ON_SUPPORT), puis score desc.
      copy.sort((a, b) => {
        const za = isInBuyZone(a.context) ? 1 : 0;
        const zb = isInBuyZone(b.context) ? 1 : 0;
        if (za !== zb) return zb - za;
        return titanKey(b) - titanKey(a);
      });
    } else {
      const key = sortMode === 'score' ? titanKey : weightKey;
      copy.sort((a, b) => (key(b) - key(a)));
    }
    return copy;
  }, [proposalsQ.data, sortMode]);

  // Validation locale par proposition.
  const validated = useMemo(() => {
    const errors = [];
    const okIds = new Set();
    for (const p of sortedItems) {
      if (!selected.has(p.id)) continue;
      if (p.status !== 'pending') continue;
      const e = edits[p.id] || {};
      const entry = parseFloat(e.entry ?? p.entry);
      const sl    = parseFloat(e.stop_loss ?? p.stop_loss);
      const tp    = parseFloat(e.take_profit ?? p.take_profit);
      const size  = parseInt(e.size ?? p.size, 10);
      if (!(entry > 0))             errors.push(`${p.ticker}: Entry invalide`);
      else if (!(sl > 0 && sl < entry)) errors.push(`${p.ticker}: SL < Entry requis`);
      else if (!(tp > entry))           errors.push(`${p.ticker}: TP > Entry requis`);
      else if (!(size > 0))             errors.push(`${p.ticker}: Size > 0 requis`);
      else {
        const ctx = p.context || {};
        const overCap = !!(ctx.sector_exposure || {}).over_cap;
        const held    = !!ctx.already_held;
        if (overCap && !sectorAck.has(p.id))
          errors.push(`${p.ticker}: sur-exposition à acquitter`);
        else if (held && !topUpAck.has(p.id))
          errors.push(`${p.ticker}: top-up à autoriser`);
        else okIds.add(p.id);
      }
    }
    return { errors, okIds };
  }, [sortedItems, selected, edits, sectorAck, topUpAck]);

  const nSelected = [...selected].filter(id =>
    sortedItems.some(p => p.id === id && p.status === 'pending'),
  ).length;
  const canApprove = nSelected > 0 && validated.errors.length === 0
                   && !marketClosed && !approveM.isPending;

  // ── Handlers ────────────────────────────────────────────────────
  const setEdit = (id, field, value) => {
    setEdits(prev => ({ ...prev, [id]: { ...prev[id], [field]: value } }));
  };

  const toggleSelected = (id) => {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };
  const toggleSet = (setter, id) => setter(prev => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });
  const selectAllPending = () => setSelected(new Set(
    sortedItems.filter(p => p.status === 'pending').map(p => p.id),
  ));
  const clearAll = () => setSelected(new Set());

  // Factorise la gestion du résultat — même shape que /refresh pour
  // /regenerate (avec n_expired en diagnostics).
  const handleRunResult = (res, { isRegenerate = false } = {}) => {
    if (!res?.ok) {
      toast(`${isRegenerate ? 'Régénération' : 'Refresh'} KO : ${res?.detail || res?.error || 'erreur'}`, 'err');
      return;
    }
    const blockingGate = (res.gates || []).find(g => !g.ok);
    if (blockingGate) {
      toast(`🔴 ${blockingGate.name} : ${blockingGate.detail}`, 'warn');
      return;
    }
    const n = (res.proposals || []).length;
    const expired = res.diagnostics?.n_expired;
    if (isRegenerate) {
      toast(
        n === 0
          ? `🗑️ ${expired ?? 0} purgée${expired > 1 ? 's' : ''}, aucune nouvelle proposition générée`
          : `🗑️ ${expired ?? 0} purgée${expired > 1 ? 's' : ''} · ${n} nouvelle${n > 1 ? 's' : ''}`,
        n > 0 ? 'ok' : 'warn',
      );
    } else {
      toast(
        n === 0 ? 'Aucune nouvelle proposition (file déjà à jour)'
                : `${n} nouvelle${n > 1 ? 's' : ''} proposition${n > 1 ? 's' : ''}`,
        n > 0 ? 'ok' : 'warn',
      );
    }
  };

  const handleRefresh = async (params) => {
    const res = await refreshM.mutateAsync(params);
    handleRunResult(res);
  };

  const handleRegenerate = async (params) => {
    const res = await regenerateM.mutateAsync(params);
    handleRunResult(res, { isRegenerate: true });
    // Reset sélection/édition — les IDs des nouvelles propositions sont différents.
    setSelected(new Set());
    setEdits({});
    setSectorAck(new Set());
    setTopUpAck(new Set());
  };

  const buildBatchPayload = () => sortedItems
    .filter(p => validated.okIds.has(p.id))
    .map(p => {
      const e = edits[p.id] || {};
      const ctx = p.context || {};
      const overrides = {};
      if (e.entry != null && e.entry !== '') overrides.entry = parseFloat(e.entry);
      if (e.stop_loss != null && e.stop_loss !== '') overrides.stop_loss = parseFloat(e.stop_loss);
      if (e.take_profit != null && e.take_profit !== '') overrides.take_profit = parseFloat(e.take_profit);
      if (e.size != null && e.size !== '') overrides.size = parseInt(e.size, 10);
      return {
        id: p.id,
        overrides: Object.keys(overrides).length ? overrides : undefined,
        ack_sector_warning: !!(ctx.sector_exposure || {}).over_cap,
        allow_top_up: !!ctx.already_held,
      };
    });

  const handleApprove = async () => {
    const payload = buildBatchPayload();
    const res = await approveM.mutateAsync(payload);
    if (!res?.ok && !res?.n_executed) {
      toast(res?.detail || res?.error || '❌ Échec exécution', 'err');
      return;
    }
    const ok   = res.n_executed ?? 0;
    const fail = res.n_failed ?? 0;
    if (fail === 0) toast(`✅ ${ok} trade${ok > 1 ? 's' : ''} ouvert${ok > 1 ? 's' : ''}`, 'ok');
    else            toast(`⚠️ ${ok} OK · ${fail} échec${fail > 1 ? 's' : ''}`, 'warn');
    setSelected(new Set());
    setEdits({});
    setSectorAck(new Set());
    setTopUpAck(new Set());
    setConfirmOpen(false);
  };

  const handleReject = async () => {
    const ids = [...selected].filter(id =>
      sortedItems.some(p => p.id === id && p.status === 'pending'));
    if (ids.length === 0) return;
    const reason = window.prompt(
      `Raison du rejet pour ${ids.length} proposition${ids.length > 1 ? 's' : ''} (optionnel)`,
      'user_veto',
    );
    if (reason === null) return;  // cancel
    const res = await rejectM.mutateAsync(
      ids.map(id => ({ id, reason: reason || 'user_veto' })),
    );
    if (!res?.ok && !res?.n_rejected) {
      toast(res?.detail || '❌ Rejet KO', 'err');
      return;
    }
    toast(`✕ ${res.n_rejected} rejetée${res.n_rejected > 1 ? 's' : ''}`, 'warn');
    setSelected(new Set());
  };

  // Items pour la modal de confirmation.
  const confirmItems = useMemo(() => sortedItems
    .filter(p => validated.okIds.has(p.id))
    .map(p => {
      const e = edits[p.id] || {};
      return {
        id: p.id, ticker: p.ticker,
        entry:  parseFloat(e.entry ?? p.entry),
        sl:     parseFloat(e.stop_loss ?? p.stop_loss),
        tp:     parseFloat(e.take_profit ?? p.take_profit),
        size:   parseInt(e.size ?? p.size, 10),
        alreadyHeld: !!p.context?.already_held,
        overCap:     !!(p.context?.sector_exposure || {}).over_cap,
      };
    }), [sortedItems, validated.okIds, edits]);

  // ── Rendu ───────────────────────────────────────────────────────
  return (
    <div className="page-content">
      {/* Toasts */}
      <div style={{ position: 'fixed', top: 80, right: 20, zIndex: 9999,
                    display: 'flex', flexDirection: 'column', gap: 8 }}>
        {toasts.map(t => (
          <div key={t.id} style={{
            background: t.type === 'err'  ? 'var(--danger)' :
                        t.type === 'warn' ? 'var(--warning)' : 'var(--success)',
            color: '#0d0d1a', padding: '8px 16px', borderRadius: 8,
            fontWeight: 600, fontSize: '0.85rem',
            minWidth: 260, boxShadow: '0 4px 16px rgba(0,0,0,0.3)',
          }}>{t.text}</div>
        ))}
      </div>

      <StatusStrip
        lastRefresh={lastRefresh}
        marketStatus={marketQ.data}
        proposalsData={proposalsQ.data}
      />

      {marketClosed && (
        <div className="card" style={{
          padding: 10, marginBottom: 12,
          background: 'rgba(245,158,11,0.10)',
          borderLeft: '3px solid var(--warning)', fontSize: '0.82rem',
        }}>
          <strong>🕒 NYSE fermée</strong> — les approbations sont désactivées.
          {marketQ.data?.next_open && (
            <> Prochaine ouverture : <code>{new Date(marketQ.data.next_open).toLocaleString()}</code></>
          )}
        </div>
      )}

      <RefreshPanel
        onRun={handleRefresh}
        onRegenerate={handleRegenerate}
        isPending={refreshM.isPending}
        isRegenerating={regenerateM.isPending}
        nPending={proposalsQ.data?.n_pending ?? 0}
        defaults={lastRefresh?.requested_params}
      />

      <PresetBar
        scope="proposals"
        label="Vues proposals"
        current={{ statusFilter, sortMode }}
        onApply={(p) => {
          if (p?.statusFilter !== undefined) setStatusFilter(p.statusFilter);
          if (p?.sortMode     !== undefined) setSortMode(p.sortMode);
        }}
      />

      {/* Filtres + tri */}
      <div style={{
        display: 'flex', gap: 8, flexWrap: 'wrap',
        justifyContent: 'space-between', marginBottom: 10, alignItems: 'center',
      }}>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {STATUS_FILTERS.map(f => (
            <button key={String(f.id)}
                    onClick={() => setStatusFilter(f.id)}
                    style={{
                      padding: '4px 12px', borderRadius: 6,
                      border: '1px solid var(--border)',
                      background: statusFilter === f.id ? f.color : 'transparent',
                      color: statusFilter === f.id ? '#0d0d1a' : 'var(--text)',
                      cursor: 'pointer', fontSize: '0.75rem', fontWeight: 600,
                    }}>
              {f.label}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <div role="group" style={{
            display: 'inline-flex', border: '1px solid var(--border)',
            borderRadius: 6, overflow: 'hidden', fontSize: '0.72rem',
          }}>
            <button type="button" onClick={() => setSortMode('buy_zone')}
                    title="Met en haut les propositions TITAN ≥ 80 + sur support technique"
                    style={{
                      padding: '0.3rem 0.6rem', border: 'none',
                      background: sortMode === 'buy_zone' ? 'rgba(34,197,94,0.6)' : 'transparent',
                      color: sortMode === 'buy_zone' ? '#0b1220' : 'var(--text-muted)',
                      fontWeight: 700, cursor: 'pointer',
                    }}>✓ Buy Zone</button>
            <button type="button" onClick={() => setSortMode('score')}
                    style={{
                      padding: '0.3rem 0.6rem', border: 'none',
                      borderLeft: '1px solid var(--border)',
                      background: sortMode === 'score' ? 'var(--accent-primary)' : 'transparent',
                      color: sortMode === 'score' ? '#0b1220' : 'var(--text-muted)',
                      fontWeight: 600, cursor: 'pointer',
                    }}>Score ▼</button>
            <button type="button" onClick={() => setSortMode('weight')}
                    style={{
                      padding: '0.3rem 0.6rem', border: 'none',
                      borderLeft: '1px solid var(--border)',
                      background: sortMode === 'weight' ? 'var(--accent-primary)' : 'transparent',
                      color: sortMode === 'weight' ? '#0b1220' : 'var(--text-muted)',
                      fontWeight: 600, cursor: 'pointer',
                    }}>Poids ▼</button>
          </div>
          <button type="button" onClick={selectAllPending}
                  className="scan-filter-btn"
                  style={{ padding: '0.25rem 0.55rem', fontSize: '0.7rem' }}>
            Tout cocher
          </button>
          <button type="button" onClick={clearAll}
                  className="scan-filter-btn"
                  style={{ padding: '0.25rem 0.55rem', fontSize: '0.7rem' }}>
            Aucun
          </button>
        </div>
      </div>

      {/* États */}
      {proposalsQ.isLoading && (
        <div className="card" style={{ padding: 24, textAlign: 'center' }}>
          <div className="spinner" />
        </div>
      )}
      {proposalsQ.isError && (
        <ApiErrorBanner
          msg={proposalsQ.error?.message || 'Erreur chargement propositions'}
          onRetry={proposalsQ.refetch}
        />
      )}

      {!proposalsQ.isLoading && !proposalsQ.isError && sortedItems.length === 0 && (
        <div className="card" style={{
          padding: 32, textAlign: 'center', color: 'var(--text-muted)',
        }}>
          {statusFilter === 'pending'
            ? '📭 Aucune proposition en attente. Lancez un refresh pour générer un plan.'
            : `Aucune proposition (statut "${statusFilter || 'toutes'}").`}
        </div>
      )}

      {sortedItems.length > 0 && (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <div className="scan-table-wrap">
            <table className="scan-table">
              <thead>
                <tr>
                  <th style={{ width: 32 }}></th>
                  <th style={{ width: 32, textAlign: 'right', paddingRight: 6 }}>#</th>
                  <th>Ticker & secteur</th>
                  <th style={{ width: 58, textAlign: 'center' }}>TITAN</th>
                  <th style={{ width: 96, textAlign: 'center' }} title="Niveau de support technique détecté (prix). Hover pour breakdown qualité.">Support</th>
                  <th style={{ width: 70, textAlign: 'right' }}>Poids</th>
                  <th style={{ width: 110 }}>Entry</th>
                  <th style={{ width: 110 }}>SL</th>
                  <th style={{ width: 110 }}>TP</th>
                  <th style={{ width: 80 }}>Size</th>
                  <th style={{ width: 90, textAlign: 'right' }}>Notional</th>
                  <th style={{ width: 80, textAlign: 'center' }} title="Sparkline prix sur l'historique disponible">Tendance</th>
                  <th style={{ width: 72, textAlign: 'right' }}>Mom 6M</th>
                  <th>Expire / décision</th>
                </tr>
              </thead>
              <tbody>
                {sortedItems.map((p, i) => (
                  <ProposalRow
                    key={p.id}
                    p={p}
                    rank={i + 1}
                    selected={selected.has(p.id)}
                    onToggle={() => toggleSelected(p.id)}
                    edits={edits}
                    setEdit={setEdit}
                    sectorAck={sectorAck.has(p.id)}
                    onToggleSectorAck={() => toggleSet(setSectorAck, p.id)}
                    topUpAck={topUpAck.has(p.id)}
                    onToggleTopUpAck={() => toggleSet(setTopUpAck, p.id)}
                    onOpenAnalysis={setAnalysisTicker}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {sortedItems.some(p => p.status === 'pending') && (
        <ActionBar
          nSelected={nSelected}
          errors={validated.errors}
          canApprove={canApprove}
          isApproving={approveM.isPending}
          isRejecting={rejectM.isPending}
          onApprove={() => setConfirmOpen(true)}
          onReject={handleReject}
        />
      )}

      {confirmOpen && (
        <ConfirmModal
          items={confirmItems}
          isPending={approveM.isPending}
          onConfirm={handleApprove}
          onCancel={() => setConfirmOpen(false)}
        />
      )}

      {analysisTicker && (
        <TickerAnalysisModal
          ticker={analysisTicker}
          onClose={() => setAnalysisTicker(null)}
        />
      )}
    </div>
  );
}
