// TickerSpark — sparkline auto-fetchée pour un ticker.
//
// Wrapper léger autour de Sparkline + useTickerHistory (champ current_price
// uniquement pour minimiser la charge réseau). Cache RQ par ticker partagé
// entre tous les composants qui en ont besoin.

import { useMemo } from 'react';
import { useTickerHistory } from '../../hooks/useApi';
import Sparkline from './Sparkline';

export default function TickerSpark({ ticker, width = 80, height = 22 }) {
  const histQ = useTickerHistory(ticker, { fields: 'current_price' });

  const series = useMemo(() => {
    const rows = histQ.data?.history || [];
    return rows
      .map(r => r.current_price)
      .filter(v => Number.isFinite(v));
  }, [histQ.data]);

  if (histQ.isLoading) {
    return (
      <span style={{
        display: 'inline-block', width, height,
        opacity: 0.4, color: 'var(--text-muted)',
        fontSize: '0.6rem', fontFamily: 'monospace',
        textAlign: 'center', lineHeight: `${height}px`,
      }}>·····</span>
    );
  }

  return <Sparkline data={series} width={width} height={height} />;
}
