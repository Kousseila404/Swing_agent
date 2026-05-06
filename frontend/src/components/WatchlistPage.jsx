// WatchlistPage — observation de tickers sans engagement capital.
//
// Stockage backend DuckDB (data/watchlist.duckdb), endpoints
// /api/watchlist (GET/POST) + /api/watchlist/{ticker} (DELETE).
//
// Ouvre TickerAnalysisModal au click sur un ticker (factsheet complet
// + onglet Notes via le hook useNotes).

import { useMemo, useState } from 'react';
import {
  useAddToWatchlist,
  useRemoveFromWatchlist,
  useWatchlist,
} from '../hooks/useApi';
import ApiErrorBanner from './common/ApiErrorBanner';
import EmptyState from './common/EmptyState';
import { PageSkeleton } from './common/Skeleton';
import TickerSpark from './common/TickerSpark';
import TickerAnalysisModal from './TickerAnalysisModal';
import TitanAlertsPanel from './TitanAlertsPanel';
import PriceAlertsPanel from './PriceAlertsPanel';
import { pushToast } from '../utils/toastBus';

const TAG_PRESETS = ['LT_QARP', 'GARP', 'Momentum', 'Cyclical', 'Defensif', 'Reopening'];

export default function WatchlistPage() {
  const watchQ = useWatchlist({ refetchInterval: 60_000 });
  const addMut = useAddToWatchlist();
  const removeMut = useRemoveFromWatchlist();

  const [openTicker, setOpenTicker] = useState(null);
  const [form, setForm] = useState({
    ticker: '', tag: '', target_buy: '', comment: '',
  });
  const [error, setError] = useState(null);

  // On lit `items` à l'intérieur du useMemo pour ne pas dépendre d'une
  // référence (`||  []` crée un nouvel array à chaque render).
  const sortedItems = useMemo(() => {
    const list = watchQ.data?.items || [];
    return [...list].sort((a, b) =>
      (b.added_at || '').localeCompare(a.added_at || '')
    );
  }, [watchQ.data]);
  const items = watchQ.data?.items || [];

  const handleAdd = (ev) => {
    ev.preventDefault();
    setError(null);
    const t = (form.ticker || '').toUpperCase().trim();
    if (!t) {
      setError('Ticker requis');
      return;
    }
    const target = form.target_buy ? parseFloat(form.target_buy) : null;
    if (form.target_buy && (!Number.isFinite(target) || target <= 0)) {
      setError('Target buy invalide');
      return;
    }
    addMut.mutate({
      ticker: t,
      tag:    form.tag || null,
      target_buy: target,
      comment:    form.comment || null,
    }, {
      onSuccess: (res) => {
        if (res?.ok === false) {
          setError(res.detail || res.error || 'Erreur ajout');
          pushToast(`❌ ${t} : erreur ajout watchlist`, 'err');
          return;
        }
        setForm({ ticker: '', tag: '', target_buy: '', comment: '' });
        pushToast(`👁 ${t} ajouté à la watchlist`);
      },
    });
  };

  const handleRemove = (ticker) => {
    if (!window.confirm(`Retirer ${ticker} de la watchlist ?`)) return;
    removeMut.mutate(ticker, {
      onSuccess: (res) => {
        if (res?.ok !== false) pushToast(`🗑 ${ticker} retiré de la watchlist`);
      },
    });
  };

  if (watchQ.isLoading) {
    return <PageSkeleton tiles={3} blockHeight={180} rows={5} />;
  }

  if (watchQ.isError) {
    return (
      <ApiErrorBanner
        msg={watchQ.error?.message || 'Erreur watchlist'}
        onRetry={() => watchQ.refetch()}
      />
    );
  }

  return (
    <div className="control-panel animate-fade-in">
      {/* ── Alertes TITAN seuil utilisateur (Tier B #3) ── */}
      <TitanAlertsPanel />

      {/* ── Alertes prix multi-niveaux (entry_plan tiers) ── */}
      <PriceAlertsPanel />

      {/* ── Add form ── */}
      <div className="card" style={{ padding: 16, marginBottom: 14 }}>
        <h3 style={{ marginTop: 0, marginBottom: 12, fontSize: '0.95rem' }}>
          ➕ Ajouter à la watchlist
        </h3>
        <form onSubmit={handleAdd}
              style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'flex-end' }}>
          <div>
            <label style={{ display: 'block', fontSize: '0.7rem',
                            color: 'var(--text-muted)', marginBottom: 3 }}>
              Ticker
            </label>
            <input
              type="text"
              value={form.ticker}
              onChange={e => setForm(f => ({ ...f, ticker: e.target.value.toUpperCase() }))}
              placeholder="NVDA"
              className="mini-input"
              style={{ width: 90, fontFamily: 'monospace', textTransform: 'uppercase' }}
              autoFocus
            />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '0.7rem',
                            color: 'var(--text-muted)', marginBottom: 3 }}>
              Tag
            </label>
            <select
              value={form.tag}
              onChange={e => setForm(f => ({ ...f, tag: e.target.value }))}
              className="scan-select"
              style={{ minWidth: 130, fontSize: '0.85rem' }}
            >
              <option value="">— Aucun —</option>
              {TAG_PRESETS.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '0.7rem',
                            color: 'var(--text-muted)', marginBottom: 3 }}>
              Target buy ($)
            </label>
            <input
              type="number"
              step="0.01"
              min="0"
              value={form.target_buy}
              onChange={e => setForm(f => ({ ...f, target_buy: e.target.value }))}
              placeholder="180.00"
              className="mini-input"
              style={{ width: 100, fontFamily: 'monospace' }}
            />
          </div>
          <div style={{ flex: 1, minWidth: 220 }}>
            <label style={{ display: 'block', fontSize: '0.7rem',
                            color: 'var(--text-muted)', marginBottom: 3 }}>
              Commentaire (optionnel)
            </label>
            <input
              type="text"
              value={form.comment}
              onChange={e => setForm(f => ({ ...f, comment: e.target.value }))}
              placeholder="Pullback support 200d, attendre catalyseur earnings"
              className="mini-input"
              style={{ width: '100%' }}
            />
          </div>
          <button
            type="submit"
            className="action-btn"
            disabled={addMut.isPending}
            style={{ padding: '0.55rem 1rem' }}
          >
            {addMut.isPending ? '⏳' : '✓ Ajouter'}
          </button>
        </form>
        {error && (
          <div style={{ marginTop: 8, color: 'var(--danger)', fontSize: '0.78rem' }}>
            ⚠️ {error}
          </div>
        )}
      </div>

      {/* ── Liste ── */}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', display: 'flex',
                      justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h3 style={{ margin: 0, fontSize: '0.95rem' }}>
            👁 {items.length} ticker{items.length > 1 ? 's' : ''} surveillé{items.length > 1 ? 's' : ''}
          </h3>
          <button
            type="button"
            className="scan-filter-btn"
            onClick={() => watchQ.refetch()}
            disabled={watchQ.isFetching}
            style={{ fontSize: '0.72rem' }}
          >
            {watchQ.isFetching ? '⏳' : '🔄'} Refresh
          </button>
        </div>

        {sortedItems.length === 0 ? (
          <EmptyState
            icon="👁"
            title="Watchlist vide"
            desc="Ajoute un ticker via le formulaire ci-dessus pour commencer à le suivre — tu pourras lui attacher un tag (LT_QARP, GARP…), un target buy et des notes."
          />
        ) : (
          <table className="scan-table" style={{ margin: 0 }}>
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Tendance</th>
                <th>Tag</th>
                <th>Target buy</th>
                <th>Notes</th>
                <th>Commentaire</th>
                <th>Ajouté</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {sortedItems.map(it => (
                <tr key={it.ticker} className="scan-row" data-ticker={it.ticker}>
                  <td>
                    <button
                      type="button"
                      onClick={() => setOpenTicker(it.ticker)}
                      style={{
                        background: 'none', border: 'none', padding: 0,
                        color: 'var(--accent-primary)', cursor: 'pointer',
                        fontWeight: 700, fontFamily: 'inherit', fontSize: 'inherit',
                      }}
                    >
                      {it.ticker}
                    </button>
                  </td>
                  <td>
                    <TickerSpark ticker={it.ticker} />
                  </td>
                  <td>
                    {it.tag ? (
                      <span className="scan-signal-badge" style={{
                        color: 'var(--accent-secondary)',
                        borderColor: 'rgba(139,92,246,0.4)',
                      }}>
                        {it.tag}
                      </span>
                    ) : (
                      <span style={{ color: 'var(--text-muted)' }}>—</span>
                    )}
                  </td>
                  <td style={{ fontFamily: 'monospace' }}>
                    {Number.isFinite(it.target_buy)
                      ? `$${it.target_buy.toFixed(2)}`
                      : <span style={{ color: 'var(--text-muted)' }}>—</span>}
                  </td>
                  <td>
                    {it.n_notes > 0 ? (
                      <span style={{
                        background: 'rgba(59,130,246,0.15)',
                        color: 'var(--accent-primary)',
                        padding: '2px 8px', borderRadius: 4,
                        fontSize: '0.72rem', fontWeight: 600,
                      }}>
                        📝 {it.n_notes}
                      </span>
                    ) : (
                      <span style={{ color: 'var(--text-muted)' }}>—</span>
                    )}
                  </td>
                  <td style={{ fontSize: '0.82rem',
                               maxWidth: 320, overflow: 'hidden',
                               textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                      title={it.comment || ''}>
                    {it.comment || <span style={{ color: 'var(--text-muted)' }}>—</span>}
                  </td>
                  <td style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                    {(it.added_at || '').slice(0, 10)}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <button
                      type="button"
                      className="scan-filter-btn"
                      onClick={() => setOpenTicker(it.ticker)}
                      style={{ fontSize: '0.72rem', marginRight: 4 }}
                      title="Ouvrir factsheet"
                    >
                      🎯
                    </button>
                    <button
                      type="button"
                      className="scan-filter-btn"
                      onClick={() => handleRemove(it.ticker)}
                      style={{
                        fontSize: '0.72rem',
                        color: 'var(--danger)',
                        borderColor: 'rgba(239,68,68,0.3)',
                      }}
                      title="Retirer de la watchlist"
                    >
                      🗑
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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
