// CommandPalette — palette globale Cmd+K / Ctrl+K.
//
// Indexe :
//   - Pages de la nav (jump-to-page)
//   - Actions globales (toggle theme/density, refresh proposals, …)
//   - Watchlist (jump-to-factsheet, contextuelle)
//   - Tickers de l'univers (jump-to-factsheet, gros volume)
//   - Recents (les 6 dernières exécutions, persistés en localStorage)
//
// Implémentation maison (zéro dépendance) :
//   - Esc ferme
//   - ↑↓ ou Ctrl+P/N navigue
//   - Enter exécute
//   - Cmd+Enter (si dispo) : exécute en gardant la palette ouverte
//
// Fuzzy matching simple : sous-chaîne + boost si match au début. Sections
// groupées avec en-tête sticky. Footer affiche les raccourcis.

import { useEffect, useMemo, useRef, useState } from 'react';

import { NAV_TARGETS } from '../config/nav';
import { useUniverse, useWatchlist } from '../hooks/useApi';
import { readJSON, writeJSON } from '../utils/storage';

// Actions globales — déclenchées via onAction(actionId).
const GLOBAL_ACTIONS = [
  { id: 'toggle-theme',   icon: '🌓', label: 'Basculer thème clair/sombre', hint: 'Action · prefers-color-scheme' },
  { id: 'toggle-density', icon: '↕️',  label: 'Basculer densité compact/cosy', hint: 'Action · plus de lignes visibles' },
  { id: 'goto-briefing',  icon: '☀️', label: 'Ouvrir Briefing du jour',     hint: 'Action · raccourci' },
  { id: 'goto-proposals', icon: '📬', label: 'Ouvrir Propositions',         hint: 'Action · veto humain' },
  { id: 'goto-settings',  icon: '⚙️', label: 'Ouvrir Préférences',          hint: 'Action · apparence + API token' },
];

const RECENTS_KEY = 'cmdk_recents';
const RECENTS_MAX = 6;

function readRecents() {
  return readJSON(RECENTS_KEY, []);
}

function pushRecent(item) {
  const list = readRecents().filter((x) => x.id !== item.id);
  list.unshift({
    id: item.id, kind: item.kind, label: item.label,
    hint: item.hint, icon: item.icon, payload: item.payload,
    ts: Date.now(),
  });
  writeJSON(RECENTS_KEY, list.slice(0, RECENTS_MAX));
}

// Fuzzy score simple : 0 si pas de match, sinon plus haut = meilleur.
function fuzzyScore(query, target) {
  if (!query) return 1;
  const q = query.toLowerCase();
  const t = (target || '').toLowerCase();
  if (!t) return 0;
  if (t === q) return 1000;
  if (t.startsWith(q)) return 500;
  const idx = t.indexOf(q);
  if (idx >= 0) return 200 - idx;
  let i = 0;
  for (const c of t) {
    if (c === q[i]) i++;
    if (i === q.length) return 50;
  }
  return 0;
}

export default function CommandPalette({ open, onClose, onNavigate, onOpenTicker, onAction }) {
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  const universeQ  = useUniverse(null, { enabled: open });
  const watchlistQ = useWatchlist({ enabled: open });

  // Reset à l'ouverture (état ajusté pendant le render, pas dans un effet)
  // puis focus sur l'input.
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setQuery('');
      setCursor(0);
    }
  }
  useEffect(() => {
    if (!open) return undefined;
    const t = setTimeout(() => inputRef.current?.focus(), 10);
    return () => clearTimeout(t);
  }, [open]);

  const tickerItems = useMemo(() => {
    const tickers = universeQ.data?.tickers || {};
    return Object.entries(tickers).map(([t, info]) => ({
      kind: 'ticker',
      id: `ticker:${t}`,
      icon: '🎯',
      label: t,
      hint: [info?.name, info?.sector].filter(Boolean).join(' · ') || 'Ticker',
      payload: t,
    }));
  }, [universeQ.data]);

  const watchlistItems = useMemo(() => {
    const items = watchlistQ.data?.items || [];
    return items.map(it => ({
      kind: 'watchlist',
      id: `watch:${it.ticker}`,
      icon: '👁',
      label: it.ticker,
      hint: `Watchlist${it.tag ? ' · ' + it.tag : ''}${it.n_notes ? ` · 📝 ${it.n_notes}` : ''}`,
      payload: it.ticker,
    }));
  }, [watchlistQ.data]);

  const navItems = useMemo(() =>
    NAV_TARGETS.map(n => ({
      kind: 'nav', id: `nav:${n.id}`, icon: n.icon,
      label: n.label, hint: n.hint, payload: n.id,
    })), []);

  const actionItems = useMemo(() =>
    GLOBAL_ACTIONS.map(a => ({
      kind: 'action', id: `act:${a.id}`, icon: a.icon,
      label: a.label, hint: a.hint, payload: a.id,
    })), []);

  // Recents sont rechargés à chaque ouverture (et après exécution).
  const [recentTick, setRecentTick] = useState(0);
  const recentItems = useMemo(() => {
    if (!open) return [];
    return readRecents().map(r => ({ ...r, kind: r.kind || 'nav' }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, recentTick]);

  // Sections regroupées en sortie : pas de dédup entre sections (la dédup
  // tickers vs watchlist se fait quand on construit la liste filtrée).
  const sections = useMemo(() => {
    if (!query) {
      // Vue par défaut : Recents + Actions + Pages + Watchlist (top 6)
      const out = [];
      if (recentItems.length > 0) out.push({ label: 'Récents', items: recentItems });
      out.push({ label: 'Actions', items: actionItems });
      out.push({ label: 'Pages', items: navItems });
      if (watchlistItems.length > 0) {
        out.push({ label: 'Watchlist', items: watchlistItems.slice(0, 8) });
      }
      return out;
    }

    // Recherche : on score chaque kind séparément, puis on garde top N par section.
    const scoreList = (items, boostKind = 0) => {
      const scored = [];
      for (const item of items) {
        const labelScore = fuzzyScore(query, item.label);
        const hintScore  = fuzzyScore(query, item.hint) * 0.3;
        const total = labelScore + hintScore;
        if (total > 0) scored.push({ ...item, _score: total + boostKind });
      }
      scored.sort((a, b) => b._score - a._score);
      return scored;
    };

    const navMatches    = scoreList(navItems, 50).slice(0, 8);
    const actionMatches = scoreList(actionItems, 30).slice(0, 6);
    const watchMatches  = scoreList(watchlistItems, 20).slice(0, 8);
    const tickerMatches = scoreList(tickerItems).slice(0, 14);

    // Dédup : si un ticker apparaît déjà en watchlist, on le retire de Tickers.
    const watchLabels = new Set(watchMatches.map(w => w.label));
    const tickerFiltered = tickerMatches.filter(t => !watchLabels.has(t.label));

    const out = [];
    if (actionMatches.length) out.push({ label: 'Actions', items: actionMatches });
    if (navMatches.length)    out.push({ label: 'Pages',   items: navMatches });
    if (watchMatches.length)  out.push({ label: 'Watchlist', items: watchMatches });
    if (tickerFiltered.length) out.push({ label: 'Tickers', items: tickerFiltered });
    return out;
  }, [query, navItems, actionItems, watchlistItems, tickerItems, recentItems]);

  // Liste à plat pour la navigation clavier (cursor index).
  const flatList = useMemo(() => sections.flatMap(s => s.items), [sections]);

  // Scroll auto pour garder le cursor visible.
  useEffect(() => {
    if (!listRef.current) return;
    const active = listRef.current.querySelector(`[data-idx="${cursor}"]`);
    if (active) active.scrollIntoView({ block: 'nearest' });
  }, [cursor]);

  const execute = (item, keepOpen = false) => {
    if (!item) return;
    pushRecent(item);
    setRecentTick(t => t + 1);
    if (item.kind === 'nav') {
      onNavigate?.(item.payload);
    } else if (item.kind === 'action') {
      onAction?.(item.payload);
    } else if (item.kind === 'ticker' || item.kind === 'watchlist') {
      onOpenTicker?.(item.payload);
    }
    if (!keepOpen) onClose?.();
  };

  const onKeyDown = (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose?.();
      return;
    }
    if (e.key === 'ArrowDown' || (e.key === 'n' && e.ctrlKey)) {
      e.preventDefault();
      setCursor(c => Math.min(flatList.length - 1, c + 1));
      return;
    }
    if (e.key === 'ArrowUp' || (e.key === 'p' && e.ctrlKey)) {
      e.preventDefault();
      setCursor(c => Math.max(0, c - 1));
      return;
    }
    if (e.key === 'Home') {
      e.preventDefault(); setCursor(0); return;
    }
    if (e.key === 'End') {
      e.preventDefault(); setCursor(flatList.length - 1); return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      execute(flatList[cursor], e.metaKey || e.ctrlKey);
      return;
    }
  };

  if (!open) return null;

  // On numérote chaque item de manière monotone à travers les sections
  // pour que cursor reste cohérent.
  let runningIdx = -1;

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true" aria-label="Palette de commandes">
      <div className="cmdk-root" onClick={e => e.stopPropagation()}>
        <div className="cmdk-input-row">
          <span style={{ fontSize: '1.05rem', opacity: 0.6 }} aria-hidden="true">⌘</span>
          <input
            ref={inputRef}
            value={query}
            onChange={e => { setQuery(e.target.value); setCursor(0); }}
            onKeyDown={onKeyDown}
            placeholder="Tape pour chercher : ticker, page, action…"
            className="cmdk-input"
            aria-label="Recherche dans la palette"
            aria-autocomplete="list"
            aria-controls="cmdk-listbox"
          />
          <kbd>esc</kbd>
        </div>

        <div ref={listRef} className="cmdk-list" id="cmdk-listbox" role="listbox">
          {flatList.length === 0 && (
            <div className="cmdk-empty">
              📭 Aucun résultat pour <strong>"{query}"</strong>
            </div>
          )}
          {sections.map(section => (
            <div key={section.label} className="cmdk-section">
              <div className="cmdk-section-label">{section.label}</div>
              {section.items.map((it) => {
                runningIdx++;
                const idx = runningIdx;
                const active = idx === cursor;
                return (
                  <div
                    key={it.id}
                    data-idx={idx}
                    onClick={() => execute(it)}
                    onMouseEnter={() => setCursor(idx)}
                    className={`cmdk-item ${active ? 'active' : ''}`}
                    role="option"
                    aria-selected={active}
                  >
                    <span className="cmdk-item-icon">{it.icon}</span>
                    <div className="cmdk-item-body">
                      <div
                        className="cmdk-item-label"
                        style={{ fontFamily: it.kind === 'ticker' || it.kind === 'watchlist' ? 'monospace' : 'inherit' }}
                      >
                        {it.label}
                      </div>
                      <div className="cmdk-item-hint">{it.hint}</div>
                    </div>
                    <span className="cmdk-item-tag">
                      {it.kind === 'ticker' ? 'Ticker'
                        : it.kind === 'watchlist' ? 'Watch'
                        : it.kind === 'action' ? 'Action'
                        : 'Page'}
                    </span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>

        <div className="cmdk-footer">
          <span style={{ opacity: 0.7 }}>
            {flatList.length} résultat{flatList.length > 1 ? 's' : ''}
          </span>
          <div className="cmdk-footer-keys">
            <span><kbd>↑</kbd><kbd>↓</kbd> naviguer</span>
            <span><kbd>↵</kbd> exécuter</span>
            <span><kbd>⌘↵</kbd> garder ouvert</span>
            <span><kbd>esc</kbd> fermer</span>
          </div>
        </div>
      </div>
    </div>
  );
}
