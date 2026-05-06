// TableKeyNav — pose-le au-dessus d'une `.scan-table` pour activer
// la navigation clavier j/k + Enter pour ouvrir le ticker actif.
//
// Comment ça marche :
//   - Le composant scope un ref sur l'élément parent.
//   - À chaque keydown j/k (hors input), il avance le focus sur la prochaine
//     ligne <tr data-ticker="..."> visible et lui ajoute la classe `is-cursor`.
//   - Enter sur la ligne courante → onOpen(ticker)
//
// Ne consomme aucune state React (manipule juste DOM/classes), ce qui évite
// les re-renders coûteux sur les grandes tables.

import { useEffect, useRef } from 'react';

const CURSOR_CLASS = 'kbd-cursor';

const isInput = () => {
  const tag = document.activeElement?.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
};

export default function TableKeyNav({ onOpen }) {
  const cursorIdxRef = useRef(0);

  useEffect(() => {
    const onKey = (e) => {
      if (isInput()) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      const rows = Array.from(
        document.querySelectorAll('tr[data-ticker]')
      ).filter(r => r.offsetParent !== null);  // ignore les rows hidden

      if (rows.length === 0) return;

      const setCursor = (idx) => {
        rows.forEach(r => r.classList.remove(CURSOR_CLASS));
        const target = rows[(idx + rows.length) % rows.length];
        target.classList.add(CURSOR_CLASS);
        target.scrollIntoView({ block: 'nearest' });
        cursorIdxRef.current = (idx + rows.length) % rows.length;
      };

      const cur = rows.findIndex(r => r.classList.contains(CURSOR_CLASS));
      const idx = cur >= 0 ? cur : cursorIdxRef.current;

      if (e.key === 'j' || e.key === 'ArrowDown') {
        e.preventDefault();
        setCursor(idx + 1);
        return;
      }
      if (e.key === 'k' || e.key === 'ArrowUp') {
        e.preventDefault();
        setCursor(idx - 1);
        return;
      }
      if (e.key === 'Enter') {
        const target = rows.find(r => r.classList.contains(CURSOR_CLASS));
        if (!target) return;
        e.preventDefault();
        const t = target.getAttribute('data-ticker');
        if (t) onOpen?.(t.toUpperCase());
        return;
      }
    };

    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      // Cleanup cursor class
      document.querySelectorAll(`.${CURSOR_CLASS}`).forEach(
        el => el.classList.remove(CURSOR_CLASS)
      );
    };
  }, [onOpen]);

  return null;
}
