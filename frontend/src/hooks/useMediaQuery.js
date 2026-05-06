// useMediaQuery — hook minimaliste pour réagir aux breakpoints CSS depuis JS.
//
// Implémenté avec useSyncExternalStore (React 18+), qui est exactement
// l'API faite pour s'abonner à un store externe sans cascading render et
// sans warning du lint react-hooks/set-state-in-effect.
//
// Ex: const isDesktop = useMediaQuery('(min-width: 1024px)');
// SSR-safe : renvoie false côté serveur (snapshot serveur).

import { useEffect, useState, useSyncExternalStore } from 'react';

function subscribeMql(query, callback) {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {};
  const mql = window.matchMedia(query);
  if (mql.addEventListener) {
    mql.addEventListener('change', callback);
    return () => mql.removeEventListener('change', callback);
  }
  // Safari < 14 fallback
  mql.addListener(callback);
  return () => mql.removeListener(callback);
}

function getSnapshot(query) {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia(query).matches;
}

export function useMediaQuery(query) {
  return useSyncExternalStore(
    (cb) => subscribeMql(query, cb),
    () => getSnapshot(query),
    () => false,  // server snapshot
  );
}

// Helpers usuels.
export const useIsDesktop = () => useMediaQuery('(min-width: 1024px)');
export const useIsMobile  = () => useMediaQuery('(max-width: 767px)');
export const useReducedMotion = () =>
  useMediaQuery('(prefers-reduced-motion: reduce)');

// useDebouncedValue — retarde la propagation d'une valeur (ex: input
// search). Évite de spam la query côté React Query / l'algorithme de
// fuzzy match à chaque keystroke.
//
// Ex: const debounced = useDebouncedValue(query, 200);
//     useEffect(() => fetch(debounced), [debounced]);
export function useDebouncedValue(value, delayMs = 200) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    if (delayMs <= 0) return undefined;
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);
  }, [value, delayMs]);
  // Cas dégénéré delayMs <= 0 : pass-through direct, pas de state cascade.
  return delayMs <= 0 ? value : debounced;
}
