// kpiCompare.js — config KPI + logique de coloration partagées par les vues
// de comparaison de tickers (PeerComparison auto-sectoriel + ComparePage
// multi-tickers manuel). Extrait de PeerComparison.jsx pour éviter de
// dupliquer les mêmes 10 lignes/formattage entre les deux vues.

import { fmtNum, fmtPct } from './format.js';

export const KPI_ROWS = [
  { key: 'titan_composite_score', label: 'TITAN', fmt: (v) => fmtNum(v, 1), goodHigh: true },
  { key: 'trailing_pe',           label: 'P/E TTM', fmt: (v) => fmtNum(v, 1), goodHigh: false, ignoreNegative: true },
  { key: 'forward_pe',            label: 'Fwd P/E', fmt: (v) => fmtNum(v, 1), goodHigh: false, ignoreNegative: true },
  { key: 'ev_to_ebitda',          label: 'EV/EBITDA', fmt: (v) => fmtNum(v, 1), goodHigh: false, ignoreNegative: true },
  { key: 'return_on_equity',      label: 'ROE', fmt: (v) => fmtPct(v, 1), goodHigh: true },
  { key: 'operating_margin',      label: 'Op margin', fmt: (v) => fmtPct(v, 1), goodHigh: true },
  { key: 'revenue_growth',        label: 'Rev growth', fmt: (v) => fmtPct(v, 1), goodHigh: true },
  { key: 'earnings_growth',       label: 'EPS growth', fmt: (v) => fmtPct(v, 1), goodHigh: true },
  { key: 'debt_to_equity',        label: 'D/E', fmt: (v) => fmtNum(v, 1), goodHigh: false },
  { key: 'dividend_yield',        label: 'Div yield', fmt: (v) => fmtPct(v, 2), goodHigh: true },
];

// Couleur d'une valeur relative à une référence (médiane) : vert si "mieux",
// rouge si "moins bien", neutre si proche (~5%) ou données insuffisantes.
export function toneFor(value, reference, goodHigh, ignoreNegative) {
  if (value == null || reference == null || !Number.isFinite(value) || !Number.isFinite(reference)) return 'var(--text-muted)';
  if (ignoreNegative && (value < 0 || reference <= 0)) return 'var(--text-muted)';
  const delta = value - reference;
  if (Math.abs(delta) / Math.max(Math.abs(reference), 1e-9) < 0.05) return undefined; // ~ référence → neutre
  const isBetter = goodHigh ? delta > 0 : delta < 0;
  return isBetter ? '#4ade80' : '#f87171';
}
