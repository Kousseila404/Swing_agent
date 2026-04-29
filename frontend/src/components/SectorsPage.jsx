import { useState, useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSectors, useSector } from '../hooks/useApi';
import SectorCard from './SectorCard';
import { factorColor } from '../utils/colors';
import { fmtNum, fmtSignedPct as fmtPct, fmtMarketCap } from '../utils/format';
import Pagination from './Pagination';
import PresetBar from './common/PresetBar';

// SectorsPage — Rotation sectorielle : grille SectorCard + drill-down tickers.
// Consomme /api/sectors (agrégats GICS + momentum 6M) + /api/sectors/{name}.

const POLL_MS = 30_000;

const SORTS = [
  { id: 'titan_composite_score', label: '🏆 TITAN Composite'      },
  { id: 'quality_score_mean',    label: '💎 Quality'              },
  { id: 'value_score_mean',      label: '💰 Value'                },
  { id: 'risk_score_mean',       label: '🛡 Risk (safer first)'   },
  { id: 'sentiment_score_mean',  label: '👁 Sentiment'            },
  { id: 'momentum_risk_adjusted',label: '🚀 Momentum RA (Sharpe 6M)' },
  { id: 'momentum_return_pct',   label: '📈 Momentum 6M brut'     },
  { id: 'upside_mean_pct',       label: '🎯 Upside moyen'         },
  { id: 'forward_pe_median',     label: '📉 Fwd P/E (cheap first)' },
  { id: 'count',                 label: '🔢 Nb de tickers'        },
];

const TICKER_COLUMNS = [
  { key: 'ticker',              label: 'Ticker',      dir: 'asc',  numeric: false },
  { key: 'industry',            label: 'Industrie',   dir: 'asc',  numeric: false },
  { key: 'current_price',       label: 'Prix Actuel', dir: 'desc', numeric: true  },
  { key: 'forward_pe',          label: 'Fwd P/E',     dir: 'asc',  numeric: true  },
  { key: 'recommendation_mean', label: 'Reco',        dir: 'asc',  numeric: true  },
  { key: 'price_target_mean',   label: 'Target',      dir: 'desc', numeric: true  },
  { key: 'upside_pct',          label: 'Upside',      dir: 'desc', numeric: true  },
  { key: 'momentum_risk_adjusted', label: 'Mom RA', title: 'Momentum 6M Risk-Adjusted (≈ Sharpe 6M)',
    dir: 'desc', numeric: true, momentum: true },
  { key: 'quality_score',       label: 'Q',  title: 'Quality — ROE + Operating Margin',
    dir: 'desc', numeric: true, factor: true },
  { key: 'value_score',         label: 'V',  title: 'Value — EV/EBITDA (fallback Fwd P/E) + FCF Yield',
    dir: 'desc', numeric: true, factor: true },
  { key: 'risk_score',          label: 'R',  title: 'Risk — Debt/Equity + Current Ratio (plus haut = plus sûr)',
    dir: 'desc', numeric: true, factor: true },
  { key: 'sentiment_score',     label: 'S',  title: 'Sentiment — Reco analystes + Upside target',
    dir: 'desc', numeric: true, factor: true },
];

export default function SectorsPage() {
  const qc = useQueryClient();
  const [selected, setSelected]       = useState(null);   // sector name
  const [refreshingMom, setRefresh]   = useState(false);
  const [sortBy, setSortBy]           = useState('titan_composite_score');

  const sectorsQuery = useSectors({}, { refetchInterval: POLL_MS });
  const payload = sectorsQuery.data || null;
  const loading = sectorsQuery.isLoading;
  const error   = sectorsQuery.isError
    ? (sectorsQuery.error?.status === 0
        ? 'API injoignable. Vérifie que le backend tourne sur :8000.'
        : `Backend error : ${sectorsQuery.error?.message || 'erreur inconnue'}`)
    : null;

  const sectorQuery = useSector(selected);
  const detail = sectorQuery.isError ? null : (sectorQuery.data || null);
  const detailLoading = sectorQuery.isFetching && !detail;

  const refreshMomentum = async () => {
    setRefresh(true);
    try {
      await qc.fetchQuery({
        queryKey: ['sectors', true],
        queryFn: () => import('../api/client').then(m => m.fetchSectors({ refreshMomentum: true })),
      });
      qc.invalidateQueries({ queryKey: ['sectors'] });
    } finally {
      setRefresh(false);
    }
  };

  const sectors = useMemo(() => {
    if (!payload?.sectors) return [];
    return Object.values(payload.sectors);
  }, [payload]);

  const sortedSectors = useMemo(() => {
    const list = [...sectors];
    list.sort((a, b) => {
      switch (sortBy) {
        case 'forward_pe_median':
          return (a.forward_pe_median ?? 1e9) - (b.forward_pe_median ?? 1e9);
        case 'count':
          return (b.count ?? 0) - (a.count ?? 0);
        default: {
          const key = sortBy;
          return (b[key] ?? -Infinity) - (a[key] ?? -Infinity);
        }
      }
    });
    return list;
  }, [sectors, sortBy]);

  const handleRefreshMomentum = () => refreshMomentum();

  if (loading) {
    return (
      <div className="loading-pulse">
        <div className="spinner" />
        <p>Chargement du dashboard sectoriel…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="api-error">
        <div className="api-error-icon">⚠️</div>
        <h3>Dashboard indisponible</h3>
        <p>{error}</p>
      </div>
    );
  }

  const freshDays   = payload?.freshness_days;
  const momDate     = payload?.momentum_updated_at;
  const momHit      = payload?.momentum_cache_hit;
  const titanW      = payload?.weights?.titan || {};

  return (
    <div className="family-page animate-fade-in">
      {/* ── Stats bar ── */}
      <div className="family-stats-bar">
        <div className="scan-chip" style={{ borderColor: 'var(--accent-primary)', color: 'var(--accent-primary)' }}>
          🏛 <strong>{sectors.length}</strong> secteurs GICS
        </div>
        <div className="scan-chip" style={{ borderColor: 'var(--success)', color: 'var(--success)' }}>
          🎯 <strong>{payload?.universe_count ?? 0}</strong> tickers univers
        </div>
        <div className="scan-chip" style={{ borderColor: 'var(--text-muted)', color: 'var(--text-muted)' }}>
          ⏱ Rebuild {freshDays != null ? `il y a ${freshDays < 1 ? '< 1 j' : `${Math.floor(freshDays)} j`}` : '—'}
        </div>
        <div className="scan-chip" style={{ borderColor: 'var(--text-muted)', color: 'var(--text-muted)' }}>
          🚀 Momentum : {momDate ? formatTs(momDate) : '—'}
          {momHit ? ' (cache)' : ' (live)'}
        </div>
        <button
          className="scan-filter-btn"
          onClick={handleRefreshMomentum}
          disabled={refreshingMom}
          title="Re-download batch yfinance des 11 ETF sectoriels"
        >
          {refreshingMom ? <><div className="spinner" style={{ width: 12, height: 12, borderWidth: 2 }} /> Refresh…</> : '🔄 Refresh momentum'}
        </button>
      </div>

      {/* ── Formule TITAN explicitée ── */}
      <div
        className="card"
        style={{
          padding: '0.6rem 0.9rem', fontSize: '0.78rem', color: 'var(--text-muted)',
          display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap',
        }}
      >
        <span>
          📐 <b>TITAN Composite</b> =
          <b style={{ color: 'var(--accent-primary)' }}> {titanW.quality ?? 0.30}</b> × Quality +
          <b style={{ color: 'var(--accent-primary)' }}> {titanW.value ?? 0.25}</b> × Value +
          <b style={{ color: 'var(--accent-primary)' }}> {titanW.risk ?? 0.20}</b> × Risk +
          <b style={{ color: 'var(--accent-primary)' }}> {titanW.sentiment ?? 0.25}</b> × Sentiment
        </span>
        <span style={{ opacity: 0.7 }}>
          · Sous-scores 0-100 (percentile-rank cross-universe) · Agrégat sectoriel pondéré market-cap · Momentum 6M <b>Risk-Adjusted</b> (≈ rendement / σ annualisée) affiché séparément.
        </span>
      </div>

      <PresetBar
        scope="sectors"
        label="Vues secteurs"
        current={{ sortBy, selected }}
        onApply={(p) => {
          if (p?.sortBy   !== undefined) setSortBy(p.sortBy);
          if (p?.selected !== undefined) setSelected(p.selected);
        }}
      />

      {/* ── Layout 2 cols ── */}
      <div className="family-layout">
        {/* LEFT — grille de cards */}
        <div className="family-grid-wrap">
          <h3 className="family-section-title">
            🏛 Secteurs GICS
            <span style={{ fontSize: '0.75rem', marginLeft: 'auto', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <label style={{ textTransform: 'none', letterSpacing: 0, color: 'var(--text-muted)' }}>Trier</label>
              <select
                value={sortBy}
                onChange={e => setSortBy(e.target.value)}
                className="scan-select"
              >
                {SORTS.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
            </span>
          </h3>

          <div className="family-grid">
            {sortedSectors.map((s, i) => (
              <SectorCard
                key={s.sector}
                sector={s}
                selected={selected === s.sector}
                rank={sortBy === 'titan_composite_score' ? i + 1 : null}
                onSelect={setSelected}
              />
            ))}
          </div>
        </div>

        {/* RIGHT — drill-down */}
        <div className="family-detail-wrap">
          {selected ? (
            <SectorDetail
              sectorName={selected}
              detail={detail}
              loading={detailLoading}
              onClose={() => setSelected(null)}
            />
          ) : (
            <div className="family-detail-empty card">
              <div style={{ fontSize: '2.5rem', marginBottom: '0.75rem' }}>🏛</div>
              <h3>Sélectionne un secteur</h3>
              <p>Clique sur une carte à gauche pour voir le classement des tickers du secteur (upside, reco, valorisation) et la répartition par sous-secteur.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SectorDetail({ sectorName, detail, loading, onClose }) {
  const [sortKey, setSortKey]         = useState('upside_pct');
  const [sortDir, setSortDir]         = useState('desc');
  const [bucketFilter, setBucketFlt]  = useState('ALL');
  const [search, setSearch]           = useState('');
  const PAGE_SIZE = 50;
  const [page, setPage] = useState(1);
  const filterKey = `${sectorName}|${sortKey}|${sortDir}|${bucketFilter}|${search}`;
  const [lastFilterKey, setLastFilterKey] = useState(filterKey);
  if (filterKey !== lastFilterKey) {
    setLastFilterKey(filterKey);
    setPage(1);
  }

  const toggleSort = (col) => {
    if (sortKey === col.key) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(col.key);
      setSortDir(col.dir);
    }
  };

  if (loading) {
    return (
      <div className="family-detail-empty card">
        <div className="spinner" />
        <p style={{ marginTop: '1rem' }}>Chargement {sectorName}…</p>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="family-detail-empty card">
        <div style={{ fontSize: '2rem' }}>⚠️</div>
        <h3>Détail indisponible</h3>
        <p>Le backend n'a pas pu livrer le drill-down pour <b>{sectorName}</b>.</p>
      </div>
    );
  }

  const stats = detail.stats || {};
  const tickers = Array.isArray(detail.tickers) ? detail.tickers : [];

  const filtered = tickers.filter(t => {
    if (bucketFilter !== 'ALL' && t.reco_bucket !== bucketFilter) return false;
    if (search) {
      const q = search.toUpperCase();
      return (
        (t.ticker || '').includes(q) ||
        (t.name || '').toUpperCase().includes(q) ||
        (t.industry || '').toUpperCase().includes(q)
      );
    }
    return true;
  });

  const col = TICKER_COLUMNS.find(c => c.key === sortKey) || TICKER_COLUMNS[0];
  const sorted = [...filtered].sort((a, b) => {
    const va = a[sortKey];
    const vb = b[sortKey];
    const aNull = va == null || (typeof va === 'number' && !isFinite(va));
    const bNull = vb == null || (typeof vb === 'number' && !isFinite(vb));
    if (aNull && bNull) return 0;
    if (aNull) return 1;
    if (bNull) return -1;
    const cmp = col.numeric
      ? (va - vb)
      : String(va).localeCompare(String(vb));
    return sortDir === 'asc' ? cmp : -cmp;
  });

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const pageClamped = Math.min(page, totalPages);
  const paginated = sorted.slice((pageClamped - 1) * PAGE_SIZE, pageClamped * PAGE_SIZE);

  return (
    <div className="family-detail">
      {/* Header */}
      <div className="fd-header card">
        <div className="fd-title">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <span className="fd-etf">{stats.etf || '—'}</span>
            <div>
              <h2>{sectorName}</h2>
              <p>
                <b>{stats.count}</b> tickers · Mkt Cap total <b>{fmtMarketCap(stats.market_cap_total)}</b>
                {stats.titan_composite_score != null && (
                  <> · TITAN <b style={{ color: factorColor(stats.titan_composite_score) }}>
                    {stats.titan_composite_score.toFixed(1)}/100
                  </b></>
                )}
              </p>
            </div>
          </div>
          <div className="fd-header-actions">
            <button className="fd-close" onClick={onClose} aria-label="Fermer le détail du secteur">✕</button>
          </div>
        </div>
      </div>

      {/* KPIs résumé */}
      <div className="card">
        <div className="card-title">📊 Agrégats sectoriels</div>
        <div className="fd-kpi-grid">
          <DKPI label="Fwd P/E médian"  value={fmtNum(stats.forward_pe_median, 2)}
                hint={`σ = ${fmtNum(stats.forward_pe_std, 2)}`} />
          <DKPI label="Reco moyenne"    value={fmtNum(stats.recommendation_mean, 2)}
                hint={`σ = ${fmtNum(stats.recommendation_std, 2)}`} />
          <DKPI label="Upside moyen"    value={fmtPct(stats.upside_mean_pct)}
                hint={`médian ${fmtPct(stats.upside_median_pct)}`} />
          <DKPI label="Momentum 6M RA"
                value={stats.momentum_risk_adjusted != null
                  ? `${stats.momentum_risk_adjusted >= 0 ? '+' : ''}${stats.momentum_risk_adjusted.toFixed(2)}`
                  : '—'}
                color={momentumTone(stats.momentum_risk_adjusted)}
                hint={`Gain ${fmtPct(stats.momentum_return_pct)} · Vol ${stats.momentum_volatility_pct != null ? stats.momentum_volatility_pct.toFixed(0) + '%' : '—'}`} />
          <DKPI label="Mkt Cap total"   value={fmtMarketCap(stats.market_cap_total)}
                hint={`médian ${fmtMarketCap(stats.market_cap_median)}`} />
          <DKPI label="# Analystes"     value={stats.num_analysts_total ?? '—'}
                hint="Somme des analystes sur tous les tickers du secteur" />
        </div>
      </div>

      {/* Sous-secteurs (industry) — ajouté UNIQUEMENT dans le drill-down */}
      {stats.subsectors && Object.keys(stats.subsectors).length > 0 && (
        <div className="card">
          <div className="card-title">🧬 Sous-secteurs (top {Object.keys(stats.subsectors).length})</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
            {Object.entries(stats.subsectors).map(([industry, n]) => (
              <div
                key={industry}
                className="scan-chip"
                style={{
                  borderColor: 'rgba(99,102,241,0.4)',
                  color: '#a5b4fc',
                  fontSize: '0.78rem',
                }}
              >
                {industry} <b style={{ marginLeft: '0.3rem' }}>{n}</b>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Distribution reco */}
      {stats.reco_dist && (
        <div className="card">
          <div className="card-title">👁 Distribution des recommandations analystes</div>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            {Object.entries(RECO_LABELS).map(([key, { label, color }]) => {
              const n = stats.reco_dist[key] || 0;
              const active = bucketFilter === key;
              return (
                <button
                  key={key}
                  className={`scan-filter-btn ${active ? 'active' : ''}`}
                  onClick={() => setBucketFlt(active ? 'ALL' : key)}
                  style={{
                    borderColor: active ? color : undefined,
                    color:       active ? color : undefined,
                  }}
                >
                  {label} <span style={{ marginLeft: '0.35rem', opacity: 0.7 }}>{n}</span>
                </button>
              );
            })}
            {bucketFilter !== 'ALL' && (
              <button className="scan-filter-btn" onClick={() => setBucketFlt('ALL')}>
                ✕ Reset
              </button>
            )}
          </div>
        </div>
      )}

      {/* Table tickers */}
      <div className="card">
        <div className="card-title" style={{ justifyContent: 'space-between', gap: '0.75rem', flexWrap: 'wrap' }}>
          <span>📈 Classement tickers — <b>{sorted.length}</b> / {tickers.length}</span>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <input
              type="search"
              placeholder="🔍 Ticker / nom / industrie…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="mini-input"
              style={{ width: 220 }}
            />
            <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
              💡 Clique sur un en-tête pour trier
            </span>
          </div>
        </div>

        <div className="scan-table-wrap">
          <table className="scan-table sortable-table">
            <thead>
              <tr>
                {TICKER_COLUMNS.map(c => {
                  const active = sortKey === c.key;
                  return (
                    <th
                      key={c.key}
                      onClick={() => toggleSort(c)}
                      className={`sortable-th ${active ? 'active' : ''} ${c.factor ? 'factor-th' : ''}`}
                      style={{
                        cursor: 'pointer',
                        userSelect: 'none',
                        color: active ? 'var(--accent-primary)' : undefined,
                        textAlign: (c.factor || c.momentum) ? 'center' : undefined,
                        width: c.factor ? 44 : c.momentum ? 60 : undefined,
                        padding: (c.factor || c.momentum) ? '0.35rem 0.25rem' : undefined,
                      }}
                      title={c.title ? `${c.title} — clic pour trier` : `Trier par ${c.label}`}
                    >
                      {c.label}
                      <span style={{ marginLeft: 3, opacity: active ? 1 : 0.25, fontSize: '0.75em' }}>
                        {active ? (sortDir === 'asc' ? '▲' : '▼') : '↕'}
                      </span>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {paginated.map(t => {
                const lbl = RECO_LABELS[t.reco_bucket];
                return (
                  <tr key={t.ticker} className="scan-row" data-ticker={t.ticker}>
                    <td>
                      <div className="scan-ticker-cell">
                        <span className="scan-ticker-logo">📈</span>
                        <div>
                          <strong>{t.ticker}</strong>
                          <small>{(t.name || t.ticker).slice(0, 30)}</small>
                        </div>
                      </div>
                    </td>
                    <td>
                      <small style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>
                        {(t.industry || '—').slice(0, 28)}
                      </small>
                    </td>
                    <td style={{ fontFamily: 'monospace' }}>
                      {t.current_price != null ? `$${t.current_price.toFixed(2)}` : '—'}
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
                      {lbl ? (
                        <span
                          className="scan-signal-badge"
                          style={{
                            color: lbl.color, borderColor: lbl.color + '55',
                            background: lbl.color + '12',
                          }}
                        >
                          {lbl.label}
                        </span>
                      ) : <span style={{ color: 'var(--text-muted)' }}>—</span>}
                    </td>
                    <td style={{ fontFamily: 'monospace' }}>
                      {t.price_target_mean != null ? `$${t.price_target_mean.toFixed(2)}` : '—'}
                    </td>
                    <td style={{
                      fontFamily: 'monospace', fontWeight: 700,
                      color: t.upside_pct == null ? 'var(--text-muted)'
                           : t.upside_pct > 0 ? 'var(--success)'
                           : 'var(--danger)',
                    }}>
                      {fmtPct(t.upside_pct)}
                    </td>
                    <MomentumCell ra={t.momentum_risk_adjusted} ret={t.momentum_return_pct} />
                    <FactorCell score={t.quality_score} />
                    <FactorCell score={t.value_score} />
                    <FactorCell score={t.risk_score} />
                    <FactorCell score={t.sentiment_score} />
                  </tr>
                );
              })}
              {sorted.length === 0 && (
                <tr>
                  <td colSpan={TICKER_COLUMNS.length} style={{ textAlign: 'center', padding: '1.5rem', color: 'var(--text-muted)' }}>
                    Aucun ticker ne correspond aux filtres.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {sorted.length > PAGE_SIZE && (
          <Pagination
            page={pageClamped}
            totalPages={totalPages}
            total={sorted.length}
            pageSize={PAGE_SIZE}
            onChange={setPage}
          />
        )}
      </div>
    </div>
  );
}

const RECO_LABELS = {
  strong_buy:   { label: 'Strong Buy',    color: 'var(--success)' },
  buy:          { label: 'Buy',           color: '#6ee7b7'        },
  hold:         { label: 'Hold',          color: '#facc15'        },
  underperform: { label: 'Underperform',  color: '#fb923c'        },
  sell:         { label: 'Sell',          color: 'var(--danger)'  },
};

function momentumTone(ra) {
  if (ra == null || !isFinite(ra)) return 'var(--text-muted)';
  if (ra >= 1.0)  return 'var(--success)';
  if (ra >= 0.5)  return '#a3e635';
  if (ra >= 0)    return '#facc15';
  if (ra >= -0.5) return '#fb923c';
  return            'var(--danger)';
}

function MomentumCell({ ra, ret }) {
  const color = momentumTone(ra);
  const raTxt = (ra == null || !isFinite(ra)) ? '—'
    : `${ra >= 0 ? '+' : ''}${ra.toFixed(2)}`;
  const retTxt = (ret == null || !isFinite(ret)) ? ''
    : `${ret >= 0 ? '+' : ''}${ret.toFixed(0)}%`;
  return (
    <td style={{
      textAlign: 'center', fontFamily: 'monospace',
      width: 60, padding: '0.35rem 0.25rem', lineHeight: 1.15,
    }}>
      <div style={{ color, fontWeight: 700 }}>{raTxt}</div>
      <div style={{ color: 'var(--text-muted)', fontSize: '0.68rem' }}>{retTxt}</div>
    </td>
  );
}

function FactorCell({ score }) {
  const display = (score == null || !isFinite(score)) ? '—' : Math.round(score);
  const color = factorColor(score);
  return (
    <td style={{
      textAlign: 'center',
      fontFamily: 'monospace',
      fontWeight: 700,
      color,
      width: 44,
      padding: '0.35rem 0.25rem',
    }}>
      {display}
    </td>
  );
}

function DKPI({ label, value, hint, color }) {
  return (
    <div className="fd-kpi">
      <span className="fd-kpi-label">{label}</span>
      <span className="fd-kpi-value" style={color ? { color } : {}}>{value}</span>
      {hint && <span className="fd-kpi-hint">{hint}</span>}
    </div>
  );
}

function formatTs(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}
