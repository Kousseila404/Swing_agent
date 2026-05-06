// VixPill — pill VIX colorisée dans le header. Click → page Macro.

import { useStatus } from '../../hooks/useApi';

export default function VixPill({ onOpenMacro }) {
  const { data: status } = useStatus({ refetchInterval: 30_000 });
  const vix = status?.vix;
  if (vix == null) return null;

  const tone = vix >= 30 ? 'danger' : vix >= 20 ? 'warning' : 'success';

  return (
    <span
      className="header-pill"
      data-tone={tone}
      title="VIX (cliquer pour voir Macro)"
    >
      <strong>VIX</strong>
      <button type="button" className="pill-link" onClick={onOpenMacro}>
        {vix.toFixed(1)}
      </button>
    </span>
  );
}
