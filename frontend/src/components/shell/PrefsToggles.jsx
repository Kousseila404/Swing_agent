// PrefsToggles — boutons thème + densité dans la sidebar (footer).

export default function PrefsToggles({ theme, density, onToggleTheme, onToggleDensity }) {
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
