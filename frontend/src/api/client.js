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
