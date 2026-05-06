// SettingsPage — préférences UI centralisées (localStorage côté client).
//
// Ne touche pas au backend. Centralise :
//   - api_token (déjà utilisé par client.js via localStorage)
//   - capital cible / max holdings / mode (utilisés par ProposalsPage)
//   - theme / density (déjà gérés par usePreferences)
//
// Toutes les pages qui consomment ces valeurs lisent localStorage à
// chaque montage, donc un changement ici prend effet au prochain refetch.

import { useEffect, useState } from 'react';
import { fetchMonitorPreview, runMonitorAlerts } from '../api/client';
import { usePreferences } from '../utils/preferences';

const KEYS = {
  apiToken:       'api_token',
  capital:        'pref_default_capital',
  maxHoldings:    'pref_default_max_holdings',
  topNMode:       'pref_default_top_n_mode',
  fractional:     'pref_default_fractional',
};

const DEFAULTS = {
  capital:     100_000,
  maxHoldings: 20,
  topNMode:    'free_slots',
  fractional:  false,
};

function readPref(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    if (v == null) return fallback;
    if (typeof fallback === 'number') {
      const n = parseFloat(v);
      return Number.isFinite(n) ? n : fallback;
    }
    if (typeof fallback === 'boolean') return v === 'true';
    return v;
  } catch { return fallback; }
}

function writePref(key, value) {
  try { localStorage.setItem(key, String(value)); } catch { /* private mode */ }
}

function FieldGroup({ title, hint, children }) {
  return (
    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
      <h3 style={{ margin: '0 0 4px', fontSize: '0.95rem' }}>{title}</h3>
      {hint && (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)',
                      marginBottom: 12 }}>{hint}</div>
      )}
      {children}
    </div>
  );
}

function FieldRow({ label, hint, children }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '0.6rem 0', borderBottom: '1px solid var(--border)',
      flexWrap: 'wrap',
    }}>
      <div style={{ flex: '1 1 240px', minWidth: 200 }}>
        <div style={{ fontSize: '0.85rem', fontWeight: 600 }}>{label}</div>
        {hint && (
          <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)',
                        marginTop: 2 }}>{hint}</div>
        )}
      </div>
      <div style={{ flex: '1 1 200px', minWidth: 200 }}>
        {children}
      </div>
    </div>
  );
}

function TelegramMonitorPanel() {
  const [preview, setPreview] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [sendResult, setSendResult] = useState(null);

  const runPreview = async () => {
    setError(null); setSendResult(null);
    setRunning(true);
    try {
      const r = await fetchMonitorPreview();
      setPreview(r);
    } catch (e) {
      setError(e?.message || 'Erreur preview');
    } finally {
      setRunning(false);
    }
  };

  const sendNow = async () => {
    if (!window.confirm('Envoyer maintenant le message Telegram aux positions signalées ?')) return;
    setError(null); setSendResult(null);
    setRunning(true);
    const r = await runMonitorAlerts();
    setRunning(false);
    if (r?.ok) setSendResult(r);
    else setError(r?.detail || r?.error || 'Erreur envoi');
  };

  const alerts = preview?.alerts || sendResult?.alerts || [];

  return (
    <FieldGroup
      title="🛰 Monitor positions LT (Telegram)"
      hint="Scanne tes positions OPEN et alerte sur 3 signaux : drift thèse (≥15 pts), TITAN drop ≥5/7j, support broken. Idéal en cron quotidien."
    >
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        <button type="button"
                onClick={runPreview}
                disabled={running}
                className="action-btn"
                style={{ padding: '0.45rem 0.95rem', fontSize: '0.82rem' }}>
          {running ? '⏳…' : '👁 Preview (dry run)'}
        </button>
        <button type="button"
                onClick={sendNow}
                disabled={running || !preview || preview.n_alerts === 0}
                className="action-btn"
                style={{
                  padding: '0.45rem 0.95rem', fontSize: '0.82rem',
                  background: 'rgba(34,197,94,0.18)',
                  borderColor: 'rgba(34,197,94,0.5)',
                  color: '#22c55e',
                }}>
          📤 Envoyer Telegram
        </button>
      </div>

      {error && (
        <div style={{
          padding: '0.6rem', borderRadius: 6, fontSize: '0.78rem',
          background: 'rgba(248,113,113,0.10)', color: '#fb7185',
          border: '1px solid rgba(248,113,113,0.3)', marginBottom: 8,
        }}>
          ⚠️ {error}
        </div>
      )}

      {sendResult?.sent && (
        <div style={{
          padding: '0.55rem', borderRadius: 6, fontSize: '0.78rem',
          background: 'rgba(34,197,94,0.10)', color: 'var(--success)',
          border: '1px solid rgba(34,197,94,0.4)', marginBottom: 8,
        }}>
          ✓ Message envoyé · {sendResult.n_alerts} position(s)
        </div>
      )}

      {preview && preview.n_alerts === 0 && (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
          ✅ Aucun signal détecté sur les positions OPEN.
        </div>
      )}

      {alerts.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)',
                        textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            {alerts.length} position(s) signalée(s)
          </div>
          {alerts.map(a => (
            <div key={a.ticker} style={{
              padding: '0.6rem 0.7rem', borderRadius: 6,
              background: 'var(--bg-tertiary)', border: '1px solid var(--border)',
            }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline',
                            marginBottom: 4 }}>
                <strong style={{ fontFamily: 'monospace', fontSize: '0.95rem',
                                 color: 'var(--accent-primary)' }}>
                  {a.ticker}
                </strong>
                <span style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>
                  TITAN {a.current?.toFixed?.(0) ?? '—'}
                  {a.entry != null && ` · entry ${a.entry.toFixed(0)}`}
                  {a.sector && ` · ${a.sector}`}
                </span>
              </div>
              <ul style={{ margin: '0 0 0 18px', padding: 0, fontSize: '0.78rem',
                           lineHeight: 1.5, color: 'var(--text-main)' }}>
                {a.signals.map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </div>
          ))}
        </div>
      )}

      {preview?.preview && (
        <details style={{ marginTop: 12, fontSize: '0.72rem' }}>
          <summary style={{ cursor: 'pointer', color: 'var(--text-muted)' }}>
            Aperçu du message Telegram
          </summary>
          <pre style={{
            background: 'var(--bg-tertiary)', padding: '0.6rem',
            borderRadius: 6, marginTop: 6,
            border: '1px solid var(--border)',
            whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            fontFamily: 'monospace', fontSize: '0.72rem',
            color: 'var(--text-main)',
          }}>{preview.preview}</pre>
        </details>
      )}
    </FieldGroup>
  );
}

export default function SettingsPage() {
  const { theme, density, toggleTheme, toggleDensity } = usePreferences();

  const [apiToken,    setApiToken]    = useState(() => readPref(KEYS.apiToken, ''));
  const [capital,     setCapital]     = useState(() => readPref(KEYS.capital, DEFAULTS.capital));
  const [maxHoldings, setMaxHoldings] = useState(() => readPref(KEYS.maxHoldings, DEFAULTS.maxHoldings));
  const [topNMode,    setTopNMode]    = useState(() => readPref(KEYS.topNMode, DEFAULTS.topNMode));
  const [fractional,  setFractional]  = useState(() => readPref(KEYS.fractional, DEFAULTS.fractional));

  const [savedFlash, setSavedFlash] = useState(false);
  useEffect(() => {
    if (!savedFlash) return;
    const t = setTimeout(() => setSavedFlash(false), 1800);
    return () => clearTimeout(t);
  }, [savedFlash]);

  const persist = (key, value) => {
    writePref(key, value);
    setSavedFlash(true);
  };

  const resetAll = () => {
    if (!window.confirm('Réinitialiser toutes les préférences UI ?')) return;
    [
      KEYS.capital, KEYS.maxHoldings, KEYS.topNMode, KEYS.fractional,
    ].forEach(k => { try { localStorage.removeItem(k); } catch { /* private mode */ } });
    setCapital(DEFAULTS.capital);
    setMaxHoldings(DEFAULTS.maxHoldings);
    setTopNMode(DEFAULTS.topNMode);
    setFractional(DEFAULTS.fractional);
    setSavedFlash(true);
  };

  return (
    <div className="control-panel animate-fade-in">
      {savedFlash && (
        <div style={{
          position: 'fixed', top: '1rem', right: '1rem', zIndex: 1500,
          padding: '0.55rem 0.95rem', borderRadius: 8,
          background: 'rgba(34,197,94,0.18)',
          color: 'var(--success)',
          border: '1px solid rgba(34,197,94,0.5)',
          fontSize: '0.82rem', fontWeight: 600,
        }}>
          ✓ Enregistré
        </div>
      )}

      {/* ─── Apparence ─── */}
      <FieldGroup
        title="🎨 Apparence"
        hint="Réglages visuels appliqués à toute l'interface, persistés sur ce navigateur."
      >
        <FieldRow label="Thème" hint="Sombre par défaut. Le clair est utile en plein jour.">
          <button type="button" onClick={toggleTheme}
                  className="scan-filter-btn"
                  style={{ minWidth: 120, justifyContent: 'center' }}>
            {theme === 'light' ? '☀️ Clair' : '🌙 Sombre'} — basculer
          </button>
        </FieldRow>
        <FieldRow label="Densité" hint="Compact = plus de lignes/écran (recommandé sur grandes tables).">
          <button type="button" onClick={toggleDensity}
                  className="scan-filter-btn"
                  style={{ minWidth: 120, justifyContent: 'center' }}>
            {density === 'compact' ? '▤ Compact' : '▦ Cosy'} — basculer
          </button>
        </FieldRow>
      </FieldGroup>

      {/* ─── Sécurité / API ─── */}
      <FieldGroup
        title="🔑 Authentification"
        hint="Bearer token utilisé pour les mutations (POST/PUT/DELETE). Lit localStorage."
      >
        <FieldRow label="API Token"
                  hint="Le token doit correspondre à API_TOKEN dans backend/.env">
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              type="password"
              value={apiToken}
              onChange={e => setApiToken(e.target.value)}
              onBlur={() => persist(KEYS.apiToken, apiToken)}
              placeholder="sk-titan-..."
              className="mini-input"
              style={{ flex: 1, fontFamily: 'monospace', fontSize: '0.85rem' }}
            />
            <button type="button" className="action-btn"
                    onClick={() => persist(KEYS.apiToken, apiToken)}
                    style={{ padding: '0.45rem 0.85rem', fontSize: '0.78rem' }}>
              💾
            </button>
          </div>
        </FieldRow>
      </FieldGroup>

      {/* ─── Defaults Proposals ─── */}
      <FieldGroup
        title="📬 Defaults Propositions"
        hint="Valeurs pré-remplies dans le formulaire 'Générer un plan'."
      >
        <FieldRow label="Capital cible ($)"
                  hint="Capital total alloué pour le sizing — le proposer répartit dessus.">
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span style={{ color: 'var(--text-muted)', fontFamily: 'monospace' }}>$</span>
            <input
              type="number" min="1000" step="1000"
              value={capital}
              onChange={e => {
                const v = parseFloat(e.target.value) || 0;
                setCapital(v);
              }}
              onBlur={() => persist(KEYS.capital, capital)}
              className="mini-input"
              style={{ width: 140, fontFamily: 'monospace' }}
            />
          </div>
        </FieldRow>

        <FieldRow label="Max holdings"
                  hint="Nombre maximum de positions ouvertes simultanément.">
          <input
            type="number" min="1" max="100"
            value={maxHoldings}
            onChange={e => {
              const v = Math.max(1, Math.min(100, parseInt(e.target.value, 10) || 1));
              setMaxHoldings(v);
            }}
            onBlur={() => persist(KEYS.maxHoldings, maxHoldings)}
            className="mini-input"
            style={{ width: 80, fontFamily: 'monospace', textAlign: 'center' }}
          />
        </FieldRow>

        <FieldRow label="Mode top-N par défaut"
                  hint="Combler = juste les slots libres ; Rebalance = jusqu'à max.">
          <div style={{ display: 'inline-flex', border: '1px solid var(--border)',
                        borderRadius: 6, overflow: 'hidden' }}>
            {[
              { id: 'free_slots',   label: 'Combler' },
              { id: 'max_holdings', label: 'Rebalance' },
            ].map(o => (
              <button key={o.id} type="button"
                      onClick={() => {
                        setTopNMode(o.id);
                        persist(KEYS.topNMode, o.id);
                      }}
                      style={{
                        padding: '0.4rem 0.85rem', border: 'none',
                        background: topNMode === o.id ? 'var(--accent-primary)' : 'transparent',
                        color: topNMode === o.id ? '#0b1220' : 'var(--text-muted)',
                        fontWeight: 600, cursor: 'pointer', fontSize: '0.82rem',
                      }}>
                {o.label}
              </button>
            ))}
          </div>
        </FieldRow>

        <FieldRow label="Shares fractionnaires"
                  hint="Permet l'achat de fractions de share (utile sur tickers chers).">
          <label style={{ display: 'inline-flex', alignItems: 'center',
                          gap: 8, cursor: 'pointer' }}>
            <input type="checkbox" checked={fractional}
                   onChange={e => {
                     setFractional(e.target.checked);
                     persist(KEYS.fractional, e.target.checked);
                   }}
                   style={{ width: 16, height: 16 }} />
            <span style={{ fontSize: '0.85rem' }}>
              {fractional ? 'Activé' : 'Désactivé'}
            </span>
          </label>
        </FieldRow>
      </FieldGroup>

      {/* ─── Telegram alerts ─── */}
      <TelegramMonitorPanel />

      {/* ─── Actions ─── */}
      <FieldGroup title="⚙️ Maintenance">
        <FieldRow label="Reset des préférences"
                  hint="Remet capital / holdings / mode aux valeurs par défaut.">
          <button type="button"
                  onClick={resetAll}
                  className="scan-filter-btn"
                  style={{
                    background: 'rgba(239,68,68,0.10)',
                    borderColor: 'rgba(239,68,68,0.4)',
                    color: 'var(--danger)',
                  }}>
            🔄 Réinitialiser
          </button>
        </FieldRow>
      </FieldGroup>

      <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)',
                    padding: '0.5rem 0', textAlign: 'center' }}>
        Les préférences sont stockées dans <code>localStorage</code> de ce navigateur uniquement.
      </div>
    </div>
  );
}
