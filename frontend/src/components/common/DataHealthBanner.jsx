// DataHealthBanner — bandeau global affichant la santé des providers data.
//
// Visible UNIQUEMENT quand severity_global ∈ {warning, critical}. En "ok"
// le composant ne rend rien (silence). Pour le détail, l'utilisateur clique
// sur "Détails" → page dédiée DataHealthPage.

import { useDataHealth } from '../../hooks/useApi';

const SEVERITY_CONFIG = {
  warning: {
    bg: 'rgba(251, 191, 36, 0.15)',
    border: '#fbbf24',
    color: '#fde68a',
    icon: '⚠️',
    label: 'WARNING',
  },
  critical: {
    bg: 'rgba(239, 68, 68, 0.15)',
    border: '#ef4444',
    color: '#fca5a5',
    icon: '🔴',
    label: 'CRITICAL',
  },
};

function summary(d) {
  /** Résume en 1 phrase ce qui ne va pas — priorise le plus impactant. */
  const reasons = [];
  if (d.yf_breaker?.tripped) {
    const ageMin = Math.round((d.yf_breaker.tripped_age_sec || 0) / 60);
    const resetMin = Math.round((d.yf_breaker.auto_reset_in_sec || 0) / 60);
    reasons.push(`yfinance rate-limited (depuis ${ageMin} min, auto-reset dans ${resetMin} min)`);
  }
  const fa = d.universe?.fetched_at;
  if (fa?.n_severe > 0) {
    reasons.push(`${fa.n_severe} ticker(s) avec data > ${fa.severe_threshold_days}j`);
  }
  // Pire field manquant
  const fields = d.universe?.fields_missing || {};
  const worstField = Object.entries(fields)
    .filter(([, v]) => v.severity !== 'ok')
    .sort((a, b) => b[1].missing_pct - a[1].missing_pct)[0];
  if (worstField) {
    reasons.push(`${worstField[0]} manquant pour ${worstField[1].missing_pct.toFixed(0)}% des tickers`);
  }
  if (d.fmp?.quota_exhausted) reasons.push('FMP quota épuisée');
  return reasons.slice(0, 2).join(' · ') || 'Dégradation détectée';
}

export default function DataHealthBanner({ onShowDetails }) {
  const { data, isError } = useDataHealth();

  if (isError || !data) return null;
  const sev = data.severity_global;
  if (sev === 'ok') return null;

  const cfg = SEVERITY_CONFIG[sev] || SEVERITY_CONFIG.warning;

  return (
    <div role="status" aria-live="polite" style={{
      background: cfg.bg,
      border: `1px solid ${cfg.border}`,
      color: cfg.color,
      padding: '8px 14px',
      margin: '0 0 12px 0',
      borderRadius: 8,
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      fontSize: '0.85rem',
    }}>
      <span style={{ fontSize: '1rem' }}>{cfg.icon}</span>
      <strong style={{ minWidth: 70 }}>{cfg.label}</strong>
      <span style={{ flex: 1 }}>{summary(data)}</span>
      {onShowDetails && (
        <button
          type="button"
          onClick={onShowDetails}
          style={{
            background: 'transparent',
            border: `1px solid ${cfg.border}`,
            color: cfg.color,
            padding: '4px 10px',
            borderRadius: 6,
            fontSize: '0.78rem',
            cursor: 'pointer',
            fontWeight: 600,
          }}
          aria-label="Voir détails santé data"
        >
          Détails
        </button>
      )}
    </div>
  );
}
