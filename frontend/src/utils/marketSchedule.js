// marketSchedule — utilitaire 100 % client pour estimer l'état NYSE.
//
// On NE remplace PAS l'endpoint backend /market_status (qui prend en compte
// les fériés US et les half-days). On fournit juste un fallback offline et
// un formatter "ferme dans 3h12" / "ouvre dans 14h22".
//
// Heures NYSE (régulier) :
//   PRE     04:00 → 09:30 ET
//   OPEN    09:30 → 16:00 ET
//   POST    16:00 → 20:00 ET
//   CLOSED  reste (et week-end)
//
// On exprime tout en minutes-depuis-minuit ET pour rester simple.

const NY_TZ = 'America/New_York';

const SCHEDULE = {
  PRE_OPEN:    4 * 60,        // 04:00
  REGULAR_OPEN:  9 * 60 + 30,   // 09:30
  REGULAR_CLOSE: 16 * 60,       // 16:00
  POST_CLOSE:    20 * 60,       // 20:00
};

// Renvoie {h, m, dayOfWeek (0=dim..6=sam)} en heure de New York.
export function nowInNY(date = new Date()) {
  // Intl.DateTimeFormat permet d'extraire les composantes en TZ NY sans
  // dépendance externe (luxon/dayjs).
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: NY_TZ,
    hour12: false,
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
  const parts = Object.fromEntries(
    fmt.formatToParts(date).map((p) => [p.type, p.value]),
  );
  const wkMap = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  const h = parseInt(parts.hour, 10) % 24;   // "24" → 0 dans certains envs
  const m = parseInt(parts.minute, 10);
  const dow = wkMap[parts.weekday] ?? new Date().getDay();
  return { h, m, dow, mins: h * 60 + m };
}

// Renvoie 'OPEN' | 'PRE' | 'POST' | 'CLOSED' (week-end ou nuit).
export function marketState(date = new Date()) {
  const { dow, mins } = nowInNY(date);
  if (dow === 0 || dow === 6) return 'CLOSED';
  if (mins >= SCHEDULE.REGULAR_OPEN && mins < SCHEDULE.REGULAR_CLOSE) return 'OPEN';
  if (mins >= SCHEDULE.PRE_OPEN     && mins < SCHEDULE.REGULAR_OPEN)  return 'PRE';
  if (mins >= SCHEDULE.REGULAR_CLOSE && mins < SCHEDULE.POST_CLOSE)   return 'POST';
  return 'CLOSED';
}

// Format "Xh YYm" / "YYm" / "<1m".
function _fmtDelta(totalMin) {
  if (totalMin <= 0) return 'maintenant';
  if (totalMin < 1) return '<1m';
  const h = Math.floor(totalMin / 60);
  const m = Math.round(totalMin - h * 60);
  if (h === 0) return `${m}m`;
  return `${h}h ${String(m).padStart(2, '0')}m`;
}

// Renvoie l'évènement marché suivant + le delta humanisé.
// Ne gère pas les fériés US — backend /market_status reste source de vérité
// pour les vraies décisions ; ici c'est un affichage indicatif.
export function nextMarketEvent(date = new Date()) {
  const { dow, mins } = nowInNY(date);
  const state = marketState(date);

  // Week-end → prochaine ouverture lundi 09:30
  if (dow === 0 || dow === 6) {
    const daysToMon = dow === 0 ? 1 : 2;
    const minsToMonOpen = (24 - mins / 60) * 60 + (daysToMon - 1) * 24 * 60 + SCHEDULE.REGULAR_OPEN;
    return { state, action: 'OPEN', label: 'NYSE ferme', delta: _fmtDelta(minsToMonOpen) };
  }

  if (state === 'OPEN') {
    return {
      state,
      action: 'CLOSE',
      label: 'NYSE ouvert',
      delta: _fmtDelta(SCHEDULE.REGULAR_CLOSE - mins),
    };
  }
  if (state === 'PRE') {
    return {
      state,
      action: 'OPEN',
      label: 'Pre-market',
      delta: _fmtDelta(SCHEDULE.REGULAR_OPEN - mins),
    };
  }
  if (state === 'POST') {
    return {
      state,
      action: 'OPEN',
      label: 'After-hours',
      // ouverture demain 09:30 (sauf si on est vendredi soir → lundi)
      delta: _fmtDelta(_minsUntilNextOpen(dow, mins)),
    };
  }
  // CLOSED en semaine (avant 04:00 ou après 20:00)
  return {
    state,
    action: 'OPEN',
    label: 'NYSE fermé',
    delta: _fmtDelta(_minsUntilNextOpen(dow, mins)),
  };
}

function _minsUntilNextOpen(dow, mins) {
  // Si on est avant 09:30 le même jour → minutes restantes dans la journée
  if (mins < SCHEDULE.REGULAR_OPEN) return SCHEDULE.REGULAR_OPEN - mins;
  // Sinon : reste de la journée + nuit + 09:30
  const remainToday = 24 * 60 - mins;
  // Si vendredi → +2 jours (sam+dim) avant lundi
  const skipDays = dow === 5 ? 2 : 0;
  return remainToday + skipDays * 24 * 60 + SCHEDULE.REGULAR_OPEN;
}

// Heure NY HH:MM, à afficher dans la status bar.
export function formatNYClock(date = new Date()) {
  const { h, m } = nowInNY(date);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}
