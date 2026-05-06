// Helpers purs partagés entre les sections du TickerAnalysisModal.

// Tone : positif vert / négatif rouge / null muted.
export function pctToneSigned(v) {
  if (v == null || !Number.isFinite(v)) return 'var(--text-muted)';
  return v >= 0 ? 'var(--success)' : 'var(--danger)';
}

// Formate un timestamp en "il y a Xmin / Xh / Xj".
export function relTime(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    const ms = Date.now() - d.getTime();
    const m = Math.round(ms / 60_000);
    if (m < 60) return `il y a ${m}min`;
    const h = Math.round(m / 60);
    if (h < 24) return `il y a ${h}h`;
    const j = Math.round(h / 24);
    return `il y a ${j}j`;
  } catch {
    return iso;
  }
}
