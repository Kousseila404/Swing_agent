// Conversion heure civile (fuseau nommé) -> instant UTC, sans lib dédiée
// (Intl uniquement) — utilisé par le formulaire de saisie du journal
// d'exécution (Upgrade 4, docs/UPGRADES_MY_PORTFOLIO.md) pour éviter le cas
// limite "erreur de fuseau à la saisie" (mieux vaut convertir explicitement
// que deviner un fuseau).

// Offset (minutes, positif = en avance sur UTC) de `timeZone` à l'instant
// `date` — DST-aware car dépend de la date réelle passée, même principe que
// `zoneinfo` côté backend (modules/exchange_hours.py).
export function tzOffsetMinutes(date, timeZone) {
  const dtf = new Intl.DateTimeFormat('en-US', {
    timeZone, hourCycle: 'h23',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  const parts = dtf.formatToParts(date).reduce((acc, p) => { acc[p.type] = p.value; return acc; }, {});
  const asUtc = Date.UTC(
    +parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second
  );
  return (asUtc - date.getTime()) / 60000;
}

// Convertit une heure civile (`dateStr` "YYYY-MM-DD" + `timeStr` "HH:MM",
// saisie dans le fuseau `timeZone`) en instant UTC réel — 2 passes pour
// affiner l'offset autour d'une transition DST.
export function zonedWallClockToUtc(dateStr, timeStr, timeZone) {
  if (!dateStr || !timeStr) return null;
  const [y, mo, d] = dateStr.split('-').map(Number);
  const [h, mi] = timeStr.split(':').map(Number);
  const guessUtcMs = Date.UTC(y, mo - 1, d, h, mi);
  const offset1 = tzOffsetMinutes(new Date(guessUtcMs), timeZone);
  const offset2 = tzOffsetMinutes(new Date(guessUtcMs - offset1 * 60000), timeZone);
  return new Date(guessUtcMs - offset2 * 60000);
}
