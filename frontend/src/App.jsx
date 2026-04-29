import React, { lazy, Suspense, useEffect, useState } from 'react';
import CommandPalette from './components/CommandPalette';
import TableKeyNav from './components/common/TableKeyNav';
import TickerContextMenu from './components/common/TickerContextMenu';
import ToastBell from './components/common/ToastBell';
import DataHealthBanner from './components/common/DataHealthBanner';
import TickerAnalysisModal from './components/TickerAnalysisModal';
import { useStatus } from './hooks/useApi';
import { useHashRoute, usePreferences } from './utils/preferences';

// Code-splitting par page — Vite génère un chunk séparé par lazy() au build,
// si bien qu'un utilisateur qui ouvre Universe ne télécharge pas le JS des
// autres pages tant qu'il ne clique pas dessus.
const BriefingPage        = lazy(() => import('./components/BriefingPage'));
const WatchlistPage       = lazy(() => import('./components/WatchlistPage'));
const CalendarPage        = lazy(() => import('./components/CalendarPage'));
const NewsFirehosePage    = lazy(() => import('./components/NewsFirehosePage'));
const AttributionPage     = lazy(() => import('./components/AttributionPage'));
const SettingsPage        = lazy(() => import('./components/SettingsPage'));
const UniverseManagerPage = lazy(() => import('./components/UniverseManagerPage'));
const SectorsPage         = lazy(() => import('./components/SectorsPage'));
const PortfolioPage       = lazy(() => import('./components/PortfolioPage'));
const ProposalsPage       = lazy(() => import('./components/ProposalsPage'));
const PerformancePage     = lazy(() => import('./components/PerformancePage'));
const TickerDetailPage    = lazy(() => import('./components/TickerDetailPage'));
const DataHealthPage      = lazy(() => import('./components/DataHealthPage'));
const RiskMonitorPage     = lazy(() => import('./components/RiskMonitorPage'));
const MacroCalendarPage   = lazy(() => import('./components/MacroCalendarPage'));
const AuditPage           = lazy(() => import('./components/AuditPage'));

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

const NAV_SECTIONS = [
  {
    label: 'Pilotage',
    items: [
      { id: 'briefing',    label: 'Briefing',      icon: '☀️' },
      { id: 'proposals',   label: 'Propositions',  icon: '📬' },
      { id: 'portfolio',   label: 'Portfolio',     icon: '📊' },
    ],
  },
  {
    label: 'Découverte',
    items: [
      { id: 'watchlist',   label: 'Watchlist',     icon: '👁' },
      { id: 'universe',    label: 'Univers',       icon: '🌐' },
      { id: 'sectors',     label: 'Secteurs',      icon: '🏛' },
      { id: 'ticker',      label: 'Ticker Detail', icon: '🎯' },
    ],
  },
  {
    label: 'Catalyseurs',
    items: [
      { id: 'calendar',    label: 'Catalysts',     icon: '🗓' },
      { id: 'news',        label: 'News',          icon: '📰' },
      { id: 'macro',       label: 'Macro',         icon: '📅' },
    ],
  },
  {
    label: 'Santé',
    items: [
      { id: 'performance', label: 'Performance',   icon: '📈' },
      { id: 'attribution', label: 'Attribution',   icon: '🎲' },
      { id: 'risk',        label: 'Risk Monitor',  icon: '⚠️' },
      { id: 'datahealth',  label: 'Data Health',   icon: '🩺' },
      { id: 'audit',       label: 'Audit',         icon: '🔍' },
    ],
  },
  {
    label: 'Système',
    items: [
      { id: 'settings',    label: 'Préférences',   icon: '⚙️' },
    ],
  },
];

const NAV_ITEMS = NAV_SECTIONS.flatMap(s => s.items);

const PAGE_META = {
  briefing:    { title: 'Briefing du jour',              subtitle: 'Régime · Macro · Action requise · Positions à surveiller' },
  watchlist:   { title: 'Watchlist & Notes',             subtitle: 'Tickers observés · Thèses & rappels personnels' },
  universe:    { title: 'Univers Quantamental',          subtitle: 'Smart Beta · Rotation Sectorielle · Fondamentaux yfinance' },
  sectors:     { title: 'Rotation Sectorielle',          subtitle: '11 secteurs GICS · Momentum 6M · Rotation Score composite' },
  portfolio:   { title: 'Portfolio & Journal',           subtitle: 'Données réelles — trade_journal.csv' },
  proposals:   { title: 'Propositions auto · Veto humain', subtitle: 'Trades suggérés par TITAN — approuver ou rejeter' },
  performance: { title: 'Performance & Métriques',       subtitle: 'Sharpe · Sortino · Calmar · DD · Expectancy · Distribution PnL' },
  attribution: { title: 'Performance Attribution',       subtitle: 'Win rate par bucket de score TITAN à l\'entrée — calibration du moteur' },
  ticker:      { title: 'Ticker Detail · Score history', subtitle: "Évolution scores TITAN d'un ticker via universe_history" },
  datahealth:  { title: 'Data Health · Providers + cache', subtitle: 'Santé providers + cache fundamentals + fields manquants' },
  risk:        { title: 'Risk Monitor',                  subtitle: 'Budget · VIX · concentration · Kelly' },
  calendar:    { title: 'Catalyst Calendar',             subtitle: 'Earnings (positions + watchlist) + Macro consolidés' },
  news:        { title: 'News firehose',                 subtitle: 'Flux consolidé positions + watchlist · Finnhub' },
  macro:       { title: 'Calendrier Macro',              subtitle: 'FOMC · CPI · NFP — zones de blackout J-1' },
  audit:       { title: 'Audit · Survivorship + WFO',    subtitle: 'Registry delisted · Poids OOS vs prod · IC test history' },
  settings:    { title: 'Préférences',                   subtitle: 'Apparence · API Token · Defaults Propositions' },
};

const VALID_PAGES = NAV_ITEMS.map(n => n.id);

export default function App() {
  const [activePage, setActivePage] = useHashRoute('briefing', VALID_PAGES);
  const { theme, density, toggleTheme, toggleDensity } = usePreferences();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteTicker, setPaletteTicker] = useState(null);
  const meta = PAGE_META[activePage] || { title: 'SwingQuant', subtitle: '' };

  // Cmd+K / Ctrl+K — ouvre la palette globale, sauf dans les inputs.
  useEffect(() => {
    const onKey = (e) => {
      const isInput = ['INPUT', 'TEXTAREA', 'SELECT'].includes(
        document.activeElement?.tagName || '',
      );
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setPaletteOpen(true);
      } else if (e.key === '/' && !isInput && !paletteOpen) {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [paletteOpen]);

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
            {NAV_SECTIONS.map((section, si) => (
              <div key={section.label}
                   style={{ marginTop: si === 0 ? 0 : '0.85rem' }}>
                <div style={{
                  fontSize: '0.62rem', fontWeight: 700,
                  letterSpacing: '0.12em', textTransform: 'uppercase',
                  color: 'var(--text-muted)', opacity: 0.6,
                  padding: '0.25rem 1rem 0.4rem',
                }}>
                  {section.label}
                </div>
                {section.items.map(n => (
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
              </div>
            ))}
          </nav>

          <div style={{ marginTop: 'auto' }}>
            <button
              type="button"
              onClick={() => setPaletteOpen(true)}
              style={{
                width: '100%', padding: '0.5rem 0.7rem', marginBottom: 8,
                background: 'rgba(255,255,255,0.04)',
                border: '1px solid var(--panel-border)',
                borderRadius: 8, color: 'var(--text-muted)',
                cursor: 'pointer', fontSize: '0.78rem', fontWeight: 500,
                fontFamily: 'inherit', display: 'flex',
                alignItems: 'center', justifyContent: 'space-between', gap: 6,
              }}
              title="Palette globale (Cmd+K, Ctrl+K, ou /)"
            >
              <span>🔍 Recherche…</span>
              <span style={{
                fontSize: '0.65rem', fontFamily: 'monospace',
                border: '1px solid var(--panel-border)',
                padding: '1px 5px', borderRadius: 3,
              }}>⌘K</span>
            </button>
            <PrefsToggles
              theme={theme} density={density}
              onToggleTheme={toggleTheme} onToggleDensity={toggleDensity}
            />
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
            <div className="user-profile" style={{ display: 'flex',
                                                    alignItems: 'center',
                                                    gap: 12 }}>
              <ToastBell />
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
              {activePage === 'briefing'    && <BriefingPage onNavigate={setActivePage} />}
              {activePage === 'watchlist'   && <WatchlistPage />}
              {activePage === 'universe'    && <UniverseManagerPage />}
              {activePage === 'sectors'     && <SectorsPage />}
              {activePage === 'portfolio'   && <PortfolioPage />}
              {activePage === 'proposals'   && <ProposalsPage />}
              {activePage === 'performance' && <PerformancePage />}
              {activePage === 'ticker'      && <TickerDetailPage />}
              {activePage === 'datahealth'  && <DataHealthPage />}
              {activePage === 'risk'        && <RiskMonitorPage />}
              {activePage === 'calendar'    && <CalendarPage />}
              {activePage === 'news'        && <NewsFirehosePage />}
              {activePage === 'macro'       && <MacroCalendarPage />}
              {activePage === 'attribution' && <AttributionPage />}
              {activePage === 'audit'       && <AuditPage />}
              {activePage === 'settings'    && <SettingsPage />}
            </Suspense>
          </ErrorBoundary>
        </main>

        <CommandPalette
          open={paletteOpen}
          onClose={() => setPaletteOpen(false)}
          onNavigate={(id) => setActivePage(id)}
          onOpenTicker={(t) => setPaletteTicker(t)}
        />
        <TickerContextMenu
          onOpenTicker={(t) => setPaletteTicker(t)}
          onNavigate={setActivePage}
        />
        <TableKeyNav onOpen={(t) => setPaletteTicker(t)} />
        {paletteTicker && (
          <TickerAnalysisModal
            ticker={paletteTicker}
            onClose={() => setPaletteTicker(null)}
          />
        )}
      </div>
    </ErrorBoundary>
  );
}

function PrefsToggles({ theme, density, onToggleTheme, onToggleDensity }) {
  const btnStyle = {
    flex: 1,
    padding: '0.45rem 0.5rem',
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid var(--panel-border)',
    borderRadius: 8,
    color: 'var(--text-muted)',
    cursor: 'pointer',
    fontSize: '0.72rem',
    fontWeight: 600,
    fontFamily: 'inherit',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    transition: 'all 0.15s',
  };
  return (
    <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
      <button
        type="button"
        onClick={onToggleTheme}
        style={btnStyle}
        title={`Thème : ${theme} (cliquer pour basculer)`}
        aria-label="Basculer thème clair/sombre"
      >
        <span>{theme === 'light' ? '☀️' : '🌙'}</span>
        <span style={{ textTransform: 'capitalize' }}>{theme}</span>
      </button>
      <button
        type="button"
        onClick={onToggleDensity}
        style={btnStyle}
        title={`Densité : ${density} (cliquer pour basculer)`}
        aria-label="Basculer densité compact/confortable"
      >
        <span>{density === 'compact' ? '▤' : '▦'}</span>
        <span>{density === 'compact' ? 'Compact' : 'Cosy'}</span>
      </button>
    </div>
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
