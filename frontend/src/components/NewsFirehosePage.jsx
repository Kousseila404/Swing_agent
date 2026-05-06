// NewsFirehosePage — flux consolidé des news positions OPEN + watchlist.
//
// Endpoint /api/news/portfolio/firehose. Lazy fetch, cache 10min côté UI
// (le backend cache déjà 1h par ticker). Filtres : scope, ticker.

import { useMemo, useState } from 'react';
import { useNewsFirehose } from '../hooks/useApi';
import ApiErrorBanner from './common/ApiErrorBanner';
import EmptyState from './common/EmptyState';
import { PageSkeleton } from './common/Skeleton';
import TickerAnalysisModal from './TickerAnalysisModal';

const HORIZONS = [3, 7, 14, 30];

function relTime(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    const diffMs = Date.now() - d.getTime();
    const m = Math.round(diffMs / 60_000);
    if (m < 60)  return `il y a ${m}min`;
    const h = Math.round(m / 60);
    if (h < 24)  return `il y a ${h}h`;
    const j = Math.round(h / 24);
    return `il y a ${j}j`;
  } catch { return iso; }
}

const SCOPE_META = {
  position:  { label: 'Position',  bg: 'rgba(34,197,94,0.16)',  fg: '#4ade80' },
  watchlist: { label: 'Watchlist', bg: 'rgba(168,85,247,0.16)', fg: '#c084fc' },
};

export default function NewsFirehosePage() {
  const [days, setDays] = useState(7);
  const [scopeFilter, setScopeFilter] = useState('all');
  const [tickerFilter, setTickerFilter] = useState('');
  const [openTicker, setOpenTicker] = useState(null);

  const newsQ = useNewsFirehose(days, 5);
  const data = newsQ.data || {};
  const items = data.items || [];
  const errors = data.errors || [];
  const scope = data.scope || {};

  const tickers = useMemo(
    () => [...new Set(items.map(i => i.ticker))].sort(),
    [items],
  );

  const filtered = useMemo(() => {
    return items.filter(it => {
      if (scopeFilter === 'position'  && !it.scope?.includes('position'))  return false;
      if (scopeFilter === 'watchlist' && !it.scope?.includes('watchlist')) return false;
      if (tickerFilter && it.ticker !== tickerFilter) return false;
      return true;
    });
  }, [items, scopeFilter, tickerFilter]);

  if (newsQ.isLoading) {
    // 1er chargement peut prendre plusieurs secondes (le backend rebuild
    // le cache 1h par ticker) — skeleton en attendant.
    return <PageSkeleton tiles={3} blockHeight={120} rows={6} />;
  }

  if (newsQ.isError) {
    return (
      <ApiErrorBanner
        msg={newsQ.error?.message || 'Erreur firehose'}
        onRetry={() => newsQ.refetch()}
      />
    );
  }

  return (
    <div className="control-panel animate-fade-in">
      {/* ── KPIs ── */}
      <div className="cp-status-bar">
        <div className="status-chip">
          <span className="sc-lbl">Articles</span>
          <span className="sc-val">{items.length}</span>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
            sur {data.n_tickers || 0} tickers
          </span>
        </div>
        <div className="status-chip">
          <span className="sc-lbl">Positions</span>
          <span className="sc-val" style={{ color: '#4ade80' }}>{scope.n_open || 0}</span>
        </div>
        <div className="status-chip">
          <span className="sc-lbl">Watchlist</span>
          <span className="sc-val" style={{ color: '#c084fc' }}>{scope.n_watchlist || 0}</span>
        </div>
        <div className="status-chip">
          <span className="sc-lbl">Fenêtre</span>
          <span className="sc-val">{days}j</span>
        </div>
      </div>

      {/* ── Controls ── */}
      <div className="card cp-section-card">
        <div style={{ display: 'flex', justifyContent: 'space-between',
                      alignItems: 'center', flexWrap: 'wrap', gap: 10,
                      marginBottom: '0.85rem' }}>
          <div>
            <div className="card-title" style={{ marginBottom: 0 }}>
              📰 News firehose
            </div>
            <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)',
                          marginTop: '0.25rem' }}>
              Flux consolidé positions OPEN + watchlist · cache 1h backend
            </div>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {HORIZONS.map(h => (
              <button key={h}
                      className={`scan-filter-btn ${days === h ? 'active' : ''}`}
                      onClick={() => setDays(h)}>
                {h}j
              </button>
            ))}
            <button className="scan-filter-btn" onClick={() => newsQ.refetch()}
                    disabled={newsQ.isFetching}>
              {newsQ.isFetching ? '⏳' : '🔄'}
            </button>
          </div>
        </div>

        {/* Filtres */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap',
                      alignItems: 'center', marginBottom: '0.75rem' }}>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            Scope :
          </span>
          {[
            { id: 'all',       label: 'Tout' },
            { id: 'position',  label: 'Positions' },
            { id: 'watchlist', label: 'Watchlist' },
          ].map(s => (
            <button key={s.id}
                    className={`scan-filter-btn ${scopeFilter === s.id ? 'active' : ''}`}
                    onClick={() => setScopeFilter(s.id)}
                    style={{ fontSize: '0.72rem' }}>
              {s.label}
            </button>
          ))}
          {tickers.length > 0 && (
            <>
              <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)',
                             marginLeft: 8 }}>
                Ticker :
              </span>
              <select value={tickerFilter}
                      onChange={e => setTickerFilter(e.target.value)}
                      className="scan-select"
                      style={{ minWidth: 120, fontSize: '0.82rem' }}>
                <option value="">— Tous —</option>
                {tickers.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </>
          )}
        </div>

        {/* Errors banner */}
        {errors.length > 0 && (
          <details style={{
            marginBottom: '0.85rem', fontSize: '0.78rem',
            color: 'var(--warning)',
          }}>
            <summary style={{ cursor: 'pointer' }}>
              ⚠️ {errors.length} ticker{errors.length > 1 ? 's' : ''} en erreur
            </summary>
            <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
              {errors.slice(0, 5).map((e, i) => (
                <li key={i}>{e.ticker} : {e.error}</li>
              ))}
            </ul>
          </details>
        )}

        {/* List */}
        {filtered.length === 0 ? (
          <EmptyState
            icon="📰"
            title="Aucun article"
            desc="Pas de news pour ces filtres. Élargis l'horizon, retire le filtre ticker, ou vérifie que ta watchlist + positions ouvertes ne sont pas vides."
          />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {filtered.map(a => (
              <div key={`${a.id}-${a.ticker}`}
                   style={{
                     display: 'flex', gap: 10,
                     padding: '0.7rem 0.85rem',
                     background: 'var(--bg-tertiary)',
                     borderRadius: 7,
                     border: '1px solid var(--border)',
                     transition: 'border-color 0.15s',
                   }}>
                {a.image && (
                  <img src={a.image} alt=""
                       loading="lazy"
                       onError={(e) => { e.currentTarget.style.display = 'none'; }}
                       style={{
                         width: 80, height: 60, objectFit: 'cover',
                         borderRadius: 5, flexShrink: 0,
                       }} />
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    display: 'flex', gap: 8, alignItems: 'center',
                    marginBottom: 4, flexWrap: 'wrap',
                  }}>
                    <button type="button"
                            onClick={() => setOpenTicker(a.ticker)}
                            style={{
                              background: 'none', border: 'none', padding: 0,
                              color: 'var(--accent-primary)', cursor: 'pointer',
                              fontWeight: 800, fontFamily: 'monospace',
                              fontSize: '0.85rem',
                              textDecoration: 'underline dotted',
                              textUnderlineOffset: 3,
                            }}>
                      {a.ticker}
                    </button>
                    {(a.scope || []).map(s => {
                      const m = SCOPE_META[s];
                      if (!m) return null;
                      return (
                        <span key={s} style={{
                          fontSize: '0.6rem', fontWeight: 700,
                          padding: '0.06rem 0.4rem', borderRadius: 4,
                          background: m.bg, color: m.fg,
                          textTransform: 'uppercase', letterSpacing: '0.04em',
                        }}>{m.label}</span>
                      );
                    })}
                    {a.source && (
                      <span style={{
                        fontSize: '0.66rem', fontWeight: 700,
                        color: 'var(--text-muted)',
                      }}>
                        · {a.source}
                      </span>
                    )}
                    {a.datetime && (
                      <span style={{ fontSize: '0.66rem', color: 'var(--text-muted)' }}>
                        · {relTime(a.datetime)}
                      </span>
                    )}
                  </div>
                  <a href={a.url} target="_blank" rel="noopener noreferrer"
                     style={{
                       fontSize: '0.85rem', fontWeight: 600, lineHeight: 1.35,
                       color: 'var(--text-main)', textDecoration: 'none',
                     }}
                     onMouseEnter={e => e.currentTarget.style.color = 'var(--accent-primary)'}
                     onMouseLeave={e => e.currentTarget.style.color = 'var(--text-main)'}>
                    {a.headline}
                  </a>
                  {a.summary && (
                    <div style={{
                      fontSize: '0.74rem', color: 'var(--text-muted)',
                      marginTop: 4, lineHeight: 1.4,
                      display: '-webkit-box', WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical', overflow: 'hidden',
                    }}>
                      {a.summary}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {openTicker && (
        <TickerAnalysisModal
          ticker={openTicker}
          onClose={() => setOpenTicker(null)}
        />
      )}
    </div>
  );
}
