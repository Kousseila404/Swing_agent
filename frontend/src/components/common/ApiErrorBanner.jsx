// ApiErrorBanner — bannière d'erreur réutilisable (extraite pour préserver le code-splitting).

export default function ApiErrorBanner({ msg, onRetry }) {
  return (
    <div className="api-error">
      <div className="api-error-icon">⚠️</div>
      <p>{msg}</p>
      {onRetry && (
        <button
          className="action-btn"
          style={{ maxWidth: 200 }}
          onClick={onRetry}
          aria-label="Réessayer la requête"
        >
          Réessayer
        </button>
      )}
    </div>
  );
}
