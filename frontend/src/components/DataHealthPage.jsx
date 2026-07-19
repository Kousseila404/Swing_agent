// DataHealthPage — page dédiée au monitoring santé des providers data.
//
// Sections :
//   1. Severity globale + age données + n_tickers
//   2. YF circuit-breaker (état + cooldown)
//   3. Universe fields_missing (table % manquant par champ)
//   4. Distribution fetched_at (median/oldest, n_stale, n_severe)
//   5. Sources providers (qui a fetché quoi)
//   6. Cache fundamentals (n_cached, ages)

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { refreshFlaggedTickers } from '../api/client';
import { useDataHealth } from '../hooks/useApi';
import { fmtNum, fmtPctRaw } from '../utils/format';

const SEV_COLOR = {
  ok:       'var(--success)',
  warning:  'var(--warning, #fbbf24)',
  critical: 'var(--danger)',
};

function SeverityBadge({ sev }) {
  const color = SEV_COLOR[sev] || SEV_COLOR.ok;
  return (
    <span style={{
      background: `${color}25`, color,
      padding: '2px 10px', borderRadius: 6,
      fontSize: '0.72rem', fontWeight: 700,
      textTransform: 'uppercase', letterSpacing: '1px',
    }}>{sev}</span>
  );
}

function Card({ title, children }) {
  return (
    <div className="card" style={{ padding: 16, marginBottom: 16 }}>
      <h3 style={{ margin: '0 0 12px 0', fontSize: '0.95rem' }}>{title}</h3>
      {children}
    </div>
  );
}

function Row({ label, value, mono = false, color }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between',
                  padding: '4px 0', fontSize: '0.85rem',
                  borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ color: color || 'var(--text)',
                      fontFamily: mono ? 'monospace' : 'inherit',
                      fontWeight: 600 }}>{value}</span>
    </div>
  );
}

export default function DataHealthPage() {
  const { data, isLoading, isError, error, refetch } = useDataHealth();
  const qc = useQueryClient();
  const [lastResult, setLastResult] = useState(null);

  const refreshMut = useMutation({
    mutationFn: refreshFlaggedTickers,
    onSuccess: (res) => {
      setLastResult(res);
      qc.invalidateQueries({ queryKey: ['data_health'] });
    },
    onError: (err) => {
      setLastResult({ ok: false, error: err?.message || 'unknown' });
    },
  });

  if (isLoading) {
    return <div style={{ padding: 24 }}><div className="spinner" /></div>;
  }
  if (isError) {
    return (
      <div className="card" style={{ padding: 16 }}>
        Erreur chargement data_health : {error?.message || 'inconnue'}
        <button onClick={refetch} className="action-btn" style={{ marginLeft: 12 }}>
          Réessayer
        </button>
      </div>
    );
  }
  if (!data) return null;

  const yf = data.yf_breaker || {};
  const inv = data.universe || {};
  const cache = data.fundamentals_cache || {};
  const fmp = data.fmp || {};
  const sanitize = data.sanitize || {};
  const fields = inv.fields_missing || {};
  const fa = inv.fetched_at || {};
  const nFlagged = sanitize.n_tickers_flagged || 0;
  const providers = data.providers || {};

  return (
    <div className="page-content">
      {/* ── Severity globale ── */}
      <Card title="🩺 Severity globale">
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <SeverityBadge sev={data.severity_global} />
          <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            Generated at {data.generated_at} ·
            {' '}{inv.n_tickers ?? '?'} tickers ·
            {' '}universe updated {inv.universe_updated_at || '?'}
          </span>
        </div>
      </Card>

      {/* ── YF Breaker ── */}
      <Card title="⚡ yfinance circuit-breaker">
        <Row label="État" value={yf.tripped ? '🔴 OPEN (tripped)' : '🟢 CLOSED'}
             color={yf.tripped ? 'var(--danger)' : 'var(--success)'} />
        {yf.tripped && (
          <>
            <Row label="Tripped depuis"
                 value={`${yf.tripped_age_sec ?? '?'} s (${Math.round((yf.tripped_age_sec || 0) / 60)} min)`}
                 mono />
            <Row label="Auto-reset dans"
                 value={`${yf.auto_reset_in_sec ?? '?'} s (${Math.round((yf.auto_reset_in_sec || 0) / 60)} min)`}
                 mono color="var(--warning, #fbbf24)" />
            <Row label="Raison" value={yf.reason || '?'} mono />
          </>
        )}
        <Row label="Cooldown configuré" value={`${yf.cooldown_seconds ?? '?'} s`} mono />
      </Card>

      {/* ── Universe fields_missing ── */}
      <Card title="📊 Universe — % de tickers avec champ manquant">
        {!inv.loaded && (
          <p style={{ color: 'var(--danger)' }}>
            Universe non chargeable : {inv.reason || 'inconnu'}
          </p>
        )}
        {inv.loaded && (
          <table style={{ width: '100%', fontSize: '0.85rem', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)',
                            color: 'var(--text-muted)', fontSize: '0.75rem' }}>
                <th style={{ textAlign: 'left',  padding: '6px 4px' }}>Champ</th>
                <th style={{ textAlign: 'right', padding: '6px 4px' }}>Missing</th>
                <th style={{ textAlign: 'right', padding: '6px 4px' }}>%</th>
                <th style={{ textAlign: 'right', padding: '6px 4px' }}>Sev</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(fields)
                .sort((a, b) => b[1].missing_pct - a[1].missing_pct)
                .map(([f, v]) => (
                  <tr key={f} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                    <td style={{ padding: '4px 4px', fontFamily: 'monospace' }}>{f}</td>
                    <td style={{ padding: '4px 4px', textAlign: 'right',
                                  fontFamily: 'monospace' }}>{v.missing}</td>
                    <td style={{ padding: '4px 4px', textAlign: 'right',
                                  fontFamily: 'monospace',
                                  color: SEV_COLOR[v.severity] }}>
                      {fmtPctRaw(v.missing_pct, 1)}
                    </td>
                    <td style={{ padding: '4px 4px', textAlign: 'right' }}>
                      <SeverityBadge sev={v.severity} />
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* ── Fetched_at distribution ── */}
      {inv.loaded && (
        <Card title="🕒 Universe — distribution fetched_at">
          <Row label="Median age" value={`${fmtNum(fa.median_age_days, 2)} jours`} mono />
          <Row label="Oldest age" value={`${fmtNum(fa.oldest_age_days, 2)} jours`} mono />
          <Row label={`N stale (≥ ${fa.stale_threshold_days}j)`}
               value={fa.n_stale} mono
               color={fa.n_stale > 0 ? 'var(--warning, #fbbf24)' : 'var(--text)'} />
          <Row label={`N severe (≥ ${fa.severe_threshold_days}j)`}
               value={fa.n_severe} mono
               color={fa.n_severe > 0 ? 'var(--danger)' : 'var(--text)'} />
        </Card>
      )}

      {/* ── Sources ── */}
      {inv.loaded && (
        <Card title="🏷 Sources providers (qui a fetché)">
          <table style={{ width: '100%', fontSize: '0.85rem' }}>
            <tbody>
              {Object.entries(inv.sources || {})
                .sort((a, b) => b[1] - a[1])
                .map(([src, n]) => (
                  <tr key={src} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                    <td style={{ padding: '4px 4px', fontFamily: 'monospace',
                                  color: src.includes('stale') ? 'var(--warning, #fbbf24)' : 'var(--text)' }}>
                      {src}
                    </td>
                    <td style={{ padding: '4px 4px', textAlign: 'right',
                                  fontFamily: 'monospace' }}>{n}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </Card>
      )}

      {/* ── Sanitize flags (audit 2026-04-23) ── */}
      <Card title="🧹 Qualité données — valeurs aberrantes neutralisées">
        <Row
          label="Tickers avec au moins un flag"
          value={nFlagged}
          mono
          color={nFlagged > 50 ? 'var(--warning, #fbbf24)'
                  : nFlagged > 0 ? 'var(--text)'
                  : 'var(--success)'}
        />
        {nFlagged > 0 && sanitize.flags && (
          <>
            <div style={{ marginTop: 8, fontSize: '0.75rem',
                          color: 'var(--text-muted)' }}>
              Raisons :
            </div>
            <table style={{ width: '100%', fontSize: '0.82rem', marginTop: 4 }}>
              <tbody>
                {Object.entries(sanitize.flags).map(([tag, n]) => (
                  <tr key={tag}>
                    <td style={{ padding: '3px 4px', fontFamily: 'monospace' }}>{tag}</td>
                    <td style={{ padding: '3px 4px', textAlign: 'right',
                                  fontFamily: 'monospace' }}>{n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        <div style={{ marginTop: 12, display: 'flex', gap: 10,
                      alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            className="action-btn"
            disabled={refreshMut.isPending || nFlagged === 0}
            onClick={() => {
              if (window.confirm(
                `Invalider ${nFlagged} entrées cache et relancer un refresh ciblé ?\n` +
                `Coût : ~${nFlagged} appels yfinance (peut déclencher le circuit-breaker).`
              )) {
                refreshMut.mutate();
              }
            }}
            title="Invalide le cache des tickers flaggés puis déclenche un refresh staggered ciblé"
          >
            {refreshMut.isPending ? (
              <><div className="spinner" style={{ width: 14, height: 14,
                                                    borderWidth: 2 }} /> Refresh en cours…</>
            ) : (
              `🔄 Rafraîchir les ${nFlagged} tickers flaggés`
            )}
          </button>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            Sinon, refresh automatique au prochain cron (06:00 UTC).
          </span>
        </div>
        {lastResult && (
          <div style={{ marginTop: 10, fontSize: '0.8rem',
                        padding: '8px 10px', borderRadius: 6,
                        background: lastResult.ok
                          ? 'rgba(34,197,94,0.1)'
                          : 'rgba(239,68,68,0.1)',
                        color: lastResult.ok ? 'var(--success)' : 'var(--danger)' }}>
            {lastResult.ok ? (
              <>
                ✓ Job <code>{lastResult.job?.id?.slice?.(0, 8) || '?'}</code> lancé,
                {' '}{lastResult.n_invalidated}/{lastResult.n_flagged} entrées invalidées.
                {lastResult.message && <> {lastResult.message}</>}
              </>
            ) : (
              <>✗ Erreur : {lastResult.error}</>
            )}
          </div>
        )}
      </Card>

      {/* ── Cache fundamentals ── */}
      <Card title="💾 Cache fundamentals (Lot 11)">
        <Row label="N tickers cachés" value={cache.n_cached ?? 0} mono />
        {cache.n_cached > 0 && (
          <>
            <Row label="Plus jeune (sec)" value={fmtNum(cache.youngest_age_sec, 0)} mono />
            <Row label="Médian (sec)" value={fmtNum(cache.median_age_sec, 0)} mono />
            <Row label="Plus vieux (sec)" value={fmtNum(cache.oldest_age_sec, 0)} mono />
          </>
        )}
      </Card>

      {/* ── Sources non-fondamentales (finnhub/insider/SEC/news) ── */}
      <Card title="🌐 Sources enrichissement (finnhub / insider / SEC / news)">
        {[
          ['finnhub', 'Finnhub (revisions/earnings)', providers.finnhub],
          ['news', 'Finnhub news', providers.news],
          ['insider', 'SEC insider (Form 4)', providers.insider],
          ['sec_filings', 'SEC filings', providers.sec_filings],
        ].map(([key, label, src]) => {
          const c = (src && src.cache) || {};
          return (
            <div key={key} style={{ marginBottom: 10 }}>
              <Row
                label={label}
                value={`${c.n_cached ?? 0} cachés${c.n_errors ? ` · ${c.n_errors} erreurs` : ''}`}
                mono
                color={c.n_errors > 0 ? 'var(--warning, #fbbf24)' : undefined}
              />
              {src && 'configured' in src && (
                <Row label="Configuré" value={src.configured ? '✓ oui' : '✗ non'} />
              )}
            </div>
          );
        })}
        <Row label="CIK map (âge)"
             value={providers.cik_map_age_sec != null
               ? `${fmtNum(providers.cik_map_age_sec / 3600, 1)} h`
               : '—'}
             mono />
      </Card>

      {/* ── FMP status ── */}
      <Card title="🔌 FMP">
        <Row label="API key configurée" value={fmp.configured ? '✓ oui' : '✗ non'} />
        <Row label="FMP_ENABLED" value={fmp.enabled ? '✓ activé' : '✗ désactivé (free tier inutile)'}
             color={fmp.enabled ? 'var(--success)' : 'var(--text-muted)'} />
        {fmp.quota_max && (
          <Row label="Quota" value={`${fmp.quota_used ?? '?'} / ${fmp.quota_max}`} mono />
        )}
        {fmp.quota_exhausted && (
          <Row label="Quota épuisée" value="🔴 oui" color="var(--danger)" />
        )}
      </Card>
    </div>
  );
}
