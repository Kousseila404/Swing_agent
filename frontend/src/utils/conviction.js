// conviction.js — style/tri de la segmentation conviction (backend
// modules/signal_qualification.py, Étape 1 roadmap). Séparé de
// ConvictionBadge.jsx (composant) car un fichier de composant React ne peut
// exporter que des composants sans casser le Fast Refresh (règle eslint
// react-refresh/only-export-components).

export const CONVICTION_STYLE = {
  new_signal: { label: '🔥 Nouveau', bg: 'rgba(251,146,60,0.18)', fg: '#fb923c' },
  confirmed:  { label: '⭐ Confirmé', bg: 'rgba(34,197,94,0.16)', fg: '#22c55e' },
  watch:      { label: '👁 Surveillance', bg: 'rgba(251,191,36,0.16)', fg: '#fbbf24' },
};
export const CONVICTION_RANK = { new_signal: 3, confirmed: 2, watch: 1, other: 0 };
