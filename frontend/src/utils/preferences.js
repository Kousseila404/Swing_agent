// Préférences UI persistées dans localStorage et appliquées via data-attrs
// sur <html>. Lus une fois au mount + setters qui réécrivent.
//
//   data-theme   = "dark" | "light"      (CSS overrides dans index.css)
//   data-density = "comfortable" | "compact"

import { useEffect, useState } from 'react';

import { readBoolean, readNumber, readString, writeString } from './storage';

const KEY_THEME   = 'pref_theme';
const KEY_DENSITY = 'pref_density';

const DEFAULT_THEME   = 'dark';
const DEFAULT_DENSITY = 'comfortable';

const _apply = (theme, density) => {
  if (typeof document === 'undefined') return;
  document.documentElement.setAttribute('data-theme', theme);
  document.documentElement.setAttribute('data-density', density);
};

// Hook tout-en-un. Le state est local au composant qui consomme — on ne
// partage pas via context : un seul consommateur dans App.jsx.
export function usePreferences() {
  const [theme, setThemeState]     = useState(() => readString(KEY_THEME, '') || DEFAULT_THEME);
  const [density, setDensityState] = useState(() => readString(KEY_DENSITY, '') || DEFAULT_DENSITY);

  useEffect(() => { _apply(theme, density); }, [theme, density]);

  const setTheme = (t) => {
    const v = t === 'light' ? 'light' : 'dark';
    writeString(KEY_THEME, v);
    setThemeState(v);
  };

  const setDensity = (d) => {
    const v = d === 'compact' ? 'compact' : 'comfortable';
    writeString(KEY_DENSITY, v);
    setDensityState(v);
  };

  const toggleTheme   = () => setTheme(theme === 'light' ? 'dark' : 'light');
  const toggleDensity = () => setDensity(density === 'compact' ? 'comfortable' : 'compact');

  return { theme, density, setTheme, setDensity, toggleTheme, toggleDensity };
}

// Lit les defaults Propositions depuis localStorage (écrits par
// SettingsPage). Retourne un objet utilisable directement comme prop
// `defaults` de RefreshPanel.
export function loadProposalDefaults() {
  return {
    total_capital:           readNumber('pref_default_capital', 100_000),
    max_holdings:            readNumber('pref_default_max_holdings', 20),
    top_n_mode:              readString('pref_default_top_n_mode', 'free_slots'),
    allow_fractional_shares: readBoolean('pref_default_fractional', false),
  };
}

// Sync activePage avec window.location.hash (#/<page>). Permet :
//  - rafraîchissement F5 sans perdre l'onglet
//  - back/forward navigateur entre pages
//  - URL partageable
//
// Liste blanche de pages : refuse tout hash inconnu (fallback DEFAULT_PAGE).
export function useHashRoute(defaultPage, validPages) {
  const _readHash = () => {
    const h = (typeof window !== 'undefined' ? window.location.hash : '') || '';
    const m = h.match(/^#\/([a-z0-9_-]+)/i);
    const id = m ? m[1] : null;
    return validPages.includes(id) ? id : defaultPage;
  };

  const [activePage, setActivePageState] = useState(_readHash);

  useEffect(() => {
    const onHashChange = () => setActivePageState(_readHash());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setActivePage = (id) => {
    if (!validPages.includes(id)) return;
    if (typeof window !== 'undefined') {
      // pushState pour conserver l'historique back/forward.
      const next = `#/${id}`;
      if (window.location.hash !== next) {
        window.history.pushState(null, '', next);
      }
    }
    setActivePageState(id);
  };

  return [activePage, setActivePage];
}
