import React, { lazy, Suspense, useEffect, useRef, useState } from 'react';
import CommandPalette from './components/CommandPalette';
import TableKeyNav from './components/common/TableKeyNav';
import TickerContextMenu from './components/common/TickerContextMenu';
import ToastBell from './components/common/ToastBell';
import DataHealthBanner from './components/common/DataHealthBanner';
import MarketClock from './components/common/MarketClock';
import { PageSkeleton } from './components/common/Skeleton';
import TickerAnalysisModal from './components/TickerAnalysisModal';
import { useStatus } from './hooks/useApi';
import { useIsDesktop } from './hooks/useMediaQuery';
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

// Skeleton fallback : remplace l'ancien spinner brutal pour éviter le
// flash entre changements de page (fait paraître l'app plus rapide).
function PageFallback() {
  return (
    <div role="status" aria-live="polite" style={{ padding: '0.5rem 0' }}>
      <span className="sr-only" style={{
        position: 'absolute', width: 1, height: 1, padding: 0, margin: -1,
        overflow: 'hidden', clip: 'rect(0,0,0,0)', whiteSpace: 'nowrap', border: 0,
      }}>Chargement du module…</span>
      <PageSkeleton tiles={4} blockHeight={280} rows={4} />
    </div>
  );
}

class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { hasError: false, error: null, showStack: false }; }
  static getDerivedStateFromError(error) { return { hasError: true, error }; }
  componentDidCatch(error, info) {
    console.error('ErrorBoundary caught:', error, info);
  }
  reset = () => this.setState({ hasError: false, error: null, showStack: false });
  toggleStack = () => this.setState((s) => ({ showStack: !s.showStack }));
  render() {
    if (this.state.hasError) {
      const err = this.state.error;
      return (
        <div className="error-boundary-card" role="alert">
          <div className="error-boundary-head">
            <div className="error-boundary-icon" aria-hidden="true">⚠️</div>
            <div>
              <div className="error-boundary-title">Une erreur est survenue dans ce module</div>
              <div style={{ color: 'var(--text-muted)', fontSize: '0.82rem' }}>
                Le reste de l'application reste utilisable. Tu peux réessayer ou recharger.
              </div>
            </div>
          </div>
          {this.state.showStack && err && (
            <div className="error-boundary-trace">
              {err.toString()}
              {err.stack ? `\n\n${err.stack}` : ''}
            </div>
          )}
          <div className="error-boundary-actions">
            <button type="button" className="btn btn-primary" onClick={this.reset}>
              Réessayer
            </button>
            <button type="button" className="btn" onClick={() => window.location.reload()}>
              Recharger la page
            </button>
            <button type="button" className="btn btn-ghost" onClick={this.toggleStack}>
              {this.state.showStack ? 'Masquer la trace' : 'Voir la trace'}
            </button>
          </div>
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
const SECTION_BY_PAGE = Object.fromEntries(
  NAV_SECTIONS.flatMap(sec => sec.items.map(it => [it.id, sec.label]))
);

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

const SIDEBAR_PREF_KEY = 'pref_sidebar';

function readSidebarPref() {
  try {
    return localStorage.getItem(SIDEBAR_PREF_KEY) || 'expanded';
  } catch { return 'expanded'; }
}

function writeSidebarPref(v) {
  try { localStorage.setItem(SIDEBAR_PREF_KEY, v); } catch { /* private mode */ }
}

export default function App() {
  const [activePage, setActivePage] = useHashRoute('briefing', VALID_PAGES);
  const { theme, density, toggleTheme, toggleDensity } = usePreferences();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteTicker, setPaletteTicker] = useState(null);
  const isDesktop = useIsDesktop();

  // Mode sidebar : 'expanded' | 'collapsed' (desktop) ; 'visible' | 'hidden' (mobile)
  const [sidebarDesktop, setSidebarDesktop] = useState(readSidebarPref); // expanded|collapsed
  const [sidebarMobileOpen, setSidebarMobileOpen] = useState(false);

  // Header sticky : ombre subtile quand on scrolle (Intersection observer
  // serait plus propre mais scroll-listener léger suffit ici).
  const mainRef = useRef(null);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const el = mainRef.current;
    if (!el) return;
    const onScroll = () => setScrolled(el.scrollTop > 4);
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  // Reset scroll quand on change de page (sinon Universe → Briefing garde
  // le scroll d'Universe et on rate le KPI du haut).
  useEffect(() => {
    if (mainRef.current) mainRef.current.scrollTop = 0;
  }, [activePage]);

  // Ferme le drawer mobile quand on change de page.
  useEffect(() => { setSidebarMobileOpen(false); }, [activePage]);

  // Quand on bascule sur desktop, le drawer mobile n'a plus de sens.
  // On dérive `effectiveMobileOpen` plutôt que de muter le state dans
  // un effet (évite un cascading render).
  const effectiveMobileOpen = isDesktop ? false : sidebarMobileOpen;

  const meta = PAGE_META[activePage] || { title: 'SwingQuant', subtitle: '' };
  const sectionLabel = SECTION_BY_PAGE[activePage] || '';

  // Cmd+K / Ctrl+K — palette globale, sauf dans les inputs.
  // Cmd+B — toggle sidebar (collapse/expand) — convention IDE.
  useEffect(() => {
    const onKey = (e) => {
      const isInput = ['INPUT', 'TEXTAREA', 'SELECT'].includes(
        document.activeElement?.tagName || '',
      );
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setPaletteOpen(true);
      } else if ((e.metaKey || e.ctrlKey) && (e.key === 'b' || e.key === 'B')) {
        e.preventDefault();
        if (isDesktop) {
          setSidebarDesktop((s) => {
            const next = s === 'collapsed' ? 'expanded' : 'collapsed';
            writeSidebarPref(next);
            return next;
          });
        } else {
          setSidebarMobileOpen((s) => !s);
        }
      } else if (e.key === '/' && !isInput && !paletteOpen) {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [paletteOpen, isDesktop]);

  // data-sidebar : pilote le CSS (collapsed/hidden).
  const sidebarAttr = !isDesktop
    ? (effectiveMobileOpen ? 'mobile-open' : 'hidden')
    : sidebarDesktop;

  return (
    <ErrorBoundary>
      <a href="#main" className="skip-link">Aller au contenu principal</a>
      <div className="app-container" data-sidebar={sidebarAttr}>
        {/* Overlay mobile (clic ferme le drawer). */}
        <div
          className="sidebar-overlay"
          onClick={() => setSidebarMobileOpen(false)}
          aria-hidden="true"
        />

        {/* ── SIDEBAR ── */}
        <aside className="sidebar" aria-label="Navigation principale">
          {/* Bouton collapse (desktop). */}
          <button
            type="button"
            className="sidebar-collapse-btn"
            onClick={() => {
              setSidebarDesktop((s) => {
                const next = s === 'collapsed' ? 'expanded' : 'collapsed';
                writeSidebarPref(next);
                return next;
              });
            }}
            data-tooltip={sidebarDesktop === 'collapsed' ? 'Étendre (Cmd+B)' : 'Replier (Cmd+B)'}
            aria-label="Replier ou étendre la barre latérale"
          >
            {sidebarDesktop === 'collapsed' ? '›' : '‹'}
          </button>

          <div className="brand">
            <div className="brand-icon" aria-hidden="true">⚡</div>
            <div>
              <div style={{ fontSize: '1.1rem' }}>SwingQuant</div>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 400, letterSpacing: '2px' }}>
                V5 · TITAN
              </div>
            </div>
          </div>

          <nav className="nav-menu" aria-label="Pages">
            {NAV_SECTIONS.map((section, si) => (
              <div key={section.label} style={{ marginTop: si === 0 ? 0 : '0.85rem' }}>
                <div className="nav-section-label">{section.label}</div>
                {section.items.map(n => (
                  <button
                    key={n.id}
                    id={`nav-${n.id}`}
                    type="button"
                    className={`nav-item ${activePage === n.id ? 'active' : ''}`}
                    onClick={() => setActivePage(n.id)}
                    aria-current={activePage === n.id ? 'page' : undefined}
                    title={sidebarDesktop === 'collapsed' ? n.label : undefined}
                  >
                    <span style={{ fontSize: '1.25rem' }} aria-hidden="true">{n.icon}</span>
                    {n.label}
                  </button>
                ))}
              </div>
            ))}
          </nav>

          <div style={{ marginTop: 'auto' }}>
            <button
              type="button"
              className="palette-launch-btn"
              onClick={() => setPaletteOpen(true)}
              title="Palette globale (Cmd+K, Ctrl+K, ou /)"
            >
              <span className="palette-launch-text">🔍 Recherche…</span>
              <kbd>⌘K</kbd>
            </button>

            <PrefsToggles
              theme={theme} density={density}
              onToggleTheme={toggleTheme} onToggleDensity={toggleDensity}
            />

            <MarketClock />
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
        <main className="main-content" id="main" ref={mainRef} tabIndex={-1}>
          <header className={`top-header ${scrolled ? 'scrolled' : ''}`}>
            <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
              <div className="header-context">
                {!isDesktop && (
                  <button
                    type="button"
                    className="mobile-menu-btn"
                    onClick={() => setSidebarMobileOpen(true)}
                    aria-label="Ouvrir le menu"
                    style={{ marginRight: 8 }}
                  >☰</button>
                )}
                {sectionLabel && (
                  <>
                    <span>{sectionLabel}</span>
                    <span className="crumb-sep">›</span>
                  </>
                )}
                <span className="crumb-page">{meta.title}</span>
              </div>
              <h1 className="page-title" style={{ marginTop: 0 }}>{meta.title}</h1>
              <span className="page-subtitle">{meta.subtitle}</span>
              <div className="header-pill-row" style={{ marginTop: 8 }}>
                <MarketClock variant="pill" />
                <VixPill onOpenMacro={() => setActivePage('macro')} />
              </div>
            </div>

            <div className="user-profile" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
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
          onAction={(actionId) => handleGlobalAction(actionId, {
            toggleTheme, toggleDensity, setActivePage,
          })}
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

// Actions globales déclenchables depuis la palette (Cmd+K).
function handleGlobalAction(id, ctx) {
  switch (id) {
    case 'toggle-theme':   ctx.toggleTheme();   break;
    case 'toggle-density': ctx.toggleDensity(); break;
    case 'goto-settings':  ctx.setActivePage('settings'); break;
    case 'goto-briefing':  ctx.setActivePage('briefing'); break;
    case 'goto-proposals': ctx.setActivePage('proposals'); break;
    default: break;
  }
}

function PrefsToggles({ theme, density, onToggleTheme, onToggleDensity }) {
  return (
    <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
      <button
        type="button"
        className="btn btn-sm"
        onClick={onToggleTheme}
        style={{ flex: 1 }}
        title={`Thème : ${theme} (cliquer pour basculer)`}
        aria-label="Basculer thème clair/sombre"
      >
        <span aria-hidden="true">{theme === 'light' ? '☀️' : '🌙'}</span>
        <span className="prefs-toggle-text" style={{ textTransform: 'capitalize' }}>{theme}</span>
      </button>
      <button
        type="button"
        className="btn btn-sm"
        onClick={onToggleDensity}
        style={{ flex: 1 }}
        title={`Densité : ${density} (cliquer pour basculer)`}
        aria-label="Basculer densité compact/confortable"
      >
        <span aria-hidden="true">{density === 'compact' ? '▤' : '▦'}</span>
        <span className="prefs-toggle-text">{density === 'compact' ? 'Compact' : 'Cosy'}</span>
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

// Pill VIX dans le header — couleur dynamique + click pour aller voir Macro.
function VixPill({ onOpenMacro }) {
  const { data: status } = useStatus({ refetchInterval: 30_000 });
  const vix = status?.vix;
  if (vix == null) return null;
  const tone = vix >= 30 ? 'danger' : vix >= 20 ? 'warning' : 'success';
  return (
    <span className="header-pill" data-tone={tone} title="VIX (cliquer pour voir Macro)">
      <strong>VIX</strong>
      <button type="button" className="pill-link" onClick={onOpenMacro}>
        {vix.toFixed(1)}
      </button>
    </span>
  );
}
