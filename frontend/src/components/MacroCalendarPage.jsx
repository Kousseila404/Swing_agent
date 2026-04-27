import { useState } from 'react';
import { useMacroCalendar } from '../hooks/useApi';

const TYPE_COLOR = {
  FOMC: 'var(--accent-secondary)',
  CPI:  'var(--warning)',
  NFP:  'var(--accent-primary)',
};

function daysLabel(d) {
  if (d === 0)  return "Aujourd'hui";
  if (d === 1)  return 'Demain';
  if (d === -1) return 'Hier';
  if (d > 0)    return `Dans ${d}j`;
  return `il y a ${-d}j`;
}

export default function MacroCalendarPage() {
  const [horizon, setHorizon] = useState(60);
  const { data, isLoading, isError, refetch, isFetching } = useMacroCalendar(horizon);

  if (isLoading) return <div className="loading-pulse"><div className="spinner" /><p>Chargement calendrier…</p></div>;
  if (isError || !data) return (
    <div className="api-error">
      <div className="api-error-icon">⚠️</div>
      <p>Erreur /api/macro_calendar — vérifiez FastAPI + data/macro_calendar.json</p>
      <button className="action-btn" style={{ maxWidth: 200 }} onClick={() => refetch()}>Réessayer</button>
    </div>
  );

  const events = data.events || [];
  const weekEvents = events.filter(e => e.days_delta >= 0 && e.days_delta <= 7);

  return (
    <div className="control-panel animate-fade-in">
      {/* ── KPIs top ── */}
      <div className="cp-status-bar">
        <div className={`ks-status ${data.in_blackout ? 'blocked' : 'ok'}`} style={{ minWidth: 240 }}>
          {data.in_blackout ? '🚫 BLACKOUT ACTIF' : "✅ Pas de blackout aujourd'hui"}
        </div>
        <div className="status-chip">
          <span className="sc-lbl">Prochain événement</span>
          <span className="sc-val" style={{ fontSize: '0.95rem' }}>
            {data.next_event ? data.next_event.label : 'Aucun'}
          </span>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            {data.next_event ? daysLabel(data.next_event.days_delta) : '—'}
          </span>
        </div>
        <div className="status-chip">
          <span className="sc-lbl">Semaine</span>
          <span className="sc-val">{weekEvents.length}</span>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            événements à 7j
          </span>
        </div>
        <div className="status-chip">
          <span className="sc-lbl">Aujourd'hui</span>
          <span className="sc-val" style={{ fontFamily: 'monospace', fontSize: '0.95rem' }}>{data.today}</span>
        </div>
      </div>

      {/* ── Controls ── */}
      <div className="card cp-section-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <div>
            <div className="card-title" style={{ marginBottom: 0 }}>📅 Calendrier Macro</div>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
              Blackout J-1 à J0 — met à jour <code>data/macro_calendar.json</code> trimestriellement
            </div>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            {[30, 60, 90, 180].map(h => (
              <button
                key={h}
                className={`scan-filter-btn ${horizon === h ? 'active' : ''}`}
                onClick={() => setHorizon(h)}
              >
                {h}j
              </button>
            ))}
            <button className="scan-filter-btn" onClick={() => refetch()} disabled={isFetching}>
              {isFetching ? '⏳' : '🔄'}
            </button>
          </div>
        </div>

        {events.length === 0 ? (
          <div className="as-empty" style={{ padding: '3rem' }}>
            📭 Aucun événement sur l'horizon {horizon}j.
          </div>
        ) : (
          <table className="scan-table">
            <thead>
              <tr>
                <th>Statut</th>
                <th>Date</th>
                <th>Type</th>
                <th>Événement</th>
                <th>Jours</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={`${e.date || '?'}-${e.name || e.event || i}`} className="scan-row">
                  <td>
                    {e.blackout ? (
                      <span className="result-badge loss">🔴 BLACKOUT</span>
                    ) : e.upcoming ? (
                      <span className="result-badge" style={{ background: 'rgba(245,158,11,0.15)', color: 'var(--warning)', border: '1px solid rgba(245,158,11,0.3)' }}>🟡 À venir</span>
                    ) : e.days_delta < 0 ? (
                      <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>⬜ Passé</span>
                    ) : (
                      <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>⬜</span>
                    )}
                  </td>
                  <td style={{ fontFamily: 'monospace', fontSize: '0.85rem' }}>{e.date}</td>
                  <td>
                    <span
                      className="scan-signal-badge"
                      style={{ color: TYPE_COLOR[e.type] || 'var(--text-main)', borderColor: (TYPE_COLOR[e.type] || 'var(--text-muted)') + '50' }}
                    >
                      {e.type}
                    </span>
                  </td>
                  <td>{e.label}</td>
                  <td style={{ color: 'var(--text-muted)', fontSize: '0.85rem', fontFamily: 'monospace' }}>
                    {daysLabel(e.days_delta)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
