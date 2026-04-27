import React, { useState, lazy, Suspense } from 'react';
import DataHealthBanner from './components/common/DataHealthBanner';
import { useStatus } from './hooks/useApi';

// Code-splitting par page — Vite génère un chunk séparé par lazy() au build,
// si bien qu'un utilisateur qui ouvre Universe ne télécharge pas le JS des
// autres pages tant qu'il ne clique pas dessus.
const UniverseManagerPage = lazy(() => import('./components/UniverseManagerPage'));
const SectorsPage         = lazy(() => import('./components/SectorsPage'));
const PortfolioPage       = lazy(() => import('./components/PortfolioPage'));
const ProposalsPage       = lazy(() => import('./components/ProposalsPage'));
const PerformancePage     = lazy(() => import('./components/PerformancePage'));
const TickerDetailPage    = lazy(() => import('./components/TickerDetailPage'));
const DataHealthPage      = lazy(() => import('./components/DataHealthPage'));
const RiskMonitorPage     = lazy(() => import('./components/RiskMonitorPage'));
const MacroCalendarPage   = lazy(() => import('./components/MacroCalendarPage'));

function PageFallback() {
  return (
    <div className="loading-pulse" style={{ padding: '3rem', textAlign: 'center' }}>
      <div className="spinner" />
      <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.75rem' }}>
        Chargement du module…
      </p>
    </div>
  );
}

class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { hasError: false, error: null }; }
  static getDerivedStateFromError(error) { return { hasError: true, error }; }
  componentDidCatch(error, info) { console.error('ErrorBoundary caught:', error, info); }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: '2rem', color: '#ef4444', fontFamily: 'monospace', background: '#0d0d1a', borderRadius: '12px', margin: '1rem' }}>
          <h2>⚠️ Erreur composant</h2>
          <pre style={{ fontSize: '0.8rem', color: '#a6adc8', whiteSpace: 'pre-wrap' }}>{this.state.error?.toString()}</pre>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            style={{ marginTop: '1rem', padding: '0.5rem 1rem', background: '#3b82f6', border: 'none', borderRadius: '8px', color: 'white', cursor: 'pointer' }}
          >
            Réessayer
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const NAV_ITEMS = [
  { id: 'universe',    label: 'Univers',       icon: '🌐' },
  { id: 'sectors',     label: 'Secteurs',      icon: '🏛' },
  { id: 'portfolio',   label: 'Portfolio',     icon: '📊' },
  { id: 'proposals',   label: 'Propositions',  icon: '📬' },
  { id: 'performance', label: 'Performance',   icon: '📈' },
  { id: 'ticker',      label: 'Ticker Detail', icon: '🎯' },
  { id: 'datahealth',  label: 'Data Health',   icon: '🩺' },
  { id: 'risk',        label: 'Risk Monitor',  icon: '⚠️' },
  { id: 'macro',       label: 'Macro',         icon: '📅' },
];

const PAGE_META = {
  universe:    { title: 'Univers Quantamental',          subtitle: 'Smart Beta · Rotation Sectorielle · Fondamentaux yfinance' },
  sectors:     { title: 'Rotation Sectorielle',          subtitle: '11 secteurs GICS · Momentum 6M · Rotation Score composite' },
  portfolio:   { title: 'Portfolio & Journal',           subtitle: 'Données réelles — trade_journal.csv' },
  proposals:   { title: 'Propositions auto · Veto humain', subtitle: 'Trades suggérés par TITAN — approuver ou rejeter' },
  performance: { title: 'Performance & Métriques',       subtitle: 'Sharpe · Sortino · Calmar · DD · Expectancy · Distribution PnL' },
  ticker:      { title: 'Ticker Detail · Score history', subtitle: "Évolution scores TITAN d'un ticker via universe_history" },
  datahealth:  { title: 'Data Health · Providers + cache', subtitle: 'Santé providers + cache fundamentals + fields manquants' },
  risk:        { title: 'Risk Monitor',                  subtitle: 'Budget · VIX · concentration · Kelly' },
  macro:       { title: 'Calendrier Macro',              subtitle: 'FOMC · CPI · NFP — zones de blackout J-1' },
};

export default function App() {
  const [activePage, setActivePage] = useState('universe');
  const meta = PAGE_META[activePage] || { title: 'SwingQuant', subtitle: '' };

  return (
    <ErrorBoundary>
      <div className="app-container">
        {/* ── SIDEBAR ── */}
        <aside className="sidebar">
          <div className="brand">
            <div className="brand-icon">⚡</div>
            <div>
              <div style={{ fontSize: '1.1rem' }}>SwingQuant</div>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 400, letterSpacing: '2px' }}>V5 · TITAN</div>
            </div>
          </div>

          <nav className="nav-menu">
            {NAV_ITEMS.map(n => (
              <button
                key={n.id}
                id={`nav-${n.id}`}
                className={`nav-item ${activePage === n.id ? 'active' : ''}`}
                onClick={() => setActivePage(n.id)}
              >
                <span style={{ fontSize: '1.25rem' }}>{n.icon}</span>
                {n.label}
              </button>
            ))}
          </nav>

          <div style={{ marginTop: 'auto' }}>
            <LiveStatusBadge />
            <div className="sidebar-account">
              <div className="sa-label">SwingQuant TITAN</div>
              <div className="sa-value">Quantamental</div>
              <div className="sa-risk">Long-Term · Multi-Factor</div>
              <div className="sa-mode">
                <span className="mode-dot paper" />
                Mode Paper
              </div>
            </div>
          </div>
        </aside>

        {/* ── MAIN ── */}
        <main className="main-content">
          <header className="top-header">
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              <h1 className="page-title">{meta.title}</h1>
              <span className="page-subtitle">{meta.subtitle}</span>
            </div>
            <div className="user-profile">
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontWeight: 600 }}>Prop Trader</div>
                <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Alpaca Markets</div>
              </div>
              <div className="avatar" />
            </div>
          </header>

          <DataHealthBanner onShowDetails={() => setActivePage('datahealth')} />

          <ErrorBoundary>
            <Suspense fallback={<PageFallback />}>
              {activePage === 'universe'    && <UniverseManagerPage />}
              {activePage === 'sectors'     && <SectorsPage />}
              {activePage === 'portfolio'   && <PortfolioPage />}
              {activePage === 'proposals'   && <ProposalsPage />}
              {activePage === 'performance' && <PerformancePage />}
              {activePage === 'ticker'      && <TickerDetailPage />}
              {activePage === 'datahealth'  && <DataHealthPage />}
              {activePage === 'risk'        && <RiskMonitorPage />}
              {activePage === 'macro'       && <MacroCalendarPage />}
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>
    </ErrorBoundary>
  );
}

function LiveStatusBadge() {
  const { data: status, isError } = useStatus({ refetchInterval: 15_000 });
  const ok = status && !status.detail;

  if (isError || !ok) {
    return (
      <div className="live-badge" style={{ marginBottom: '0.75rem', borderColor: 'rgba(239,68,68,0.2)' }}>
        <span className="mode-dot" style={{ background: 'var(--text-muted)', boxShadow: 'none' }} />
        <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>API hors ligne</span>
      </div>
    );
  }

  return (
    <div className="live-badge" style={{ marginBottom: '0.75rem' }}>
      <span className="mode-dot" style={{ background: 'var(--success)', boxShadow: '0 0 6px var(--success)' }} />
      <span style={{ fontSize: '0.78rem', color: 'var(--success)' }}>API connectée</span>
      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginLeft: 'auto' }}>
        VIX {status.vix ?? '—'}
      </span>
    </div>
  );
}
