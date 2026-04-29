// Préférences UI persistées dans localStorage et appliquées via data-attrs
// sur <html>. Lus une fois au mount + setters qui réécrivent.
//
//   data-theme   = "dark" | "light"      (CSS overrides dans index.css)
//   data-density = "comfortable" | "compact"

import { useEffect, useState } from 'react';

const KEY_THEME   = 'pref_theme';
const KEY_DENSITY = 'pref_density';

const DEFAULT_THEME   = 'dark';
const DEFAULT_DENSITY = 'comfortable';

const _read = (key, fallback) => {
  try {
    const v = typeof localStorage !== 'undefined' ? localStorage.getItem(key) : null;
    return v || fallback;
  } catch { return fallback; }
};

const _write = (key, value) => {
  try { localStorage.setItem(key, value); } catch { /* private mode */ }
};

const _apply = (theme, density) => {
  if (typeof document === 'undefined') return;
  document.documentElement.setAttribute('data-theme', theme);
  document.documentElement.setAttribute('data-density', density);
};

// Hook tout-en-un. Le state est local au composant qui consomme — on ne
// partage pas via context : un seul consommateur dans App.jsx.
export function usePreferences() {
  const [theme, setThemeState]     = useState(() => _read(KEY_THEME, DEFAULT_THEME));
  const [density, setDensityState] = useState(() => _read(KEY_DENSITY, DEFAULT_DENSITY));

  useEffect(() => { _apply(theme, density); }, [theme, density]);

  const setTheme = (t) => {
    const v = t === 'light' ? 'light' : 'dark';
    _write(KEY_THEME, v);
    setThemeState(v);
  };

  const setDensity = (d) => {
    const v = d === 'compact' ? 'compact' : 'comfortable';
    _write(KEY_DENSITY, v);
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
  const _read = (key, fallback) => {
    try {
      const v = localStorage.getItem(key);
      if (v == null) return fallback;
      if (typeof fallback === 'number') {
        const n = parseFloat(v);
        return Number.isFinite(n) ? n : fallback;
      }
      if (typeof fallback === 'boolean') return v === 'true';
      return v;
    } catch { return fallback; }
  };
  return {
    total_capital:           _read('pref_default_capital', 100_000),
    max_holdings:            _read('pref_default_max_holdings', 20),
    top_n_mode:              _read('pref_default_top_n_mode', 'free_slots'),
    allow_fractional_shares: _read('pref_default_fractional', false),
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
