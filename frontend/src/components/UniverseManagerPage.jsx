import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useDebouncedValue } from '../hooks/useMediaQuery';
import { useQueryClient } from '@tanstack/react-query';
import {
  rebuildUniverse, killJob, pushManualProposal, runQuickBacktest, addToWatchlist,
  addTitanAlert,
} from '../api/client';
import { useUniverse, useJob } from '../hooks/useApi';
import { fmtNum, fmtMarketCap, fmtSignedPct, safeCompare } from '../utils/format';
import { factorColor } from '../utils/colors';
import Pagination from './Pagination';
import PresetBar from './common/PresetBar';
import LastUpdated from './common/LastUpdated';
import TickerAnalysisModal from './TickerAnalysisModal';
import { readJSON, writeJSON } from '../utils/storage';
import { useHashSearchParams } from '../utils/preferences';

// Seuils de la règle d'achat manuelle (cf. memory user_buy_rule_manual.md) :
// TITAN ≥ 80 nominal, override possible si TITAN ∈ [70, 80[ + signal externe.
// On expose ici le filtre "Buy candidates" comme premier accès au scanner.
const BUY_TITAN_THRESHOLD = 80;
const BUY_TITAN_OVERRIDE_THRESHOLD = 70;

// Persistance des filtres (Suivi #23) — un reload ne perd pas la vue. Clé
// unique scopée à la page pour ne pas polluer d'autres préfs.
const PERSIST_KEY = 'swing.universePage.filters.v1';

const loadPersistedFilters = () => readJSON(PERSIST_KEY, null);
const savePersistedFilters = (state) => writeJSON(PERSIST_KEY, state);

// Tooltips détaillés (Tier C #3) — composition de chaque pilier TITAN.
// Source : modules/sector_metrics/_scoring.py. On vise une explication courte
// activable au hover, pas une doc exhaustive.
const COL_TOOLTIPS = {
  ticker:     'Ticker boursier — clique pour ouvrir l\'analyse complète (factsheet 11 sections).',
  status:     'État dans ton portefeuille / file de propositions — précédence : HELD > PROPOSED > VETOED > WATCH.',
  sector:     'Secteur GICS issu de FMP/yfinance.',
  titan:      'TITAN composite (0-100) — agrégation pondérée des 6 piliers Q/V/R/S/M + Piotroski. Sector-relative ranking × DQ-coef.',
  drift:      'Δ TITAN sur 7 jours = score actuel − score il y a 7 jours. Détecte les upgrades silencieuses (≥+5 = significatif).',
  piotroski:  'F-Score absolu (0-9) — santé fondamentale binaire (ROA>0, OCF>0, OCF>NI, CR>1, Y/Y revisions). ≥7 = strong.',
  quality:    'Quality (poids 22%) — ROE, ROA, gross margin, EBIT margin. Sector-relative percentile rank.',
  value:      'Value (poids 18%) — Fwd P/E, EV/EBITDA, FCF yield, P/B, PEG, E/P. Cheap-junk filter (-10pts).',
  risk:       'Risk (poids 13%) — debt/equity, interest coverage, beta, current ratio. Stabilité bilancielle.',
  sentiment:  'Sentiment (poids 18%) — recos analystes, target upside, revisions, insider activity.',
  momentum:   'Momentum (poids 18%) — return 6m, return 12m, vol-adjusted, distance to 52w high.',
  market_cap: 'Capitalisation boursière (USD).',
  forward_pe: 'Forward Price/Earnings — multiple sur les bénéfices anticipés N+1. <0 = perte attendue, >40 = cher.',
  reco:       'Note moyenne analystes (1=Strong Buy, 5=Strong Sell). Tri ↑ = Buy en tête.',
  upside:     'Upside vs target moyen analystes : (target / current_price − 1) × 100.',
};

// Polling adaptatif : 4s pendant rebuild (liste) + 3s pendant job → 30s idle.
const POLL_ACTIVE_MS = 4000;
const POLL_IDLE_MS   = 30000;
const POLL_JOB_MS    = 3000;

const INDEX_OPTIONS = [
  { id: 'sp500',  label: 'S&P 500',    desc: '~500 large caps US' },
  { id: 'ndx100', label: 'Nasdaq 100', desc: '100 leaders tech' },
];

const CAP_PRESETS = [
  { label: '1 B',  value: 1e9 },
  { label: '5 B',  value: 5e9 },
  { label: '10 B', value: 1e10 },
  { label: '50 B', value: 5e10 },
  { label: '100 B', value: 1e11 },
];

// recommendation_mean = 1 (Strong Buy) … 5 (Strong Sell) par convention yfinance.
const recoBucket = (mean) => {
  if (mean == null) return { label: '—', color: 'var(--text-muted)', weight: 0 };
  if (mean < 1.5)  return { label: 'Strong Buy',  color: 'var(--success)',       weight: 5 };
  if (mean < 2.5)  return { label: 'Buy',         color: '#6ee7b7',              weight: 4 };
  if (mean < 3.5)  return { label: 'Hold',        color: 'var(--warning)',       weight: 3 };
  if (mean < 4.5)  return { label: 'Underperform',color: '#fb923c',              weight: 2 };
  return             { label: 'Sell',        color: 'var(--danger)',        weight: 1 };
};

export default function UniverseManagerPage() {
  const qc = useQueryClient();
  const [launching, setLaunching]       = useState(false);
  const [confirmRebuild, setConfirmReb] = useState(false);
  const [toasts, setToasts]             = useState([]);
  const [showOptions, setShowOptions]   = useState(false);

  const [optIndices, setOptIndices] = useState(['sp500', 'ndx100']);
  const [optMinCap, setOptMinCap]   = useState(1e10);
  // full = rebuild intégral (~1500 calls FMP) ; staggered (défaut) = budget 50/j.
  const [optFull, setOptFull]       = useState(false);

  // Hydrate : query params URL prioritaires, sinon localStorage, sinon defaults.
  // Permet de partager une vue (link `#/universe?sector=tech&sort=drift`) et
  // de F5 sans perte. searchText reste local (saisie volatile, pas d'intérêt
  // à le partager). Pattern Seeking Alpha / Koyfin.
  const [hashParams, setHashParams] = useHashSearchParams();
  const persistedFilters = loadPersistedFilters() || {};
  const _initial = (key, fallback) => hashParams[key] ?? persistedFilters[key] ?? fallback;
  const [sectorFilter, setSectorFilter] = useState(() => _initial('sector', 'ALL'));
  const [searchText, setSearchText]     = useState('');
  const [sortBy, setSortBy]             = useState(() => _initial('sort', 'titan'));
  // Direction : par défaut 'desc' pour les scores (qui ont du sens en top-down).
  // Les colonnes "ascendantes par nature" (reco mean, forward_pe) inversent
  // automatiquement leur sens dans le comparateur ci-dessous.
  const [sortDir, setSortDir]           = useState(() => _initial('dir', 'desc'));
  // 'all' = pas de filtre TITAN ; 'buy_strict' = ≥ 80 ; 'buy_override' = ≥ 70 ;
  // 'buy_strict_fscore' = ≥ 80 ET F-Score ≥ 7 (règle compound).
  const [buyFilter, setBuyFilter]       = useState(() => _initial('buy', 'all'));
  // Filtre status portefeuille : 'all' | 'held' | 'proposed' | 'free'.
  const [statusFilter, setStatusFilter] = useState(() => _initial('status', 'all'));

  // Snapshot pour persistance (localStorage + URL). Debouncé 400ms : sync
  // localStorage à chaque keystroke = stutter, URL replaceState coûte aussi.
  const filtersSnapshot = useMemo(
    () => ({ sectorFilter, sortBy, sortDir, buyFilter, statusFilter }),
    [sectorFilter, sortBy, sortDir, buyFilter, statusFilter],
  );
  const debouncedFilters = useDebouncedValue(filtersSnapshot, 400);
  useEffect(() => {
    savePersistedFilters(debouncedFilters);
    // Sync URL — les défauts ('ALL', 'titan', 'desc', 'all', 'all') sont
    // implicites → on supprime ces clés pour garder l'URL courte.
    setHashParams({
      sector: debouncedFilters.sectorFilter === 'ALL' ? '' : debouncedFilters.sectorFilter,
      sort:   debouncedFilters.sortBy === 'titan' ? '' : debouncedFilters.sortBy,
      dir:    debouncedFilters.sortDir === 'desc' ? '' : debouncedFilters.sortDir,
      buy:    debouncedFilters.buyFilter === 'all' ? '' : debouncedFilters.buyFilter,
      status: debouncedFilters.statusFilter === 'all' ? '' : debouncedFilters.statusFilter,
    });
    // setHashParams identité stable (useState setter pattern)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedFilters]);
  const [analysisTicker, setAnalysisTicker] = useState(null);
  // Trace par ticker des push manuels en cours (pour disable + spinner).
  const [pushingTickers, setPushingTickers] = useState({});
  // Sélection pour le comparateur multi-tickers (max 5 — au-delà la lecture
  // des barres groupées devient illisible).
  const COMPARE_MAX = 5;
  const [compareSet, setCompareSet] = useState(() => new Set());
  const [compareOpen, setCompareOpen] = useState(false);
  // Backtest in-page state.
  const [backtestRunning, setBacktestRunning] = useState(false);
  const [backtestResult, setBacktestResult]   = useState(null);
  const [backtestError, setBacktestError]     = useState(null);

  const toggleCompare = useCallback((ticker) => {
    setCompareSet(prev => {
      const next = new Set(prev);
      if (next.has(ticker)) {
        next.delete(ticker);
      } else if (next.size < COMPARE_MAX) {
        next.add(ticker);
      } else {
        // toast inline pour rester pure dans le callback memoized
        const id = Date.now() + Math.random();
        setToasts(t => [...t, { id, msg: `⚠️ Comparateur limité à ${COMPARE_MAX} tickers`, type: 'error' }]);
        setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 4500);
      }
      return next;
    });
  }, []);

  const PAGE_SIZE = 50;
  const [page, setPage] = useState(1);

  const prevRebuildingRef = useRef(false);

  const toast = useCallback((msg, type = 'ok') => {
    const id = Date.now() + Math.random();
    setToasts(t => [...t, { id, msg, type }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 4500);
  }, []);

  const universeQuery = useUniverse(null, {
    refetchInterval: (q) => (q.state.data?.rebuilding ? POLL_ACTIVE_MS : POLL_IDLE_MS),
  });
  const payload   = universeQuery.data || null;
  const loading   = universeQuery.isLoading;
  const loadError = universeQuery.isError
    ? (universeQuery.error?.status === 0
        ? 'API injoignable. Vérifie que le backend tourne sur :8000.'
        : `Backend error : ${universeQuery.error?.message || 'erreur inconnue'}`)
    : null;

  const reloadUniverse = () => qc.invalidateQueries({ queryKey: ['universe'] });

  const rebuilding = Boolean(payload?.rebuilding);

  useEffect(() => {
    const wasRebuilding = prevRebuildingRef.current;
    prevRebuildingRef.current = rebuilding;
    if (wasRebuilding && !rebuilding) {
      const stats = payload?.stats || {};
      const kept = stats.n_kept ?? '?';
      const total = stats.n_scanned ?? '?';
      setTimeout(() => {
        toast(`✅ Univers reconstruit — ${kept} / ${total} tickers conservés`);
      }, 0);
    }
  }, [rebuilding, payload, toast]);

  const jobId = payload?.rebuild_job_id;
  const jobQuery = useJob(jobId, {
    refetchInterval: (q) => (q.state.data?.running ? POLL_JOB_MS : false),
  });
  const jobOutput = jobId && !jobQuery.isError ? (jobQuery.data || null) : null;

  const handleRebuild = async () => {
    if (launching || rebuilding) return;
    setLaunching(true);
    setConfirmReb(false);
    const opts = { mode: optFull ? 'full' : 'staggered' };
    if (optIndices.length) opts.indices = optIndices;
    // min_market_cap n'est utilisé qu'en mode full (le scheduler hérite du
    // seuil déjà appliqué dans universe.json).
    if (optFull && optMinCap && optMinCap !== 1e10) opts.min_market_cap = optMinCap;
    const res = await rebuildUniverse(opts);
    setLaunching(false);
    if (res.ok) {
      const label = optFull ? 'Rebuild complet' : 'Refresh staggered';
      toast(`🚀 ${label} lancé — job ${res.job?.job_id}`);
      setTimeout(reloadUniverse, 500);
    } else {
      toast(`❌ ${res.detail || res.error || 'Rebuild impossible'}`, 'error');
    }
  };

  // Note : on lit `pushingTickers` via le setter functional updater pour ne
  // pas avoir à le mettre en dep (sinon le callback re-créerait à chaque push).
  const handlePushProposal = useCallback(async (ticker) => {
    if (!ticker) return;
    let alreadyPushing = false;
    setPushingTickers(prev => {
      if (prev[ticker]) { alreadyPushing = true; return prev; }
      return { ...prev, [ticker]: true };
    });
    if (alreadyPushing) return;
    try {
      const res = await pushManualProposal(ticker);
      if (res?.ok && res.proposal) {
        toast(`📥 ${ticker} → file Propositions (${res.proposal.id})`);
      } else if (res && res.ok === false) {
        // Dédup silencieux (pending existant, cooldown veto/win) — informatif.
        toast(`ℹ️ ${ticker} : ${res.message || 'déjà en file ou cooldown actif'}`, 'error');
      } else {
        toast(`❌ ${ticker} : push impossible (${res?.detail || 'erreur'})`, 'error');
      }
    } catch (err) {
      toast(`❌ ${ticker} : ${err?.message || 'erreur réseau'}`, 'error');
    } finally {
      setPushingTickers(prev => {
        const next = { ...prev };
        delete next[ticker];
        return next;
      });
    }
  }, [toast]);

  const handleSetAlert = useCallback(async (ticker, currentTitan) => {
    if (!ticker) return;
    // Suggestion : si TITAN actuel < 80, propose un seuil "above 80" (alerte
    // d'opportunité d'achat). Sinon "below 70" (alerte de dégradation).
    const cur = Number.isFinite(currentTitan) ? currentTitan : 50;
    const defaultDir = cur < 80 ? 'above' : 'below';
    const defaultThr = cur < 80 ? 80 : 70;
    const raw = window.prompt(
      `🔔 Alerte TITAN sur ${ticker}\n\n` +
      `Score actuel : ${Number.isFinite(currentTitan) ? fmtNum(currentTitan, 1) : '—'}\n\n` +
      `Format : "above 80" ou "below 70"\n` +
      `(Telegram daily — cooldown 24h)`,
      `${defaultDir} ${defaultThr}`,
    );
    if (!raw) return;
    const m = raw.trim().toLowerCase().match(/^(above|below)\s+(\d+(?:\.\d+)?)$/);
    if (!m) {
      toast(`❌ Format invalide. Attendu : "above 80" ou "below 70"`, 'error');
      return;
    }
    const direction = m[1];
    const threshold = parseFloat(m[2]);
    if (threshold < 0 || threshold > 100) {
      toast(`❌ Seuil hors bornes [0, 100] : ${threshold}`, 'error');
      return;
    }
    try {
      const res = await addTitanAlert({ ticker, direction, threshold });
      if (res?.ok) {
        toast(`🔔 Alerte ${ticker} : TITAN ${direction === 'above' ? '≥' : '≤'} ${threshold}`);
      } else {
        toast(`❌ ${res?.detail || 'Alerte impossible'}`, 'error');
      }
    } catch (err) {
      toast(`❌ ${err?.message || 'Alerte impossible'}`, 'error');
    }
  }, [toast]);

  const handleExportCsv = () => {
    if (filteredSorted.length === 0) {
      toast('⚠️ Aucun ticker à exporter', 'error');
      return;
    }
    // Schéma stable + minimal — l'utilisateur peut toujours rejouer un build
    // Excel/sheet dessus. On évite d'embarquer la sparkline (array → CSV moche).
    const cols = [
      ['ticker', t => t.ticker],
      ['name', t => t.name || ''],
      ['sector', t => t.sector || ''],
      ['industry', t => t.industry || ''],
      ['titan_score', t => t.titan_composite_score ?? ''],
      ['drift_7d', t => t.titan_drift_7d ?? ''],
      ['quality', t => t.quality_score ?? ''],
      ['value', t => t.value_score ?? ''],
      ['risk', t => t.risk_score ?? ''],
      ['sentiment', t => t.sentiment_score ?? ''],
      ['momentum', t => t.momentum_score ?? ''],
      ['piotroski', t => t.piotroski_score ?? ''],
      ['f_score', t => t.f_score ?? ''],
      ['market_cap', t => t.market_cap ?? ''],
      ['forward_pe', t => t.forward_pe ?? ''],
      ['recommendation_mean', t => t.recommendation_mean ?? ''],
      ['price_target_mean', t => t.price_target_mean ?? ''],
      ['portfolio_status', t => t.portfolio_status ?? ''],
    ];
    const escape = (v) => {
      const s = String(v ?? '');
      // RFC 4180 : entoure de guillemets si , ; " ou newline ; double les ".
      return /[",;\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [cols.map(([h]) => h).join(',')];
    filteredSorted.forEach(t => lines.push(cols.map(([, fn]) => escape(fn(t))).join(',')));
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const ts = new Date().toISOString().slice(0, 10);
    a.download = `universe_${ts}_${filteredSorted.length}rows.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast(`💾 ${filteredSorted.length} tickers exportés`);
  };

  const handlePushToWatchlist = async () => {
    // Source : sélection comparateur si > 0, sinon top 10 de la vue filtrée.
    const source = compareSet.size > 0
      ? [...compareSet]
      : filteredSorted.slice(0, 10).map(t => t.ticker);
    if (source.length === 0) {
      toast('⚠️ Aucun ticker à ajouter', 'error');
      return;
    }
    let nOk = 0;
    let nErr = 0;
    for (const tk of source) {
      try {
        await addToWatchlist({ ticker: tk, tag: 'universe-scan', comment: `auto from scan ${new Date().toISOString().slice(0, 10)}` });
        nOk += 1;
      } catch {
        nErr += 1;
      }
    }
    toast(
      nErr === 0
        ? `⭐ ${nOk} ticker${nOk > 1 ? 's' : ''} ajouté${nOk > 1 ? 's' : ''} à la watchlist`
        : `⭐ ${nOk} ajoutés, ${nErr} échecs (déjà présents ?)`,
      nErr === 0 ? 'ok' : 'error',
    );
  };

  const handleQuickBacktest = async () => {
    if (backtestRunning) return;
    const tickers = filteredSorted.map(t => t.ticker);
    if (tickers.length < 5) {
      toast(`⚠️ Minimum 5 tickers requis (${tickers.length} actuellement)`, 'error');
      return;
    }
    // top_n adapté à la taille du panier — on cap à 20 (default LT) ou
    // 1/3 du panier si plus petit, pour garder un ranking informatif.
    const topN = Math.min(20, Math.max(5, Math.floor(tickers.length / 3)));
    setBacktestRunning(true);
    setBacktestError(null);
    try {
      const res = await runQuickBacktest({ tickers, top_n: topN, weighting: 'equal' });
      setBacktestResult(res);
    } catch (err) {
      setBacktestError(err?.message || 'Backtest impossible');
      toast(`❌ Backtest : ${err?.message || 'erreur'}`, 'error');
    } finally {
      setBacktestRunning(false);
    }
  };

  const handleKillJob = async () => {
    if (!jobId) return;
    const res = await killJob(jobId);
    toast(res.ok ? '⏹ Rebuild interrompu' : `❌ ${res.detail}`, res.ok ? 'ok' : 'error');
    setTimeout(reloadUniverse, 500);
  };

  const toggleIndex = (id) => {
    setOptIndices(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  };

  const tickers = useMemo(() => {
    if (!payload?.tickers) return [];
    return Object.values(payload.tickers);
  }, [payload]);

  const sectorDistribution = useMemo(() => {
    if (!payload?.sectors) return [];
    return Object.entries(payload.sectors)
      .map(([name, arr]) => ({ name, count: arr.length }))
      .sort((a, b) => b.count - a.count);
  }, [payload]);

  // Heatmap secteur × pilier (Tier A #1) — agrégation moyenne par secteur des
  // 6 piliers TITAN. On exclut les valeurs null/non-finies pour éviter qu'un
  // pilier manquant tire la moyenne vers 0. Valeur affichée arrondie à l'entier.
  const sectorPillarMatrix = useMemo(() => {
    const pillars = [
      { key: 'quality_score',   label: 'Q', tooltip: COL_TOOLTIPS.quality },
      { key: 'value_score',     label: 'V', tooltip: COL_TOOLTIPS.value },
      { key: 'risk_score',      label: 'R', tooltip: COL_TOOLTIPS.risk },
      { key: 'sentiment_score', label: 'S', tooltip: COL_TOOLTIPS.sentiment },
      { key: 'momentum_score',  label: 'M', tooltip: COL_TOOLTIPS.momentum },
      { key: 'piotroski_score', label: 'P', tooltip: COL_TOOLTIPS.piotroski },
    ];
    const bySector = new Map();
    tickers.forEach(t => {
      const sec = t.sector || 'Unknown';
      if (!bySector.has(sec)) {
        bySector.set(sec, { count: 0, sums: pillars.map(() => 0), counts: pillars.map(() => 0) });
      }
      const acc = bySector.get(sec);
      acc.count += 1;
      pillars.forEach((p, i) => {
        const v = t[p.key];
        if (Number.isFinite(v)) {
          acc.sums[i] += v;
          acc.counts[i] += 1;
        }
      });
    });
    const rows = [...bySector.entries()]
      .map(([sector, acc]) => ({
        sector,
        count: acc.count,
        scores: pillars.map((p, i) =>
          acc.counts[i] > 0 ? acc.sums[i] / acc.counts[i] : null,
        ),
      }))
      .sort((a, b) => b.count - a.count);
    return { pillars, rows };
  }, [tickers]);

  const filteredSorted = useMemo(() => {
    const q = searchText.trim().toUpperCase();
    const buyMin =
      buyFilter === 'buy_strict'        ? BUY_TITAN_THRESHOLD :
      buyFilter === 'buy_strict_fscore' ? BUY_TITAN_THRESHOLD :
      buyFilter === 'buy_override'      ? BUY_TITAN_OVERRIDE_THRESHOLD :
      null;
    const requireStrongFScore = buyFilter === 'buy_strict_fscore';
    let list = tickers.filter(t => {
      if (sectorFilter !== 'ALL' && (t.sector || 'Unknown') !== sectorFilter) return false;
      if (buyMin !== null) {
        // Si le score TITAN n'est pas encore calculé pour ce ticker, on l'exclut
        // du filtre "Buy candidates" (sinon le filtre serait silencieusement faux).
        if (!Number.isFinite(t.titan_composite_score)) return false;
        if (t.titan_composite_score < buyMin) return false;
      }
      if (requireStrongFScore) {
        if (!Number.isFinite(t.f_score) || t.f_score < 7) return false;
      }
      if (statusFilter === 'held' && t.portfolio_status !== 'HELD') return false;
      if (statusFilter === 'proposed' && t.portfolio_status !== 'PROPOSED') return false;
      // 'free' = ni HELD ni PROPOSED ni VETOED — candidats achetables propres.
      if (statusFilter === 'free' && ['HELD', 'PROPOSED', 'VETOED'].includes(t.portfolio_status)) return false;
      if (q) {
        return (
          t.ticker?.includes(q) ||
          (t.name || '').toUpperCase().includes(q) ||
          (t.industry || '').toUpperCase().includes(q)
        );
      }
      return true;
    });

    // Direction effective : pour reco/forward_pe le "naturel" est ascendant
    // (Strong Buy = 1, P/E bas = bon marché). On définit ici l'orientation
    // par défaut puis on applique sortDir comme override "click utilisateur".
    const ASC_BY_DEFAULT = new Set(['reco', 'forward_pe', 'sector', 'ticker']);
    const flip = ASC_BY_DEFAULT.has(sortBy)
      ? (sortDir === 'asc' ? 1 : -1)
      : (sortDir === 'desc' ? 1 : -1);
    list = [...list].sort((a, b) => {
      let res = 0;
      switch (sortBy) {
        case 'titan':
          res = (b.titan_composite_score ?? -1) - (a.titan_composite_score ?? -1); break;
        case 'drift':
          res = (b.titan_drift_7d ?? -Infinity) - (a.titan_drift_7d ?? -Infinity); break;
        case 'piotroski':
          res = (b.f_score ?? -1) - (a.f_score ?? -1); break;
        case 'quality':
          res = (b.quality_score ?? -1) - (a.quality_score ?? -1); break;
        case 'value':
          res = (b.value_score ?? -1) - (a.value_score ?? -1); break;
        case 'momentum':
          res = (b.momentum_score ?? -1) - (a.momentum_score ?? -1); break;
        case 'market_cap':
          res = (b.market_cap ?? 0) - (a.market_cap ?? 0); break;
        case 'reco':
          res = (a.recommendation_mean ?? 99) - (b.recommendation_mean ?? 99); break;
        case 'forward_pe':
          res = (a.forward_pe ?? 1e9) - (b.forward_pe ?? 1e9); break;
        case 'sector':
          res = (a.sector || 'zzz').localeCompare(b.sector || 'zzz'); break;
        case 'ticker':
          res = (a.ticker || '').localeCompare(b.ticker || ''); break;
        case 'upside':
          res = upsidePct(b) - upsidePct(a); break;
        case 'status':
          // Précédence visuelle : HELD > PROPOSED > VETOED > WATCH > null.
          { const order = { HELD: 4, PROPOSED: 3, VETOED: 2, WATCH: 1 };
            res = (order[b.portfolio_status] ?? 0) - (order[a.portfolio_status] ?? 0); }
          break;
        default:
          res = 0;
      }
      return res * flip;
    });
    return list;
  }, [tickers, sectorFilter, searchText, sortBy, sortDir, buyFilter, statusFilter]);

  // Reset page à chaque changement de filtre/tri — évite de rester sur
  // une page vide. Pattern React "adjust state on input change" (évite
  // le cascading render d'un useEffect).
  const filterKey = `${sectorFilter}|${searchText}|${sortBy}|${buyFilter}|${statusFilter}`;
  const [lastFilterKey, setLastFilterKey] = useState(filterKey);
  if (filterKey !== lastFilterKey) {
    setLastFilterKey(filterKey);
    setPage(1);
  }

  const totalPages = Math.max(1, Math.ceil(filteredSorted.length / PAGE_SIZE));
  const pageClamped = Math.min(page, totalPages);
  const paginatedRows = useMemo(
    () => filteredSorted.slice((pageClamped - 1) * PAGE_SIZE, pageClamped * PAGE_SIZE),
    [filteredSorted, pageClamped],
  );

  if (loading) {
    return (
      <div className="loading-pulse">
        <div className="spinner" />
        <p>Chargement de l'univers quantamental…</p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="api-error">
        <div className="api-error-icon">⚠️</div>
        <h3>Univers indisponible</h3>
        <p>{loadError}</p>
      </div>
    );
  }

  const stats   = payload?.stats || {};
  const updated = payload?.updated_at;
  const count   = payload?.count ?? tickers.length;
  const hasData = count > 0;

  return (
    <div className="scanner-page animate-fade-in">
      {/* Toasts */}
      <div className="toast-stack">
        {toasts.map(t => (
          <div key={t.id} className={`api-toast ${t.type === 'error' ? 'error' : ''}`}>{t.msg}</div>
        ))}
      </div>

      {/* ── REBUILD BANNER ── */}
      <RebuildBanner
        rebuilding={rebuilding}
        launching={launching}
        jobId={jobId}
        updated={updated}
        stats={stats}
        count={count}
        sourceIndices={payload?.source_indices}
        showOptions={showOptions}
        setShowOptions={setShowOptions}
        optIndices={optIndices}
        optMinCap={optMinCap}
        optFull={optFull}
        toggleIndex={toggleIndex}
        setOptMinCap={setOptMinCap}
        setOptFull={setOptFull}
        confirm={confirmRebuild}
        setConfirm={setConfirmReb}
        onRebuild={handleRebuild}
        onKill={handleKillJob}
      />

      {/* ── CONSOLE live pendant rebuild ── */}
      {jobOutput && (rebuilding || (jobOutput.lines?.length > 0)) && (
        <div className="card animate-fade-in">
          <div className="card-title" style={{ justifyContent: 'space-between' }}>
            <span>
              📟 Console rebuild
              {rebuilding
                ? <span className="fs-poll" style={{ marginLeft: '0.75rem' }}>Running · poll 3s</span>
                : <span style={{ marginLeft: '0.75rem', color: 'var(--success)', fontSize: '0.8rem' }}>Terminé</span>}
            </span>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
              job {jobId}
            </span>
          </div>
          <div className="console-output" style={{ maxHeight: 240 }}>
            {(jobOutput.lines || []).slice(-120).join('\n') || '(aucune sortie)'}
          </div>
        </div>
      )}

      {hasData ? (
        <>
          {/* ── STATS RESUME ── */}
          <div className="scan-summary">
            <div className="scan-chip" style={{ borderColor: 'var(--accent-primary)', color: 'var(--accent-primary)' }}>
              🎯 Univers : <strong>{count}</strong> actions
            </div>
            <div className="scan-chip" style={{ borderColor: 'var(--success)', color: 'var(--success)' }}>
              🧬 Secteurs : <strong>{sectorDistribution.length}</strong>
            </div>
            {stats.n_scanned != null && (
              <div className="scan-chip" style={{ borderColor: 'var(--text-muted)', color: 'var(--text-muted)' }}>
                📥 Scannés : <strong>{stats.n_scanned}</strong>
                {stats.n_rejected_low_cap > 0 && ` · ${stats.n_rejected_low_cap} filtrés (cap)`}
                {stats.n_errors > 0 && ` · ${stats.n_errors} err`}
              </div>
            )}
            {payload?.filter?.min_market_cap_usd && (
              <div className="scan-chip" style={{ borderColor: 'var(--warning)', color: 'var(--warning)' }}>
                💰 Seuil : <strong>≥ {fmtMarketCap(payload.filter.min_market_cap_usd)}</strong>
              </div>
            )}
            {payload?.is_stale && (
              <div className="scan-chip" style={{ borderColor: 'var(--danger)', color: 'var(--danger)' }}
                   title={`Dernière MAJ il y a ${fmtNum(payload.stale_days, 1)} jours — seuil ${payload.staleness_threshold_days}j`}>
                🕒 Cache périmé : <strong>{fmtNum(payload.stale_days, 1)} j</strong> — rebuild recommandé
              </div>
            )}
            {payload?.incomplete_count > 0 && (
              <div className="scan-chip" style={{ borderColor: 'var(--warning)', color: 'var(--warning)' }}
                   title="Tickers avec market_cap ou sector manquant — impact sur filtres & tri">
                ⚠️ Incomplets : <strong>{payload.incomplete_count}</strong>
                {' '}({fmtNum((payload.incomplete_ratio ?? 0) * 100, 1)}%)
              </div>
            )}
          </div>

          {/* ── Distribution sectorielle (chips cliquables) ── */}
          <SectorChips
            distribution={sectorDistribution}
            active={sectorFilter}
            onSelect={setSectorFilter}
            totalCount={count}
          />

          {/* ── Heatmap secteur × pilier ── */}
          <SectorPillarHeatmap
            matrix={sectorPillarMatrix}
            activeSector={sectorFilter}
            onSelect={setSectorFilter}
          />

          {/* ── CONTROLS : filtre + search + tri ── */}
          <div className="scan-controls" style={{ flexWrap: 'wrap', gap: '0.75rem' }}>
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <input
                type="search"
                placeholder="🔍 Ticker / nom / industrie…"
                value={searchText}
                onChange={e => setSearchText(e.target.value)}
                className="mini-input"
                style={{ width: 280 }}
              />
              {(searchText || sectorFilter !== 'ALL' || buyFilter !== 'all' || statusFilter !== 'all') && (
                <button
                  className="scan-filter-btn"
                  onClick={() => {
                    setSearchText('');
                    setSectorFilter('ALL');
                    setBuyFilter('all');
                    setStatusFilter('all');
                  }}
                  style={{ padding: '0.5rem 0.75rem' }}
                >
                  ✕ Reset
                </button>
              )}
            </div>

            {/* Filtre rapide "Buy candidates" — opérationalise la règle manuelle
                TITAN ≥ 80 (ou override 70-80) sans re-scroller toute l'univers. */}
            <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
              <button
                className={`scan-filter-btn ${buyFilter === 'all' ? 'active' : ''}`}
                onClick={() => setBuyFilter('all')}
              >
                Tous
              </button>
              <button
                className={`scan-filter-btn ${buyFilter === 'buy_strict' ? 'active' : ''}`}
                onClick={() => {
                  setBuyFilter('buy_strict');
                  setSortBy('titan');
                }}
                title={`TITAN ≥ ${BUY_TITAN_THRESHOLD} — règle d'achat stricte`}
                style={buyFilter === 'buy_strict' ? {
                  borderColor: 'rgba(34,197,94,0.5)',
                  color: 'var(--success)',
                  background: 'rgba(34,197,94,0.1)',
                } : undefined}
              >
                ✓ Buy ≥ {BUY_TITAN_THRESHOLD}
              </button>
              <button
                className={`scan-filter-btn ${buyFilter === 'buy_strict_fscore' ? 'active' : ''}`}
                onClick={() => {
                  setBuyFilter('buy_strict_fscore');
                  setSortBy('titan');
                }}
                title={`TITAN ≥ ${BUY_TITAN_THRESHOLD} ET F-Score ≥ 7 — règle compound (qualité fondamentale forte)`}
                style={buyFilter === 'buy_strict_fscore' ? {
                  borderColor: 'rgba(99,102,241,0.55)',
                  color: '#a5b4fc',
                  background: 'rgba(99,102,241,0.1)',
                } : undefined}
              >
                ⭐ Buy strict (TITAN+F)
              </button>
              <button
                className={`scan-filter-btn ${buyFilter === 'buy_override' ? 'active' : ''}`}
                onClick={() => setBuyFilter('buy_override')}
                title={`TITAN ≥ ${BUY_TITAN_OVERRIDE_THRESHOLD} — élargit pour override (Support/Piotroski)`}
                style={buyFilter === 'buy_override' ? {
                  borderColor: 'rgba(245,158,11,0.5)',
                  color: 'var(--warning)',
                  background: 'rgba(245,158,11,0.1)',
                } : undefined}
              >
                Watch ≥ {BUY_TITAN_OVERRIDE_THRESHOLD}
              </button>
            </div>

            {/* Filtre status portefeuille — vues complémentaires aux Buy filtres. */}
            <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
              <button
                className={`scan-filter-btn ${statusFilter === 'all' ? 'active' : ''}`}
                onClick={() => setStatusFilter('all')}
              >
                Status: tous
              </button>
              <button
                className={`scan-filter-btn ${statusFilter === 'free' ? 'active' : ''}`}
                onClick={() => setStatusFilter('free')}
                title="Ni HELD, ni PROPOSED, ni VETOED — candidats achetables propres"
              >
                ⚪ Libres
              </button>
              <button
                className={`scan-filter-btn ${statusFilter === 'held' ? 'active' : ''}`}
                onClick={() => setStatusFilter('held')}
                title="Positions actuellement OPEN dans le portefeuille"
              >
                💼 Détenus
              </button>
              <button
                className={`scan-filter-btn ${statusFilter === 'proposed' ? 'active' : ''}`}
                onClick={() => setStatusFilter('proposed')}
                title="Tickers en attente de décision dans la file"
              >
                ⏳ Proposés
              </button>
            </div>

            {/* Action backtest sur la vue filtrée */}
            <button
              className="action-btn"
              onClick={handleQuickBacktest}
              disabled={backtestRunning || filteredSorted.length < 5}
              title={
                filteredSorted.length < 5
                  ? `Minimum 5 tickers requis (${filteredSorted.length} dans la vue)`
                  : `Backtest TITAN sur les ${filteredSorted.length} tickers de la vue`
              }
              style={{ padding: '0.5rem 0.85rem', fontSize: '0.8rem', minWidth: 180 }}
            >
              {backtestRunning ? '⏳ Backtest…' : `📈 Backtest cette vue (${filteredSorted.length})`}
            </button>

            {/* Export CSV + Watchlist push — actions de capture */}
            <div style={{ display: 'flex', gap: '0.4rem' }}>
              <button
                className="scan-filter-btn"
                onClick={handleExportCsv}
                disabled={filteredSorted.length === 0}
                title={`Exporter ${filteredSorted.length} lignes en CSV (RFC 4180)`}
                style={{ padding: '0.5rem 0.65rem', fontSize: '0.78rem' }}
              >
                💾 CSV
              </button>
              <button
                className="scan-filter-btn"
                onClick={handlePushToWatchlist}
                disabled={filteredSorted.length === 0}
                title={
                  compareSet.size > 0
                    ? `Ajouter les ${compareSet.size} tickers sélectionnés à la watchlist`
                    : `Ajouter les 10 premiers tickers de la vue à la watchlist`
                }
                style={{ padding: '0.5rem 0.65rem', fontSize: '0.78rem' }}
              >
                ⭐ Watchlist {compareSet.size > 0 ? `(${compareSet.size})` : '(top 10)'}
              </button>
            </div>

            <div className="scan-sort">
              <label>Trier par&nbsp;</label>
              <select value={sortBy} onChange={e => setSortBy(e.target.value)} className="scan-select">
                <option value="titan">TITAN ↓</option>
                <option value="drift">Δ TITAN 7j ↓ (upgrades silencieuses)</option>
                <option value="piotroski">F-Score ↓</option>
                <option value="quality">Quality ↓</option>
                <option value="value">Value ↓</option>
                <option value="momentum">Momentum ↓</option>
                <option value="market_cap">Market Cap ↓</option>
                <option value="reco">Avis analystes ↑ (Buy en tête)</option>
                <option value="upside">Upside Target ↓</option>
                <option value="forward_pe">Forward P/E ↑</option>
                <option value="sector">Secteur (A→Z)</option>
                <option value="ticker">Ticker (A→Z)</option>
              </select>
            </div>
          </div>

          {/* ── PRESETS ── */}
          <PresetBar
            scope="universe"
            label="Vues univers"
            current={{ sectorFilter, searchText, sortBy, buyFilter }}
            onApply={(p) => {
              if (p?.sectorFilter !== undefined) setSectorFilter(p.sectorFilter);
              if (p?.searchText   !== undefined) setSearchText(p.searchText);
              if (p?.sortBy       !== undefined) setSortBy(p.sortBy);
              if (p?.buyFilter    !== undefined) setBuyFilter(p.buyFilter);
              setPage(1);
            }}
          />

          {/* Compteur + fraîcheur des données (pattern Seeking Alpha). */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            gap: 'var(--space-3)', flexWrap: 'wrap',
            margin: '0.5rem 0 0.25rem', fontSize: 'var(--fs-xs)',
            color: 'var(--text-muted)',
          }}>
            <span>
              <strong style={{ color: 'var(--text-main)' }}>{filteredSorted.length}</strong>
              {filteredSorted.length !== count && <> / {count}</>} tickers
            </span>
            <LastUpdated
              updatedAt={universeQuery.dataUpdatedAt}
              isFetching={universeQuery.isFetching}
            />
          </div>

          {/* ── TABLE ── */}
          <div className="scan-table-wrap">
            <table className="scan-table">
              <thead>
                <tr>
                  <th style={{ width: 28 }} title="Sélectionner pour comparaison (max 5)">⇄</th>
                  <SortableTh col="ticker"     sticky {...{ sortBy, sortDir, setSortBy, setSortDir }} title={COL_TOOLTIPS.ticker}>Ticker</SortableTh>
                  <SortableTh col="status"     {...{ sortBy, sortDir, setSortBy, setSortDir }} title={COL_TOOLTIPS.status}>Status</SortableTh>
                  <SortableTh col="sector"     {...{ sortBy, sortDir, setSortBy, setSortDir }} title={COL_TOOLTIPS.sector}>Secteur</SortableTh>
                  <SortableTh col="titan"      {...{ sortBy, sortDir, setSortBy, setSortDir }} title={COL_TOOLTIPS.titan}>TITAN</SortableTh>
                  <SortableTh col="drift"      {...{ sortBy, sortDir, setSortBy, setSortDir }} title={COL_TOOLTIPS.drift}>Δ 7j</SortableTh>
                  <SortableTh col="piotroski"  {...{ sortBy, sortDir, setSortBy, setSortDir }} title={COL_TOOLTIPS.piotroski}>F-Score</SortableTh>
                  <SortableTh col="market_cap" {...{ sortBy, sortDir, setSortBy, setSortDir }} title={COL_TOOLTIPS.market_cap}>Market Cap</SortableTh>
                  <SortableTh col="forward_pe" {...{ sortBy, sortDir, setSortBy, setSortDir }} title={COL_TOOLTIPS.forward_pe}>Fwd P/E</SortableTh>
                  <SortableTh col="reco"       {...{ sortBy, sortDir, setSortBy, setSortDir }} title={COL_TOOLTIPS.reco}>Avis analystes</SortableTh>
                  <SortableTh col="upside"     {...{ sortBy, sortDir, setSortBy, setSortDir }} title={COL_TOOLTIPS.upside}>Upside</SortableTh>
                  <th style={{ width: 60 }} title="Actions : 📥 push proposition · 🔔 alerte Telegram">Action</th>
                </tr>
              </thead>
              <tbody>
                {paginatedRows.map(t => {
                  const reco = recoBucket(t.recommendation_mean);
                  const up   = upsidePct(t);
                  return (
                    <tr
                      key={t.ticker}
                      className="scan-row"
                      data-ticker={t.ticker}
                      onClick={() => setAnalysisTicker(t.ticker)}
                      style={{
                        cursor: 'pointer',
                        background: compareSet.has(t.ticker)
                          ? 'rgba(99,102,241,0.08)'
                          : undefined,
                      }}
                      title="Cliquer pour ouvrir l'analyse complète"
                    >
                      <td onClick={(e) => e.stopPropagation()} style={{ textAlign: 'center' }}>
                        <input
                          type="checkbox"
                          checked={compareSet.has(t.ticker)}
                          onChange={() => toggleCompare(t.ticker)}
                          aria-label={`Ajouter ${t.ticker} au comparateur`}
                          style={{ cursor: 'pointer' }}
                        />
                      </td>
                      <td data-sticky="left">
                        <div className="scan-ticker-cell">
                          <span className="scan-ticker-logo">📈</span>
                          <div>
                            <strong>
                              {t.ticker}
                              {t.is_incomplete && (
                                <span title="Champs critiques manquants (market_cap ou sector)"
                                      style={{ marginLeft: '0.35rem', color: 'var(--warning)', fontSize: '0.75rem' }}>
                                  ⚠
                                </span>
                              )}
                            </strong>
                            <small>{(t.name || t.ticker).slice(0, 36)}</small>
                          </div>
                        </div>
                      </td>
                      <td><StatusBadge status={t.portfolio_status} /></td>
                      <td>
                        <div style={{ lineHeight: 1.3 }}>
                          <div style={{ fontWeight: 600, fontSize: '0.85rem' }}>
                            {t.sector || 'Unknown'}
                          </div>
                          {t.industry && (
                            <small style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>
                              {t.industry.slice(0, 32)}
                            </small>
                          )}
                        </div>
                      </td>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                          <TitanBadge value={t.titan_composite_score} />
                          <Sparkline points={t.titan_sparkline} />
                        </div>
                      </td>
                      <td><DriftBadge drift={t.titan_drift_7d} /></td>
                      <td><FScoreBadge n={t.f_score} max={t.f_score_max} /></td>
                      <td style={{ fontFamily: 'monospace', fontWeight: 700 }}>
                        {fmtMarketCap(t.market_cap)}
                      </td>
                      <td style={{
                        fontFamily: 'monospace',
                        color: t.forward_pe == null ? 'var(--text-muted)'
                             : t.forward_pe < 0 ? 'var(--danger)'
                             : t.forward_pe > 40 ? 'var(--warning)' : 'inherit',
                      }}>
                        {fmtNum(t.forward_pe, 1)}
                      </td>
                      <td>
                        <RecoBadge reco={reco} num={t.num_analysts} mean={t.recommendation_mean} />
                      </td>
                      <td style={{
                        fontFamily: 'monospace', fontWeight: 600,
                        color: !safeCompare(up, '>', -1e9) ? 'var(--text-muted)'
                             : safeCompare(up, '>', 0) ? 'var(--success)'
                             : 'var(--danger)',
                      }}>
                        {fmtSignedPct(up, 1)}
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <div style={{ display: 'flex', gap: '0.25rem' }}>
                          <PushButton
                            ticker={t.ticker}
                            titan={t.titan_composite_score}
                            pushing={!!pushingTickers[t.ticker]}
                            onPush={handlePushProposal}
                          />
                          <button
                            className="scan-filter-btn"
                            onClick={() => handleSetAlert(t.ticker, t.titan_composite_score)}
                            title={`Alerter Telegram sur changement TITAN — ${t.ticker}`}
                            style={{ padding: '0.25rem 0.45rem', fontSize: '0.75rem' }}
                          >
                            🔔
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
                {filteredSorted.length === 0 && (
                  <tr>
                    <td colSpan={12} style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
                      Aucun ticker ne correspond aux filtres.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* ── PAGINATION ── */}
          {filteredSorted.length > PAGE_SIZE && (
            <Pagination
              page={pageClamped}
              totalPages={totalPages}
              total={filteredSorted.length}
              pageSize={PAGE_SIZE}
              onChange={setPage}
            />
          )}
        </>
      ) : (
        <div className="card" style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
          <div style={{ fontSize: '2.5rem', marginBottom: '0.75rem' }}>🌐</div>
          <h3 style={{ color: 'var(--text-main)', marginBottom: '0.5rem' }}>Univers vide</h3>
          <p style={{ marginBottom: '1.5rem' }}>
            Aucune donnée dans <code>data/universe.json</code>.<br/>
            Lance un premier rebuild pour constituer l'univers institutionnel.
          </p>
        </div>
      )}

      {analysisTicker && (
        <TickerAnalysisModal
          ticker={analysisTicker}
          onClose={() => setAnalysisTicker(null)}
        />
      )}

      {/* Barre comparateur — apparaît dès 1 sélection. Sticky bottom. */}
      {compareSet.size > 0 && (
        <div style={{
          position: 'fixed',
          bottom: 16,
          left: '50%',
          transform: 'translateX(-50%)',
          zIndex: 100,
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
          padding: '0.6rem 1rem',
          borderRadius: 12,
          background: 'rgba(15, 23, 42, 0.92)',
          backdropFilter: 'blur(8px)',
          border: '1px solid rgba(99,102,241,0.4)',
          boxShadow: '0 4px 24px rgba(0,0,0,0.4)',
        }}>
          <span style={{ fontSize: '0.85rem', color: 'var(--text-main)' }}>
            ⇄ <strong>{compareSet.size}</strong>/{COMPARE_MAX} ticker{compareSet.size > 1 ? 's' : ''} :
            <span style={{ marginLeft: '0.4rem', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
              {[...compareSet].join(', ')}
            </span>
          </span>
          <button
            className="action-btn"
            onClick={() => setCompareOpen(true)}
            disabled={compareSet.size < 2}
            style={{ padding: '0.35rem 0.8rem', fontSize: '0.8rem' }}
            title={compareSet.size < 2 ? 'Sélectionne au moins 2 tickers' : 'Ouvrir le comparateur'}
          >
            📊 Comparer
          </button>
          <button
            className="scan-filter-btn"
            onClick={() => setCompareSet(new Set())}
            style={{ padding: '0.35rem 0.6rem', fontSize: '0.78rem' }}
          >
            ✕ Vider
          </button>
        </div>
      )}

      {compareOpen && compareSet.size >= 2 && (
        <CompareModal
          tickers={[...compareSet].map(t => tickers.find(x => x.ticker === t)).filter(Boolean)}
          onClose={() => setCompareOpen(false)}
          onOpenAnalysis={(tk) => { setCompareOpen(false); setAnalysisTicker(tk); }}
        />
      )}

      {(backtestResult || backtestError) && (
        <BacktestResultModal
          result={backtestResult}
          error={backtestError}
          onClose={() => { setBacktestResult(null); setBacktestError(null); }}
        />
      )}
    </div>
  );
}

function TitanBadge({ value }) {
  if (!Number.isFinite(value)) {
    return <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>—</span>;
  }
  const color = factorColor(value);
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        minWidth: 44,
        padding: '0.2rem 0.55rem',
        borderRadius: 6,
        border: `1px solid ${color}55`,
        background: `${color}12`,
        color,
        fontFamily: 'monospace',
        fontWeight: 700,
        fontSize: '0.85rem',
      }}
    >
      {fmtNum(value, 1)}
    </span>
  );
}

function BacktestResultModal({ result, error, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const stats = result?.stats || {};
  const meta = result?.meta || {};
  const periods = result?.periods || [];
  const equity = result?.equity_curve || [];

  // Mini chart equity curve via SVG (cohérent avec Sparkline ailleurs).
  const equityChart = (() => {
    if (equity.length < 2) return null;
    const w = 560, h = 100;
    const ys = equity.map(p => p[1]);
    const min = Math.min(...ys, 1);
    const max = Math.max(...ys, 1);
    const range = max - min || 1;
    const stepX = w / (equity.length - 1);
    const path = equity
      .map((p, i) => `${(i * stepX).toFixed(1)},${(h - ((p[1] - min) / range) * h).toFixed(1)}`)
      .join(' ');
    const final = ys[ys.length - 1];
    const stroke = final >= 1 ? 'var(--success)' : 'var(--danger)';
    return (
      <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} style={{ marginTop: '0.5rem' }}>
        <line x1={0} y1={h - ((1 - min) / range) * h} x2={w} y2={h - ((1 - min) / range) * h}
              stroke="rgba(148,163,184,0.3)" strokeDasharray="3 3" />
        <polyline points={path} fill="none" stroke={stroke} strokeWidth={1.6} />
      </svg>
    );
  })();

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, zIndex: 200,
      background: 'rgba(0,0,0,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: '2rem',
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: 'min(700px, 100%)',
        maxHeight: '90vh',
        overflow: 'auto',
        background: 'var(--panel-bg, #1f2937)',
        border: '1px solid var(--panel-border, #334155)',
        borderRadius: 12,
        padding: '1.25rem 1.5rem',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <h3 style={{ margin: 0 }}>📈 Backtest TITAN — vue filtrée</h3>
          <button onClick={onClose} className="scan-filter-btn">✕ Fermer (ESC)</button>
        </div>

        {error && (
          <div style={{ padding: '1rem', background: 'rgba(239,68,68,0.1)', borderRadius: 8, color: 'var(--danger)' }}>
            ❌ {error}
          </div>
        )}

        {result && (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '0.75rem', marginBottom: '1rem' }}>
              <Metric label="Total return" value={fmtSignedPct((stats.total_return ?? 0) * 100, 2)}
                      color={(stats.total_return ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'} />
              <Metric label="vs Benchmark" value={fmtSignedPct((stats.alpha ?? 0) * 100, 2)}
                      color={(stats.alpha ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'} />
              <Metric label="Sharpe (annual)" value={fmtNum(stats.sharpe_annual, 2)} />
              <Metric label="Sharpe (daily)" value={fmtNum(stats.sharpe_daily, 3)} />
              <Metric label="Max DD" value={fmtSignedPct((stats.max_drawdown ?? 0) * -100, 2)}
                      color="var(--danger)" />
              <Metric label="Hit rate" value={fmtSignedPct((stats.hit_rate ?? 0) * 100, 0)} />
              <Metric label="Périodes" value={periods.length} />
              <Metric label="Top-N" value={meta.top_n ?? '—'} />
            </div>

            <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '0.25rem' }}>
              Equity curve ({equity.length} points)
            </div>
            {equityChart}

            <details style={{ marginTop: '1rem', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
              <summary style={{ cursor: 'pointer' }}>Détails par période ({periods.length})</summary>
              <table style={{ width: '100%', marginTop: '0.5rem', fontFamily: 'monospace', fontSize: '0.72rem' }}>
                <thead>
                  <tr><th align="left">Date</th><th align="right">Return</th><th align="right">Bench</th><th align="right">Top-N</th></tr>
                </thead>
                <tbody>
                  {periods.slice(-10).map((p, i) => (
                    <tr key={i}>
                      <td>{p.signal_date}</td>
                      <td align="right" style={{ color: (p.return ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                        {fmtSignedPct((p.return ?? 0) * 100, 2)}
                      </td>
                      <td align="right">{fmtSignedPct((p.benchmark_return ?? 0) * 100, 2)}</td>
                      <td align="right">{(p.tickers || []).slice(0, 5).join(' ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </>
        )}
      </div>
    </div>
  );
}

function Metric({ label, value, color }) {
  return (
    <div style={{
      padding: '0.6rem 0.8rem',
      background: 'rgba(148,163,184,0.05)',
      borderRadius: 6,
      borderLeft: `3px solid ${color || 'var(--accent-primary)'}`,
    }}>
      <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>
        {label}
      </div>
      <div style={{ fontSize: '0.95rem', fontWeight: 600, fontFamily: 'monospace', color: color || 'inherit' }}>
        {value}
      </div>
    </div>
  );
}

function SortableTh({ col, sortBy, sortDir, setSortBy, setSortDir, children, title, sticky }) {
  const active = sortBy === col;
  const arrow = active ? (sortDir === 'desc' ? '↓' : '↑') : '';
  const handleClick = () => {
    if (active) {
      setSortDir(sortDir === 'desc' ? 'asc' : 'desc');
    } else {
      setSortBy(col);
      // Default direction reset à 'desc' (dominant pour les scores).
      setSortDir('desc');
    }
  };
  return (
    <th
      onClick={handleClick}
      data-sticky={sticky ? 'left' : undefined}
      title={title || `Trier par ${col}`}
      style={{
        cursor: 'pointer',
        userSelect: 'none',
        color: active ? 'var(--accent-primary)' : 'inherit',
        fontWeight: active ? 700 : undefined,
      }}
    >
      {children} <span style={{ opacity: active ? 1 : 0.3, marginLeft: 4 }}>{arrow || '⇅'}</span>
    </th>
  );
}

function StatusBadge({ status }) {
  // 4 status backend possibles + null. Couleurs alignées avec la sémantique de
  // décision : HELD = neutre informatif, PROPOSED = action requise (warning),
  // VETOED = bloqué (danger), WATCH = succès récent.
  if (!status) return <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>—</span>;
  const map = {
    HELD:     { label: '💼 HELD',     color: 'var(--accent-primary)' },
    PROPOSED: { label: '⏳ PROPOSED', color: 'var(--warning)' },
    VETOED:   { label: '🚫 VETOED',   color: 'var(--danger)' },
    WATCH:    { label: '👁 WATCH',    color: 'var(--success)' },
  };
  const conf = map[status] || { label: status, color: 'var(--text-muted)' };
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '0.15rem 0.45rem',
        borderRadius: 4,
        border: `1px solid ${conf.color}55`,
        background: `${conf.color}12`,
        color: conf.color,
        fontSize: '0.7rem',
        fontWeight: 600,
        whiteSpace: 'nowrap',
      }}
      title={`Status portefeuille : ${status}`}
    >
      {conf.label}
    </span>
  );
}

function CompareModal({ tickers, onClose, onOpenAnalysis }) {
  // Modal de comparaison Q/V/R/S/M/P + TITAN + F-Score sur 2-5 tickers.
  // Pas de dep externe — barres CSS pour rester light + cohérent avec la UI.
  const pillars = [
    { key: 'titan_composite_score', label: 'TITAN' },
    { key: 'quality_score',         label: 'Quality' },
    { key: 'value_score',           label: 'Value' },
    { key: 'risk_score',            label: 'Risk' },
    { key: 'sentiment_score',       label: 'Sentiment' },
    { key: 'momentum_score',        label: 'Momentum' },
    { key: 'piotroski_score',       label: 'Piotroski' },
  ];
  // ESC pour fermer (cohérent avec TickerAnalysisModal).
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Couleur par ticker — palette stable, pas de dep aléatoire.
  const palette = ['#a5b4fc', '#6ee7b7', '#fbbf24', '#fb923c', '#f472b6'];

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, zIndex: 200,
      background: 'rgba(0,0,0,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: '2rem',
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: 'min(900px, 100%)',
        maxHeight: '90vh',
        overflow: 'auto',
        background: 'var(--panel-bg, #1f2937)',
        border: '1px solid var(--panel-border, #334155)',
        borderRadius: 12,
        padding: '1.25rem 1.5rem',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <h3 style={{ margin: 0 }}>📊 Comparateur quantamental — {tickers.length} tickers</h3>
          <button onClick={onClose} className="scan-filter-btn">✕ Fermer (ESC)</button>
        </div>

        {/* Légende tickers cliquables → ouvre analyse complète */}
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
          {tickers.map((t, i) => (
            <button key={t.ticker}
                    onClick={() => onOpenAnalysis(t.ticker)}
                    className="scan-filter-btn"
                    title="Ouvrir l'analyse complète"
                    style={{
                      borderColor: palette[i] + '88',
                      color: palette[i],
                      fontWeight: 600,
                    }}>
              ● {t.ticker} <small style={{ opacity: 0.7, marginLeft: '0.25rem' }}>{t.sector || ''}</small>
            </button>
          ))}
        </div>

        {/* Barres groupées par pilier */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
          {pillars.map(p => (
            <div key={p.key}>
              <div style={{
                fontSize: '0.78rem',
                color: 'var(--text-muted)',
                marginBottom: '0.2rem',
                fontWeight: 500,
              }}>
                {p.label}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
                {tickers.map((t, i) => {
                  const v = t[p.key];
                  const valid = Number.isFinite(v);
                  const w = valid ? Math.max(2, Math.min(100, v)) : 0;
                  return (
                    <div key={t.ticker} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <span style={{
                        width: 56,
                        fontFamily: 'monospace',
                        fontSize: '0.72rem',
                        color: palette[i],
                        fontWeight: 600,
                      }}>
                        {t.ticker}
                      </span>
                      <div style={{ flex: 1, height: 14, background: 'rgba(148,163,184,0.12)', borderRadius: 3, position: 'relative' }}>
                        {valid && (
                          <div style={{
                            width: `${w}%`,
                            height: '100%',
                            background: palette[i],
                            opacity: 0.75,
                            borderRadius: 3,
                          }} />
                        )}
                      </div>
                      <span style={{
                        width: 50,
                        fontFamily: 'monospace',
                        fontSize: '0.78rem',
                        textAlign: 'right',
                        color: valid ? 'var(--text-main)' : 'var(--text-muted)',
                      }}>
                        {valid ? fmtNum(v, 1) : '—'}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        {/* Tableau récap rapide — F-Score, market cap, secteur */}
        <table style={{
          width: '100%',
          marginTop: '1.25rem',
          borderCollapse: 'collapse',
          fontSize: '0.8rem',
        }}>
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(148,163,184,0.2)' }}>
              <th style={{ textAlign: 'left', padding: '0.4rem' }}>Ticker</th>
              <th style={{ textAlign: 'left', padding: '0.4rem' }}>Secteur</th>
              <th style={{ textAlign: 'right', padding: '0.4rem' }}>F-Score</th>
              <th style={{ textAlign: 'right', padding: '0.4rem' }}>Δ 7j</th>
              <th style={{ textAlign: 'right', padding: '0.4rem' }}>Market Cap</th>
              <th style={{ textAlign: 'right', padding: '0.4rem' }}>Fwd P/E</th>
            </tr>
          </thead>
          <tbody>
            {tickers.map((t, i) => (
              <tr key={t.ticker} style={{ borderBottom: '1px solid rgba(148,163,184,0.08)' }}>
                <td style={{ padding: '0.35rem 0.4rem', color: palette[i], fontWeight: 600, fontFamily: 'monospace' }}>
                  {t.ticker}
                </td>
                <td style={{ padding: '0.35rem 0.4rem' }}>{t.sector || '—'}</td>
                <td style={{ padding: '0.35rem 0.4rem', textAlign: 'right', fontFamily: 'monospace' }}>
                  {Number.isFinite(t.f_score) ? `${t.f_score}/${t.f_score_max ?? 9}` : '—'}
                </td>
                <td style={{ padding: '0.35rem 0.4rem', textAlign: 'right' }}>
                  <DriftBadge drift={t.titan_drift_7d} />
                </td>
                <td style={{ padding: '0.35rem 0.4rem', textAlign: 'right', fontFamily: 'monospace' }}>
                  {fmtMarketCap(t.market_cap)}
                </td>
                <td style={{ padding: '0.35rem 0.4rem', textAlign: 'right', fontFamily: 'monospace' }}>
                  {fmtNum(t.forward_pe, 1)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Sparkline({ points }) {
  // SVG inline pur — pas de lib pour 8 valeurs (gain bundle + zéro re-render).
  // Vue : 60×18 px, polyline normalisée sur le min/max local pour amplifier le
  // signal visuel. Si série < 2 points, on ne dessine rien.
  if (!Array.isArray(points) || points.length < 2) return null;
  const w = 60;
  const h = 18;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const stepX = w / (points.length - 1);
  const path = points
    .map((v, i) => `${(i * stepX).toFixed(1)},${(h - ((v - min) / range) * h).toFixed(1)}`)
    .join(' ');
  const last = points[points.length - 1];
  const first = points[0];
  // Tendance : montante = success, descendante = danger, plate = muted.
  const delta = last - first;
  const stroke = Math.abs(delta) < 1
    ? 'var(--text-muted)'
    : delta > 0
      ? 'var(--success)'
      : 'var(--danger)';
  return (
    <svg
      width={w} height={h}
      viewBox={`0 0 ${w} ${h}`}
      style={{ flexShrink: 0, opacity: 0.85 }}
      title={`TITAN ${points.length} pts : ${fmtNum(first, 0)} → ${fmtNum(last, 0)}`}
    >
      <polyline
        points={path}
        fill="none"
        stroke={stroke}
        strokeWidth={1.4}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function PushButton({ ticker, titan, pushing, onPush }) {
  // Désactivé si pas de score TITAN (impossible de prioriser ce ticker dans le
  // pipeline d'allocation), ou si TITAN < seuil override (la file Propositions
  // ne doit pas devenir un dépotoir — bypass cron mais pas bypass logique).
  const eligible = Number.isFinite(titan) && titan >= BUY_TITAN_OVERRIDE_THRESHOLD;
  const disabled = pushing || !eligible;
  const title = !Number.isFinite(titan)
    ? 'TITAN non calculé pour ce ticker'
    : titan < BUY_TITAN_OVERRIDE_THRESHOLD
      ? `TITAN ${fmtNum(titan, 0)} < ${BUY_TITAN_OVERRIDE_THRESHOLD} (seuil push manuel)`
      : `Pousser ${ticker} dans la file Propositions (TITAN ${fmtNum(titan, 0)})`;
  return (
    <button
      className="scan-filter-btn"
      onClick={() => onPush(ticker)}
      disabled={disabled}
      title={title}
      style={{
        padding: '0.25rem 0.55rem',
        fontSize: '0.75rem',
        opacity: disabled ? 0.45 : 1,
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
    >
      {pushing ? '⏳' : '📥'}
    </button>
  );
}

function DriftBadge({ drift }) {
  if (drift == null || !Number.isFinite(drift)) {
    return <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>—</span>;
  }
  // Seuils calibrés pour le scoring TITAN : ±2 = bruit, ±5 = significatif,
  // ±10 = upgrade/downgrade massive (révision fondamentale).
  const abs = Math.abs(drift);
  const noise = abs < 2;
  const sign = drift > 0 ? '+' : '';
  const arrow = drift > 0.5 ? '▲' : drift < -0.5 ? '▼' : '·';
  const color = noise
    ? 'var(--text-muted)'
    : drift > 0
      ? 'var(--success)'
      : 'var(--danger)';
  const weight = abs >= 5 ? 700 : 500;
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.2rem',
        fontFamily: 'monospace',
        fontWeight: weight,
        color,
        fontSize: '0.8rem',
      }}
      title={`Δ TITAN sur 7j : ${sign}${fmtNum(drift, 2)} pts`}
    >
      <span style={{ fontSize: '0.7rem' }}>{arrow}</span>
      {sign}{fmtNum(drift, 1)}
    </span>
  );
}

function FScoreBadge({ n, max }) {
  if (n == null || !Number.isFinite(n)) {
    return <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>—</span>;
  }
  // Échelle Piotroski : ≥7 = strong, 4-6 = neutre, ≤3 = faible.
  const color = n >= 7 ? 'var(--success)'
              : n >= 4 ? '#94a3b8'
              : 'var(--danger)';
  const denom = Number.isFinite(max) && max > 0 ? max : 9;
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'baseline',
        gap: '0.15rem',
        fontFamily: 'monospace',
        fontWeight: 700,
        color,
        fontSize: '0.85rem',
      }}
      title={`Piotroski F-Score : ${n} critères passés sur ${denom}`}
    >
      {n}
      <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 500 }}>
        /{denom}
      </span>
    </span>
  );
}

function upsidePct(t) {
  const tgt = t.price_target_mean;
  const cur = t.current_price ?? t.regularMarketPrice;
  if (tgt == null || cur == null || cur === 0) return null;
  return (tgt / cur - 1) * 100;
}

function RebuildBanner({
  rebuilding, launching, jobId, updated, stats, count, sourceIndices,
  showOptions, setShowOptions,
  optIndices, optMinCap, optFull, toggleIndex, setOptMinCap, setOptFull,
  confirm, setConfirm, onRebuild, onKill,
}) {
  const busy = rebuilding || launching;
  return (
    <div className="family-global-panel card">
      <div className="fgp-info" style={{ flex: 1 }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          🌐 Univers Quantamental
          {rebuilding && <span className="fs-poll">REBUILD EN COURS</span>}
        </h3>
        <p>
          {count > 0
            ? <>
                {count} actions institutionnelles
                {sourceIndices?.length ? ` · ${sourceIndices.map(s => s.toUpperCase()).join(' + ')}` : ''}
                {updated ? ` · MAJ ${formatTs(updated)}` : ''}
                {stats?.elapsed_seconds ? ` · Build ${stats.elapsed_seconds}s` : ''}
              </>
            : 'Aucune donnée — lance un premier rebuild pour initialiser l\'univers.'}
        </p>
      </div>

      <div className="fgp-controls" style={{ flexDirection: 'column', alignItems: 'flex-end', gap: '0.5rem' }}>
        {!confirm ? (
          <>
            <button
              className="action-btn fgp-btn"
              onClick={() => rebuilding ? null : setConfirm(true)}
              disabled={busy}
              style={{ minWidth: 280 }}
            >
              {busy
                ? <><div className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} /> {optFull ? 'Rebuild complet…' : 'Refresh staggered…'}</>
                : (optFull ? '🔴 Rebuild complet (urgence)' : '⚡ Refresh quotidien (50 tickers)')}
            </button>

            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <button
                className="fd-edit-btn"
                onClick={() => setShowOptions(v => !v)}
                style={{ padding: '0.4rem 0.75rem', fontSize: '0.78rem' }}
              >
                {showOptions ? '▾' : '▸'} Options ({optFull ? 'full' : 'staggered'} · {optIndices.length} index{optFull ? ` · ≥ ${fmtMarketCap(optMinCap)}` : ''})
              </button>
              {rebuilding && jobId && (
                <button
                  className="fd-edit-btn fgp-kill"
                  onClick={onKill}
                  style={{
                    padding: '0.4rem 0.75rem', fontSize: '0.78rem',
                    borderColor: 'rgba(239,68,68,0.4)', color: 'var(--danger)',
                  }}
                >
                  ⏹ Interrompre
                </button>
              )}
            </div>
          </>
        ) : (
          <div className="sc-confirm" style={{ minWidth: 340 }}>
            {optFull ? (
              <p>
                <strong style={{ color: 'var(--danger)' }}>⚠ Rebuild COMPLET</strong> depuis {optIndices.map(s => s.toUpperCase()).join(' + ') || '(aucun index)'} ?
                <br/>
                <small style={{ color: 'var(--text-muted)' }}>
                  ≥ {fmtMarketCap(optMinCap)} Market Cap · ~1500 calls FMP · crame le quota journalier
                </small>
              </p>
            ) : (
              <p>
                <strong>Refresh staggered</strong> — 50 tickers les plus anciens (fetched_at &gt; 14j).
                <br/>
                <small style={{ color: 'var(--text-muted)' }}>
                  Les tickers récents sont skip · ~150 calls FMP · merge non-destructif
                </small>
              </p>
            )}
            <div className="sc-confirm-btns">
              <button className="btn-confirm-yes" onClick={onRebuild} disabled={optIndices.length === 0}>
                Oui, lancer
              </button>
              <button className="btn-confirm-no" onClick={() => setConfirm(false)}>
                Annuler
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Options panel (collapsible) */}
      {showOptions && !confirm && (
        <div style={{
          width: '100%', flexBasis: '100%', marginTop: '0.75rem',
          paddingTop: '0.75rem', borderTop: '1px solid var(--panel-border)',
          display: 'flex', gap: '1.5rem', flexWrap: 'wrap', alignItems: 'flex-start',
        }}>
          {/* Mode */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', minWidth: 280 }}>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)',
                          textTransform: 'uppercase', letterSpacing: 0.5 }}>
              Mode
            </div>
            <div style={{ display: 'flex', gap: '0.4rem' }}>
              <button
                className={`scan-filter-btn ${!optFull ? 'active' : ''}`}
                onClick={() => setOptFull(false)}
                disabled={rebuilding}
                title="Refresh 50 tickers les plus anciens · skip <14j · ~150 calls FMP"
              >
                {!optFull ? '✓ ' : ''}Staggered (recommandé)
              </button>
              <button
                className={`scan-filter-btn ${optFull ? 'active' : ''}`}
                onClick={() => setOptFull(true)}
                disabled={rebuilding}
                title="Rebuild intégral ~1500 calls FMP — crame le quota journalier"
                style={optFull ? {
                  borderColor: 'rgba(239,68,68,0.5)',
                  color: 'var(--danger)',
                  background: 'rgba(239,68,68,0.08)',
                } : undefined}
              >
                {optFull ? '✓ ' : ''}🔴 Full (urgence)
              </button>
            </div>
            <small style={{ color: 'var(--text-muted)', fontSize: '0.72rem', lineHeight: 1.35 }}>
              {optFull
                ? 'Rebuild intégral · ~1500 calls FMP · consomme tout le quota journalier.'
                : 'Skip les tickers refreshés < 14j · merge non-destructif · compatible cron 6h.'}
            </small>
          </div>

          {/* Indices */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)',
                          textTransform: 'uppercase', letterSpacing: 0.5 }}>
              Sources
            </div>
            <div style={{ display: 'flex', gap: '0.4rem' }}>
              {INDEX_OPTIONS.map(opt => (
                <button
                  key={opt.id}
                  className={`scan-filter-btn ${optIndices.includes(opt.id) ? 'active' : ''}`}
                  onClick={() => toggleIndex(opt.id)}
                  title={opt.desc}
                  disabled={rebuilding}
                >
                  {optIndices.includes(opt.id) ? '✓ ' : ''}{opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Market Cap threshold — uniquement pertinent en mode full */}
          {optFull && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)',
                            textTransform: 'uppercase', letterSpacing: 0.5 }}>
                Market Cap minimum
              </div>
              <div style={{ display: 'flex', gap: '0.4rem' }}>
                {CAP_PRESETS.map(p => (
                  <button
                    key={p.label}
                    className={`scan-filter-btn ${optMinCap === p.value ? 'active' : ''}`}
                    onClick={() => setOptMinCap(p.value)}
                    disabled={rebuilding}
                  >
                    ≥ {p.label}$
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SectorPillarHeatmap({ matrix, activeSector, onSelect }) {
  if (!matrix?.rows?.length) return null;
  // Couleur cellulaire = factorColor sur la moyenne (cohérent avec TitanBadge).
  // L'intensité de fond varie en alpha pour amplifier le contraste — un secteur
  // moyenne 88 doit "sauter aux yeux" vs un 72.
  const cellStyle = (val) => {
    if (!Number.isFinite(val)) {
      return { color: 'var(--text-muted)', background: 'transparent' };
    }
    const c = factorColor(val);
    // Alpha proportionnelle à |val - 50| → bg plus saturé loin de la médiane.
    const intensity = Math.min(1, Math.abs(val - 50) / 50);
    const alpha = (0.08 + intensity * 0.32).toFixed(2);
    return {
      color: c,
      background: c.startsWith('var(')
        ? `color-mix(in srgb, ${c} ${Math.round(alpha * 100)}%, transparent)`
        : `${c}${Math.round(alpha * 255).toString(16).padStart(2, '0')}`,
    };
  };
  return (
    <div className="card" style={{ padding: '0.75rem 1rem' }}>
      <div className="card-title" style={{ fontSize: '0.85rem' }}>
        🧭 Heatmap secteur × pilier
        <small style={{ marginLeft: '0.5rem', color: 'var(--text-muted)', fontWeight: 400 }}>
          score moyen — clique un secteur pour filtrer la table
        </small>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{
          width: '100%',
          borderCollapse: 'separate',
          borderSpacing: '2px',
          fontFamily: 'monospace',
          fontSize: '0.78rem',
        }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem', color: 'var(--text-muted)', fontWeight: 500 }}>
                Secteur
              </th>
              {matrix.pillars.map(p => (
                <th key={p.key}
                    title={p.tooltip || p.key}
                    style={{ padding: '0.25rem 0.5rem', color: 'var(--text-muted)', fontWeight: 500, width: 44, cursor: 'help' }}>
                  {p.label}
                </th>
              ))}
              <th style={{ padding: '0.25rem 0.5rem', color: 'var(--text-muted)', fontWeight: 500, width: 44 }}>
                n
              </th>
            </tr>
          </thead>
          <tbody>
            {matrix.rows.map(r => {
              const isActive = activeSector === r.sector;
              return (
                <tr key={r.sector}>
                  <td
                    onClick={() => onSelect(isActive ? 'ALL' : r.sector)}
                    style={{
                      padding: '0.25rem 0.5rem',
                      cursor: 'pointer',
                      fontWeight: isActive ? 700 : 500,
                      color: isActive ? 'var(--accent-primary)' : 'inherit',
                      textDecoration: isActive ? 'underline' : 'none',
                    }}
                  >
                    {r.sector}
                  </td>
                  {r.scores.map((v, i) => (
                    <td key={matrix.pillars[i].key}
                        title={`${r.sector} · ${matrix.pillars[i].label} = ${Number.isFinite(v) ? fmtNum(v, 1) : '—'}`}
                        style={{
                          padding: '0.25rem 0',
                          textAlign: 'center',
                          fontWeight: 600,
                          borderRadius: 4,
                          ...cellStyle(v),
                        }}>
                      {Number.isFinite(v) ? Math.round(v) : '—'}
                    </td>
                  ))}
                  <td style={{ padding: '0.25rem 0.5rem', textAlign: 'right', color: 'var(--text-muted)' }}>
                    {r.count}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SectorChips({ distribution, active, onSelect, totalCount }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', alignItems: 'center' }}>
      <button
        className={`scan-filter-btn ${active === 'ALL' ? 'active' : ''}`}
        onClick={() => onSelect('ALL')}
      >
        Tous <span style={{ marginLeft: '0.35rem', opacity: 0.6 }}>{totalCount}</span>
      </button>
      {distribution.map(s => (
        <button
          key={s.name}
          className={`scan-filter-btn ${active === s.name ? 'active' : ''}`}
          onClick={() => onSelect(s.name)}
        >
          {s.name} <span style={{ marginLeft: '0.35rem', opacity: 0.6 }}>{s.count}</span>
        </button>
      ))}
    </div>
  );
}

function RecoBadge({ reco, num, mean }) {
  if (reco.weight === 0) {
    return <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>—</span>;
  }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
      <span
        className="scan-signal-badge"
        style={{
          color: reco.color,
          borderColor: reco.color + '55',
          background: reco.color + '12',
        }}
      >
        {reco.label}
      </span>
      <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.1 }}>
        <small style={{ fontFamily: 'monospace', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          {fmtNum(mean, 2)} / 5
        </small>
        {num != null && (
          <small style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>
            {num} analystes
          </small>
        )}
      </div>
    </div>
  );
}

function formatTs(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}
