// Hooks React Query — cache stale-while-revalidate partagé entre composants.
// Un 2e composant qui mount pendant qu'un autre refetch le même endpoint
// ne re-déclenche pas d'appel réseau.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  addNote,
  addToWatchlist,
  fetchCatalystCalendar,
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
  refreshProposals,
  regenerateProposals,
  rejectProposal,
  rejectProposalsBatch,
  removeFromWatchlist,
  updateNote,
} from '../api/client.js'

export const useStatus = (opts = {}) =>
  useQuery({ queryKey: ['status'], queryFn: fetchStatus, refetchInterval: 30_000, ...opts })

// Poll ~60s : l'horloge NYSE bascule 2× / jour (open + close).
export const useMarketStatus = (opts = {}) =>
  useQuery({
    queryKey: ['market_status'],
    queryFn: fetchMarketStatus,
    refetchInterval: 60_000,
    staleTime: 30_000,
    ...opts,
  })

export const usePortfolio = (opts = {}) =>
  useQuery({ queryKey: ['portfolio'], queryFn: fetchPortfolio, ...opts })

export const useEquityCurve = (opts = {}) =>
  useQuery({ queryKey: ['equity_curve'], queryFn: fetchEquityCurve, ...opts })

export const usePerformanceMetrics = (opts = {}) =>
  useQuery({
    queryKey: ['performance_metrics'],
    queryFn: fetchPerformanceMetrics,
    staleTime: 60_000,
    ...opts,
  })

export const useMacro = (opts = {}) =>
  useQuery({ queryKey: ['macro'], queryFn: fetchMacro, ...opts })

export const useMacroCalendar = (horizonDays = 60, opts = {}) =>
  useQuery({
    queryKey: ['macro_calendar', horizonDays],
    queryFn: () => fetchMacroCalendar(horizonDays),
    staleTime: 5 * 60_000,
    ...opts,
  })

export const useUniverse = (sector, opts = {}) =>
  useQuery({
    queryKey: ['universe', sector ?? null],
    queryFn: () => fetchUniverse(sector),
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
    refetchInterval: (q) => (q.state.data?.running ? 2_000 : false),
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
    refetchInterval: 30_000,
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
    staleTime: 60_000,
    ...opts,
  })

export const useDataHealth = (opts = {}) =>
  useQuery({
    queryKey: ['data_health'],
    queryFn: fetchDataHealth,
    refetchInterval: 60_000,
    staleTime: 30_000,
    ...opts,
  })

export const useTickerHistory = (ticker, opts = {}) =>
  useQuery({
    queryKey: ['ticker_history', ticker, opts.start, opts.end, opts.fields],
    queryFn: () => fetchTickerHistory(ticker, opts),
    enabled: !!ticker,
    staleTime: 60_000,
    ...opts,
  })

// ─────────────────────────────────────────────────────────────────
// AUDIT — Audit S1.1 + S1.3
// ─────────────────────────────────────────────────────────────────
export const useDelisted = (opts = {}) =>
  useQuery({
    queryKey: ['delisted'],
    queryFn: fetchDelisted,
    staleTime: 5 * 60_000,
    ...opts,
  })

export const useWfoWeights = (opts = {}) =>
  useQuery({
    queryKey: ['wfo_weights'],
    queryFn: fetchWfoWeights,
    staleTime: 60 * 60_000, // poids changent au plus mensuellement (cron)
    retry: false,            // 404 si jamais lancé → pas de retry
    ...opts,
  })

export const useWfoHistory = (limit = 50, opts = {}) =>
  useQuery({
    queryKey: ['wfo_history', limit],
    queryFn: () => fetchWfoHistory({ limit }),
    staleTime: 60 * 60_000,
    ...opts,
  })

export const useAuditFull = (opts = {}) =>
  useQuery({
    queryKey: ['audit_full'],
    queryFn: () => fetchAuditFull(),
    staleTime: 30 * 60_000, // backend cache 24h ; on rafraîchit rarement
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
    staleTime: 30 * 60_000,   // news cache backend = 1h ; UI stale = 30min
    ...opts,
  })

export const useNewsFirehose = (days = 7, maxPerTicker = 5, opts = {}) =>
  useQuery({
    queryKey: ['news_firehose', days, maxPerTicker],
    queryFn: () => fetchNewsFirehose(days, maxPerTicker),
    staleTime: 10 * 60_000,
    ...opts,
  })

export const useAttribution = (opts = {}) =>
  useQuery({
    queryKey: ['attribution'],
    queryFn: fetchAttribution,
    staleTime: 5 * 60_000,
    ...opts,
  })

export const useSectorBenchmarkPortfolio = (opts = {}) =>
  useQuery({
    queryKey: ['sector_benchmark_portfolio'],
    queryFn: fetchSectorBenchmarkPortfolio,
    staleTime: 30 * 60_000,  // backend cache 1h, UI 30min
    ...opts,
  })

export const useSecFilings = (ticker, limit = 30, opts = {}) =>
  useQuery({
    queryKey: ['sec_filings', ticker, limit],
    queryFn: () => fetchSecFilings(ticker, limit),
    enabled: !!ticker,
    staleTime: 60 * 60_000,  // backend cache 6h, UI 1h
    ...opts,
  })

export const useCatalystCalendar = (days = 30, opts = {}) =>
  useQuery({
    queryKey: ['calendar', days],
    queryFn: () => fetchCatalystCalendar(days),
    staleTime: 5 * 60_000,
    ...opts,
  })

export const useWatchlist = (opts = {}) =>
  useQuery({
    queryKey: ['watchlist'],
    queryFn: fetchWatchlist,
    staleTime: 30_000,
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
    staleTime: 30_000,
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
