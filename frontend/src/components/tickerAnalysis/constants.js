// Palettes / constantes pour TickerAnalysisModal et ses sous-composants.
//
// Centralisé ici pour éviter de gonfler le composant principal (1500 lignes).
// Toutes les valeurs sont des tones / couleurs / labels — aucune logique
// métier ne doit vivre dans ce fichier.

export const FLAG_PALETTE = {
  ok:     { bg: 'rgba(34,197,94,0.15)',  fg: '#4ade80', border: 'rgba(34,197,94,0.5)'  },
  warn:   { bg: 'rgba(251,191,36,0.15)', fg: '#fbbf24', border: 'rgba(251,191,36,0.5)' },
  danger: { bg: 'rgba(248,113,113,0.15)',fg: '#f87171', border: 'rgba(248,113,113,0.5)' },
  info:   { bg: 'rgba(96,165,250,0.12)', fg: '#60a5fa', border: 'rgba(96,165,250,0.45)' },
};

// Factor Grades (lettres A+/A/B+/B/C+/C/D/F).
export const GRADE_PALETTE = {
  'A+': { bg: 'rgba(34,197,94,0.20)',  fg: '#22c55e' },
  'A':  { bg: 'rgba(34,197,94,0.15)',  fg: '#4ade80' },
  'B+': { bg: 'rgba(132,204,22,0.18)', fg: '#a3e635' },
  'B':  { bg: 'rgba(132,204,22,0.12)', fg: '#bef264' },
  'C+': { bg: 'rgba(251,191,36,0.18)', fg: '#fbbf24' },
  'C':  { bg: 'rgba(251,191,36,0.12)', fg: '#fcd34d' },
  'D':  { bg: 'rgba(248,113,113,0.15)',fg: '#fb7185' },
  'F':  { bg: 'rgba(248,113,113,0.20)',fg: '#f87171' },
  'N/A':{ bg: 'rgba(148,163,184,0.10)',fg: 'var(--text-muted)' },
};

// Quant Rating (TITAN composite mapping).
export const RATING_LABEL = {
  STRONG_BUY:  { label: 'STRONG BUY',   bg: 'rgba(34,197,94,0.20)',  fg: '#22c55e' },
  BUY:         { label: 'BUY',          bg: 'rgba(132,204,22,0.18)', fg: '#a3e635' },
  HOLD:        { label: 'HOLD',         bg: 'rgba(251,191,36,0.16)', fg: '#fbbf24' },
  SELL:        { label: 'SELL',         bg: 'rgba(251,113,113,0.18)',fg: '#fb7185' },
  STRONG_SELL: { label: 'STRONG SELL',  bg: 'rgba(248,113,113,0.22)',fg: '#f87171' },
  'N/A':       { label: 'N/A',          bg: 'rgba(148,163,184,0.12)',fg: 'var(--text-muted)' },
};

export const DIVIDEND_LEVEL_TONE = {
  VERY_SAFE: '#22c55e', SAFE: '#4ade80', MODERATE: '#fbbf24',
  RISKY: '#fb7185', UNSAFE: '#f87171',
  NO_DIVIDEND: 'var(--text-muted)', INSUFFICIENT_DATA: 'var(--text-muted)',
};

export const SURPRISE_LEVEL_TONE = {
  STRONG_BEAT: '#22c55e', BEAT: '#4ade80', INLINE: 'var(--text-muted)',
  MISS: '#fb7185', STRONG_MISS: '#f87171', INSUFFICIENT_DATA: 'var(--text-muted)',
};

export const BUY_SIGNAL_PALETTE = {
  STRONG_BUY:        { bg: 'rgba(34,197,94,0.22)',  fg: '#22c55e', icon: '🟢🟢', big: true },
  BUY:               { bg: 'rgba(132,204,22,0.20)', fg: '#84cc16', icon: '🟢',   big: true },
  WATCH:             { bg: 'rgba(251,191,36,0.18)', fg: '#fbbf24', icon: '👁️',   big: false },
  EARNINGS_BLACKOUT: { bg: 'rgba(248,113,113,0.20)',fg: '#f87171', icon: '⏸️',   big: false },
  CHEAP_JUNK:        { bg: 'rgba(248,113,113,0.20)',fg: '#f87171', icon: '⚠️',   big: false },
  FALLING_KNIFE:     { bg: 'rgba(248,113,113,0.20)',fg: '#f87171', icon: '🔻',   big: false },
  SKIP:              { bg: 'rgba(148,163,184,0.10)',fg: 'var(--text-muted)', icon: '—', big: false },
  NO_DATA:           { bg: 'rgba(148,163,184,0.08)',fg: 'var(--text-muted)', icon: '?', big: false },
};

export const ENTRY_RECO_PALETTE = {
  WAIT_PULLBACK: { bg: 'rgba(248,113,113,0.18)', fg: '#f87171', icon: '⏸️',
                   label: 'Wait pullback' },
  SPLIT_3:       { bg: 'rgba(251,191,36,0.18)',  fg: '#fbbf24', icon: '📊',
                   label: 'Entry fractionnée (3 tranches)' },
  SPLIT_2:       { bg: 'rgba(132,204,22,0.16)',  fg: '#a3e635', icon: '⚖️',
                   label: 'Entry fractionnée (2 tranches)' },
  MARKET_FULL:   { bg: 'rgba(34,197,94,0.18)',   fg: '#22c55e', icon: '🟢',
                   label: 'Market — full size' },
};

// Phase 2 SL/TP — thesis_stop fondamental.
export const THESIS_PALETTE = {
  INTACT:  { bg: 'rgba(34,197,94,0.16)',   fg: '#22c55e', icon: '🟢',
             label: 'Thèse intacte' },
  WARN:    { bg: 'rgba(251,191,36,0.18)',  fg: '#fbbf24', icon: '⚠️',
             label: 'Thèse en alerte' },
  BROKEN:  { bg: 'rgba(248,113,113,0.20)', fg: '#f87171', icon: '🔴',
             label: 'Thèse cassée — exit recommandé' },
  NO_DATA: { bg: 'rgba(148,163,184,0.10)', fg: 'var(--text-muted)', icon: '?',
             label: "Pas d'entry scores" },
};

// SEC filings (formes EDGAR) — palette par type.
export const FORM_TONE = {
  '10-K':   { bg: 'rgba(34,197,94,0.18)',  fg: '#4ade80', icon: '📘' },
  '10-K/A': { bg: 'rgba(34,197,94,0.12)',  fg: '#4ade80', icon: '📘' },
  '10-Q':   { bg: 'rgba(132,204,22,0.16)', fg: '#a3e635', icon: '📗' },
  '10-Q/A': { bg: 'rgba(132,204,22,0.12)', fg: '#a3e635', icon: '📗' },
  '8-K':    { bg: 'rgba(251,191,36,0.18)', fg: '#fbbf24', icon: '⚡' },
  '8-K/A':  { bg: 'rgba(251,191,36,0.12)', fg: '#fbbf24', icon: '⚡' },
  '4':      { bg: 'rgba(168,85,247,0.16)', fg: '#c084fc', icon: '🏛️' },
  '4/A':    { bg: 'rgba(168,85,247,0.12)', fg: '#c084fc', icon: '🏛️' },
  '13F-HR': { bg: 'rgba(96,165,250,0.16)', fg: '#60a5fa', icon: '🐋' },
  'DEF 14A':{ bg: 'rgba(148,163,184,0.16)', fg: 'var(--text-muted)', icon: '🗳️' },
  'S-1':    { bg: 'rgba(248,113,113,0.16)', fg: '#fb7185', icon: '🚀' },
  '20-F':   { bg: 'rgba(34,197,94,0.16)',   fg: '#4ade80', icon: '🌐' },
};

// Tabs du modal — onglets de navigation latérale.
export const MODAL_TABS = [
  { id: 'overview',     icon: '🎯', label: 'Aperçu' },
  { id: 'fundamentals', icon: '💰', label: 'Fondamentaux' },
  { id: 'action',       icon: '📈', label: 'Action & catalyseurs' },
  { id: 'peers',        icon: '🤝', label: 'Peers & analystes' },
  { id: 'news',         icon: '📰', label: 'News' },
  { id: 'notes',        icon: '📝', label: 'Notes' },
];
