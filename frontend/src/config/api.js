// Constantes de configuration React Query — source unique pour les
// intervalles de polling et les durées de fraîcheur des caches UI.
//
// Convention :
//   - POLL.* : refetchInterval (millisecondes) — fréquence d'auto-refresh
//   - STALE.* : staleTime (ms) — durée pendant laquelle on considère
//               la donnée fraîche (n'invalide pas, n'auto-refetch pas)
//
// Aligner ces valeurs sur les TTL du cache backend pour éviter de tirer
// pour rien. Si le backend cache 1h, mettre staleTime ≥ 30min côté UI.

export const POLL = {
  STATUS:        15_000,    // /status — 15s
  STATUS_FAST:   30_000,    // 30s — usage paliers
  MARKET_STATUS: 60_000,    // /market_status — bascule 2× par jour
  PORTFOLIO:     15_000,
  PROPOSALS:     30_000,
  DATA_HEALTH:   60_000,
  JOB:            2_000,    // poll un job en cours
};

export const STALE = {
  STATUS:        30_000,
  TICKER_HISTORY: 60_000,
  PERFORMANCE:   60_000,
  WATCHLIST:     30_000,
  NOTES:         30_000,
  PORTFOLIO:     30_000,
  CALENDAR:      5 * 60_000,        // 5 min
  ATTRIBUTION:   5 * 60_000,
  MACRO_CALENDAR: 5 * 60_000,
  SNAPSHOTS:     60_000,
  SECTORS:       60_000,
  NEWS_FIREHOSE: 10 * 60_000,        // 10 min (backend 1h, UI 10min)
  SEC_FILINGS:   60 * 60_000,        // 1h (backend 6h)
  NEWS:          30 * 60_000,        // 30 min (backend 1h)
  SECTOR_BENCH:  30 * 60_000,        // 30 min (backend 1h)
  AUDIT_FULL:    30 * 60_000,        // backend 24h, UI 30min
  WFO:           60 * 60_000,        // 1h (cron mensuel)
  DELISTED:      5 * 60_000,
};
