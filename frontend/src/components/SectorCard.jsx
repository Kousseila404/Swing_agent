// SectorCard — macro-view d'un secteur GICS.
// Props : sector (objet /api/sectors.sectors[name]), selected, rank, onSelect.

import { factorColor } from '../utils/colors';
import { fmtNum, fmtSignedPct as fmtPct, fmtMarketCap } from '../utils/format';

function scoreTone(score) {
  if (score == null) return { bg: 'rgba(148,163,184,0.12)', fg: 'var(--text-muted)', border: 'rgba(148,163,184,0.3)' };
  if (score >= 65)   return { bg: 'rgba(34,197,94,0.14)',  fg: 'var(--success)',    border: 'rgba(34,197,94,0.5)' };
  if (score >= 50)   return { bg: 'rgba(132,204,22,0.14)', fg: '#a3e635',           border: 'rgba(132,204,22,0.45)' };
  if (score >= 35)   return { bg: 'rgba(250,204,21,0.14)', fg: '#facc15',           border: 'rgba(250,204,21,0.45)' };
  if (score >= 20)   return { bg: 'rgba(251,146,60,0.14)', fg: '#fb923c',           border: 'rgba(251,146,60,0.45)' };
  return             { bg: 'rgba(239,68,68,0.14)',   fg: 'var(--danger)',     border: 'rgba(239,68,68,0.45)' };
}

// Ratio RA = rendement 6M / volatilité annualisée ≈ Sharpe 6M (hors Rf).
function momentumTone(ra) {
  if (ra == null) return 'var(--text-muted)';
  if (ra >= 1.0)  return 'var(--success)';
  if (ra >= 0.5)  return '#a3e635';
  if (ra >= 0)    return '#facc15';
  if (ra >= -0.5) return '#fb923c';
  return            'var(--danger)';
}

const fmtRA = (v) => {
  if (v == null || !isFinite(v)) return '—';
  const s = Number(v).toFixed(2);
  return v >= 0 ? `+${s}` : s;
};

function recoTone(reco) {
  if (reco == null) return 'var(--text-muted)';
  if (reco < 2)   return 'var(--success)';
  if (reco < 2.5) return '#a3e635';
  if (reco < 3)   return '#facc15';
  return            'var(--danger)';
}

function peTone(pe) {
  if (pe == null) return 'var(--text-muted)';
  if (pe < 15)  return 'var(--success)';
  if (pe < 22)  return 'inherit';
  if (pe < 30)  return '#facc15';
  return          'var(--danger)';
}

export default function SectorCard({ sector, selected, rank, onSelect }) {
  const score = sector.titan_composite_score;
  const tone  = scoreTone(score);
  const momRA   = sector.momentum_risk_adjusted;
  const momRet  = sector.momentum_return_pct ?? sector.momentum_6m_pct;  // legacy alias
  const momVol  = sector.momentum_volatility_pct;
  const momColor = momentumTone(momRA);
  const reco = sector.recommendation_mean;
  const fpe  = sector.forward_pe_median;
  const q = sector.quality_score_mean;
  const v = sector.value_score_mean;
  const r = sector.risk_score_mean;
  const s = sector.sentiment_score_mean;

  return (
    <div
      className={`family-card ${selected ? 'selected' : ''}`}
      onClick={() => onSelect?.(sector.sector)}
      role="button"
      tabIndex={0}
      style={{ borderColor: selected ? 'var(--accent-primary)' : tone.border }}
    >
      <div className="fc-header">
        <div className="fc-title">
          <span className="fc-etf">
            {rank != null && <span style={{ marginRight: '0.4rem', opacity: 0.6 }}>#{rank}</span>}
            {sector.sector}
          </span>
          <span className="fc-name">
            {sector.etf && <>ETF proxy · <b>{sector.etf}</b> · </>}
            {sector.count} tickers
          </span>
        </div>
        <div
          className="fc-badge"
          style={{
            background: tone.bg,
            color:      tone.fg,
            border:     `1px solid ${tone.border}`,
          }}
          title="TITAN Composite = 0.30·Quality + 0.25·Value + 0.20·Risk + 0.25·Sentiment"
        >
          TITAN {score != null ? score.toFixed(1) : '—'}
        </div>
      </div>

      <div
        className="fc-score-bar"
        title="TITAN Composite Score — moyenne pondérée market-cap des 4 piliers Smart-Beta"
      >
        <div
          className="fc-score-fill"
          style={{
            width: `${Math.min(100, Math.max(0, score ?? 0))}%`,
            background: tone.fg,
          }}
        />
        <span className="fc-score-label" style={{ color: tone.fg }}>
          TITAN Composite {score != null ? `${score.toFixed(1)} / 100` : '—'}
        </span>
      </div>

      <div
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          fontSize: '0.74rem', fontFamily: 'monospace',
          padding: '0.25rem 0.1rem', letterSpacing: 0.2,
        }}
        title="Sous-scores TITAN (0-100) — Quality · Value · Risk · Sentiment"
      >
        <PillarChip letter="Q" score={q} title="Quality — ROE + Operating Margin" />
        <Sep />
        <PillarChip letter="V" score={v} title="Value — EV/EBITDA (fallback Fwd P/E) + FCF Yield" />
        <Sep />
        <PillarChip letter="R" score={r} title="Risk — Debt/Equity + Current Ratio (plus haut = plus sûr)" />
        <Sep />
        <PillarChip letter="S" score={s} title="Sentiment — Reco analystes + Upside target" />
      </div>

      <div
        style={{
          display: 'flex', flexDirection: 'column', gap: 2,
          padding: '0.4rem 0.6rem',
          borderRadius: 8,
          background: 'rgba(0,0,0,0.25)',
          border: `1px solid ${tone.border}`,
        }}
        title="Momentum Risk-Adjusted ≈ Rendement 6M / Volatilité annualisée (approx Sharpe 6M, hors taux sans risque)"
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>
            Momentum 6M <span style={{ opacity: 0.6 }}>RA</span>
          </span>
          <span style={{ fontSize: '1.25rem', fontWeight: 800, fontFamily: 'monospace', color: momColor }}>
            {fmtRA(momRA)}
          </span>
        </div>
        <div style={{
          display: 'flex', justifyContent: 'space-between',
          fontSize: '0.68rem', fontFamily: 'monospace', color: 'var(--text-muted)',
        }}>
          <span>Gain {fmtPct(momRet, 1)}</span>
          <span>Vol {momVol != null ? `${momVol.toFixed(0)}%` : '—'}</span>
        </div>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: '0.5rem',
          fontSize: '0.78rem',
        }}
      >
        <MiniKPI label="Fwd P/E"  value={fmtNum(fpe, 1)} color={peTone(fpe)} />
        <MiniKPI label="Reco"     value={fmtNum(reco, 2)} color={recoTone(reco)} />
        <MiniKPI label="Upside"   value={fmtPct(sector.upside_mean_pct, 1)}
                 color={sector.upside_mean_pct >= 0 ? 'var(--success)' : 'var(--danger)'} />
        <MiniKPI label="Mkt Cap"  value={fmtMarketCap(sector.market_cap_total)} />
      </div>

      {sector.reco_dist && (
        <RecoDistBar dist={sector.reco_dist} count={sector.count} />
      )}
    </div>
  );
}

function PillarChip({ letter, score, title }) {
  const color = factorColor(score);
  const display = (score == null || !isFinite(score)) ? '—' : Math.round(score);
  return (
    <span title={title} style={{ display: 'inline-flex', gap: 4, alignItems: 'baseline' }}>
      <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>{letter}:</span>
      <span style={{ color, fontWeight: 700 }}>{display}</span>
    </span>
  );
}

function Sep() {
  return <span style={{ color: 'rgba(148,163,184,0.35)' }}>|</span>;
}

function MiniKPI({ label, value, color }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>
        {label}
      </span>
      <span style={{ fontFamily: 'monospace', fontWeight: 700, color: color || 'inherit' }}>
        {value}
      </span>
    </div>
  );
}

function RecoDistBar({ dist, count }) {
  const segments = [
    { key: 'strong_buy',   color: 'var(--success)', label: 'SB' },
    { key: 'buy',          color: '#6ee7b7',        label: 'B'  },
    { key: 'hold',         color: '#facc15',        label: 'H'  },
    { key: 'underperform', color: '#fb923c',        label: 'U'  },
    { key: 'sell',         color: 'var(--danger)',  label: 'S'  },
  ];
  const total = Math.max(1, count);
  return (
    <div
      style={{
        display: 'flex', height: 6, width: '100%', borderRadius: 3, overflow: 'hidden',
        background: 'rgba(255,255,255,0.05)',
      }}
      title={segments.map(s => `${s.label}=${dist[s.key] || 0}`).join('  ')}
    >
      {segments.map(s => {
        const n = dist[s.key] || 0;
        const pct = (n / total) * 100;
        if (pct <= 0) return null;
        return (
          <div key={s.key} style={{ width: `${pct}%`, background: s.color }} />
        );
      })}
    </div>
  );
}
