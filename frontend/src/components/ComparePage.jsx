// ComparePage — comparaison side-by-side de tickers choisis par l'utilisateur.
//
// Distinct de PeerComparison (auto-sélection sector + market_cap proche
// autour d'un seul target, affiché dans TickerAnalysisModal) : ici
// l'utilisateur choisit lui-même la liste, sans contrainte sectorielle —
// utile pour comparer des tickers de sa watchlist entre eux, ou face à un
// concurrent hors périmètre "peer" classique. Consomme GET /api/compare.

import { useState } from 'react';

import { useCompare } from '../hooks/useApi';
import { fmtMarketCap } from '../utils/format';
import { KPI_ROWS, toneFor } from '../utils/kpiCompare';
import ApiErrorBanner from './common/ApiErrorBanner';

const MAX_TICKERS = 8;

function parseTickers(input) {
  return input
    .split(/[,\s]+/)
    .map(t => t.trim().toUpperCase())
    .filter(Boolean);
}

export default function ComparePage() {
  const [input, setInput] = useState('');
  const [tickers, setTickers] = useState([]);

  const compareQ = useCompare(tickers);

  const handleSubmit = (e) => {
    e.preventDefault();
    const parsed = [...new Set(parseTickers(input))].slice(0, MAX_TICKERS);
    setTickers(parsed);
  };

  const data = compareQ.data;
  const rows = data?.rows || [];
  const missing = data?.missing || [];
  const median = data?.median || {};

  const cellStyle = {
    padding: '0.35rem 0.5rem', fontSize: '0.72rem',
    fontFamily: 'monospace', textAlign: 'right', whiteSpace: 'nowrap',
  };
  const headerStyle = {
    padding: '0.35rem 0.5rem', fontSize: '0.65rem', fontWeight: 700,
    color: 'var(--text-muted)', textTransform: 'uppercase',
    letterSpacing: '0.05em', textAlign: 'right',
    borderBottom: '1px solid var(--border)',
  };

  return (
    <div className="page-content">
      {/* ── Ticker picker ── */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <form onSubmit={handleSubmit}
              style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <h2 style={{ margin: 0, fontSize: '1.05rem' }}>⚖️ Comparer des tickers</h2>
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder="AAPL, MSFT, NVDA…"
            aria-label="Tickers à comparer (séparés par virgule ou espace)"
            style={{
              padding: '6px 12px', borderRadius: 6,
              background: 'rgba(255,255,255,0.04)',
              border: '1px solid var(--border)', color: 'var(--text)',
              fontSize: '0.9rem', textTransform: 'uppercase',
              fontFamily: 'monospace', minWidth: 220, flex: '1 1 220px',
            }}
          />
          <button className="action-btn" type="submit"
                  aria-label="Comparer les tickers saisis">
            Comparer
          </button>
          <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
            2 à {MAX_TICKERS} tickers, séparés par virgule ou espace.
          </span>
        </form>
      </div>

      {tickers.length > 0 && tickers.length < 2 && (
        <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
          Au moins 2 tickers sont nécessaires pour une comparaison.
        </div>
      )}

      {compareQ.isError && (
        <ApiErrorBanner msg={compareQ.error?.message || 'Erreur chargement comparaison'}
                        onRetry={() => compareQ.refetch()} />
      )}

      {compareQ.isLoading && tickers.length >= 2 && (
        <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
          Chargement…
        </div>
      )}

      {missing.length > 0 && (
        <div className="card" style={{ padding: '10px 16px', marginBottom: 16, color: '#fbbf24', fontSize: '0.8rem' }}>
          Introuvable dans l'univers scoré : {missing.join(', ')}
        </div>
      )}

      {rows.length >= 2 && (
        <div className="card" style={{ padding: 16, overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 600 }}>
            <thead>
              <tr>
                <th style={{ ...headerStyle, textAlign: 'left' }}>KPI</th>
                {rows.map(r => (
                  <th key={r.ticker} style={headerStyle}>{r.ticker}</th>
                ))}
                <th style={headerStyle}>Médiane</th>
              </tr>
              <tr>
                <th style={{ ...cellStyle, textAlign: 'left', color: 'var(--text-muted)', fontSize: '0.6rem' }}>
                  Market Cap
                </th>
                {rows.map(r => (
                  <th key={r.ticker} style={{ ...cellStyle, color: 'var(--text-muted)', fontSize: '0.65rem' }}>
                    {fmtMarketCap(r.market_cap)}
                  </th>
                ))}
                <th style={{ ...cellStyle, color: 'var(--text-muted)' }}>—</th>
              </tr>
            </thead>
            <tbody>
              {KPI_ROWS.map(row => {
                const mv = median[row.key];
                return (
                  <tr key={row.key}>
                    <td style={{ ...cellStyle, textAlign: 'left', fontWeight: 600 }}>{row.label}</td>
                    {rows.map(r => (
                      <td key={r.ticker} style={{
                        ...cellStyle,
                        color: toneFor(r[row.key], mv, row.goodHigh, row.ignoreNegative),
                      }}>
                        {row.fmt(r[row.key])}
                      </td>
                    ))}
                    <td style={{ ...cellStyle, color: 'var(--text-muted)' }}>{row.fmt(mv)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {tickers.length === 0 && (
        <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
          Saisis 2 à {MAX_TICKERS} tickers pour les comparer côte à côte (fondamentaux + score TITAN).
        </div>
      )}
    </div>
  );
}
