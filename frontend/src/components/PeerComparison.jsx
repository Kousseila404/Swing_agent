/**
 * PeerComparison — table comparative target vs peers (sectoriels, mcap-similar).
 *
 * Inspirée Seeking Alpha "Peer Comparison" : 1 col par ticker, 1 ligne par KPI,
 * + col "Sector Median" pour situer le target. Un highlight color sur le KPI
 * du target l'aide à se voir dans la masse.
 *
 * Lazy fetch : quand le parent monte le composant. Re-fetch quand `ticker` change.
 */
import { useQuery } from '@tanstack/react-query';

import { fetchPeers } from '../api/client.js';
import { fmtMarketCap } from '../utils/format.js';
import { KPI_ROWS, toneFor } from '../utils/kpiCompare.js';

export default function PeerComparison({ ticker }) {
  // Migration vers React Query : annule le fetch précédent au changement de
  // ticker, partage le cache avec d'autres consommateurs, et supprime les
  // useEffect/setState manuels (pattern du reste du projet).
  const peersQ = useQuery({
    queryKey: ['peers', ticker, 5],
    queryFn: () => fetchPeers(ticker, 5),
    enabled: !!ticker,
    staleTime: 5 * 60_000,
  });

  if (!ticker) return null;
  if (peersQ.isLoading) return <div style={{ padding: '0.5rem 0', fontSize: '0.7rem', color: 'var(--text-muted)' }}>Chargement peers…</div>;
  if (peersQ.isError) return <div style={{ padding: '0.5rem 0', fontSize: '0.7rem', color: '#fbbf24' }}>Peers indisponibles : {peersQ.error?.message || 'erreur'}</div>;
  const data = peersQ.data;
  if (!data) return null;

  const peers = data.peers || [];
  if (!peers.length) {
    return <div style={{ padding: '0.5rem 0', fontSize: '0.7rem', color: 'var(--text-muted)' }}>
      Aucun peer comparable trouvé (secteur ou market_cap atypique).
    </div>;
  }

  const target = data.target;
  const median = data.sector_median || {};

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
    <div style={{ overflowX: 'auto' }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 600 }}>
        <thead>
          <tr>
            <th style={{ ...headerStyle, textAlign: 'left' }}>KPI</th>
            <th style={{
              ...headerStyle,
              background: 'rgba(96,165,250,0.08)',
              color: '#60a5fa',
            }}>{target.ticker}</th>
            <th style={headerStyle}>Médiane</th>
            {peers.map((p) => (
              <th key={p.ticker} style={headerStyle}>{p.ticker}</th>
            ))}
          </tr>
          <tr>
            <th style={{ ...cellStyle, textAlign: 'left', color: 'var(--text-muted)', fontSize: '0.6rem' }}>
              Market Cap
            </th>
            <th style={{ ...cellStyle, color: '#60a5fa', background: 'rgba(96,165,250,0.06)' }}>
              {fmtMarketCap(target.market_cap)}
            </th>
            <th style={{ ...cellStyle, color: 'var(--text-muted)' }}>—</th>
            {peers.map((p) => (
              <th key={p.ticker} style={{ ...cellStyle, color: 'var(--text-muted)', fontSize: '0.65rem' }}>
                {fmtMarketCap(p.market_cap)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {KPI_ROWS.map((row) => {
            const tv = target[row.key];
            const mv = median[row.key];
            return (
              <tr key={row.key}>
                <td style={{ ...cellStyle, textAlign: 'left', fontWeight: 600 }}>{row.label}</td>
                <td style={{
                  ...cellStyle,
                  background: 'rgba(96,165,250,0.06)',
                  color: toneFor(tv, mv, row.goodHigh, row.ignoreNegative) || '#60a5fa',
                  fontWeight: 700,
                }}>
                  {row.fmt(tv)}
                </td>
                <td style={{ ...cellStyle, color: 'var(--text-muted)' }}>{row.fmt(mv)}</td>
                {peers.map((p) => (
                  <td key={p.ticker} style={{
                    ...cellStyle,
                    color: toneFor(p[row.key], mv, row.goodHigh, row.ignoreNegative),
                  }}>
                    {row.fmt(p[row.key])}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: 4, textAlign: 'right' }}>
        Peers même secteur, market cap dans [0.3×, 3×]. Industrie identique = priorité.
      </div>
    </div>
  );
}
