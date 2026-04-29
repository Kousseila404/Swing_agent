// TitanAlertsPanel — gestion des alertes TITAN seuil utilisateur (Tier B #3).
//
// Liste les alertes existantes (above/below + threshold + last_fired_at).
// Permet d'en supprimer. La création se fait depuis la page Univers (bouton
// 🔔 par ligne) ou via /api/titan_alerts (POST).

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchTitanAlerts, deleteTitanAlert } from '../api/client';
import { fmtNum } from '../utils/format';
import { pushToast } from '../utils/toastBus';

export default function TitanAlertsPanel() {
  const qc = useQueryClient();
  const [collapsed, setCollapsed] = useState(false);

  const alertsQ = useQuery({
    queryKey: ['titan_alerts'],
    queryFn: fetchTitanAlerts,
    refetchInterval: 60_000,
  });

  const delMut = useMutation({
    mutationFn: (id) => deleteTitanAlert(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['titan_alerts'] });
      pushToast({ msg: '🔕 Alerte supprimée' });
    },
    onError: (err) => {
      pushToast({ msg: `❌ ${err?.message || 'Suppression impossible'}`, type: 'error' });
    },
  });

  const items = alertsQ.data?.items || [];
  const count = items.length;

  return (
    <div className="card" style={{ marginBottom: '1rem' }}>
      <div
        className="card-title"
        onClick={() => setCollapsed(c => !c)}
        style={{ cursor: 'pointer', justifyContent: 'space-between' }}
      >
        <span>
          🔔 Alertes TITAN <small style={{ marginLeft: '0.5rem', color: 'var(--text-muted)', fontWeight: 400 }}>
            {count} active{count > 1 ? 's' : ''} · cooldown 24h · Telegram daily
          </small>
        </span>
        <span style={{ color: 'var(--text-muted)' }}>{collapsed ? '▸' : '▾'}</span>
      </div>

      {!collapsed && (
        <>
          {alertsQ.isLoading && (
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Chargement…</p>
          )}
          {alertsQ.isError && (
            <p style={{ color: 'var(--danger)', fontSize: '0.85rem' }}>
              Erreur chargement alertes : {alertsQ.error?.message}
            </p>
          )}
          {!alertsQ.isLoading && count === 0 && (
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: 0 }}>
              Aucune alerte. Crée-en depuis la page Univers (bouton 🔔 par ticker).
            </p>
          )}
          {count > 0 && (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid rgba(148,163,184,0.2)' }}>
                    <th style={{ textAlign: 'left', padding: '0.4rem' }}>Ticker</th>
                    <th style={{ textAlign: 'left', padding: '0.4rem' }}>Condition</th>
                    <th style={{ textAlign: 'right', padding: '0.4rem' }}>Seuil</th>
                    <th style={{ textAlign: 'right', padding: '0.4rem' }}>Firings</th>
                    <th style={{ textAlign: 'left', padding: '0.4rem' }}>Dernier firing</th>
                    <th style={{ textAlign: 'left', padding: '0.4rem' }}>Créée</th>
                    <th style={{ width: 50 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map(a => (
                    <tr key={a.id} style={{ borderBottom: '1px solid rgba(148,163,184,0.08)' }}>
                      <td style={{ padding: '0.4rem', fontFamily: 'monospace', fontWeight: 700 }}>
                        {a.ticker}
                      </td>
                      <td style={{ padding: '0.4rem' }}>
                        <span style={{
                          color: a.direction === 'above' ? 'var(--success)' : 'var(--warning)',
                          fontWeight: 600,
                        }}>
                          TITAN {a.direction === 'above' ? '≥' : '≤'}
                        </span>
                      </td>
                      <td style={{ padding: '0.4rem', textAlign: 'right', fontFamily: 'monospace' }}>
                        {fmtNum(a.threshold, 1)}
                      </td>
                      <td style={{
                        padding: '0.4rem',
                        textAlign: 'right',
                        fontFamily: 'monospace',
                        color: a.fire_count > 0 ? 'var(--accent-primary)' : 'var(--text-muted)',
                      }}>
                        {a.fire_count || 0}
                      </td>
                      <td style={{ padding: '0.4rem', color: 'var(--text-muted)', fontSize: '0.78rem' }}>
                        {a.last_fired_at ? formatTs(a.last_fired_at) : 'jamais'}
                      </td>
                      <td style={{ padding: '0.4rem', color: 'var(--text-muted)', fontSize: '0.78rem' }}>
                        {a.created_at ? formatTs(a.created_at) : '—'}
                      </td>
                      <td style={{ padding: '0.4rem', textAlign: 'right' }}>
                        <button
                          className="scan-filter-btn"
                          onClick={() => {
                            if (window.confirm(`Supprimer l'alerte ${a.ticker} (${a.direction} ${a.threshold}) ?`)) {
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
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
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
