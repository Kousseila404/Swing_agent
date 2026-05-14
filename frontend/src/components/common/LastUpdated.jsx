// LastUpdated — affiche "Updated 2m ago" auto-rafraîchi.
//
// Pattern Seeking Alpha / Koyfin : sous chaque section data, indiquer
// l'âge de la donnée pour que l'utilisateur sache à quoi se fier.
//
// Lit `dataUpdatedAt` (epoch ms) tel que retourné par React Query, et
// re-render à intervalles adaptatifs (chaque 10s sous 1min, chaque 30s
// sous 5min, chaque 60s ensuite) pour rester accurate sans coût CPU.

import { useEffect, useState } from 'react';

const REFRESH_SHORT_MS  = 10_000;   // < 1 min  → tick 10s
const REFRESH_MEDIUM_MS = 30_000;   // < 5 min  → tick 30s
const REFRESH_LONG_MS   = 60_000;   // ≥ 5 min  → tick 60s

function formatAge(ageMs) {
  if (!Number.isFinite(ageMs) || ageMs < 0) return null;
  const s = Math.floor(ageMs / 1000);
  if (s < 5)    return 'just now';
  if (s < 60)   return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60)   return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24)   return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function pickInterval(ageMs) {
  if (ageMs < 60_000)     return REFRESH_SHORT_MS;
  if (ageMs < 300_000)    return REFRESH_MEDIUM_MS;
  return REFRESH_LONG_MS;
}

// Props :
//   updatedAt  : epoch ms (e.g. `useQuery().dataUpdatedAt`). 0/undefined = pas encore chargé.
//   isFetching : booléen (`useQuery().isFetching`). Affiche "Refreshing…" pendant les refetch.
//   prefix     : préfixe texte (par défaut "Updated").
//   compact    : version courte sans le préfixe.
export default function LastUpdated({
  updatedAt,
  isFetching = false,
  prefix = 'Updated',
  compact = false,
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!updatedAt) return undefined;
    const ageMs = Date.now() - updatedAt;
    const interval = pickInterval(ageMs);
    const id = setInterval(() => setNow(Date.now()), interval);
    return () => clearInterval(id);
  }, [updatedAt, now]);

  if (!updatedAt) {
    return (
      <span className="last-updated last-updated--idle" aria-live="polite">
        {isFetching ? 'Loading…' : '—'}
      </span>
    );
  }

  const age = formatAge(now - updatedAt);
  const iso = new Date(updatedAt).toISOString();

  return (
    <span
      className={`last-updated${isFetching ? ' last-updated--refreshing' : ''}`}
      title={`Last fetched at ${iso}`}
      aria-live="polite"
    >
      {isFetching && <span className="last-updated-dot" aria-hidden="true" />}
      {compact ? age : `${prefix} ${age}`}
    </span>
  );
}
