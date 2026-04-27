// Hooks React Query — cache stale-while-revalidate partagé entre composants.
// Un 2e composant qui mount pendant qu'un autre refetch le même endpoint
// ne re-déclenche pas d'appel réseau.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  approveProposal,
  approveProposalsBatch,
  fetchDataHealth,
  fetchEquityCurve,
  fetchJob,
  fetchMacro,
  fetchMacroCalendar,
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
  killJob,
  refreshProposals,
  regenerateProposals,
  rejectProposal,
  rejectProposalsBatch,
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
