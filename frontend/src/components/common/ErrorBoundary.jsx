// ErrorBoundary — capture les erreurs React non gérées dans l'arbre.
//
// Affiche une carte avec :
//   - icône ⚠️ + titre
//   - bouton "Réessayer" (reset le state interne)
//   - bouton "Recharger la page" (window.location.reload)
//   - toggle "Voir la trace" (stack collapsible)
//
// Class component obligatoire (React n'a pas encore d'API hook officielle
// pour les error boundaries en 2026).

import React from 'react';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, showStack: false };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error('ErrorBoundary caught:', error, info);
  }

  reset = () => this.setState({ hasError: false, error: null, showStack: false });

  toggleStack = () => this.setState((s) => ({ showStack: !s.showStack }));

  render() {
    if (!this.state.hasError) return this.props.children;

    const err = this.state.error;
    return (
      <div className="error-boundary-card" role="alert">
        <div className="error-boundary-head">
          <div className="error-boundary-icon" aria-hidden="true">⚠️</div>
          <div>
            <div className="error-boundary-title">Une erreur est survenue dans ce module</div>
            <div style={{ color: 'var(--text-muted)', fontSize: '0.82rem' }}>
              Le reste de l&apos;application reste utilisable. Tu peux réessayer ou recharger.
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
}
