// Color-coding factoriel STRICT (règle TITAN partagée avec la data-table) :
//   > 75           → Vert (Leader)
//   40 ≤ s ≤ 75    → Gris/Neutre (Moyen)
//   < 40           → Rouge (Danger)
// Utilisé par SectorCard, SectorsPage et RecommendationsPanel.
export function factorColor(score) {
  if (score == null || !isFinite(score)) return 'var(--text-muted)';
  if (score > 75) return 'var(--success)';
  if (score >= 40) return '#94a3b8';  // slate-400, neutre propre en dark mode
  return 'var(--danger)';
}
