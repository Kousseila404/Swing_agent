// Performance helpers — extraits de PerformancePage.jsx pour tests unitaires.

export const INITIAL_CAPITAL = 100_000;

export function histogramBins(values, nBins = 20) {
  if (!values || values.length === 0) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (max === min) {
    return [{
      bin: min.toFixed(0),
      count: values.length,
      color: min >= 0 ? 'var(--success)' : 'var(--danger)',
    }];
  }
  const width = (max - min) / nBins;
  const bins = Array.from({ length: nBins }, (_, i) => ({
    lo: min + i * width,
    hi: min + (i + 1) * width,
    count: 0,
  }));
  for (const v of values) {
    const idx = Math.min(nBins - 1, Math.floor((v - min) / width));
    bins[idx].count += 1;
  }
  return bins.map(b => ({
    bin: b.hi.toFixed(0),
    count: b.count,
    color: b.hi >= 0 ? 'var(--success)' : 'var(--danger)',
  }));
}

// Peak initialisé à INITIAL_CAPITAL pour rester cohérent avec l'équité de départ du compte.
export function computeUnderwater(equityByDate, initialCapital = INITIAL_CAPITAL) {
  let peak = initialCapital;
  return equityByDate.map(([d, v]) => {
    if (v > peak) peak = v;
    const dd = peak > 0 ? ((v - peak) / peak) * 100 : 0;
    return { date: String(d).slice(0, 10), dd: Number(dd.toFixed(2)) };
  });
}
