// TickerContextMenu — menu contextuel global activé au clic droit sur
// tout élément qui porte data-ticker="XYZ". Centralisé pour éviter de
// re-câbler chaque table.
//
// Usage : <TickerContextMenu /> au niveau racine (App.jsx). N'importe
// où dans l'arbre, mettre data-ticker="AAPL" sur un TD/SPAN/BUTTON.
//
// Actions :
//   - Ouvrir le factsheet (TickerAnalysisModal)
//   - Ajouter à la watchlist
//   - Ouvrir TradingView
//   - Ouvrir SEC EDGAR
//   - Copier le ticker

import { useEffect, useRef, useState } from 'react';
import { addToWatchlist as apiAddToWatchlist } from '../../api/client';
import { pushToast } from '../../utils/toastBus';

const MENU_W = 220;

// Item / Divider sont des sous-composants stables, hoistés hors de
// TickerContextMenu pour ne pas être recréés à chaque render (sinon
// React Compiler signale "Cannot create components during render").
function MenuItem({ icon, label, action, kbd, onAction }) {
  return (
    <button
      type="button"
      onClick={() => onAction(action)}
      style={{
        width: '100%', textAlign: 'left',
        background: 'none', border: 'none',
        padding: '0.45rem 0.75rem',
        color: 'var(--text-main)', fontFamily: 'inherit',
        cursor: 'pointer', fontSize: '0.82rem',
        display: 'flex', alignItems: 'center', gap: 10,
        transition: 'background 0.1s',
      }}
      onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(59,130,246,0.10)'}
      onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
    >
      <span style={{ width: 18, fontSize: '0.95rem' }}>{icon}</span>
      <span style={{ flex: 1 }}>{label}</span>
      {kbd && (
        <span style={{
          fontSize: '0.65rem', color: 'var(--text-muted)',
          fontFamily: 'monospace',
          border: '1px solid var(--panel-border)', padding: '0 5px',
          borderRadius: 3,
        }}>{kbd}</span>
      )}
    </button>
  );
}

function MenuDivider() {
  return <div style={{ height: 1, background: 'var(--panel-border)', margin: '4px 0' }} />;
}

export default function TickerContextMenu({ onOpenTicker }) {
  const [state, setState] = useState({ open: false, x: 0, y: 0, ticker: '' });
  const [toast, setToast] = useState(null);
  const menuRef = useRef(null);
  const toastIdRef = useRef(0);

  // Listener global contextmenu : on intercepte UNIQUEMENT si l'élément
  // sous le curseur (ou un ancêtre) porte data-ticker.
  useEffect(() => {
    const onContext = (e) => {
      const el = e.target.closest?.('[data-ticker]');
      if (!el) return;
      const ticker = (el.getAttribute('data-ticker') || '').toUpperCase().trim();
      if (!ticker) return;
      e.preventDefault();
      // Position avec overflow correction.
      const x = Math.min(e.clientX, window.innerWidth  - MENU_W - 8);
      const y = Math.min(e.clientY, window.innerHeight - 260);
      setState({ open: true, x, y, ticker });
    };
    document.addEventListener('contextmenu', onContext);
    return () => document.removeEventListener('contextmenu', onContext);
  }, []);

  // Click outside / Escape ferme.
  useEffect(() => {
    if (!state.open) return;
    const onDown = (e) => {
      if (!menuRef.current?.contains(e.target)) {
        setState(s => ({ ...s, open: false }));
      }
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setState(s => ({ ...s, open: false }));
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown',  onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown',  onKey);
    };
  }, [state.open]);

  const close = () => setState(s => ({ ...s, open: false }));

  const flash = (text, type = 'ok') => {
    toastIdRef.current += 1;
    setToast({ text, type, id: toastIdRef.current });
    setTimeout(() => setToast(null), 2200);
  };

  const exec = async (action) => {
    const t = state.ticker;
    close();
    if (!t) return;
    switch (action) {
      case 'open':
        onOpenTicker?.(t);
        break;
      case 'watch': {
        const r = await apiAddToWatchlist({ ticker: t });
        if (r?.ok) {
          flash(`👁 ${t} ajouté à la watchlist`);
          pushToast(`👁 ${t} ajouté à la watchlist`);
        } else {
          const msg = r?.detail || 'Erreur ajout watchlist';
          flash(msg, 'err');
          pushToast(`❌ ${t} : ${msg}`, 'err');
        }
        break;
      }
      case 'tradingview':
        window.open(`https://www.tradingview.com/symbols/${encodeURIComponent(t)}/`,
                    '_blank', 'noopener,noreferrer');
        break;
      case 'sec':
        window.open(
          `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${encodeURIComponent(t)}&type=&dateb=&owner=include&count=40`,
          '_blank', 'noopener,noreferrer',
        );
        break;
      case 'finviz':
        window.open(`https://finviz.com/quote.ashx?t=${encodeURIComponent(t)}`,
                    '_blank', 'noopener,noreferrer');
        break;
      case 'copy':
        try {
          await navigator.clipboard.writeText(t);
          flash(`📋 ${t} copié`);
        } catch {
          flash('Copie impossible', 'err');
        }
        break;
      default:
        break;
    }
  };

  return (
    <>
      {state.open && (
        <div
          ref={menuRef}
          style={{
            position: 'fixed', left: state.x, top: state.y,
            width: MENU_W, zIndex: 3000,
            background: 'var(--panel-bg)',
            backdropFilter: 'blur(20px)',
            border: '1px solid var(--panel-border)',
            borderRadius: 8,
            boxShadow: '0 18px 50px rgba(0,0,0,0.4)',
            padding: '4px 0',
            fontFamily: 'inherit',
          }}
        >
          <div style={{
            padding: '0.4rem 0.75rem',
            fontFamily: 'monospace', fontWeight: 800, fontSize: '0.85rem',
            color: 'var(--accent-primary)',
            borderBottom: '1px solid var(--panel-border)',
            marginBottom: 4,
          }}>
            {state.ticker}
          </div>
          <MenuItem icon="🎯" label="Ouvrir factsheet"     action="open"        onAction={exec} />
          <MenuItem icon="👁"  label="Ajouter à watchlist"  action="watch"       onAction={exec} />
          <MenuDivider />
          <MenuItem icon="📈" label="TradingView"          action="tradingview" onAction={exec} />
          <MenuItem icon="🏛️" label="SEC EDGAR"             action="sec"         onAction={exec} />
          <MenuItem icon="📊" label="Finviz"               action="finviz"      onAction={exec} />
          <MenuDivider />
          <MenuItem icon="📋" label="Copier le ticker"     action="copy"        onAction={exec} />
        </div>
      )}
      {toast && (
        <div style={{
          position: 'fixed', bottom: '1.5rem', right: '1.5rem',
          zIndex: 3500, padding: '0.6rem 1rem', borderRadius: 8,
          fontSize: '0.85rem', fontWeight: 600,
          background: toast.type === 'err'
            ? 'rgba(239,68,68,0.18)' : 'rgba(34,197,94,0.18)',
          color: toast.type === 'err' ? 'var(--danger)' : 'var(--success)',
          border: `1px solid ${toast.type === 'err'
            ? 'rgba(239,68,68,0.5)' : 'rgba(34,197,94,0.5)'}`,
        }}>
          {toast.text}
        </div>
      )}
    </>
  );
}
