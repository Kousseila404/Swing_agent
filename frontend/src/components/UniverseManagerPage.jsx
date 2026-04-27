import { useState, useEffect, useMemo, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { rebuildUniverse, killJob } from '../api/client';
import { useUniverse, useJob } from '../hooks/useApi';
import { fmtNum, fmtMarketCap, fmtPrice, fmtSignedPct, safeCompare } from '../utils/format';
import Pagination from './Pagination';

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

  const [sectorFilter, setSectorFilter] = useState('ALL');
  const [searchText, setSearchText]     = useState('');
  const [sortBy, setSortBy]             = useState('market_cap');

  const PAGE_SIZE = 50;
  const [page, setPage] = useState(1);

  const prevRebuildingRef = useRef(false);

  const toast = (msg, type = 'ok') => {
    const id = Date.now() + Math.random();
    setToasts(t => [...t, { id, msg, type }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 4500);
  };

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
  }, [rebuilding, payload]);

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

  const filteredSorted = useMemo(() => {
    const q = searchText.trim().toUpperCase();
    let list = tickers.filter(t => {
      if (sectorFilter !== 'ALL' && (t.sector || 'Unknown') !== sectorFilter) return false;
      if (q) {
        return (
          t.ticker?.includes(q) ||
          (t.name || '').toUpperCase().includes(q) ||
          (t.industry || '').toUpperCase().includes(q)
        );
      }
      return true;
    });

    list = [...list].sort((a, b) => {
      switch (sortBy) {
        case 'market_cap':
          return (b.market_cap ?? 0) - (a.market_cap ?? 0);
        case 'reco':
          // Plus bas = meilleur avis (1 = Strong Buy)
          return (a.recommendation_mean ?? 99) - (b.recommendation_mean ?? 99);
        case 'forward_pe':
          return (a.forward_pe ?? 1e9) - (b.forward_pe ?? 1e9);
        case 'sector':
          return (a.sector || 'zzz').localeCompare(b.sector || 'zzz');
        case 'ticker':
          return (a.ticker || '').localeCompare(b.ticker || '');
        case 'upside':
          return upsidePct(b) - upsidePct(a);
        default:
          return 0;
      }
    });
    return list;
  }, [tickers, sectorFilter, searchText, sortBy]);

  // Reset page à chaque changement de filtre/tri — évite de rester sur
  // une page vide. Pattern React "adjust state on input change" (évite
  // le cascading render d'un useEffect).
  const filterKey = `${sectorFilter}|${searchText}|${sortBy}`;
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
              {(searchText || sectorFilter !== 'ALL') && (
                <button
                  className="scan-filter-btn"
                  onClick={() => { setSearchText(''); setSectorFilter('ALL'); }}
                  style={{ padding: '0.5rem 0.75rem' }}
                >
                  ✕ Reset
                </button>
              )}
            </div>
            <div className="scan-sort">
              <label>Trier par&nbsp;</label>
              <select value={sortBy} onChange={e => setSortBy(e.target.value)} className="scan-select">
                <option value="market_cap">Market Cap ↓</option>
                <option value="reco">Avis analystes ↑ (Buy en tête)</option>
                <option value="upside">Upside Target ↓</option>
                <option value="forward_pe">Forward P/E ↑</option>
                <option value="sector">Secteur (A→Z)</option>
                <option value="ticker">Ticker (A→Z)</option>
              </select>
            </div>
          </div>

          {/* ── TABLE ── */}
          <div className="scan-table-wrap">
            <table className="scan-table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Secteur</th>
                  <th>Market Cap</th>
                  <th>Fwd P/E</th>
                  <th>Avis analystes</th>
                  <th>Target moyen</th>
                  <th>Upside</th>
                  <th>Index</th>
                </tr>
              </thead>
              <tbody>
                {paginatedRows.map(t => {
                  const reco = recoBucket(t.recommendation_mean);
                  const up   = upsidePct(t);
                  return (
                    <tr key={t.ticker} className="scan-row">
                      <td>
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
                      <td style={{ fontFamily: 'monospace' }}>
                        {fmtPrice(t.price_target_mean)}
                      </td>
                      <td style={{
                        fontFamily: 'monospace', fontWeight: 600,
                        color: !safeCompare(up, '>', -1e9) ? 'var(--text-muted)'
                             : safeCompare(up, '>', 0) ? 'var(--success)'
                             : 'var(--danger)',
                      }}>
                        {fmtSignedPct(up, 1)}
                      </td>
                      <td>
                        <div style={{ display: 'flex', gap: '0.25rem' }}>
                          {(t.source_indices || []).map(src => (
                            <span key={src} className="scan-chip"
                                  style={{ padding: '0.15rem 0.5rem', fontSize: '0.7rem',
                                           borderColor: 'rgba(99,102,241,0.35)',
                                           color: '#a5b4fc' }}>
                              {src.toUpperCase()}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  );
                })}
                {filteredSorted.length === 0 && (
                  <tr>
                    <td colSpan={8} style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
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
    </div>
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
