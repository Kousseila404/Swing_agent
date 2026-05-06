// useMediaQuery — hook minimaliste pour réagir aux breakpoints CSS depuis JS.
//
// Ex: const isDesktop = useMediaQuery('(min-width: 1024px)');
//
// SSR-safe (renvoie false si window indispo). Utilise addEventListener
// (déprécié addListener) pour les MediaQueryList modernes.

import { useEffect, useState } from 'react';

export function useMediaQuery(query) {
  const get = () => {
    if (typeof window === 'undefined' || !window.matchMedia) return false;
    return window.matchMedia(query).matches;
  };
  const [match, setMatch] = useState(get);

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mql = window.matchMedia(query);
    const onChange = (e) => setMatch(e.matches);
    // Si la valeur a changé entre le lazy initializer et le mount (HMR,
    // hydratation tardive), on resync via la MQL — mais sans cascading
    // render injustifié si elle est identique.
    setMatch((prev) => (prev === mql.matches ? prev : mql.matches));
    if (mql.addEventListener) {
      mql.addEventListener('change', onChange);
      return () => mql.removeEventListener('change', onChange);
    }
    // Safari < 14
    mql.addListener(onChange);
    return () => mql.removeListener(onChange);
  }, [query]);

  return match;
}

// Helpers usuels.
export const useIsDesktop = () => useMediaQuery('(min-width: 1024px)');
export const useIsMobile  = () => useMediaQuery('(max-width: 767px)');
export const useReducedMotion = () =>
  useMediaQuery('(prefers-reduced-motion: reduce)');
