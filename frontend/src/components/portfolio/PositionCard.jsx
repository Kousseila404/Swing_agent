/**
 * PositionCard — refonte UI Portfolio Buffett 2026-04-29 (v2 anti-overlap).
 *
 * Layout :
 *   1. Header  — ticker / catégorie / badge action (3 zones, no-wrap protégé)
 *   2. Reason  — 1-2 phrases en français + CTA italique
 *   3. PriceMatrix — grille 3-cols Downside / Now / Upside (jamais d'overlap)
 *   4. KPIs    — Entry / Now / P&L / Détention (grid responsive)
 *   5. Detail  — repliable Buffett breakdown
 *   6. Actions — Renforcer / Clôturer
 */
import { useMemo, useState } from 'react';

const ACTION_PALETTE = {
  HOLD:              { label: 'CONSERVER',     border: 'rgba(148,163,184,0.35)', bg: 'rgba(148,163,184,0.06)', accent: '#94a3b8', cta: 'Position stable, rien à faire pour l\'instant.' },
  ADD_ON:            { label: 'RENFORCER',     border: 'rgba(59,130,246,0.55)',  bg: 'rgba(59,130,246,0.08)',  accent: '#60a5fa', cta: 'Buffett-style averaging-down — vérifier sizing avant d\'agir.' },
  TRIM:              { label: 'ALLÉGER',       border: 'rgba(251,191,36,0.55)',  bg: 'rgba(251,191,36,0.08)',  accent: '#fbbf24', cta: 'Conviction entamée — réduire ~30 à 50 %.' },
  EXIT_THESIS:       { label: 'SORTIR',        border: 'rgba(248,113,113,0.65)', bg: 'rgba(248,113,113,0.10)', accent: '#f87171', cta: 'Thèse fondamentale cassée — clôturer.' },
  EXIT_VALUATION:    { label: 'SORTIR',        border: 'rgba(192,132,252,0.65)', bg: 'rgba(192,132,252,0.10)', accent: '#c084fc', cta: 'Survalorisation extrême — encaisser.' },
  EXIT_CATASTROPHE:  { label: 'URGENT',        border: 'rgba(239,68,68,0.85)',   bg: 'rgba(239,68,68,0.14)',   accent: '#ef4444', cta: 'Catastrophe floor touché — sortie immédiate.' },
  NO_DATA:           { label: '—',             border: 'rgba(148,163,184,0.30)', bg: 'rgba(148,163,184,0.04)', accent: '#94a3b8', cta: 'Données partielles — surveiller.' },
};

const CATEGORY_LABEL = {
  compounder:           { icon: '🏔️', label: 'COMPOUNDER',    tip: 'Q≥85 + P≥8 — sizing ×1.5' },
  high_quality:         { icon: '⭐',  label: 'HIGH-QUALITY',  tip: 'Q≥75 + P≥7 — sizing ×1.2' },
  high_quality_partial: { icon: '⭐',  label: 'HIGH-QUALITY',  tip: 'Score partiel favorable — sizing ×1.2' },
  baseline:             null,
  baseline_no_data:     null,
  junior:               { icon: '⚠',  label: 'JUNIOR',        tip: 'Q<50 ou P<4 — sizing ×0.7' },
  junior_partial:       { icon: '⚠',  label: 'JUNIOR',        tip: 'Score partiel défavorable — sizing ×0.7' },
  junk:                 { icon: '🗑',  label: 'JUNK',          tip: 'Q<35 ET P<3 — sizing ×0.5' },
};

const fmtPct = (n) => (n == null || !Number.isFinite(n)) ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`;
const fmtUsd = (n) => (n == null || !Number.isFinite(n)) ? '—' : `$${n.toFixed(2)}`;

// Sous-composant hoisté hors de PositionCard pour éviter d'être recréé
// à chaque render (React Compiler refuse les composants définis dans le
// corps d'un autre composant).
function PriceRow({ icon, label, value, pctVs, color }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'baseline', gap: 6, lineHeight: 1.3,
      whiteSpace: 'nowrap',
    }}>
      <span style={{ color, fontSize: '0.66rem', flexShrink: 0 }}>{icon}</span>
      <span style={{ color: 'var(--text-muted)', fontSize: '0.68rem', flexShrink: 0 }}>{label}</span>
      <span style={{ color, fontSize: '0.74rem', fontWeight: 600, marginLeft: 'auto' }}>
        {fmtUsd(value)}
      </span>
      {Number.isFinite(pctVs) && (
        <span style={{ color, fontSize: '0.66rem', minWidth: 42, textAlign: 'right' }}>
          {fmtPct(pctVs)}
        </span>
      )}
    </div>
  );
}

// ─── Matrice de prix : 3 colonnes propres, zéro chevauchement ─
function PriceMatrix({ entry, current, sl, tp, buffett_sl, buffett_tp, let_it_ride }) {
  if (!entry) return null;

  // Domaine de la barre — sans le markup absolu, on n'a plus besoin de le serrer.
  const lows  = [sl, buffett_sl].filter(v => Number.isFinite(v) && v > 0);
  const highs = [tp, let_it_ride ? entry * 1.5 : buffett_tp].filter(v => Number.isFinite(v) && v > 0);
  const lo = Math.min(...lows, entry * 0.7);
  const hi = Math.max(...highs, entry * 1.3);
  const pct = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));

  const currentPct = current ? pct(current) : null;
  const entryPct   = pct(entry);
  const currentVsEntry = current ? (current / entry - 1) * 100 : null;
  const currentTone = currentVsEntry == null
    ? '#cbd5e1'
    : currentVsEntry >= 0 ? '#34d399' : '#f87171';

  return (
    <div style={{
      margin: '0.7rem 0 0.5rem',
      padding: '0.6rem 0.75rem',
      background: 'rgba(0,0,0,0.18)',
      borderRadius: 8,
      border: '1px solid rgba(148,163,184,0.10)',
    }}>
      {/* ── Grille 3 cols : downside / now / upside ────────── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(180px, 1fr) auto minmax(180px, 1fr)',
        gap: '1rem', alignItems: 'center',
      }}>
        {/* Downside (gauche, aligné droite) */}
        <div style={{ minWidth: 0 }}>
          <div style={{
            fontSize: '0.6rem', color: 'var(--text-muted)',
            textTransform: 'uppercase', letterSpacing: '0.08em',
            marginBottom: 4,
          }}>Downside</div>
          {Number.isFinite(sl) && sl > 0 && (
            <PriceRow icon="↓" label="SL broker"  value={sl} pctVs={(sl/entry-1)*100} color="#f87171" />
          )}
          {Number.isFinite(buffett_sl) && buffett_sl > 0 && (
            <PriceRow icon="↓" label="Buffett SL" value={buffett_sl} pctVs={(buffett_sl/entry-1)*100} color="#fb7185" />
          )}
        </div>

        {/* Now (centre) — gros indicateur lisible */}
        <div style={{
          textAlign: 'center', padding: '0 0.6rem',
          borderLeft: '1px solid rgba(148,163,184,0.18)',
          borderRight: '1px solid rgba(148,163,184,0.18)',
        }}>
          <div style={{
            fontSize: '0.6rem', color: 'var(--text-muted)',
            textTransform: 'uppercase', letterSpacing: '0.08em',
          }}>Now</div>
          <div style={{ fontSize: '1.15rem', fontWeight: 700, color: '#fff', lineHeight: 1.2 }}>
            {fmtUsd(current)}
          </div>
          {currentVsEntry != null && (
            <div style={{ fontSize: '0.7rem', fontWeight: 600, color: currentTone }}>
              {fmtPct(currentVsEntry)} vs entry
            </div>
          )}
        </div>

        {/* Upside (droite) */}
        <div style={{ minWidth: 0 }}>
          <div style={{
            fontSize: '0.6rem', color: 'var(--text-muted)',
            textTransform: 'uppercase', letterSpacing: '0.08em',
            marginBottom: 4,
          }}>Upside</div>
          {let_it_ride ? (
            <div style={{
              display: 'flex', alignItems: 'baseline', gap: 6,
              fontSize: '0.74rem', color: '#60a5fa', fontWeight: 700,
            }}>
              <span>🏔️</span>
              <span>Let it ride</span>
              <span style={{ fontSize: '0.62rem', fontWeight: 400, color: 'var(--text-muted)', marginLeft: 'auto' }}>
                compounder
              </span>
            </div>
          ) : (
            Number.isFinite(buffett_tp) && buffett_tp > 0 && (
              <PriceRow icon="↑" label="Buffett TP" value={buffett_tp} pctVs={(buffett_tp/entry-1)*100} color="#86efac" />
            )
          )}
          {Number.isFinite(tp) && tp > 0 && (
            <PriceRow icon="↑" label="TP broker" value={tp} pctVs={(tp/entry-1)*100} color="#22c55e" />
          )}
        </div>
      </div>

      {/* ── Barre purement visuelle ─────────────────────────── */}
      <div style={{ position: 'relative', height: 6, marginTop: 14,
        background: 'linear-gradient(90deg, rgba(248,113,113,0.30) 0%, rgba(148,163,184,0.20) 50%, rgba(34,197,94,0.30) 100%)',
        borderRadius: 3,
      }}>
        {/* Tick entry */}
        <div style={{
          position: 'absolute', left: `${entryPct}%`, top: -3,
          transform: 'translateX(-50%)',
          width: 2, height: 12, background: 'rgba(203,213,225,0.55)',
        }} title={`Entry ${fmtUsd(entry)}`} />
        {/* Indicateur current — pastille blanche + label flottant */}
        {currentPct != null && (
          <>
            <div style={{
              position: 'absolute', left: `${currentPct}%`, top: -4,
              transform: 'translateX(-50%)',
              width: 14, height: 14, borderRadius: '50%',
              background: currentTone, border: '2px solid #0f172a',
              boxShadow: `0 0 8px ${currentTone}80`,
            }} title={`Now ${fmtUsd(current)}`} />
          </>
        )}
      </div>
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        fontSize: '0.58rem', color: 'var(--text-muted)',
        marginTop: 4, opacity: 0.7,
      }}>
        <span>perte</span>
        <span>Entry {fmtUsd(entry)}</span>
        <span>gain</span>
      </div>
    </div>
  );
}

// ─── KPIs grid (responsive : 4 cols → 2 cols si < 480px) ───
function KpiGrid({ children }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))',
      gap: '0.55rem',
      paddingTop: '0.5rem', marginTop: '0.4rem',
      borderTop: '1px solid rgba(148,163,184,0.16)',
    }}>{children}</div>
  );
}
function Kpi({ label, value, sub, tone }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{
        fontSize: '0.6rem', color: 'var(--text-muted)',
        textTransform: 'uppercase', letterSpacing: '0.06em',
        marginBottom: 2,
      }}>{label}</div>
      <div style={{
        fontSize: '0.86rem', fontWeight: 700,
        color: tone || '#fff',
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      }}>{value}</div>
      {sub && (
        <div style={{ fontSize: '0.66rem', color: 'var(--text-muted)' }}>{sub}</div>
      )}
    </div>
  );
}

// ─── Card ──────────────────────────────────────────────────────
export default function PositionCard({ position, lt, onClickClose, onClickAdd, onTickerClick }) {
  const [showDetails, setShowDetails] = useState(false);
  const action = lt?.action || 'NO_DATA';
  const palette = ACTION_PALETTE[action] || ACTION_PALETTE.NO_DATA;
  const cat = CATEGORY_LABEL[lt?.buffett_category] || null;
  const reasons = lt?.reasons || [];

  const entry  = parseFloat(position.Entry) || 0;
  const current = parseFloat(position.current_price ?? lt?.current_price) || null;
  const sl     = parseFloat(position.Stop_Loss) || null;
  const tp     = parseFloat(position.Take_Profit) || null;
  const upnl   = position.unrealized_pnl;
  const pnlPct = position.pct_from_entry;

  // "Days held" : Date.now() est impur côté React Compiler. On le calcule
  // au mount uniquement (lazy init), suffisant pour un compteur quotidien
  // qui ne change pas pendant une session de quelques heures.
  const [days] = useState(() =>
    position.Date
      ? Math.floor((Date.now() - new Date(position.Date).getTime()) / 86_400_000)
      : null,
  );

  // Les regex match() retournent un nouvel array à chaque appel — on
  // mémoïse pour éviter de re-parser à chaque render.
  const { q, f, v } = useMemo(() => ({
    q: lt?.buffett_breakdown?.[0]?.match(/Q-score (\d+)/)?.[1],
    f: lt?.buffett_breakdown?.[0]?.match(/Piotroski (\d+)\/9/)?.[1],
    v: lt?.buffett_breakdown?.[1]?.match(/V-score (\d+)/)?.[1],
  }), [lt]);

  const pnlTone = (upnl ?? 0) >= 0 ? '#34d399' : '#f87171';

  return (
    <div style={{
      border: `1.5px solid ${palette.border}`,
      background: `linear-gradient(180deg, ${palette.bg} 0%, transparent 70%)`,
      borderRadius: 10,
      padding: '0.85rem 1rem',
      marginBottom: '0.9rem',
    }}>
      {/* ── 1. Header ─────────────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        gap: '0.8rem', flexWrap: 'wrap',
      }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.6rem',
          flexWrap: 'wrap', minWidth: 0, flex: 1,
        }}>
          <strong
            onClick={() => onTickerClick && onTickerClick(position.Ticker)}
            style={{
              fontSize: '1.2rem', cursor: onTickerClick ? 'pointer' : 'default',
              color: '#fff', letterSpacing: '0.02em',
            }}
          >{position.Ticker}</strong>
          {position.Sector && (
            <span style={{
              fontSize: '0.7rem', color: 'var(--text-muted)',
              whiteSpace: 'nowrap',
            }}>{position.Sector}</span>
          )}
          {cat && (
            <span title={cat.tip}
              style={{
                fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.06em',
                color: 'var(--text-muted)', cursor: 'help',
                whiteSpace: 'nowrap',
              }}
            >{cat.icon} {cat.label}</span>
          )}
          {Number.isFinite(lt?.confidence_score) && (() => {
            const s = lt.confidence_score;
            const tone = s >= 80 ? '#34d399' : s >= 60 ? '#cbd5e1' : s >= 40 ? '#fbbf24' : '#f87171';
            const tooltip = (lt.confidence_breakdown || []).join('\n');
            return (
              <span title={tooltip}
                style={{
                  fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.04em',
                  color: tone, cursor: 'help', whiteSpace: 'nowrap',
                  padding: '0.1rem 0.35rem', borderRadius: 3,
                  border: `1px solid ${tone}40`, background: `${tone}12`,
                }}
              >🛡 {s}</span>
            );
          })()}
        </div>
        <span style={{
          padding: '0.32rem 0.8rem', borderRadius: 6,
          background: palette.accent, color: '#0f172a',
          fontSize: '0.78rem', fontWeight: 800, letterSpacing: '0.06em',
          flexShrink: 0, whiteSpace: 'nowrap',
        }}>{palette.label}</span>
      </div>

      {/* ── 2. Reason ─────────────────────────────────────────── */}
      {(reasons.length > 0 || palette.cta) && (
        <div style={{
          marginTop: '0.6rem', fontSize: '0.82rem', color: 'var(--text-main)',
          lineHeight: 1.45,
        }}>
          {reasons.slice(0, 2).map((r, i) => (
            <div key={i} style={{ marginBottom: 2, display: 'flex', gap: 6 }}>
              <span style={{ color: palette.accent, flexShrink: 0 }}>•</span>
              <span style={{ minWidth: 0 }}>{r}</span>
            </div>
          ))}
          <div style={{
            fontSize: '0.74rem', color: 'var(--text-muted)',
            marginTop: 6, fontStyle: 'italic',
          }}>→ {palette.cta}</div>
        </div>
      )}

      {/* ── 3. Price matrix (no overlap) ─────────────────────── */}
      <PriceMatrix
        entry={entry}
        current={current}
        sl={sl}
        tp={tp}
        buffett_sl={lt?.buffett_sl}
        buffett_tp={lt?.buffett_tp}
        let_it_ride={lt?.buffett_let_it_ride}
      />

      {/* ── 4. KPIs ──────────────────────────────────────────── */}
      <KpiGrid>
        <Kpi label="Entry"     value={fmtUsd(entry)} />
        <Kpi
          label="P&L latent"
          value={upnl != null ? `${upnl >= 0 ? '+' : ''}$${upnl.toFixed(0)}` : '—'}
          sub={pnlPct != null ? fmtPct(pnlPct) : null}
          tone={pnlTone}
        />
        <Kpi label="Détention" value={days != null ? `${days} j` : '—'} />
        <Kpi
          label="Taille"
          value={position.Size != null ? `${position.Size}` : '—'}
          sub={position.RR ? `RR ${position.RR}` : null}
        />
      </KpiGrid>

      {/* ── 5. Detail (repliable) ─────────────────────────────── */}
      <div style={{ marginTop: '0.6rem' }}>
        <button
          onClick={() => setShowDetails(s => !s)}
          style={{
            background: 'transparent', border: 'none', color: 'var(--text-muted)',
            fontSize: '0.7rem', cursor: 'pointer', padding: 0,
            textDecoration: 'underline dotted', textUnderlineOffset: 2,
          }}
        >
          {showDetails ? '▾ Masquer' : '▸ Voir'} le détail Buffett{q && f && v ? ` · Q${q} · P${f} · V${v}` : ''}
        </button>
        {showDetails && (
          <div style={{
            marginTop: 6, padding: '0.6rem 0.75rem', borderRadius: 6,
            background: 'rgba(15,23,42,0.55)', fontSize: '0.7rem',
            color: 'var(--text-muted)', lineHeight: 1.55,
            fontFamily: 'ui-monospace, monospace',
          }}>
            {(lt?.buffett_breakdown || []).map((line, i) => (
              <div key={i} style={{ wordBreak: 'break-word' }}>{line}</div>
            ))}
            {lt?.thesis_status && (
              <div style={{ marginTop: 6, fontFamily: 'inherit' }}>
                Thèse fondamentale : <strong style={{ color: '#cbd5e1' }}>{lt.thesis_status}</strong>
              </div>
            )}
            {lt?.buffett_size_factor != null && lt.buffett_size_factor !== 1.0 && (
              <div style={{ fontFamily: 'inherit' }}>
                Sizing tilt : <strong style={{ color: '#cbd5e1' }}>×{lt.buffett_size_factor}</strong>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── 6. Actions ───────────────────────────────────────── */}
      <div style={{ marginTop: '0.7rem', display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        {onClickAdd && action === 'ADD_ON' && (
          <button
            onClick={() => onClickAdd(position)}
            style={{
              padding: '0.4rem 0.9rem', borderRadius: 5,
              border: `1px solid ${palette.accent}`,
              background: palette.bg, color: palette.accent, fontWeight: 700,
              cursor: 'pointer', fontSize: '0.75rem',
            }}
          >📈 Renforcer</button>
        )}
        {onClickClose && (
          <button
            onClick={() => onClickClose(position)}
            style={{
              padding: '0.4rem 0.9rem', borderRadius: 5,
              border: `1px solid ${action.startsWith('EXIT') ? palette.accent : 'rgba(148,163,184,0.40)'}`,
              background: action.startsWith('EXIT') ? palette.bg : 'transparent',
              color: action.startsWith('EXIT') ? palette.accent : 'var(--text-muted)',
              fontWeight: action.startsWith('EXIT') ? 700 : 500,
              cursor: 'pointer', fontSize: '0.75rem',
            }}
          >{action.startsWith('EXIT') ? '🚪 Clôturer' : 'Clôturer'}</button>
        )}
      </div>
    </div>
  );
}
