// TopHeader — header sticky : breadcrumb + titres + pills marché + profil.

import MarketClock from '../common/MarketClock';
import ToastBell from '../common/ToastBell';
import { PAGE_META, SECTION_BY_PAGE } from '../../config/nav';
import VixPill from './VixPill';

export default function TopHeader({
  activePage,
  scrolled,
  isDesktop,
  onOpenMobileMenu,
  onOpenMacro,
}) {
  const meta = PAGE_META[activePage] || { title: 'SwingQuant', subtitle: '' };
  const sectionLabel = SECTION_BY_PAGE[activePage] || '';

  return (
    <header className={`top-header ${scrolled ? 'scrolled' : ''}`}>
      <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
        <div className="header-context">
          {!isDesktop && (
            <button
              type="button"
              className="mobile-menu-btn"
              onClick={onOpenMobileMenu}
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
          <VixPill onOpenMacro={onOpenMacro} />
        </div>
      </div>

      <div
        className="user-profile"
        style={{ display: 'flex', alignItems: 'center', gap: 12 }}
      >
        <ToastBell />
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontWeight: 600 }}>Prop Trader</div>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
            Alpaca Markets
          </div>
        </div>
        <div className="avatar" />
      </div>
    </header>
  );
}
