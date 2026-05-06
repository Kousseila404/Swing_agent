// LiveStatusBadge — badge compact "API connectée / hors ligne" dans la sidebar.

import { useStatus } from '../../hooks/useApi';

export default function LiveStatusBadge() {
  const { data: status, isError } = useStatus({ refetchInterval: 15_000 });
  const ok = status && !status.detail;

  if (isError || !ok) {
    return (
      <div
        className="live-badge"
        style={{ marginBottom: '0.75rem', borderColor: 'rgba(239,68,68,0.2)' }}
      >
        <span
          className="mode-dot"
          style={{ background: 'var(--text-muted)', boxShadow: 'none' }}
        />
        <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>
          API hors ligne
        </span>
      </div>
    );
  }

  return (
    <div className="live-badge" style={{ marginBottom: '0.75rem' }}>
      <span
        className="mode-dot"
        style={{ background: 'var(--success)', boxShadow: '0 0 6px var(--success)' }}
      />
      <span style={{ fontSize: '0.78rem', color: 'var(--success)' }}>API connectée</span>
      <span
        style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginLeft: 'auto' }}
      >
        VIX {status.vix ?? '—'}
      </span>
    </div>
  );
}
