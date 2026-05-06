// ToastBell — icône cloche avec badge non-lus, click → liste historique.
//
// Lecture des toasts via toastBus (stockage sessionStorage). Marque "lu"
// au moment où l'historique est ouvert.

import { useEffect, useMemo, useRef, useState } from 'react';
import { clearHistory, getHistory, subscribe } from '../../utils/toastBus';
import { session } from '../../utils/storage';

const READ_KEY = 'toast_last_read_ts';

const _lastRead  = () => session.readString(READ_KEY, '');
const _markRead  = (ts) => session.writeString(READ_KEY, ts);

function relTime(iso) {
  if (!iso) return '';
  try {
    const ms = Date.now() - new Date(iso).getTime();
    const m = Math.round(ms / 60000);
    if (m < 1)  return 'à l\'instant';
    if (m < 60) return `il y a ${m}min`;
    const h = Math.round(m / 60);
    if (h < 24) return `il y a ${h}h`;
    return iso.slice(11, 16);
  } catch { return ''; }
}

export default function ToastBell() {
  const [history, setHistory] = useState(() => getHistory());
  const [open, setOpen] = useState(false);
  // `lastReadTick` force le recalcul de `unread` quand on ouvre la cloche
  // (au moment où on appelle _markRead). On ne stocke pas lastRead lui-même
  // dans un state pour rester source-of-truth = sessionStorage.
  const [lastReadTick, setLastReadTick] = useState(0);
  const popRef = useRef(null);

  // Subscribe to bus
  useEffect(() => {
    const unsub = subscribe(() => setHistory(getHistory()));
    return unsub;
  }, []);

  // unread est dérivé de history + sessionStorage — useMemo plutôt que
  // useState+useEffect (évite cascading render et set-state-in-effect).
  const unread = useMemo(() => {
    const last = _lastRead();
    return history.filter(t => !last || (t.ts || '') > last).length;
    // lastReadTick fait partie des deps pour invalider le memo après _markRead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [history, lastReadTick]);

  // Click outside / Escape closes
  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (!popRef.current?.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown',  onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown',  onKey);
    };
  }, [open]);

  const openHistory = () => {
    if (!open && history.length > 0) {
      _markRead(history[history.length - 1].ts || new Date().toISOString());
      // Tick le memo de unread → recompte avec la nouvelle lastRead.
      setLastReadTick((t) => t + 1);
    }
    setOpen(o => !o);
  };

  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <button
        type="button"
        onClick={openHistory}
        title={`Historique notifications (${history.length})`}
        style={{
          position: 'relative',
          background: 'rgba(255,255,255,0.04)',
          border: '1px solid var(--panel-border)',
          color: 'var(--text-muted)',
          width: 36, height: 32, borderRadius: 8,
          cursor: 'pointer', fontSize: '1rem',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'inherit',
        }}
        aria-label="Historique notifications"
      >
        🔔
        {unread > 0 && (
          <span style={{
            position: 'absolute', top: -4, right: -4,
            minWidth: 16, height: 16, padding: '0 4px',
            background: 'var(--danger)',
            color: '#fff', fontSize: '0.62rem', fontWeight: 800,
            borderRadius: 8, display: 'flex',
            alignItems: 'center', justifyContent: 'center',
          }}>
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div ref={popRef} style={{
          position: 'absolute', top: 'calc(100% + 6px)', right: 0,
          width: 320, maxHeight: '60vh', overflowY: 'auto',
          background: 'var(--panel-bg)',
          backdropFilter: 'blur(20px)',
          border: '1px solid var(--panel-border)',
          borderRadius: 8,
          boxShadow: '0 14px 40px rgba(0,0,0,0.4)',
          zIndex: 1500,
        }}>
          <div style={{
            padding: '0.6rem 0.85rem',
            borderBottom: '1px solid var(--panel-border)',
            display: 'flex', justifyContent: 'space-between',
            alignItems: 'center', fontSize: '0.78rem',
          }}>
            <span style={{ fontWeight: 700 }}>Notifications</span>
            {history.length > 0 && (
              <button type="button"
                      onClick={() => { clearHistory(); setHistory([]); }}
                      style={{
                        background: 'none', border: 'none',
                        color: 'var(--danger)', cursor: 'pointer',
                        fontSize: '0.7rem', fontFamily: 'inherit',
                      }}>
                Effacer
              </button>
            )}
          </div>
          {history.length === 0 ? (
            <div style={{ padding: '1.5rem', textAlign: 'center',
                          color: 'var(--text-muted)', fontSize: '0.78rem' }}>
              📭 Aucune notification.
            </div>
          ) : (
            <div>
              {[...history].reverse().map(t => (
                <div key={t.id} style={{
                  padding: '0.55rem 0.85rem',
                  borderLeft: `3px solid ${t.type === 'err'
                    ? 'var(--danger)' : 'var(--success)'}`,
                  borderBottom: '1px solid var(--panel-border)',
                  fontSize: '0.78rem',
                }}>
                  <div style={{ color: 'var(--text-main)' }}>{t.text}</div>
                  <div style={{ fontSize: '0.66rem',
                                color: 'var(--text-muted)', marginTop: 2 }}>
                    {relTime(t.ts)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
