// nav.js — source unique de vérité pour la navigation.
//
// Toute la structure des pages (sections, icônes, libellés, sous-titres)
// vit ici. Consommée par :
//   - App.jsx (sidebar + header breadcrumb + routing)
//   - CommandPalette.jsx (jump-to-page + actions globales)
//
// Ajouter une page = ajouter une entrée dans NAV_SECTIONS + son meta dans
// PAGE_META + le mapping dans le routeur App.jsx. C'est tout.

export const NAV_SECTIONS = [
  {
    label: 'Pilotage',
    items: [
      { id: 'briefing',    label: 'Briefing',      icon: '☀️' },
      { id: 'proposals',   label: 'Propositions',  icon: '📬' },
      { id: 'portfolio',   label: 'Portfolio',     icon: '📊' },
    ],
  },
  {
    label: 'Découverte',
    items: [
      { id: 'watchlist',   label: 'Watchlist',     icon: '👁' },
      { id: 'universe',    label: 'Univers',       icon: '🌐' },
      { id: 'sectors',     label: 'Secteurs',      icon: '🏛' },
      { id: 'ticker',      label: 'Ticker Detail', icon: '🎯' },
    ],
  },
  {
    label: 'Catalyseurs',
    items: [
      { id: 'calendar',    label: 'Catalysts',     icon: '🗓' },
      { id: 'news',        label: 'News',          icon: '📰' },
      { id: 'macro',       label: 'Macro',         icon: '📅' },
    ],
  },
  {
    label: 'Santé',
    items: [
      { id: 'performance', label: 'Performance',   icon: '📈' },
      { id: 'attribution', label: 'Attribution',   icon: '🎲' },
      { id: 'risk',        label: 'Risk Monitor',  icon: '⚠️' },
      { id: 'datahealth',  label: 'Data Health',   icon: '🩺' },
      { id: 'audit',       label: 'Audit',         icon: '🔍' },
    ],
  },
  {
    label: 'Système',
    items: [
      { id: 'settings',    label: 'Préférences',   icon: '⚙️' },
    ],
  },
];

export const PAGE_META = {
  briefing:    { title: 'Briefing du jour',              subtitle: 'Régime · Macro · Action requise · Positions à surveiller' },
  watchlist:   { title: 'Watchlist & Notes',             subtitle: 'Tickers observés · Thèses & rappels personnels' },
  universe:    { title: 'Univers Quantamental',          subtitle: 'Smart Beta · Rotation Sectorielle · Fondamentaux yfinance' },
  sectors:     { title: 'Rotation Sectorielle',          subtitle: '11 secteurs GICS · Momentum 6M · Rotation Score composite' },
  portfolio:   { title: 'Portfolio & Journal',           subtitle: 'Données réelles — trade_journal.csv' },
  proposals:   { title: 'Propositions auto · Veto humain', subtitle: 'Trades suggérés par TITAN — approuver ou rejeter' },
  performance: { title: 'Performance & Métriques',       subtitle: 'Sharpe · Sortino · Calmar · DD · Expectancy · Distribution PnL' },
  attribution: { title: 'Performance Attribution',       subtitle: "Win rate par bucket de score TITAN à l'entrée — calibration du moteur" },
  ticker:      { title: 'Ticker Detail · Score history', subtitle: "Évolution scores TITAN d'un ticker via universe_history" },
  datahealth:  { title: 'Data Health · Providers + cache', subtitle: 'Santé providers + cache fundamentals + fields manquants' },
  risk:        { title: 'Risk Monitor',                  subtitle: 'Budget · VIX · concentration · Kelly' },
  calendar:    { title: 'Catalyst Calendar',             subtitle: 'Earnings (positions + watchlist) + Macro consolidés' },
  news:        { title: 'News firehose',                 subtitle: 'Flux consolidé positions + watchlist · Finnhub' },
  macro:       { title: 'Calendrier Macro',              subtitle: 'FOMC · CPI · NFP — zones de blackout J-1' },
  audit:       { title: 'Audit · Survivorship + WFO',    subtitle: 'Registry delisted · Poids OOS vs prod · IC test history' },
  settings:    { title: 'Préférences',                   subtitle: 'Apparence · API Token · Defaults Propositions' },
};

// Hints contextuels pour la palette de commandes (Cmd+K).
export const PAGE_PALETTE_HINTS = {
  briefing:    "Page · Briefing du jour",
  watchlist:   "Page · Tickers observés",
  universe:    "Page · Univers Quantamental",
  sectors:     "Page · Rotation sectorielle",
  portfolio:   "Page · Positions et journal",
  proposals:   "Page · Veto humain TITAN",
  performance: "Page · Sharpe, Sortino, DD",
  attribution: "Page · Win rate par bucket TITAN entry",
  ticker:      "Page · Score history",
  datahealth:  "Page · Providers et cache",
  risk:        "Page · Budget et VIX",
  calendar:    "Page · Earnings + Macro consolidés",
  news:        "Page · Firehose positions + watchlist",
  macro:       "Page · Calendrier événements",
  audit:       "Page · Survivorship + WFO",
  settings:    "Page · Apparence + Defaults + API Token",
};

// Cibles pour la palette : flatten + hint contextuel.
export const NAV_TARGETS = NAV_SECTIONS.flatMap((sec) =>
  sec.items.map((it) => ({
    id: it.id,
    label: it.label,
    icon: it.icon,
    hint: PAGE_PALETTE_HINTS[it.id] || `Page · ${it.label}`,
  })),
);

// Mapping page → nom de section (pour le breadcrumb).
export const SECTION_BY_PAGE = Object.fromEntries(
  NAV_SECTIONS.flatMap((sec) => sec.items.map((it) => [it.id, sec.label])),
);

// Ids valides (white-list pour le hash routing).
export const VALID_PAGES = NAV_SECTIONS.flatMap((s) => s.items.map((i) => i.id));
