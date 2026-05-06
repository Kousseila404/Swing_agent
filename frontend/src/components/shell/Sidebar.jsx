// Sidebar — colonne gauche (nav, market clock, badges, prefs).
//
// Découplée d'App.jsx pour que le shell reste lisible. Reçoit en props
// tout ce qui dépend de App : page active, mode collapsed, callbacks.

import MarketClock from '../common/MarketClock';
import { NAV_SECTIONS } from '../../config/nav';
import PrefsToggles from './PrefsToggles';
import LiveStatusBadge from './LiveStatusBadge';

export default function Sidebar({
  activePage,
  setActivePage,
  collapsed,
  onToggleCollapse,
  theme,
  density,
  onToggleTheme,
  onToggleDensity,
  onOpenPalette,
}) {
  return (
    <aside className="sidebar" aria-label="Navigation principale">
      {/* Bouton collapse (desktop). */}
      <button
        type="button"
        className="sidebar-collapse-btn"
        onClick={onToggleCollapse}
        data-tooltip={collapsed ? 'Étendre (Cmd+B)' : 'Replier (Cmd+B)'}
        aria-label="Replier ou étendre la barre latérale"
      >
        {collapsed ? '›' : '‹'}
      </button>

      <div className="brand">
        <div className="brand-icon" aria-hidden="true">⚡</div>
        <div>
          <div style={{ fontSize: '1.1rem' }}>SwingQuant</div>
          <div
            style={{
              fontSize: '0.7rem',
              color: 'var(--text-muted)',
              fontWeight: 400,
              letterSpacing: '2px',
            }}
          >
            V5 · TITAN
          </div>
        </div>
      </div>

      <nav className="nav-menu" aria-label="Pages">
        {NAV_SECTIONS.map((section, si) => (
          <div key={section.label} style={{ marginTop: si === 0 ? 0 : '0.85rem' }}>
            <div className="nav-section-label">{section.label}</div>
            {section.items.map((n) => (
              <button
                key={n.id}
                id={`nav-${n.id}`}
                type="button"
                className={`nav-item ${activePage === n.id ? 'active' : ''}`}
                onClick={() => setActivePage(n.id)}
                aria-current={activePage === n.id ? 'page' : undefined}
                title={collapsed ? n.label : undefined}
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
          onClick={onOpenPalette}
          title="Palette globale (Cmd+K, Ctrl+K, ou /)"
        >
          <span className="palette-launch-text">🔍 Recherche…</span>
          <kbd>⌘K</kbd>
        </button>

        <PrefsToggles
          theme={theme}
          density={density}
          onToggleTheme={onToggleTheme}
          onToggleDensity={onToggleDensity}
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
  );
}
