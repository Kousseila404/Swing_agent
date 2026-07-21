// ConvictionBadge.jsx — badge + tooltip narratif partagés pour le signal de
// qualification (backend modules/signal_qualification.py, Étape 1 roadmap).
// Extrait de ProposalsPage.jsx (Étape 15 roadmap) pour être réutilisé aussi
// par TickerAnalysisModal.jsx — même geste que l'extraction utils/kpiCompare.js
// de l'Étape 4. Style/rang partagés dans utils/conviction.js.

import { CONVICTION_STYLE } from '../../utils/conviction.js';

export default function ConvictionBadge({ qualification }) {
  const conviction = qualification?.conviction;
  const s = CONVICTION_STYLE[conviction];
  if (!s) return null;
  const tooltipParts = [qualification.narrative];
  if (qualification.causal_reasons?.length) {
    tooltipParts.push(qualification.causal_reasons.join(' · '));
  }
  const trend = qualification.trend;
  if (trend?.score_delta != null) {
    tooltipParts.push(
      `Score ${trend.score_delta >= 0 ? '+' : ''}${trend.score_delta.toFixed(1)} vs il y a ${trend.lookback_days}j`
    );
  }
  return (
    <span title={tooltipParts.filter(Boolean).join('\n')} style={{
      display: 'inline-flex', alignItems: 'center',
      fontSize: '0.62rem', fontWeight: 800, letterSpacing: '0.02em',
      padding: '0.15rem 0.5rem', borderRadius: 4,
      background: s.bg, color: s.fg,
      border: `1px solid ${s.fg}`,
      whiteSpace: 'nowrap', cursor: 'help',
    }}>
      {s.label}
    </span>
  );
}
