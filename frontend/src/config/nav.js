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
      { id: 'cockpit',     label: 'Cockpit',       icon: '🧭' },
      { id: 'proposals',   label: 'Propositions',  icon: '📬' },
      { id: 'portfolio',   label: 'Portfolio',     icon: '📊' },
    ],
  },
  {
    label: 'Recherche',
    items: [
      { id: 'universe',    label: 'Univers',       icon: '🌐' },
      { id: 'scoring_lab', label: 'Scoring Lab',   icon: '🧪' },
    ],
  },
  {
    label: 'Mon Portefeuille',
    items: [
      { id: 'my_portfolio', label: 'Mon Portefeuille', icon: '💼' },
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
  cockpit:     { title: 'Cockpit',                       subtitle: 'Compte vs SPY vs panier TITAN · Protection des positions · Santé système' },
  universe:    { title: 'Univers Quantamental',          subtitle: 'Smart Beta · Rotation Sectorielle · Fondamentaux yfinance' },
  portfolio:   { title: 'Portfolio & Journal',           subtitle: 'Données réelles — trade_journal.csv' },
  my_portfolio: { title: 'Mon Portefeuille',             subtitle: 'Book personnel long terme — allocation manuelle, indépendant du moteur TITAN' },
  proposals:   { title: 'Propositions auto · Veto humain', subtitle: 'Trades suggérés par TITAN — approuver ou rejeter' },
  scoring_lab: { title: 'Scoring Lab · edge sur la fenêtre live', subtitle: 'Paniers top-N vs univers · piliers seuls · profils de poids — sans look-ahead' },
  settings:    { title: 'Préférences',                   subtitle: 'Apparence · API Token · Defaults Propositions' },
};

// Hints contextuels pour la palette de commandes (Cmd+K).
export const PAGE_PALETTE_HINTS = {
  briefing:    "Page · Briefing du jour",
  watchlist:   "Page · Tickers observés",
  universe:    "Page · Univers Quantamental",
  sectors:     "Page · Rotation sectorielle",
  portfolio:   "Page · Positions et journal",
  my_portfolio: "Page · Book perso LT — 10 positions manuelles",
  proposals:   "Page · Veto humain TITAN",
  performance: "Page · Sharpe, Sortino, DD",
  attribution: "Page · Win rate par bucket TITAN entry",
  ticker:      "Page · Score history",
  compare:     "Page · Comparaison multi-tickers",
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
