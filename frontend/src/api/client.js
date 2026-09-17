// API Client — FastAPI backend (port 8000, proxied via /api en dev).
//
// GET throw ApiError sur non-2xx / réseau KO → RQ gère retry + error states.
// POST renvoie {ok, ...} (pas de throw) pour que les call-sites mutants
// puissent afficher res.detail sans try/catch.
//
// Token : localStorage.api_token (runtime, dev) prioritaire sur VITE_API_TOKEN
// (build). Localstorage est XSS-visible — acceptable pour SPA interne
// mono-user (cf memory reference_api_auth.md pour le trade-off).

const BASE = '/api';

const _getToken = () => {
  try {
    const ls = typeof localStorage !== 'undefined' ? localStorage.getItem('api_token') : null;
    if (ls) return ls;
  } catch { /* localStorage bloqué (Safari private, SSR…) → fallback env */ }
  return (import.meta?.env?.VITE_API_TOKEN || '').toString();
};

const _authHeaders = () => {
  const t = _getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
};

// status = 0 quand le fetch a failed côté réseau (DNS, connexion refusée…).
export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

const get = async (path, opts = {}) => {
  let r;
  try {
    r = await fetch(`${BASE}${path}`, {
      cache: 'no-store',
      ...opts,
      headers: { ..._authHeaders(), ...(opts.headers || {}) },
    });
  } catch {
    throw new ApiError('Réseau indisponible', 0);
  }
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const d = await r.json();
      if (d?.detail) detail = typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail);
    } catch { /* body non-JSON → garder detail par défaut */ }
    throw new ApiError(detail, r.status);
  }
  try {
    return await r.json();
  } catch {
    throw new ApiError('Réponse non-JSON du backend', r.status);
  }
};

const post = (path, body = {}) =>
  fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ..._authHeaders(),
    },
    body: JSON.stringify(body),
  })
    .then(async r => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok) return { ok: false, status: r.status, ...data };
      return { ok: true, ...data };
    })
    .catch(() => ({ ok: false, error: 'Réseau indisponible' }));

export const fetchStatus = () => get('/status');
export const fetchMarketStatus = () => get('/market_status');

export const fetchPortfolio          = () => get('/portfolio');
export const fetchEquityCurve        = () => get('/equity_curve');
export const fetchPerformanceMetrics = () => get('/performance_metrics');

export const fetchMacro         = () => get('/macro');
export const fetchMacroCalendar = (horizonDays = 60) => get(`/macro_calendar?horizon_days=${horizonDays}`);

export const fetchUniverse = (sector) => {
  const qs = sector ? `?sector=${encodeURIComponent(sector)}` : '';
  return get(`/universe${qs}`);
};
export const rebuildUniverse = (opts = {}) => post('/universe/rebuild', opts);

// refresh_momentum=true bypass le cache sectors (yfinance batch) → require auth.
export const fetchSectors = (opts = {}) => {
  const qs = opts.refreshMomentum ? '?refresh_momentum=true' : '';
  return get(`/sectors${qs}`);
};
export const fetchSector = (name) => get(`/sectors/${encodeURIComponent(name)}`);

// Job polling — limité au /jobs/{id} et /job/{id}/kill consommés par
// UniverseManagerPage (watch rebuild). /jobs liste et /job/run supprimés.
export const fetchJob = (id) => get(`/jobs/${id}`);
export const killJob  = (id) => post(`/job/${id}/kill`);

export const addTrade   = (data) => post('/trade/add', data);
export const closeTrade = (data) => post('/trade/close', data);

// ─────────────────────────────────────────────────────────────────
// PROPOSALS — file de propositions d'achat avec veto humain.
// GET public, mutations exigent le Bearer token.
// ─────────────────────────────────────────────────────────────────
export const fetchProposals = ({ status = null, limit = 200 } = {}) => {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (limit)  params.set('limit', String(limit));
  const qs = params.toString();
  return get(`/proposals${qs ? `?${qs}` : ''}`);
};

export const refreshProposals  = (body = {}) => post('/proposals/refresh', body);
// Push manuel d'un ticker depuis la page Univers (bypass cron auto_proposer).
// Réutilise le pipeline SL/TP σ-adaptive et la dédup pending/cooldown.
export const pushManualProposal = (ticker, opts = {}) =>
  post('/proposals/manual', { ticker, ...opts });
// Purge les pending (via expired, pas rejected → pas de cooldown veto) puis
// relance le proposer. Utile pour changer les paramètres sans perdre 7j.
export const regenerateProposals = (body = {}) => post('/proposals/regenerate', body);
export const approveProposal   = (id) => post(`/proposals/${encodeURIComponent(id)}/approve`);
export const rejectProposal    = (id, reason = 'user_veto') =>
  post(`/proposals/${encodeURIComponent(id)}/reject`, { reason });

// Bulk approve avec overrides éditables par proposition.
// items = [{id, overrides?: {entry, stop_loss, take_profit, size},
//           ack_sector_warning?: bool, allow_top_up?: bool}]
export const approveProposalsBatch = (items) =>
  post('/proposals/approve_batch', { items });

// Bulk veto.  items = [{id, reason?}]
export const rejectProposalsBatch  = (items) =>
  post('/proposals/reject_batch', { items });

// ─────────────────────────────────────────────────────────────────
// HISTORIQUE TICKER + DATA HEALTH
// ─────────────────────────────────────────────────────────────────
export const fetchSnapshotsList = () => get('/history/snapshots');
// Backtest TITAN sur une whitelist de tickers (vue filtrée page Univers).
// body = { tickers: [], top_n?: int, benchmark?: str, weighting?: 'equal'|'score'|'risk_parity' }
export const runQuickBacktest = (body) => post('/backtest/quick', body);
// Alertes TITAN seuil utilisateur (Tier B #3).
export const fetchTitanAlerts  = () => get('/titan_alerts');
export const addTitanAlert     = (body) => post('/titan_alerts', body);
export const deleteTitanAlert  = (id) =>
  _del(`/titan_alerts/${encodeURIComponent(id)}`);
// Alertes prix multi-niveaux (entry_plan tiers).
export const fetchPriceAlerts  = (ticker) =>
  get(`/price_alerts${ticker ? `?ticker=${encodeURIComponent(ticker)}` : ''}`);
export const fetchPriceAlertsStats = () => get('/price_alerts/stats');
export const addPriceAlert     = (body) => post('/price_alerts', body);
export const deletePriceAlert  = (id) =>
  _del(`/price_alerts/${encodeURIComponent(id)}`);
// Thesis status — vue cockpit positions OPEN.
export const fetchThesisStatus = () => get('/thesis_status');
// LT decision — refonte 2026-04-29 (Buffett-style). Agrégat des 4 couches
// (catastrophe / thesis / valuation / add-on) par position OPEN.
export const fetchLtDecision   = () => get('/lt_decision');
export const fetchLtDecisionForTicker = (ticker) =>
  get(`/lt_decision/${encodeURIComponent(ticker)}`);
// Backfill rétroactif des *_Entry depuis universe_history (admin).
export const backfillEntryScores = (dryRun = true) =>
  post(`/portfolio/backfill_entry_scores?dry_run=${dryRun ? 'true' : 'false'}`, {});
export const fetchDataHealth    = () => get('/data_health');
export const refreshFlaggedTickers = () => post('/data_health/refresh_flagged', {});
export const fetchTickerHistory = (ticker, opts = {}) => {
  const params = new URLSearchParams();
  if (opts.start) params.set('start', opts.start);
  if (opts.end)   params.set('end',   opts.end);
  if (opts.fields) params.set('fields', opts.fields);
  const qs = params.toString();
  return get(`/history/ticker/${encodeURIComponent(ticker)}${qs ? `?${qs}` : ''}`);
};

// ─────────────────────────────────────────────────────────────────
// AUDIT — delisted registry + WFO weights/history (Audit S1.1 + S1.3).
// Endpoints publics read-only.
// ─────────────────────────────────────────────────────────────────
export const fetchDelisted    = () => get('/delisted');
export const fetchWfoWeights  = () => get('/wfo');
export const fetchWfoHistory  = ({ limit = 50 } = {}) =>
  get(`/wfo/history?limit=${encodeURIComponent(limit)}`);
export const fetchAuditFull   = ({ refresh = false } = {}) =>
  get(`/audit/full${refresh ? '?refresh=true' : ''}`);

export const fetchTickerAnalysis = (ticker) =>
  get(`/ticker_analysis/${encodeURIComponent(ticker.toUpperCase())}`);

export const fetchPeers = (ticker, n = 5) =>
  get(`/peers/${encodeURIComponent(ticker.toUpperCase())}?n=${n}`);

export const fetchCompare = (tickers) =>
  get(`/compare?tickers=${encodeURIComponent(tickers.join(','))}`);

export const fetchCatalystCalendar = (days = 30) =>
  get(`/calendar?days=${days}`);

export const fetchNews = (ticker, days = 14) =>
  get(`/news/${encodeURIComponent(ticker.toUpperCase())}?days=${days}`);

export const fetchNewsFirehose = (days = 7, maxPerTicker = 5) =>
  get(`/news/portfolio/firehose?days=${days}&max_per_ticker=${maxPerTicker}`);

export const fetchSecFilings = (ticker, limit = 30) =>
  get(`/sec_filings/${encodeURIComponent(ticker.toUpperCase())}?limit=${limit}`);

export const fetchMonitorPreview = () => get('/monitor/preview');
export const runMonitorAlerts    = () => post('/monitor/run', {});

export const fetchSectorBenchmarkPortfolio = () => get('/sector_benchmark/portfolio');

export const fetchAttribution = () => get('/attribution');

// ─────────────────────────────────────────────────────────────────
// MON PORTEFEUILLE — book personnel LT, statique, hors univers TITAN.
// ─────────────────────────────────────────────────────────────────
export const fetchMyPortfolio = () => get('/my_portfolio');
export const fetchMyPortfolioExecutions = () => get('/my_portfolio/executions');
export const logMyPortfolioExecution = (body) => post('/my_portfolio/executions', body);
export const fetchMyPortfolioPriceHistory = (ticker, period = '1y') =>
  get(`/my_portfolio/${encodeURIComponent(ticker)}/price_history?period=${encodeURIComponent(period)}`);

// PATCH ne suit pas le pattern `post()` ci-dessus (throw sur non-2xx, pas
// de {ok, ...}) — signature alignée sur `get()` pour que l'appelant utilise
// try/catch + ApiError comme pour toute lecture.
export const fetchThesisReviewQueue = (ticker) =>
  get(`/my_portfolio/${encodeURIComponent(ticker)}/thesis_review_queue`);

export const patchThesisReviewQueueStatus = async (ticker, entryId, status) => {
  let r;
  try {
    r = await fetch(
      `${BASE}/my_portfolio/${encodeURIComponent(ticker)}/thesis_review_queue/${encodeURIComponent(entryId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ..._authHeaders() },
        body: JSON.stringify({ status }),
      },
    );
  } catch {
    throw new ApiError('Réseau indisponible', 0);
  }
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const d = await r.json();
      if (d?.detail) detail = typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail);
    } catch { /* body non-JSON → garder detail par défaut */ }
    throw new ApiError(detail, r.status);
  }
  return r.json();
};

export const patchMyPortfolioThesis = async (ticker, body) => {
  let r;
  try {
    r = await fetch(`${BASE}/my_portfolio/${encodeURIComponent(ticker)}/thesis`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ..._authHeaders() },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError('Réseau indisponible', 0);
  }
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const d = await r.json();
      if (d?.detail) detail = typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail);
    } catch { /* body non-JSON → garder detail par défaut */ }
    throw new ApiError(detail, r.status);
  }
  return r.json();
};

// ─────────────────────────────────────────────────────────────────
// WATCHLIST + NOTES
// ─────────────────────────────────────────────────────────────────
export const fetchWatchlist = () => get('/watchlist');

export const addToWatchlist = (payload) => post('/watchlist', payload);

const _del = (path) =>
  fetch(`${BASE}${path}`, { method: 'DELETE', headers: { ..._authHeaders() } })
    .then(async r => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok) return { ok: false, status: r.status, ...data };
      return { ok: true, ...data };
    })
    .catch(() => ({ ok: false, error: 'Réseau indisponible' }));

const _put = (path, body = {}) =>
  fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ..._authHeaders(),
    },
    body: JSON.stringify(body),
  })
    .then(async r => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok) return { ok: false, status: r.status, ...data };
      return { ok: true, ...data };
    })
    .catch(() => ({ ok: false, error: 'Réseau indisponible' }));

export const removeFromWatchlist = (ticker) =>
  _del(`/watchlist/${encodeURIComponent(ticker.toUpperCase())}`);

export const fetchNotes = (ticker) =>
  get(`/notes/${encodeURIComponent(ticker.toUpperCase())}`);

export const addNote = (ticker, body) =>
  post(`/notes/${encodeURIComponent(ticker.toUpperCase())}`, { body });

export const updateNote = (noteId, body) =>
  _put(`/notes/${encodeURIComponent(noteId)}`, { body });

export const deleteNote = (noteId) =>
  _del(`/notes/${encodeURIComponent(noteId)}`);

// ── Cockpit (audit 2026-09-17, Lot 6) ─────────────────────────────
export const fetchPerformanceBenchmark = (months = 6, topN = 20) =>
  get(`/performance/benchmark?months=${months}&top_n=${topN}`);
export const fetchProtection = (refresh = false) =>
  get(`/portfolio/protection${refresh ? '?refresh=true' : ''}`);
export const fetchSystemHealth = () => get('/system/health');
export const fetchScoringLab = () => get('/scoring/lab');
export const fetchShadow = () => get('/shadow');
export const fetchPerformanceGap = (months = 6, topN = 20) => get(`/performance/gap?months=${months}&top_n=${topN}`);
export const fetchRebalancePreview = () => get('/rebalance/preview');
