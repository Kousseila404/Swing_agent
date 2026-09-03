// Hooks React Query — cache stale-while-revalidate partagé entre composants.
// Un 2e composant qui mount pendant qu'un autre refetch le même endpoint
// ne re-déclenche pas d'appel réseau.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { POLL, STALE } from '../config/api.js'
import {
  addNote,
  addToWatchlist,
  fetchCatalystCalendar,
  fetchCompare,
  fetchNews,
  fetchNewsFirehose,
  fetchSecFilings,
  fetchAttribution,
  fetchSectorBenchmarkPortfolio,
  approveProposal,
  approveProposalsBatch,
  deleteNote,
  fetchAuditFull,
  fetchDataHealth,
  fetchDelisted,
  fetchEquityCurve,
  fetchJob,
  fetchMacro,
  fetchMacroCalendar,
  fetchMyPortfolio,
  fetchMyPortfolioExecutions,
  fetchMyPortfolioPriceHistory,
  fetchNotes,
  fetchPerformanceMetrics,
  fetchPortfolio,
  fetchProposals,
  fetchSnapshotsList,
  fetchTickerHistory,
  fetchSector,
  fetchSectors,
  fetchMarketStatus,
  fetchStatus,
  fetchUniverse,
  fetchWatchlist,
  fetchWfoHistory,
  fetchWfoWeights,
  killJob,
  logMyPortfolioExecution,
  refreshProposals,
  regenerateProposals,
  rejectProposal,
  rejectProposalsBatch,
  removeFromWatchlist,
  updateNote,
} from '../api/client.js'

export const useStatus = (opts = {}) =>
  useQuery({ queryKey: ['status'], queryFn: fetchStatus, refetchInterval: POLL.STATUS_FAST, ...opts })

// Poll ~60s : l'horloge NYSE bascule 2× / jour (open + close).
export const useMarketStatus = (opts = {}) =>
  useQuery({
    queryKey: ['market_status'],
    queryFn: fetchMarketStatus,
    refetchInterval: POLL.MARKET_STATUS,
    staleTime: STALE.STATUS,
    ...opts,
  })

export const usePortfolio = (opts = {}) =>
  useQuery({ queryKey: ['portfolio'], queryFn: fetchPortfolio, ...opts })

export const useMyPortfolio = (opts = {}) =>
  useQuery({
    queryKey: ['my_portfolio'],
    queryFn: fetchMyPortfolio,
    refetchInterval: POLL.MY_PORTFOLIO,
    staleTime: STALE.MY_PORTFOLIO,
    ...opts,
  })

export const useMyPortfolioExecutions = (opts = {}) =>
  useQuery({ queryKey: ['my_portfolio_executions'], queryFn: fetchMyPortfolioExecutions, ...opts })

export const useMyPortfolioPriceHistory = (ticker, period = '1y', opts = {}) =>
  useQuery({
    queryKey: ['my_portfolio_price_history', ticker, period],
    queryFn: () => fetchMyPortfolioPriceHistory(ticker, period),
    enabled: Boolean(ticker),
    staleTime: 5 * 60_000,
    ...opts,
  })

export const useLogExecution = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body) => logMyPortfolioExecution(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['my_portfolio_executions'] }),
  })
}

export const useEquityCurve = (opts = {}) =>
  useQuery({ queryKey: ['equity_curve'], queryFn: fetchEquityCurve, ...opts })

export const usePerformanceMetrics = (opts = {}) =>
  useQuery({
    queryKey: ['performance_metrics'],
    queryFn: fetchPerformanceMetrics,
    staleTime: STALE.PERFORMANCE,
    ...opts,
  })

export const useMacro = (opts = {}) =>
  useQuery({ queryKey: ['macro'], queryFn: fetchMacro, ...opts })

export const useMacroCalendar = (horizonDays = 60, opts = {}) =>
  useQuery({
    queryKey: ['macro_calendar', horizonDays],
    queryFn: () => fetchMacroCalendar(horizonDays),
    staleTime: STALE.MACRO_CALENDAR,
    ...opts,
  })

export const useUniverse = (sector, opts = {}) =>
  useQuery({
    queryKey: ['universe', sector ?? null],
    queryFn: () => fetchUniverse(sector),
    ...opts,
  })

export const useCompare = (tickers, opts = {}) =>
  useQuery({
    queryKey: ['compare', (tickers || []).join(',')],
    queryFn: () => fetchCompare(tickers),
    enabled: (tickers || []).length >= 2,
    ...opts,
  })

export const useSectors = (params = {}, opts = {}) =>
  useQuery({
    queryKey: ['sectors', params.refreshMomentum ?? false],
    queryFn: () => fetchSectors(params),
    ...opts,
  })

export const useSector = (name, opts = {}) =>
  useQuery({
    queryKey: ['sector', name],
    queryFn: () => fetchSector(name),
    enabled: !!name,
    ...opts,
  })

// Poll un job par id (utilisé par UniverseManagerPage pour watch un rebuild).
// Pause auto quand running=false pour ne pas taper le backend en boucle.
export const useJob = (id, opts = {}) =>
  useQuery({
    queryKey: ['job', id],
    queryFn: () => fetchJob(id),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data?.running ? POLL.JOB : false),
    ...opts,
  })

export const useKillJob = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: killJob,
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ['job', id] })
    },
  })
}

// ─────────────────────────────────────────────────────────────────
// PROPOSALS — file de propositions auto-générées avec veto humain.
// `useProposals` poll modéré (30s) pour voir les nouvelles propositions
// arriver après un cron sans rafraîchissement manuel.
// ─────────────────────────────────────────────────────────────────
export const useProposals = ({ status = null, limit = 200 } = {}, opts = {}) =>
  useQuery({
    queryKey: ['proposals', status, limit],
    queryFn: () => fetchProposals({ status, limit }),
    refetchInterval: POLL.PROPOSALS,
    ...opts,
  })

export const useRefreshProposals = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: refreshProposals,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['proposals'] }),
  })
}

// Purge + régénère — expire les pending (pas rejected, donc pas de cooldown
// veto) puis relance le proposer dans la même transaction.
export const useRegenerateProposals = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: regenerateProposals,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['proposals'] }),
  })
}

export const useApproveProposal = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: approveProposal,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['proposals'] })
      qc.invalidateQueries({ queryKey: ['portfolio'] })
      qc.invalidateQueries({ queryKey: ['equity_curve'] })
    },
  })
}

export const useRejectProposal = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, reason }) => rejectProposal(id, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['proposals'] }),
  })
}

// Bulk approve — invalide portfolio + equity + proposals car chaque item OK
// ouvre un trade dans le journal.
export const useApproveProposalsBatch = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (items) => approveProposalsBatch(items),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['proposals'] })
      qc.invalidateQueries({ queryKey: ['portfolio'] })
      qc.invalidateQueries({ queryKey: ['equity_curve'] })
    },
  })
}

export const useRejectProposalsBatch = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (items) => rejectProposalsBatch(items),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['proposals'] }),
  })
}

// ─────────────────────────────────────────────────────────────────
// HISTORIQUE TICKER + DATA HEALTH
// ─────────────────────────────────────────────────────────────────
export const useSnapshotsList = (opts = {}) =>
  useQuery({
    queryKey: ['history_snapshots'],
    queryFn: fetchSnapshotsList,
    staleTime: STALE.SNAPSHOTS,
    ...opts,
  })

export const useDataHealth = (opts = {}) =>
  useQuery({
    queryKey: ['data_health'],
    queryFn: fetchDataHealth,
    refetchInterval: POLL.DATA_HEALTH,
    staleTime: STALE.STATUS,
    ...opts,
  })

export const useTickerHistory = (ticker, opts = {}) =>
  useQuery({
    queryKey: ['ticker_history', ticker, opts.start, opts.end, opts.fields],
    queryFn: () => fetchTickerHistory(ticker, opts),
    enabled: !!ticker,
    staleTime: STALE.TICKER_HISTORY,
    ...opts,
  })

// ─────────────────────────────────────────────────────────────────
// AUDIT — Audit S1.1 + S1.3
// ─────────────────────────────────────────────────────────────────
export const useDelisted = (opts = {}) =>
  useQuery({
    queryKey: ['delisted'],
    queryFn: fetchDelisted,
    staleTime: STALE.DELISTED,
    ...opts,
  })

export const useWfoWeights = (opts = {}) =>
  useQuery({
    queryKey: ['wfo_weights'],
    queryFn: fetchWfoWeights,
    staleTime: STALE.WFO,    // poids changent au plus mensuellement (cron)
    retry: false,             // 404 si jamais lancé → pas de retry
    ...opts,
  })

export const useWfoHistory = (limit = 50, opts = {}) =>
  useQuery({
    queryKey: ['wfo_history', limit],
    queryFn: () => fetchWfoHistory({ limit }),
    staleTime: STALE.WFO,
    ...opts,
  })

export const useAuditFull = (opts = {}) =>
  useQuery({
    queryKey: ['audit_full'],
    queryFn: () => fetchAuditFull(),
    staleTime: STALE.AUDIT_FULL, // backend cache 24h ; on rafraîchit rarement
    ...opts,
  })

export const useRefreshAuditFull = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => fetchAuditFull({ refresh: true }),
    onSuccess: (data) => {
      qc.setQueryData(['audit_full'], data)
    },
  })
}

// ─────────────────────────────────────────────────────────────────
// WATCHLIST + NOTES
// ─────────────────────────────────────────────────────────────────
export const useNews = (ticker, days = 14, opts = {}) =>
  useQuery({
    queryKey: ['news', ticker, days],
    queryFn: () => fetchNews(ticker, days),
    enabled: !!ticker,
    staleTime: STALE.NEWS,    // news cache backend = 1h ; UI stale = 30min
    ...opts,
  })

export const useNewsFirehose = (days = 7, maxPerTicker = 5, opts = {}) =>
  useQuery({
    queryKey: ['news_firehose', days, maxPerTicker],
    queryFn: () => fetchNewsFirehose(days, maxPerTicker),
    staleTime: STALE.NEWS_FIREHOSE,
    ...opts,
  })

export const useAttribution = (opts = {}) =>
  useQuery({
    queryKey: ['attribution'],
    queryFn: fetchAttribution,
    staleTime: STALE.ATTRIBUTION,
    ...opts,
  })

export const useSectorBenchmarkPortfolio = (opts = {}) =>
  useQuery({
    queryKey: ['sector_benchmark_portfolio'],
    queryFn: fetchSectorBenchmarkPortfolio,
    staleTime: STALE.SECTOR_BENCH,  // backend cache 1h, UI 30min
    ...opts,
  })

export const useSecFilings = (ticker, limit = 30, opts = {}) =>
  useQuery({
    queryKey: ['sec_filings', ticker, limit],
    queryFn: () => fetchSecFilings(ticker, limit),
    enabled: !!ticker,
    staleTime: STALE.SEC_FILINGS,   // backend cache 6h, UI 1h
    ...opts,
  })

export const useCatalystCalendar = (days = 30, opts = {}) =>
  useQuery({
    queryKey: ['calendar', days],
    queryFn: () => fetchCatalystCalendar(days),
    staleTime: STALE.CALENDAR,
    ...opts,
  })

export const useWatchlist = (opts = {}) =>
  useQuery({
    queryKey: ['watchlist'],
    queryFn: fetchWatchlist,
    staleTime: STALE.WATCHLIST,
    ...opts,
  })

export const useAddToWatchlist = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: addToWatchlist,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watchlist'] }),
  })
}

export const useRemoveFromWatchlist = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: removeFromWatchlist,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watchlist'] }),
  })
}

export const useNotes = (ticker, opts = {}) =>
  useQuery({
    queryKey: ['notes', ticker],
    queryFn: () => fetchNotes(ticker),
    enabled: !!ticker,
    staleTime: STALE.NOTES,
    ...opts,
  })

export const useAddNote = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ ticker, body }) => addNote(ticker, body),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ['notes', vars.ticker] })
      qc.invalidateQueries({ queryKey: ['watchlist'] })
    },
  })
}

export const useUpdateNote = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }) => updateNote(id, body),
    onSuccess: (res) => {
      const t = res?.note?.ticker
      if (t) qc.invalidateQueries({ queryKey: ['notes', t] })
    },
  })
}

export const useDeleteNote = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id }) => deleteNote(id),
    onSuccess: (_res, vars) => {
      if (vars?.ticker) qc.invalidateQueries({ queryKey: ['notes', vars.ticker] })
      qc.invalidateQueries({ queryKey: ['watchlist'] })
    },
  })
}
