// Layout primitives partagées entre les sections du TickerAnalysisModal.
//
// Section : titre uppercase + souligné fin (style "fiche technique")
// KV      : key/value vertical (label muted petit + value monospace)
// Grid    : grille responsive (cols configurable, défaut 4)

export function Section({ title, children }) {
  return (
    <div style={{ marginBottom: '0.8rem' }}>
      <h3
        style={{
          fontSize: '0.7rem',
          fontWeight: 800,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: 'var(--text-muted)',
          marginBottom: '0.4rem',
          borderBottom: '1px solid var(--border)',
          paddingBottom: '0.25rem',
        }}
      >
        {title}
      </h3>
      {children}
    </div>
  );
}

export function KV({ label, value, hint, tone }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0 }}>
      <span
        style={{
          fontSize: '0.62rem',
          color: 'var(--text-muted)',
          letterSpacing: '0.03em',
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontSize: '0.85rem',
          fontFamily: 'monospace',
          fontWeight: 600,
          color: tone || 'var(--text-primary)',
        }}
        title={hint || ''}
      >
        {value}
      </span>
    </div>
  );
}

export function Grid({ children, cols = 4, minColWidth = 130 }) {
  // `cols` reste accepté (compat call sites) mais n'est plus un compte figé —
  // repeat(N, 1fr) débordait sur mobile (4 colonnes fixes dans un modal de
  // ~340px = colonnes ~55px, illisible). auto-fit laisse le nombre de
  // colonnes réel dépendre de la largeur dispo : ~4 sur desktop (modal
  // ~600-680px), 2 sur téléphone, sans media query dédiée par section.
  void cols;
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(auto-fit, minmax(${minColWidth}px, 1fr))`,
        gap: '0.6rem 1rem',
      }}
    >
      {children}
    </div>
  );
}
