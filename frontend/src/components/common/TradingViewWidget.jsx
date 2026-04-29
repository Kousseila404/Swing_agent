// TradingViewWidget — embed gratuit du widget Advanced Chart de TradingView.
//
// 100% client-side, aucune clé API. Le script `tv.js` est injecté à la
// volée et la classe `TradingView.widget` est appelée sur un container
// dédié. Si l'utilisateur est connecté à TradingView dans un autre onglet
// du même navigateur, ses indicateurs/templates persistent.
//
// Réf : https://www.tradingview.com/widget/advanced-chart/
//
// On reste simple : 1 container par ticker, theme synchronisé avec
// data-theme du <html>, hauteur paramétrable.

import { useEffect, useId, useRef } from 'react';

const TV_SCRIPT_SRC = 'https://s3.tradingview.com/tv.js';

let _scriptPromise = null;
function loadTvScript() {
  if (typeof window === 'undefined') return Promise.resolve();
  if (window.TradingView) return Promise.resolve();
  if (_scriptPromise) return _scriptPromise;
  _scriptPromise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = TV_SCRIPT_SRC;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = (e) => { _scriptPromise = null; reject(e); };
    document.head.appendChild(s);
  });
  return _scriptPromise;
}

// TradingView attend des symboles préfixés (NASDAQ:AAPL). Pour les tickers
// US qu'on ne sait pas mapper, on laisse TradingView résoudre lui-même.
function _resolveSymbol(ticker) {
  if (!ticker) return '';
  return ticker.toUpperCase().trim();
}

export default function TradingViewWidget({ ticker, height = 420 }) {
  const containerRef = useRef(null);
  const widgetRef = useRef(null);
  const id = useId().replace(/[:]/g, '_');
  const containerId = `tv_${id}`;

  useEffect(() => {
    if (!ticker) return;
    let cancelled = false;

    loadTvScript()
      .then(() => {
        if (cancelled || !containerRef.current || !window.TradingView) return;
        // Clear any previous widget DOM (re-render on ticker change).
        containerRef.current.innerHTML = '';
        const theme = document.documentElement.getAttribute('data-theme') === 'light'
          ? 'light' : 'dark';

        widgetRef.current = new window.TradingView.widget({
          container_id: containerId,
          symbol: _resolveSymbol(ticker),
          interval: 'D',
          timezone: 'Etc/UTC',
          theme,
          style: '1',           // candles
          locale: 'fr',
          toolbar_bg: 'rgba(0,0,0,0)',
          enable_publishing: false,
          hide_top_toolbar: false,
          hide_legend: false,
          save_image: false,
          allow_symbol_change: true,
          autosize: true,
          studies: ['MASimple@tv-basicstudies'],
          backgroundColor: 'rgba(0,0,0,0)',
          gridColor: 'rgba(255,255,255,0.04)',
        });
      })
      .catch((e) => {
        if (cancelled) return;
        if (containerRef.current) {
          containerRef.current.innerHTML = '<div style="padding:1rem;color:var(--text-muted);font-size:0.78rem">Impossible de charger TradingView (réseau bloqué ?)</div>';
        }
        console.warn('[TradingViewWidget] script load failed', e);
      });

    return () => {
      cancelled = true;
      try {
        widgetRef.current = null;
        if (containerRef.current) containerRef.current.innerHTML = '';
      } catch { /* noop */ }
    };
  }, [ticker, containerId]);

  return (
    <div
      ref={containerRef}
      id={containerId}
      style={{
        width: '100%', height,
        borderRadius: 8,
        overflow: 'hidden',
        background: 'var(--bg-tertiary)',
        border: '1px solid var(--border)',
      }}
    />
  );
}
