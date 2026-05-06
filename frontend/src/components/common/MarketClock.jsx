// MarketClock — affiche l'état NYSE (ouvert/fermé/pre/post) + countdown
// jusqu'au prochain évènement, avec horloge NY HH:MM.
//
// Source de vérité côté serveur : /market_status. On l'utilise si dispo
// (gère fériés US et half-days). Sinon, fallback sur marketSchedule.js
// (calcul client offline).
//
// Variants :
//   - default (sidebar)  : carte verticale avec dot pulsant
//   - compact (header)   : pill horizontale
//
// Tick interne 30s : suffisant pour mettre à jour le countdown sans
// surcharger React.

import { useEffect, useState } from 'react';
import { useMarketStatus } from '../../hooks/useApi';
import { formatNYClock, marketState, nextMarketEvent } from '../../utils/marketSchedule';

const STATE_LABELS = {
  OPEN: 'Marché ouvert',
  PRE:  'Pre-market',
  POST: 'After-hours',
  CLOSED: 'Marché fermé',
};

function useTick(intervalMs = 30_000) {
  const [n, setN] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setN((x) => x + 1), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return n;
}

export default function MarketClock({ variant = 'default' }) {
  useTick(30_000);  // re-render toutes les 30s pour rafraîchir countdown.
  const { data: serverStatus } = useMarketStatus();

  // Préférence backend si on a la clé `state` ou `is_open`.
  const localState = marketState();
  const localEvent = nextMarketEvent();
  const clock = formatNYClock();

  // Backend renvoie typiquement { is_open: bool, session: 'regular'|'pre'|'post'|'closed', next_open, next_close }
  let state = localState;
  let countdownLabel = localEvent.delta;
  let actionVerb = localEvent.action === 'OPEN' ? 'ouvre dans' : 'ferme dans';

  if (serverStatus && typeof serverStatus === 'object') {
    if (typeof serverStatus.is_open === 'boolean') {
      if (serverStatus.is_open) state = 'OPEN';
      else if (serverStatus.session === 'pre')  state = 'PRE';
      else if (serverStatus.session === 'post') state = 'POST';
      else state = 'CLOSED';
    } else if (typeof serverStatus.session === 'string') {
      const s = serverStatus.session.toUpperCase();
      if (['OPEN','PRE','POST','CLOSED'].includes(s)) state = s;
    }
  }

  const dotClass = state === 'OPEN' ? 'open'
    : state === 'CLOSED' ? 'closed'
    : state === 'PRE' ? 'pre' : 'post';

  if (variant === 'pill') {
    const tone = state === 'OPEN' ? 'success'
      : state === 'CLOSED' ? 'danger'
      : 'warning';
    return (
      <span
        className="header-pill"
        data-tone={tone}
        title={`Heure NY ${clock} · ${STATE_LABELS[state]}`}
      >
        <span
          className={`market-clock-dot ${dotClass}`}
          style={{ width: 7, height: 7 }}
          aria-hidden="true"
        />
        <strong>{STATE_LABELS[state]}</strong>
        <span style={{ fontFamily: 'monospace', opacity: 0.85 }}>
          · {actionVerb} {countdownLabel}
        </span>
        <span style={{ opacity: 0.6, fontFamily: 'monospace' }}>· {clock} ET</span>
      </span>
    );
  }

  // Default (sidebar)
  return (
    <div className="market-clock" title={`Heure NY ${clock}`}>
      <span className={`market-clock-dot ${dotClass}`} aria-hidden="true" />
      <div className="market-clock-info">
        <div className="market-clock-label">{STATE_LABELS[state]}</div>
        <div className="market-clock-sub">
          {actionVerb} {countdownLabel} · {clock} ET
        </div>
      </div>
    </div>
  );
}
