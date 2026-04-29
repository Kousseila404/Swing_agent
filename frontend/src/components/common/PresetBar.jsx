// PresetBar — barre compacte pour gérer des presets nommés.
//
// Props :
//   scope     : string (ex: "universe", "sectors", "proposals")
//   current   : objet payload courant (ce qu'on sauvera quand on clique 💾)
//   onApply   : (payload) => void — appelé quand l'utilisateur clique un preset
//   label     : libellé contextuel optionnel (ex: "Filtres univers")
//
// Persistence dans localStorage via utils/presets.js.

import { useEffect, useState } from 'react';
import {
  deletePreset, listPresets, loadLastApplied,
  markApplied, savePreset,
} from '../../utils/presets';

export default function PresetBar({ scope, current, onApply, label }) {
  const [presets, setPresets] = useState(() => listPresets(scope));
  const [active, setActive] = useState(() => loadLastApplied(scope));
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  useEffect(() => { setPresets(listPresets(scope)); }, [scope]);

  const apply = (p) => {
    onApply?.(p.payload);
    setActive(p.name);
    markApplied(scope, p.name);
  };

  const handleSave = () => {
    const name = draft.trim();
    if (!name) return;
    const list = savePreset(scope, name, current);
    setPresets(list);
    setActive(name);
    setDraft('');
    setEditing(false);
  };

  const handleDelete = (name) => {
    if (!window.confirm(`Supprimer le preset "${name}" ?`)) return;
    const list = deletePreset(scope, name);
    setPresets(list);
    if (active === name) setActive(null);
  };

  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center',
      padding: '0.4rem 0.6rem', borderRadius: 6,
      background: 'var(--bg-tertiary)',
      border: '1px solid var(--border)',
      fontSize: '0.78rem', marginBottom: 8,
    }}>
      <span style={{
        color: 'var(--text-muted)', fontWeight: 600,
        textTransform: 'uppercase', letterSpacing: '0.04em',
        fontSize: '0.66rem',
      }}>
        ⭐ {label || 'Presets'} :
      </span>

      {presets.length === 0 && !editing && (
        <span style={{ color: 'var(--text-muted)', fontStyle: 'italic',
                       fontSize: '0.74rem' }}>
          Aucun · sauvegarde tes filtres avec 💾
        </span>
      )}

      {presets.map(p => (
        <span key={p.name} style={{ display: 'inline-flex', alignItems: 'center' }}>
          <button type="button"
                  onClick={() => apply(p)}
                  style={{
                    background: active === p.name
                      ? 'rgba(59,130,246,0.18)' : 'rgba(255,255,255,0.04)',
                    border: `1px solid ${active === p.name
                      ? 'var(--accent-primary)' : 'var(--border)'}`,
                    color: active === p.name
                      ? 'var(--accent-primary)' : 'var(--text-main)',
                    fontFamily: 'inherit', fontSize: '0.74rem', fontWeight: 600,
                    padding: '0.2rem 0.55rem',
                    borderRadius: 4, cursor: 'pointer',
                    borderRight: 'none',
                    borderTopRightRadius: 0, borderBottomRightRadius: 0,
                  }}
                  title={`Appliquer "${p.name}" — sauvé le ${(p.updated_at || '').slice(0, 10)}`}>
            {p.name}
          </button>
          <button type="button"
                  onClick={() => handleDelete(p.name)}
                  style={{
                    background: 'rgba(239,68,68,0.06)',
                    border: '1px solid rgba(239,68,68,0.25)',
                    borderLeft: 'none',
                    color: 'var(--danger)',
                    fontSize: '0.7rem', padding: '0.2rem 0.4rem',
                    borderRadius: 4, cursor: 'pointer',
                    borderTopLeftRadius: 0, borderBottomLeftRadius: 0,
                  }}
                  title="Supprimer ce preset">
            ✕
          </button>
        </span>
      ))}

      {editing ? (
        <span style={{ display: 'inline-flex', gap: 4 }}>
          <input
            autoFocus
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter')  handleSave();
              if (e.key === 'Escape') { setEditing(false); setDraft(''); }
            }}
            placeholder="Nom du preset…"
            style={{
              fontFamily: 'inherit', fontSize: '0.74rem',
              padding: '0.2rem 0.45rem',
              border: '1px solid var(--accent-primary)',
              borderRadius: 4, color: 'var(--text-main)',
              background: 'var(--panel-bg)', width: 140,
            }}
          />
          <button type="button" onClick={handleSave}
                  className="scan-filter-btn"
                  style={{ fontSize: '0.7rem', padding: '0.18rem 0.55rem' }}>
            ✓
          </button>
          <button type="button" onClick={() => { setEditing(false); setDraft(''); }}
                  className="scan-filter-btn"
                  style={{ fontSize: '0.7rem', padding: '0.18rem 0.45rem' }}>
            ✕
          </button>
        </span>
      ) : (
        <button type="button" onClick={() => setEditing(true)}
                title="Sauvegarder les filtres actuels"
                style={{
                  background: 'rgba(34,197,94,0.10)',
                  border: '1px solid rgba(34,197,94,0.4)',
                  color: 'var(--success)',
                  fontFamily: 'inherit', fontSize: '0.72rem', fontWeight: 600,
                  padding: '0.2rem 0.55rem',
                  borderRadius: 4, cursor: 'pointer',
                  marginLeft: 'auto',
                }}>
          💾 Sauver l'état actuel
        </button>
      )}
    </div>
  );
}
