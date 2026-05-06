// EmptyState — primitive UI pour signaler "aucune donnée".
//
// Props :
//   icon  : emoji ou ReactNode (par défaut "📭")
//   title : titre court
//   desc  : description (optionnelle)
//   cta   : { label, onClick } pour proposer une action
//   compact : version réduite (less padding)

export default function EmptyState({
  icon = '📭',
  title,
  desc,
  cta,
  compact = false,
  children,
}) {
  return (
    <div
      className="empty-state"
      style={compact ? { padding: '1.5rem 1rem' } : undefined}
      role="status"
    >
      <div className="empty-state-icon" aria-hidden="true">{icon}</div>
      {title && <div className="empty-state-title">{title}</div>}
      {desc && <div className="empty-state-desc">{desc}</div>}
      {children}
      {cta && (
        <button
          type="button"
          className="btn btn-primary empty-state-cta"
          onClick={cta.onClick}
        >
          {cta.label}
        </button>
      )}
    </div>
  );
}
