// App.jsx — shell de l'application : layout (sidebar + main), routing
// par hash, raccourcis globaux, ErrorBoundary, Suspense.
//
// Toute la logique métier est dans les composants enfants. La config de
// navigation vit dans src/config/nav.js. Les sous-composants du shell
// (sidebar, header, badges, prefs) sont dans src/components/shell/.

import { lazy, Suspense, useEffect, useRef, useState } from 'react';

import CommandPalette from './components/CommandPalette';
import DataHealthBanner from './components/common/DataHealthBanner';
import ErrorBoundary from './components/common/ErrorBoundary';
import { PageSkeleton } from './components/common/Skeleton';
import TableKeyNav from './components/common/TableKeyNav';
import TickerAnalysisModal from './components/TickerAnalysisModal';
import TickerContextMenu from './components/common/TickerContextMenu';
import Sidebar from './components/shell/Sidebar';
import TopHeader from './components/shell/TopHeader';
import { VALID_PAGES } from './config/nav';
import { useIsDesktop } from './hooks/useMediaQuery';
import { readString, writeString } from './utils/storage';
import { useHashRoute, usePreferences } from './utils/preferences';

// Code-splitting par page — Vite génère un chunk séparé par lazy().
const BriefingPage        = lazy(() => import('./components/BriefingPage'));
const WatchlistPage       = lazy(() => import('./components/WatchlistPage'));
const CalendarPage        = lazy(() => import('./components/CalendarPage'));
const NewsFirehosePage    = lazy(() => import('./components/NewsFirehosePage'));
const AttributionPage     = lazy(() => import('./components/AttributionPage'));
const SettingsPage        = lazy(() => import('./components/SettingsPage'));
const UniverseManagerPage = lazy(() => import('./components/UniverseManagerPage'));
const SectorsPage         = lazy(() => import('./components/SectorsPage'));
const PortfolioPage       = lazy(() => import('./components/PortfolioPage'));
const MyPortfolioPage     = lazy(() => import('./components/MyPortfolioPage'));
const ProposalsPage       = lazy(() => import('./components/ProposalsPage'));
const PerformancePage     = lazy(() => import('./components/PerformancePage'));
const TickerDetailPage    = lazy(() => import('./components/TickerDetailPage'));
const ComparePage         = lazy(() => import('./components/ComparePage'));
const DataHealthPage      = lazy(() => import('./components/DataHealthPage'));
const RiskMonitorPage     = lazy(() => import('./components/RiskMonitorPage'));
const MacroCalendarPage   = lazy(() => import('./components/MacroCalendarPage'));
const AuditPage           = lazy(() => import('./components/AuditPage'));

// Mapping page → composant. Centralisé ici pour éviter une chaîne de &&
// dans le JSX (et pour qu'ajouter une page = 1 ligne).
const PAGES = {
  briefing:    BriefingPage,
  watchlist:   WatchlistPage,
  universe:    UniverseManagerPage,
  sectors:     SectorsPage,
  portfolio:   PortfolioPage,
  my_portfolio: MyPortfolioPage,
  proposals:   ProposalsPage,
  performance: PerformancePage,
  ticker:      TickerDetailPage,
  compare:     ComparePage,
  datahealth:  DataHealthPage,
  risk:        RiskMonitorPage,
  calendar:    CalendarPage,
  news:        NewsFirehosePage,
  macro:       MacroCalendarPage,
  attribution: AttributionPage,
  audit:       AuditPage,
  settings:    SettingsPage,
};

const SIDEBAR_PREF_KEY = 'pref_sidebar';

function PageFallback() {
  return (
    <div role="status" aria-live="polite" style={{ padding: '0.5rem 0' }}>
      <span style={{
        position: 'absolute', width: 1, height: 1, padding: 0, margin: -1,
        overflow: 'hidden', clip: 'rect(0,0,0,0)', whiteSpace: 'nowrap', border: 0,
      }}>Chargement du module…</span>
      <PageSkeleton tiles={4} blockHeight={280} rows={4} />
    </div>
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

export default function App() {
  const [activePage, setActivePageRaw, routeParam] = useHashRoute('briefing', VALID_PAGES);
  const { theme, density, toggleTheme, toggleDensity } = usePreferences();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteTicker, setPaletteTicker] = useState(null);
  const isDesktop = useIsDesktop();

  // Mode sidebar : 'expanded' | 'collapsed' (desktop) ; 'visible' | 'hidden' (mobile)
  const [sidebarDesktop, setSidebarDesktop] = useState(() =>
    readString(SIDEBAR_PREF_KEY, 'expanded'),
  );
  const [sidebarMobileOpen, setSidebarMobileOpen] = useState(false);

  // Wrapper navigation : ferme le drawer mobile dans le même tick qu'un
  // changement de page (évite un setState dans un useEffect → cascade render).
  // `param` : sous-route optionnelle (ex: ticker) portée par le hash, voir
  // useHashRoute — transparent pour les appelants qui ne passent qu'un id.
  const setActivePage = (id, param) => {
    setSidebarMobileOpen(false);
    setActivePageRaw(id, param);
  };

  // Header sticky : ombre subtile quand on scrolle.
  const mainRef = useRef(null);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const el = mainRef.current;
    if (!el) return undefined;
    const onScroll = () => setScrolled(el.scrollTop > 4);
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  // Reset scroll quand on change de page.
  useEffect(() => {
    if (mainRef.current) mainRef.current.scrollTop = 0;
  }, [activePage]);

  // Quand on bascule sur desktop, le drawer mobile n'a plus de sens.
  // On dérive plutôt que de muter le state dans un effet.
  const effectiveMobileOpen = isDesktop ? false : sidebarMobileOpen;

  const toggleSidebarDesktop = () => {
    setSidebarDesktop((s) => {
      const next = s === 'collapsed' ? 'expanded' : 'collapsed';
      writeString(SIDEBAR_PREF_KEY, next);
      return next;
    });
  };

  // Cmd+K palette · Cmd+B sidebar · / palette (hors input)
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
        if (isDesktop) toggleSidebarDesktop();
        else           setSidebarMobileOpen((s) => !s);
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

  const ActivePageComponent = PAGES[activePage] || BriefingPage;

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

        <Sidebar
          activePage={activePage}
          setActivePage={setActivePage}
          collapsed={sidebarDesktop === 'collapsed'}
          onToggleCollapse={toggleSidebarDesktop}
          theme={theme}
          density={density}
          onToggleTheme={toggleTheme}
          onToggleDensity={toggleDensity}
          onOpenPalette={() => setPaletteOpen(true)}
        />

        <main className="main-content" id="main" ref={mainRef} tabIndex={-1}>
          <TopHeader
            activePage={activePage}
            scrolled={scrolled}
            isDesktop={isDesktop}
            onOpenMobileMenu={() => setSidebarMobileOpen(true)}
            onOpenMacro={() => setActivePage('macro')}
          />

          <DataHealthBanner onShowDetails={() => setActivePage('datahealth')} />

          <ErrorBoundary>
            <Suspense fallback={<PageFallback />}>
              <ActivePageComponent
                onNavigate={setActivePage}
                routeParam={routeParam}
              />
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
