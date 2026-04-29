// PriceAlertsPanel — gestion des alertes prix multi-niveaux (entry_plan tiers).
//
// Liste les alertes existantes (target_price + direction + ticker + last_fired).
// Permet d'en supprimer. La création se fait depuis TickerAnalysisModal
// (bouton 🔔 Watch par tier d'entry_plan) ou via /api/price_alerts (POST).

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { deletePriceAlert, fetchPriceAlerts, fetchPriceAlertsStats } from '../api/client';
import { fmtNum } from '../utils/format';
import { pushToast } from '../utils/toastBus';

export default function PriceAlertsPanel() {
  const qc = useQueryClient();
  const [collapsed, setCollapsed] = useState(false);

  const alertsQ = useQuery({
    queryKey: ['price_alerts'],
    queryFn: () => fetchPriceAlerts(),
    refetchInterval: 60_000,
  });
  const statsQ = useQuery({
    queryKey: ['price_alerts_stats'],
    queryFn: fetchPriceAlertsStats,
    refetchInterval: 5 * 60_000,
  });

  const delMut = useMutation({
    mutationFn: (id) => deletePriceAlert(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['price_alerts'] });
      pushToast({ msg: '🔕 Alerte prix supprimée' });
    },
    onError: (err) => {
      pushToast({ msg: `❌ ${err?.message || 'Suppression impossible'}`, type: 'error' });
    },
  });

  const items = alertsQ.data?.items || [];
  const count = items.length;
  const triggered = items.filter(a => (a.fire_count || 0) > 0).length;
  const expired = items.filter(a => isExpired(a.expires_at)).length;

  return (
    <div className="card" style={{ marginBottom: '1rem' }}>
      <div
        className="card-title"
        onClick={() => setCollapsed(c => !c)}
        style={{ cursor: 'pointer', justifyContent: 'space-between' }}
      >
        <span>
          🎯 Alertes prix <small style={{ marginLeft: '0.5rem', color: 'var(--text-muted)', fontWeight: 400 }}>
            {count} total · {triggered} déclenchée{triggered > 1 ? 's' : ''} · {expired} expirée{expired > 1 ? 's' : ''} · cooldown 24h
          </small>
        </span>
        <span style={{ color: 'var(--text-muted)' }}>{collapsed ? '▸' : '▾'}</span>
      </div>

      {!collapsed && (
        <>
          {/* Stats banner — efficacité wait-pullback */}
          {statsQ.data && statsQ.data.n_total > 0 && (
            <div style={{
              display: 'flex', flexWrap: 'wrap', gap: '0.6rem 1.2rem',
              padding: '0.6rem 0.8rem', marginBottom: 10,
              background: 'rgba(96,165,250,0.08)',
              border: '1px solid rgba(96,165,250,0.25)',
              borderRadius: 6, fontSize: '0.78rem',
            }}>
              <Stat label="Hit rate"
                    value={statsQ.data.hit_rate_pct != null
                      ? `${fmtNum(statsQ.data.hit_rate_pct, 0)}%` : '—'}
                    tone={statsQ.data.hit_rate_pct >= 50 ? 'var(--success)'
                          : statsQ.data.hit_rate_pct >= 25 ? 'var(--warning)'
                          : 'var(--text-muted)'} />
              <Stat label="Délai médian"
                    value={statsQ.data.median_days_to_fire != null
                      ? `${fmtNum(statsQ.data.median_days_to_fire, 1)} j` : '—'} />
              <Stat label="Discount médian capté"
                    value={statsQ.data.median_discount_captured_pct != null
                      ? `${fmtNum(statsQ.data.median_discount_captured_pct, 1)}%` : '—'}
                    tone={statsQ.data.median_discount_captured_pct < 0 ? 'var(--success)'
                          : 'var(--text-muted)'} />
              <Stat label="Firées" value={statsQ.data.n_fired} />
              <Stat label="Actives" value={statsQ.data.n_active} />
              <Stat label="Expirées sans tirer" value={statsQ.data.n_expired_unfired}
                    tone={statsQ.data.n_expired_unfired > statsQ.data.n_fired
                      ? 'var(--danger)' : 'var(--text-muted)'} />
            </div>
          )}

          {alertsQ.isLoading && (
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Chargement…</p>
          )}
          {alertsQ.isError && (
            <p style={{ color: 'var(--danger)', fontSize: '0.85rem' }}>
              Erreur chargement : {alertsQ.error?.message}
            </p>
          )}
          {!alertsQ.isLoading && count === 0 && (
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: 0 }}>
              Aucune alerte prix. Crée-en depuis la factsheet d'un ticker (onglet Action → Plan d'entrée → 🔔 Watch).
            </p>
          )}
          {count > 0 && (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid rgba(148,163,184,0.2)' }}>
                    <th style={{ textAlign: 'left', padding: '0.4rem' }}>Ticker</th>
                    <th style={{ textAlign: 'left', padding: '0.4rem' }}>Condition</th>
                    <th style={{ textAlign: 'right', padding: '0.4rem' }}>Target</th>
                    <th style={{ textAlign: 'right', padding: '0.4rem' }}>Weight</th>
                    <th style={{ textAlign: 'left', padding: '0.4rem' }}>Note</th>
                    <th style={{ textAlign: 'right', padding: '0.4rem' }}>Firings</th>
                    <th style={{ textAlign: 'left', padding: '0.4rem' }}>Dernier firing</th>
                    <th style={{ textAlign: 'left', padding: '0.4rem' }}>Expire</th>
                    <th style={{ width: 50 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map(a => {
                    const exp = isExpired(a.expires_at);
                    return (
                      <tr key={a.id} style={{
                        borderBottom: '1px solid rgba(148,163,184,0.08)',
                        opacity: exp ? 0.55 : 1,
                      }}>
                        <td style={{ padding: '0.4rem', fontFamily: 'monospace', fontWeight: 700 }}>
                          {a.ticker}
                        </td>
                        <td style={{ padding: '0.4rem' }}>
                          <span style={{
                            color: a.direction === 'above' ? 'var(--success)' : 'var(--warning)',
                            fontWeight: 600,
                          }}>
                            Prix {a.direction === 'above' ? '≥' : '≤'}
                          </span>
                        </td>
                        <td style={{ padding: '0.4rem', textAlign: 'right', fontFamily: 'monospace' }}>
                          ${fmtNum(a.target_price, 2)}
                        </td>
                        <td style={{ padding: '0.4rem', textAlign: 'right', fontFamily: 'monospace',
                                     color: 'var(--text-muted)' }}>
                          {a.weight_pct != null ? `${fmtNum(a.weight_pct, 0)}%` : '—'}
                        </td>
                        <td style={{ padding: '0.4rem', fontSize: '0.78rem',
                                     color: 'var(--text-muted)', maxWidth: 200,
                                     overflow: 'hidden', textOverflow: 'ellipsis',
                                     whiteSpace: 'nowrap' }}
                            title={a.note || ''}>
                          {a.note || '—'}
                        </td>
                        <td style={{
                          padding: '0.4rem', textAlign: 'right', fontFamily: 'monospace',
                          color: a.fire_count > 0 ? 'var(--accent-primary)' : 'var(--text-muted)',
                        }}>
                          {a.fire_count || 0}
                        </td>
                        <td style={{ padding: '0.4rem', color: 'var(--text-muted)', fontSize: '0.78rem' }}>
                          {a.last_fired_at ? formatTs(a.last_fired_at) : 'jamais'}
                        </td>
                        <td style={{ padding: '0.4rem', fontSize: '0.78rem',
                                     color: exp ? 'var(--danger)' : 'var(--text-muted)' }}>
                          {a.expires_at ? formatTs(a.expires_at) + (exp ? ' (expirée)' : '') : '—'}
                        </td>
                        <td style={{ padding: '0.4rem', textAlign: 'right' }}>
                          <button
                            className="scan-filter-btn"
                            onClick={() => {
                              if (window.confirm(`Supprimer l'alerte ${a.ticker} (${a.direction} ${a.target_price}) ?`)) {
                                delMut.mutate(a.id);
                              }
                            }}
                            disabled={delMut.isPending}
                            title="Supprimer cette alerte"
                            style={{ padding: '0.25rem 0.45rem', fontSize: '0.75rem' }}
                          >
                            🗑
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Stat({ label, value, tone }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)',
                     letterSpacing: '0.04em', textTransform: 'uppercase' }}>
        {label}
      </span>
      <span style={{ fontFamily: 'monospace', fontWeight: 700,
                     color: tone || 'var(--text-main)' }}>
        {value}
      </span>
    </div>
  );
}

function formatTs(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

function isExpired(iso) {
  if (!iso) return false;
  const d = new Date(iso);
  return !isNaN(d.getTime()) && d.getTime() < Date.now();
}
