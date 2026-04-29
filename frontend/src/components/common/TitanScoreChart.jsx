// TitanScoreChart — historique du composite TITAN dans le modal.
// Léger (Sparkline SVG), affiche le delta sur la fenêtre.

import { useMemo } from 'react';
import { useTickerHistory } from '../../hooks/useApi';
import { fmtNum } from '../../utils/format';
import Sparkline from './Sparkline';

export default function TitanScoreChart({ ticker, width = 180, height = 36 }) {
  const histQ = useTickerHistory(ticker, { fields: 'titan_composite_score' });

  const series = useMemo(() => {
    const rows = histQ.data?.history || [];
    return rows
      .map(r => r.titan_composite_score)
      .filter(v => Number.isFinite(v));
  }, [histQ.data]);

  if (histQ.isLoading || series.length < 2) {
    return null;
  }

  const first = series[0];
  const last  = series[series.length - 1];
  const delta = last - first;
  const tone  = delta >= 0 ? 'var(--success)' : 'var(--danger)';

  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 10,
      padding: '0.5rem 0.7rem', borderRadius: 6,
      background: 'var(--bg-tertiary)', border: '1px solid var(--border)',
    }}>
      <div>
        <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)',
                      letterSpacing: '0.04em', textTransform: 'uppercase' }}>
          TITAN history ({series.length}j)
        </div>
        <div style={{ fontFamily: 'monospace', fontSize: '0.85rem', fontWeight: 700 }}>
          {fmtNum(last, 1)}
          <span style={{ marginLeft: 6, color: tone, fontSize: '0.74rem' }}>
            {delta >= 0 ? '+' : ''}{fmtNum(delta, 1)}
          </span>
        </div>
      </div>
      <Sparkline data={series} width={width} height={height} strokeWidth={1.5} />
    </div>
  );
}
