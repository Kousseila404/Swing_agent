// NotesSection — onglet Notes du TickerAnalysisModal.
//
// Liste les notes attachées à un ticker, permet d'en créer/modifier/
// supprimer, et propose un raccourci "Ajouter à la watchlist".

import { useState } from 'react';

import {
  useAddNote,
  useAddToWatchlist,
  useDeleteNote,
  useNotes,
  useUpdateNote,
} from '../../hooks/useApi';
import { Section } from './Layout';

export default function NotesSection({ ticker }) {
  const notesQ    = useNotes(ticker);
  const addMut    = useAddNote();
  const updateMut = useUpdateNote();
  const deleteMut = useDeleteNote();
  const watchMut  = useAddToWatchlist();

  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState(null); // { id, body } | null

  const notes = notesQ.data?.notes || [];

  const submitNew = (e) => {
    e.preventDefault();
    const body = draft.trim();
    if (!body) return;
    addMut.mutate({ ticker, body }, {
      onSuccess: (res) => { if (res?.ok !== false) setDraft(''); },
    });
  };

  const saveEdit = () => {
    if (!editing) return;
    const body = (editing.body || '').trim();
    if (!body) return;
    updateMut.mutate({ id: editing.id, body }, {
      onSuccess: (res) => { if (res?.ok !== false) setEditing(null); },
    });
  };

  const remove = (note) => {
    if (!window.confirm('Supprimer cette note ?')) return;
    deleteMut.mutate({ id: note.id, ticker });
  };

  const addToWatch = () => {
    watchMut.mutate({ ticker });
  };

  return (
    <Section title={`📝 Notes (${notes.length})`}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 6 }}>
        <button
          type="button"
          className="scan-filter-btn"
          onClick={addToWatch}
          disabled={watchMut.isPending}
          style={{ fontSize: '0.7rem' }}
          title="Ajouter ce ticker à la watchlist"
        >
          {watchMut.isPending ? '⏳' : '👁'} Watchlist
        </button>
      </div>

      <form onSubmit={submitNew} style={{ marginBottom: 10 }}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Thèse, observations, rappel earnings… (markdown libre)"
          rows={3}
          style={{
            width: '100%', resize: 'vertical', fontFamily: 'inherit',
            padding: '0.55rem 0.7rem', fontSize: '0.82rem',
            background: 'var(--bg-tertiary)', color: 'var(--text-main)',
            border: '1px solid var(--border)', borderRadius: 6,
          }}
        />
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 6 }}>
          <button
            type="submit"
            className="action-btn"
            disabled={addMut.isPending || !draft.trim()}
            style={{ padding: '0.4rem 0.85rem', fontSize: '0.78rem' }}
          >
            {addMut.isPending ? '⏳' : '+ Ajouter note'}
          </button>
        </div>
      </form>

      {notesQ.isLoading && (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
          Chargement notes…
        </div>
      )}

      {notes.length === 0 && !notesQ.isLoading && (
        <div style={{
          fontSize: '0.78rem', color: 'var(--text-muted)',
          textAlign: 'center', padding: '1rem',
        }}>
          📭 Aucune note pour ce ticker.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {notes.map((n) => (
          <div
            key={n.id}
            style={{
              background: 'var(--bg-tertiary)', padding: '0.55rem 0.7rem',
              borderRadius: 6, border: '1px solid var(--border)',
              fontSize: '0.82rem',
            }}
          >
            {editing?.id === n.id ? (
              <>
                <textarea
                  value={editing.body}
                  onChange={(e) => setEditing((s) => ({ ...s, body: e.target.value }))}
                  rows={3}
                  style={{
                    width: '100%', resize: 'vertical', fontFamily: 'inherit',
                    padding: '0.4rem 0.55rem', fontSize: '0.82rem',
                    background: 'var(--panel-bg)', color: 'var(--text-main)',
                    border: '1px solid var(--border)', borderRadius: 4,
                  }}
                />
                <div style={{
                  display: 'flex', gap: 6, justifyContent: 'flex-end', marginTop: 6,
                }}>
                  <button
                    type="button" className="scan-filter-btn"
                    onClick={() => setEditing(null)}
                    style={{ fontSize: '0.7rem' }}
                  >Annuler</button>
                  <button
                    type="button" className="action-btn"
                    onClick={saveEdit}
                    disabled={updateMut.isPending || !editing.body.trim()}
                    style={{ fontSize: '0.7rem', padding: '0.3rem 0.7rem' }}
                  >
                    {updateMut.isPending ? '⏳' : '💾 Enregistrer'}
                  </button>
                </div>
              </>
            ) : (
              <>
                <div style={{
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  lineHeight: 1.45, marginBottom: 6,
                }}>
                  {n.body}
                </div>
                <div style={{
                  display: 'flex', justifyContent: 'space-between',
                  alignItems: 'center', fontSize: '0.66rem',
                  color: 'var(--text-muted)',
                }}>
                  <span>
                    {n.updated_at?.slice(0, 16)?.replace('T', ' ') || '—'}
                    {n.updated_at !== n.created_at && ' (modifiée)'}
                  </span>
                  <span style={{ display: 'flex', gap: 4 }}>
                    <button
                      type="button" className="scan-filter-btn"
                      onClick={() => setEditing({ id: n.id, body: n.body })}
                      style={{ fontSize: '0.65rem', padding: '0.15rem 0.45rem' }}
                    >✎</button>
                    <button
                      type="button" className="scan-filter-btn"
                      onClick={() => remove(n)}
                      style={{
                        fontSize: '0.65rem', padding: '0.15rem 0.45rem',
                        color: 'var(--danger)',
                        borderColor: 'rgba(239,68,68,0.3)',
                      }}
                    >🗑</button>
                  </span>
                </div>
              </>
            )}
          </div>
        ))}
      </div>
    </Section>
  );
}
