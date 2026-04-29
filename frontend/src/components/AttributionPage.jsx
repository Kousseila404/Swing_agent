// AttributionPage — Performance Attribution v1.
//
// Pour chaque pilier TITAN (composite + Q/V/R/M/G/Piotroski + F-Score),
// affiche win rate + PnL moyen par bucket de score à l'entrée.
//
// Mode preview tant que <20 trades clos OU coverage faible. Marque les
// buckets sous-représentés (n < 5) en transparence pour signaler que
// le signal n'est pas encore fiable.

import { useMemo } from 'react';
import { useAttribution } from '../hooks/useApi';
import ApiErrorBanner from './common/ApiErrorBanner';

const TILT_PALETTE = {
  qarp:          { bg: 'rgba(34,197,94,0.18)',  fg: '#22c55e' },
  garp:          { bg: 'rgba(132,204,22,0.16)', fg: '#a3e635' },
  consistent:    { bg: 'rgba(96,165,250,0.16)', fg: '#60a5fa' },
  cheap_junk:    { bg: 'rgba(248,113,113,0.18)', fg: '#f87171' },
  falling_knife: { bg: 'rgba(248,113,113,0.18)', fg: '#fb7185' },
};

function fmtPct(v, dp = 1) {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(dp)}%`;
}

function winRateColor(wr) {
  if (wr == null) return 'var(--text-muted)';
  if (wr >= 60) return 'var(--success)';
  if (wr >= 45) return 'var(--warning)';
  return 'var(--danger)';
}

function pnlColor(p) {
  if (p == null) return 'var(--text-muted)';
  return p >= 0 ? 'var(--success)' : 'var(--danger)';
}

function BucketBar({ bucket, maxN }) {
  const widthPct = maxN > 0 ? (bucket.n / maxN) * 100 : 0;
  const opacity = bucket.stable ? 1 : 0.55;
  return (
    <div style={{ opacity }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        fontSize: '0.74rem', marginBottom: 2, alignItems: 'baseline',
      }}>
        <span style={{ fontFamily: 'monospace', color: 'var(--text-main)' }}>
          {bucket.name}
        </span>
        <span style={{ display: 'flex', gap: 12, fontFamily: 'monospace' }}>
          <span style={{ color: 'var(--text-muted)' }}>n={bucket.n}</span>
          <span style={{
            color: winRateColor(bucket.win_rate), fontWeight: 700, minWidth: 44,
            textAlign: 'right',
          }}>
            {bucket.win_rate != null ? `${bucket.win_rate.toFixed(0)}%` : '—'}
          </span>
          <span style={{
            color: pnlColor(bucket.pnl_avg_pct), minWidth: 56, textAlign: 'right',
          }}>
            {fmtPct(bucket.pnl_avg_pct, 1)}
          </span>
        </span>
      </div>
      <div style={{
        height: 6, background: 'var(--bg-tertiary)', borderRadius: 3,
        overflow: 'hidden',
      }}>
        <div style={{
          width: `${widthPct}%`, height: '100%',
          background: bucket.win_rate != null && bucket.win_rate >= 50
            ? 'linear-gradient(90deg, var(--success), #4ade80)'
            : 'linear-gradient(90deg, var(--danger), #fb7185)',
          transition: 'width 0.3s',
        }} />
      </div>
      {!bucket.stable && bucket.n > 0 && (
        <div style={{ fontSize: '0.62rem', color: 'var(--warning)', marginTop: 1 }}>
          ⚠️ &lt;5 trades — signal instable
        </div>
      )}
    </div>
  );
}

function PillarCard({ pillar }) {
  const maxN = Math.max(...pillar.buckets.map(b => b.n), 1);
  const totalN = pillar.buckets.reduce((s, b) => s + b.n, 0);
  return (
    <div className="card" style={{ padding: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'baseline', marginBottom: 10 }}>
        <h3 style={{ margin: 0, fontSize: '0.92rem' }}>{pillar.label}</h3>
        <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)',
                       fontFamily: 'monospace' }}>
          {totalN} trade{totalN > 1 ? 's' : ''} couvert{totalN > 1 ? 's' : ''}
        </span>
      </div>
      {totalN === 0 ? (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)',
                      padding: '0.7rem 0', textAlign: 'center' }}>
          📭 Aucun trade clos avec ce score capturé.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {pillar.buckets.map(b => (
            <BucketBar key={b.name} bucket={b} maxN={maxN} />
          ))}
        </div>
      )}
    </div>
  );
}

function FScoreCard({ fs }) {
  if (!fs || !fs.buckets?.length) return null;
  const maxN = Math.max(...fs.buckets.map(b => b.n), 1);
  return (
    <div className="card" style={{ padding: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'baseline', marginBottom: 10 }}>
        <h3 style={{ margin: 0, fontSize: '0.92rem' }}>F-Score Piotroski</h3>
        <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          {fs.n_total} trade{fs.n_total > 1 ? 's' : ''} avec F-Score
        </span>
      </div>
      {fs.n_total === 0 ? (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)',
                      padding: '0.7rem 0', textAlign: 'center' }}>
          📭 Aucun trade clos avec F-Score capturé.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {fs.buckets.map(b => (
            <BucketBar key={b.name} bucket={b} maxN={maxN} />
          ))}
        </div>
      )}
    </div>
  );
}

function TiltsTable({ tilts }) {
  if (!tilts?.flags?.length) return null;
  return (
    <div className="card" style={{ padding: 14 }}>
      <h3 style={{ margin: 0, marginBottom: 10, fontSize: '0.92rem' }}>
        🏷 Tilt flags · {tilts.n_total} trade{tilts.n_total > 1 ? 's' : ''}
      </h3>
      <table className="scan-table" style={{ margin: 0 }}>
        <thead>
          <tr>
            <th>Flag</th>
            <th style={{ textAlign: 'right' }}>N</th>
            <th style={{ textAlign: 'right' }}>Wins</th>
            <th style={{ textAlign: 'right' }}>Win rate</th>
            <th style={{ textAlign: 'right' }}>PnL avg</th>
          </tr>
        </thead>
        <tbody>
          {tilts.flags.map(f => {
            const palette = TILT_PALETTE[f.flag] || {
              bg: 'rgba(148,163,184,0.10)', fg: 'var(--text-muted)',
            };
            return (
              <tr key={f.flag} className="scan-row" style={{
                opacity: f.stable ? 1 : 0.55,
              }}>
                <td>
                  <span style={{
                    fontSize: '0.66rem', fontWeight: 800, padding: '0.1rem 0.45rem',
                    borderRadius: 4, background: palette.bg, color: palette.fg,
                    letterSpacing: '0.04em', textTransform: 'uppercase',
                  }}>
                    {f.flag.replace('_', ' ')}
                  </span>
                </td>
                <td style={{ textAlign: 'right', fontFamily: 'monospace' }}>{f.n}</td>
                <td style={{ textAlign: 'right', fontFamily: 'monospace' }}>{f.n_wins}</td>
                <td style={{
                  textAlign: 'right', fontFamily: 'monospace', fontWeight: 700,
                  color: winRateColor(f.win_rate),
                }}>
                  {f.win_rate != null ? `${f.win_rate.toFixed(0)}%` : '—'}
                </td>
                <td style={{
                  textAlign: 'right', fontFamily: 'monospace',
                  color: pnlColor(f.pnl_avg_pct),
                }}>
                  {fmtPct(f.pnl_avg_pct)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function AttributionPage() {
  const attrQ = useAttribution();

  const data = attrQ.data;
  const showCoverageWarning = useMemo(() => {
    if (!data || !data.ok) return false;
    return data.n_closed > 0 && (data.coverage_pct ?? 0) < 50;
  }, [data]);

  if (attrQ.isLoading) {
    return (
      <div className="loading-pulse">
        <div className="spinner" />
        <p>Calcul attribution…</p>
      </div>
    );
  }

  if (attrQ.isError || !data?.ok) {
    return (
      <ApiErrorBanner
        msg={attrQ.error?.message || data?.error || 'Erreur attribution'}
        onRetry={() => attrQ.refetch()}
      />
    );
  }

  if (data.n_closed === 0) {
    return (
      <div className="control-panel animate-fade-in">
        <div className="card" style={{ padding: '3rem', textAlign: 'center' }}>
          <div style={{ fontSize: '2rem', marginBottom: 8 }}>📊</div>
          <h2 style={{ margin: '0 0 8px' }}>Aucun trade clos</h2>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
            L'attribution corrèle les scores TITAN à l'entrée avec les outcomes.
            Reviens après quelques clôtures.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="control-panel animate-fade-in">
      {/* ── Stats globales ── */}
      <div className="cp-status-bar">
        <div className="status-chip">
          <span className="sc-lbl">Trades clos</span>
          <span className="sc-val">{data.n_closed}</span>
        </div>
        <div className="status-chip">
          <span className="sc-lbl">Couverture scores</span>
          <span className="sc-val" style={{
            color: data.coverage_pct >= 80 ? 'var(--success)'
                 : data.coverage_pct >= 30 ? 'var(--warning)'
                 : 'var(--danger)',
          }}>
            {data.coverage_pct?.toFixed?.(0) ?? '0'}%
          </span>
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
            trades avec scores capturés
          </span>
        </div>
        <div className="status-chip">
          <span className="sc-lbl">Mode</span>
          <span className="sc-val" style={{
            color: data.stable ? 'var(--success)' : 'var(--warning)',
          }}>
            {data.stable ? '✓ Stable' : '⚠ Preview'}
          </span>
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
            {data.stable ? 'Échantillon suffisant'
                         : `Stabilité dès ${data.min_for_stable} trades`}
          </span>
        </div>
      </div>

      {showCoverageWarning && (
        <div style={{
          padding: '0.75rem 1rem', borderRadius: 8, marginBottom: 12,
          background: 'rgba(251,191,36,0.10)',
          border: '1px solid rgba(251,191,36,0.4)',
          color: 'var(--warning)', fontSize: '0.82rem',
        }}>
          ⚠️ <strong>Couverture {data.coverage_pct?.toFixed(0)}%</strong> :
          la majorité des trades clos n'ont pas de scores capturés à l'entrée
          (<code>Titan_Score_Entry</code> a été introduit récemment via
          le batch <code>approve_batch</code>). Les trades plus anciens
          ne contribuent pas à l'attribution.
        </div>
      )}

      {/* ── Pilars TITAN ── */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
        gap: 12, marginBottom: 12,
      }}>
        {(data.pillars || []).map(p => (
          <PillarCard key={p.col} pillar={p} />
        ))}
        <FScoreCard fs={data.f_score} />
      </div>

      {/* ── Tilts ── */}
      <TiltsTable tilts={data.tilts} />

      <div style={{
        marginTop: 12, fontSize: '0.7rem', color: 'var(--text-muted)',
        padding: '0.8rem 1rem', textAlign: 'center',
        background: 'var(--bg-tertiary)', borderRadius: 6,
      }}>
        Lecture : un bon moteur a <strong>win rate croissant</strong> avec le
        bucket de score (les 85+ doivent battre les &lt;50). PnL avg %
        donne la magnitude du retour. Buckets &lt;5 trades en transparence
        = signal pas encore fiable statistiquement.
      </div>
    </div>
  );
}
