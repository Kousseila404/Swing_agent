// AuthBanner — bandeau global « accès refusé » (audit 2026-11-18 P0 #11).
//
// Avant : un token absent/expiré faisait afficher « — » ou « aucune
// proposition » partout, sans aucune indication. Le client API émet
// désormais `sq:auth-error` sur chaque 401/403 ; ce bandeau l'écoute, dit
// clairement ce qui se passe et permet de saisir le token sur place.

import { useEffect, useState } from 'react';

import { AUTH_ERROR_EVENT, hasApiToken } from '../../api/client';
import { writeString } from '../../utils/storage';

export default function AuthBanner() {
  const [error, setError] = useState(null);
  const [token, setToken] = useState('');

  useEffect(() => {
    const onAuth = (e) => setError(e.detail || { status: 401 });
    window.addEventListener(AUTH_ERROR_EVENT, onAuth);
    return () => window.removeEventListener(AUTH_ERROR_EVENT, onAuth);
  }, []);

  if (!error) return null;

  const missing = !hasApiToken();
  const save = (e) => {
    e.preventDefault();
    const t = token.trim();
    if (!t) return;
    writeString('api_token', t);
    window.location.reload();
  };

  return (
    <div
      role="alert"
      style={{
        margin: '0 0 0.75rem',
        padding: '0.6rem 0.9rem',
        borderRadius: 8,
        border: '1px solid #ef4444',
        background: 'rgba(239, 68, 68, 0.12)',
        color: 'var(--text-primary, inherit)',
        display: 'flex',
        flexWrap: 'wrap',
        gap: '0.5rem',
        alignItems: 'center',
      }}
    >
      <strong>🔒 Accès API refusé ({error.status}).</strong>
      <span style={{ flex: '1 1 260px' }}>
        {missing
          ? 'Aucun token API n’est enregistré dans ce navigateur : les données affichées sont incomplètes.'
          : 'Le token API enregistré est refusé (expiré ou modifié) : les données affichées sont incomplètes.'}
        {error.path ? ` Appel : ${error.path}` : ''}
      </span>
      <form onSubmit={save} style={{ display: 'flex', gap: '0.4rem' }}>
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Coller le token API"
          aria-label="Token API"
          autoComplete="off"
          style={{ minWidth: 180 }}
        />
        <button type="submit" className="btn">Enregistrer</button>
        <button type="button" className="btn" onClick={() => setError(null)}>Masquer</button>
      </form>
    </div>
  );
}
