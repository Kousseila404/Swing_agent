// CommandPalette — palette globale Cmd+K / Ctrl+K.
//
// Indexe :
//   - les pages de la nav (jump-to-page)
//   - les actions globales (refresh proposals, ouvrir watchlist…)
//   - tous les tickers de l'univers (jump-to-factsheet)
//
// Implémentation maison (pas de dépendance) : Set focus piégé, Esc ferme,
// j/k ou ↑/↓ navigue, Enter exécute. Fuzzy matching simple par sous-chaîne
// + boost si match au début du nom.

import { useEffect, useMemo, useRef, useState } from 'react';
import { useUniverse, useWatchlist } from '../hooks/useApi';

const NAV_TARGETS = [
  { id: 'briefing',    icon: '☀️', label: 'Briefing',     hint: 'Page · Briefing du jour' },
  { id: 'watchlist',   icon: '👁',  label: 'Watchlist',    hint: 'Page · Tickers observés' },
  { id: 'universe',    icon: '🌐', label: 'Univers',      hint: 'Page · Univers Quantamental' },
  { id: 'sectors',     icon: '🏛',  label: 'Secteurs',     hint: 'Page · Rotation sectorielle' },
  { id: 'portfolio',   icon: '📊', label: 'Portfolio',    hint: 'Page · Positions et journal' },
  { id: 'proposals',   icon: '📬', label: 'Propositions', hint: 'Page · Veto humain TITAN' },
  { id: 'performance', icon: '📈', label: 'Performance',  hint: 'Page · Sharpe, Sortino, DD' },
  { id: 'attribution', icon: '🎲', label: 'Attribution',  hint: 'Page · Win rate par bucket TITAN entry' },
  { id: 'ticker',      icon: '🎯', label: 'Ticker Detail',hint: 'Page · Score history' },
  { id: 'datahealth',  icon: '🩺', label: 'Data Health',  hint: 'Page · Providers et cache' },
  { id: 'risk',        icon: '⚠️', label: 'Risk Monitor', hint: 'Page · Budget et VIX' },
  { id: 'calendar',    icon: '🗓',  label: 'Catalysts',    hint: 'Page · Earnings + Macro consolidés' },
  { id: 'news',        icon: '📰', label: 'News',         hint: 'Page · Firehose positions + watchlist' },
  { id: 'macro',       icon: '📅', label: 'Macro',        hint: 'Page · Calendrier événements' },
  { id: 'audit',       icon: '🔍', label: 'Audit',        hint: 'Page · Survivorship + WFO' },
  { id: 'settings',    icon: '⚙️', label: 'Préférences',  hint: 'Page · Apparence + Defaults + API Token' },
];

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
  // Fallback : tous les chars de q présents dans t en ordre.
  let i = 0;
  for (const c of t) {
    if (c === q[i]) i++;
    if (i === q.length) return 50;
  }
  return 0;
}

export default function CommandPalette({ open, onClose, onNavigate, onOpenTicker }) {
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  const universeQ = useUniverse(null, { enabled: open });
  const watchlistQ = useWatchlist({ enabled: open });

  // Reset quand on ouvre.
  useEffect(() => {
    if (open) {
      setQuery('');
      setCursor(0);
      setTimeout(() => inputRef.current?.focus(), 10);
    }
  }, [open]);

  // Listes brutes
  const tickerItems = useMemo(() => {
    const tickers = universeQ.data?.tickers || {};
    return Object.entries(tickers).map(([t, info]) => ({
      kind: 'ticker',
      id: `ticker:${t}`,
      icon: '🎯',
      label: t,
      hint: [
        info?.name,
        info?.sector,
      ].filter(Boolean).join(' · ') || 'Ticker',
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
      kind: 'nav',
      id: `nav:${n.id}`,
      icon: n.icon,
      label: n.label,
      hint: n.hint,
      payload: n.id,
    })),
    [],
  );

  // Match + score + sort + cap
  const results = useMemo(() => {
    const all = [...navItems, ...watchlistItems, ...tickerItems];
    if (!query) {
      // Quand vide : nav d'abord, puis watchlist, puis quelques tickers.
      return [
        ...navItems,
        ...watchlistItems.slice(0, 5),
      ].slice(0, 30);
    }
    const scored = [];
    for (const item of all) {
      const labelScore = fuzzyScore(query, item.label);
      const hintScore  = fuzzyScore(query, item.hint) * 0.3;
      const total = labelScore + hintScore;
      if (total > 0) {
        // Boost nav et watchlist (sont moins nombreux mais plus utiles).
        const boost = item.kind === 'nav' ? 50 : item.kind === 'watchlist' ? 25 : 0;
        scored.push({ ...item, _score: total + boost });
      }
    }
    scored.sort((a, b) => b._score - a._score);
    // Dédupe ticker présent en watchlist + univers : garde watchlist (plus contextuel).
    const seenLabels = new Set();
    const out = [];
    for (const it of scored) {
      if (it.kind === 'ticker' && seenLabels.has(`label:${it.label}`)) continue;
      out.push(it);
      seenLabels.add(`label:${it.label}`);
      if (out.length >= 30) break;
    }
    return out;
  }, [query, navItems, watchlistItems, tickerItems]);

  // Reset cursor quand results changent
  useEffect(() => { setCursor(0); }, [query]);

  // Scroll auto pour garder le cursor visible
  useEffect(() => {
    if (!listRef.current) return;
    const active = listRef.current.querySelector(`[data-idx="${cursor}"]`);
    if (active) active.scrollIntoView({ block: 'nearest' });
  }, [cursor]);

  const execute = (item) => {
    if (!item) return;
    if (item.kind === 'nav') {
      onNavigate?.(item.payload);
    } else if (item.kind === 'ticker' || item.kind === 'watchlist') {
      onOpenTicker?.(item.payload);
    }
    onClose?.();
  };

  const onKeyDown = (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose?.();
      return;
    }
    if (e.key === 'ArrowDown' || (e.key === 'n' && e.ctrlKey)) {
      e.preventDefault();
      setCursor(c => Math.min(results.length - 1, c + 1));
      return;
    }
    if (e.key === 'ArrowUp' || (e.key === 'p' && e.ctrlKey)) {
      e.preventDefault();
      setCursor(c => Math.max(0, c - 1));
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      execute(results[cursor]);
      return;
    }
  };

  if (!open) return null;

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,0.55)',
        backdropFilter: 'blur(2px)',
        zIndex: 2000,
        display: 'flex', justifyContent: 'center', alignItems: 'flex-start',
        padding: '12vh 1rem 1rem',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: '100%', maxWidth: 640,
          background: 'var(--panel-bg)',
          border: '1px solid var(--panel-border)',
          borderRadius: 12,
          boxShadow: '0 24px 64px rgba(0,0,0,0.5)',
          overflow: 'hidden',
          backdropFilter: 'blur(20px)',
        }}
      >
        <div style={{
          padding: '0.85rem 1rem',
          borderBottom: '1px solid var(--panel-border)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span style={{ fontSize: '1.1rem', opacity: 0.6 }}>⌘</span>
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Tape pour chercher : ticker, page, action…"
            style={{
              flex: 1, background: 'none', border: 'none', outline: 'none',
              color: 'var(--text-main)', fontFamily: 'inherit',
              fontSize: '0.95rem',
            }}
          />
          <span style={{
            fontSize: '0.66rem', color: 'var(--text-muted)',
            border: '1px solid var(--panel-border)',
            padding: '2px 6px', borderRadius: 4,
            fontFamily: 'monospace',
          }}>esc</span>
        </div>

        <div ref={listRef} style={{ maxHeight: '50vh', overflowY: 'auto' }}>
          {results.length === 0 && (
            <div style={{
              padding: '2rem', textAlign: 'center', fontSize: '0.85rem',
              color: 'var(--text-muted)',
            }}>
              📭 Aucun résultat pour "{query}"
            </div>
          )}
          {results.map((it, i) => {
            const active = i === cursor;
            return (
              <div
                key={it.id}
                data-idx={i}
                onClick={() => execute(it)}
                onMouseEnter={() => setCursor(i)}
                style={{
                  padding: '0.55rem 1rem',
                  display: 'flex', alignItems: 'center', gap: 10,
                  cursor: 'pointer',
                  background: active ? 'rgba(59,130,246,0.10)' : 'transparent',
                  borderLeft: active ? '3px solid var(--accent-primary)' : '3px solid transparent',
                  fontSize: '0.85rem',
                }}
              >
                <span style={{ fontSize: '1.05rem', opacity: 0.85 }}>{it.icon}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontWeight: 600,
                    color: active ? 'var(--text-main)' : 'var(--text-main)',
                    fontFamily: it.kind === 'nav' ? 'inherit' : 'monospace',
                  }}>
                    {it.label}
                  </div>
                  <div style={{
                    fontSize: '0.7rem', color: 'var(--text-muted)',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {it.hint}
                  </div>
                </div>
                <span style={{
                  fontSize: '0.6rem', color: 'var(--text-muted)',
                  textTransform: 'uppercase', letterSpacing: '0.06em',
                }}>
                  {it.kind === 'ticker' ? 'Ticker' : it.kind === 'watchlist' ? 'Watch' : 'Page'}
                </span>
              </div>
            );
          })}
        </div>

        <div style={{
          padding: '0.5rem 1rem',
          borderTop: '1px solid var(--panel-border)',
          fontSize: '0.66rem', color: 'var(--text-muted)',
          display: 'flex', gap: 14, justifyContent: 'flex-end',
          fontFamily: 'monospace',
        }}>
          <span>↑↓ naviguer</span>
          <span>↵ exécuter</span>
          <span>esc fermer</span>
        </div>
      </div>
    </div>
  );
}
