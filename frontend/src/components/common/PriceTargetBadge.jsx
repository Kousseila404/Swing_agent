// PriceTargetBadge.jsx — badge + tooltip pour le prix cible fondamental
// 12 mois (backend modules/price_target.py, docs/price_target_design.md).
// Même geste d'extraction que ConvictionBadge.jsx : composant partagé entre
// ProposalsPage.jsx (table proposals) et TickerAnalysisModal.jsx.
// Distinct du consensus analystes (yfinance targetMeanPrice) — ce badge
// affiche le modèle fondamental TITAN calibré (multiple + PEG + Buffett-TP).

import { fmtPrice, fmtSignedPct } from '../../utils/format.js';

const CONFIDENCE_STYLE = [
  { min: 70, bg: 'rgba(34,197,94,0.18)', fg: '#4ade80', border: 'rgba(34,197,94,0.6)' },
  { min: 40, bg: 'rgba(251,191,36,0.15)', fg: '#fbbf24', border: 'rgba(251,191,36,0.5)' },
  { min: 0, bg: 'rgba(148,163,184,0.08)', fg: 'var(--text-muted)', border: 'var(--border)' },
];

function styleForConfidence(confidence) {
  const c = Number.isFinite(confidence) ? confidence : 0;
  return CONFIDENCE_STYLE.find(s => c >= s.min) || CONFIDENCE_STYLE[CONFIDENCE_STYLE.length - 1];
}

export default function PriceTargetBadge({ fairValue }) {
  // Accepte à la fois le contexte proposition (champs plats price_target*,
  // sans method/components — voir auto_proposer.py::_build_proposal_from_alloc)
  // et le bloc "fair_value" complet de /api/ticker_analysis (avec method).
  const priceTarget = fairValue?.price_target;
  if (!Number.isFinite(priceTarget)) {
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

  const palette = styleForConfidence(fairValue.confidence ?? fairValue.price_target_confidence);
  const upside = fairValue.upside_pct;

  const tooltip = [
    `Prix cible fondamental 12 mois : ${fmtPrice(priceTarget)}`,
    `Fourchette : ${fmtPrice(fairValue.price_target_low)} — ${fmtPrice(fairValue.price_target_high)}`,
    `Upside vs prix courant : ${fmtSignedPct(upside)}`,
    `Confidence : ${fairValue.confidence ?? fairValue.price_target_confidence ?? '—'} / 100`,
    '',
    'Composantes (blend calibré) :',
    fairValue.components?.multiple != null ? `  Multiple sector-relative : ${fmtPrice(fairValue.components.multiple)}` : '',
    fairValue.components?.peg != null ? `  PEG-reversion : ${fmtPrice(fairValue.components.peg)}` : '',
    fairValue.components?.buffett != null ? `  Ancrage Buffett-TP : ${fmtPrice(fairValue.components.buffett)}` : '',
    fairValue.tilt_flags?.length ? `Tilt flags : ${fairValue.tilt_flags.join(', ')}` : '',
    '',
    'Modèle fondamental TITAN — distinct du consensus analystes.',
  ].filter(Boolean).join('\n');

  return (
    <div title={tooltip} style={{
      display: 'inline-flex', flexDirection: 'column', alignItems: 'center',
      padding: '0.18rem 0.5rem', borderRadius: 5,
      background: palette.bg, color: palette.fg,
      border: `1px solid ${palette.border}`,
      lineHeight: 1.1, minWidth: 64, cursor: 'help',
    }}>
      <span style={{ fontFamily: 'monospace', fontWeight: 800, fontSize: '0.85rem' }}>
        {fmtPrice(priceTarget)}
      </span>
      <span style={{ fontSize: '0.55rem', fontWeight: 700, letterSpacing: '0.02em', opacity: 0.95 }}>
        {fmtSignedPct(upside)}
      </span>
    </div>
  );
}
