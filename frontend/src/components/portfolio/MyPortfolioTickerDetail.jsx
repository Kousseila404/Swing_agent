// MyPortfolioTickerDetail — page détail d'une ligne "Mon Portefeuille" (book
// perso LT, hors moteur TITAN). Route `#/my_portfolio/<ticker>`.
//
// Ne recalcule RIEN : consolide dans une seule vue ce que
// `/api/my_portfolio` (position, thèse structurée, beta/corrélations —
// Upgrade 1, earnings — Upgrade 2, rééquilibrage — Upgrade 3), le journal
// d'exécution (Upgrade 4, filtré côté client par ticker) et le nouvel
// endpoint `/api/my_portfolio/{ticker}/price_history` (seule donnée pas
// déjà exposée par la liste — réutilise le même fetch yfinance que le
// calcul beta/corrélation) exposent déjà.
//
// Thèse structurée (3 blocs, modules/my_portfolio_thesis.py côté backend) :
//   A. why_bought      — catalyseurs / valorisation / rôle dans le book.
//   B. sell_signals    — liste de signaux, chacun avec un statut éditorial
//      (intact/à surveiller/déclenché) — jamais déduit automatiquement.
//   C. verification    — dernière vérification humaine + historique.
// Édité via PATCH /api/my_portfolio/{ticker}/thesis (useUpdateThesis) —
// AUCUN de ces champs n'est jamais calculé depuis une donnée de marché.

import { useState } from 'react';
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { useMyPortfolioExecutions, useMyPortfolioPriceHistory, useUpdateThesis } from '../../hooks/useApi';
import { ageMinutes, fmtSignedPct, fmtTimeAgo } from '../../utils/format';
import { pushToast } from '../../utils/toastBus';
import StaleBadge from './StaleBadge';

// Au-delà de ce nombre de jours depuis `derniere_verification`, badge
// d'alerte — même seuil que modules/my_portfolio_thesis.py VERIFICATION_STALE_DAYS.
const VERIFICATION_STALE_DAYS = 90;

// Libellés volontairement distincts de "thèse intacte" (Bloc C) — même mot
// "intact" utilisé aux deux endroits pour des portées différentes (un signal
// isolé vs la thèse entière) créait une ambiguïté. Ici chaque libellé décrit
// directement ce qui se passerait si on lisait juste l'icône + le mot.
//
// Accessibilité daltonisme : la couleur seule (🟢/🟡/🔴, même forme) ne
// suffit pas à distinguer les 3 statuts — icônes de FORME différente
// (✅ rond plein / ⚠️ triangle / 🛑 octogone) + `borderStyle` distinct
// (solid/dashed/double) en plus du texte, qui reste le signal principal.
// bg/border repris du même pattern que THESIS_BADGE_PALETTE/LT_DECISION_PALETTE
// (PortfolioPage.jsx) — cohérence visuelle des badges de statut dans l'app.
const SIGNAL_STATUS_META = {
  intact: {
    color: 'var(--success)', bg: 'rgba(34,197,94,0.14)', border: 'rgba(34,197,94,0.35)', borderStyle: 'solid',
    icon: '✅', label: 'Pas de signal', hint: "Ce critère ne montre aucun signe d'alerte.",
  },
  a_surveiller: {
    color: 'var(--warning)', bg: 'rgba(251,191,36,0.16)', border: 'rgba(251,191,36,0.35)', borderStyle: 'dashed',
    icon: '⚠️', label: 'À surveiller', hint: 'Ce critère commence à se dégrader — à suivre de près.',
  },
  declenche: {
    color: 'var(--danger)', bg: 'rgba(239,68,68,0.16)', border: 'rgba(239,68,68,0.35)', borderStyle: 'double',
    icon: '🛑', label: 'Signal déclenché', hint: 'Le critère est atteint : le signal de vente est déclenché, réévaluer la position.',
  },
};

function daysSince(dateStr) {
  const then = new Date(`${dateStr}T00:00:00`);
  if (Number.isNaN(then.getTime())) return null;
  return Math.floor((Date.now() - then.getTime()) / 86_400_000);
}

const PERIODS = [
  { id: '3mo', label: '3M' },
  { id: '6mo', label: '6M' },
  { id: '1y',  label: '1A' },
  { id: '2y',  label: '2A' },
  { id: '5y',  label: '5A' },
  { id: 'max', label: 'Max' },
];

const STALE_PRICE_AGE_MINUTES = 3 * 24 * 60;

function fmtUsd(n) {
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : '—';
}

function fmtSignedUsd(n) {
  if (!Number.isFinite(n)) return '—';
  const sign = n > 0 ? '+' : n < 0 ? '−' : '';
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}

function pnlClass(n) {
  if (!Number.isFinite(n) || n === 0) return '';
  return n > 0 ? 'mp-pnl-pos' : 'mp-pnl-neg';
}

function fmtOrderAmount(order) {
  const usd = fmtSignedUsd(order.amount_usd);
  if (order.currency === 'USD') return usd;
  const sign = order.amount_native > 0 ? '+' : order.amount_native < 0 ? '−' : '';
  const native = `${sign}${Math.abs(order.amount_native).toFixed(2)} ${order.currency}`;
  return `${native} / ${usd}`;
}

function DetailSection({ title, action, children }) {
  return (
    <div className="mp-card mp-detail-section">
      <div className="mp-detail-section-header">
        <h3 className="mp-detail-section-title">{title}</h3>
        {action}
      </div>
      {children}
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div className="mp-detail-field">
      <span className="mp-detail-field-label">{label}</span>
      <span className="mp-detail-field-value">{children}</span>
    </div>
  );
}

function EmptyDoc({ children }) {
  return <em className="mp-detail-empty-doc">{children || 'à documenter'}</em>;
}

function PriceHistoryChart({ ticker }) {
  const [period, setPeriod] = useState('1y');
  const q = useMyPortfolioPriceHistory(ticker, period);
  const history = q.data?.history || null;

  return (
    <div>
      <div className="mp-detail-period-picker">
        {PERIODS.map(p => (
          <button
            key={p.id}
            type="button"
            className={`mp-detail-period-btn ${period === p.id ? 'is-active' : ''}`}
            onClick={() => setPeriod(p.id)}
          >
            {p.label}
          </button>
        ))}
      </div>
      {q.isLoading ? (
        <p className="mp-detail-empty">Chargement de l'historique…</p>
      ) : q.isError ? (
        <p className="mp-detail-empty">Historique indisponible ({q.error?.message || 'erreur réseau'}).</p>
      ) : !history || history.length === 0 ? (
        <p className="mp-detail-empty">Aucun historique de prix disponible pour ce ticker.</p>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={history} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--panel-border)" />
            <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={40} />
            <YAxis
              tick={{ fontSize: 10 }}
              domain={['auto', 'auto']}
              tickFormatter={(v) => `$${v.toFixed(0)}`}
              width={56}
            />
            <Tooltip
              contentStyle={{ background: 'rgba(13,13,26,0.95)', border: '1px solid var(--border)', fontSize: '0.78rem' }}
              formatter={(v) => [`$${Number(v).toFixed(2)}`, 'Prix (USD)']}
            />
            <Line type="monotone" dataKey="price" stroke="var(--mp-accent)" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function ExecutionJournalForTicker({ ticker }) {
  const q = useMyPortfolioExecutions();
  const executions = (q.data?.executions || []).filter(row => row.Ticker === ticker);

  if (q.isLoading) return <p className="mp-detail-empty">Chargement du journal d'exécution…</p>;
  if (executions.length === 0) {
    return <p className="mp-detail-empty">Aucune entrée du journal de qualité d'exécution pour ce ticker.</p>;
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="mp-table">
        <thead>
          <tr>
            <th>Date/Heure</th>
            <th>Direction</th>
            <th>Marché</th>
            <th>Prix payé</th>
            <th>Prix référence</th>
            <th>Écart</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {executions.map((row, i) => (
            <tr key={`${row.Ticker}-${row.Executed_At}-${i}`}>
              <td className="mp-price-sub">{new Date(row.Executed_At).toLocaleString('fr-FR')}</td>
              <td>{row.Direction}</td>
              <td>{row.Market_Open_At_Fill ? '✅' : '❌'}</td>
              <td className="mp-value-cell">{row.Fill_Price_Native} {row.Currency}</td>
              <td className="mp-value-cell">
                {fmtUsd(row.Reference_Price_USD)}
                {row.Reference_Price_Resolution && (
                  <span className="mp-price-sub">{row.Reference_Price_Resolution}</span>
                )}
              </td>
              <td className={Math.abs(row.Slippage_Bps ?? 0) > 200 ? 'mp-pnl-neg' : ''}>
                {Number.isFinite(row.Slippage_Bps) ? `${row.Slippage_Bps > 0 ? '+' : ''}${row.Slippage_Bps.toFixed(0)} bps` : '—'}
              </td>
              <td className="mp-reason">{row.Notes}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ListEditor — liste dynamique de chaînes (catalyseurs), sans distinction
// 1 vs N item : toujours le même formulaire add/remove.
function ListEditor({ items, onChange, placeholder }) {
  return (
    <div className="mp-detail-list-editor">
      {items.map((item, i) => (
        <div key={i} className="mp-detail-list-editor-row">
          <input
            className="mini-input"
            value={item}
            placeholder={placeholder}
            onChange={(e) => onChange(items.map((it, idx) => (idx === i ? e.target.value : it)))}
          />
          <button
            type="button"
            className="mp-detail-remove-btn"
            aria-label="Supprimer ce catalyseur"
            onClick={() => onChange(items.filter((_, idx) => idx !== i))}
          >
            ✕
          </button>
        </div>
      ))}
      <button type="button" className="mp-detail-add-btn" onClick={() => onChange([...items, ''])}>
        + Ajouter un catalyseur
      </button>
    </div>
  );
}

function EditActions({ onSave, onCancel, pending, disabled, error }) {
  return (
    <>
      <div className="mp-detail-edit-actions">
        <button type="button" className="btn btn-primary" onClick={onSave} disabled={pending || disabled}>
          {pending ? 'Enregistrement…' : 'Enregistrer'}
        </button>
        <button type="button" className="mp-detail-edit-btn" onClick={onCancel}>Annuler</button>
      </div>
      {error && <p className="mp-exec-error">{error}</p>}
    </>
  );
}

// Bloc A — "Pourquoi j'ai acheté" (catalyseurs / valorisation / rôle).
function WhyBoughtSection({ ticker, whyBought }) {
  const mutation = useUpdateThesis();
  const [editing, setEditing] = useState(false);
  const [catalyseurs, setCatalyseurs] = useState([]);
  const [valorisation, setValorisation] = useState('');
  const [rolePortefeuille, setRolePortefeuille] = useState('');

  function startEdit() {
    setCatalyseurs(whyBought.catalyseurs?.length ? [...whyBought.catalyseurs] : ['']);
    setValorisation(whyBought.valorisation || '');
    setRolePortefeuille(whyBought.role_portefeuille || '');
    setEditing(true);
  }

  function handleSave() {
    mutation.mutate(
      {
        ticker,
        body: {
          why_bought: {
            catalyseurs: catalyseurs.map((c) => c.trim()).filter(Boolean),
            valorisation,
            role_portefeuille: rolePortefeuille,
          },
        },
      },
      {
        onSuccess: () => { setEditing(false); pushToast({ msg: '✅ Thèse mise à jour' }); },
        onError: (err) => pushToast({ msg: `❌ ${err?.message || 'Échec de la mise à jour'}`, type: 'error' }),
      },
    );
  }

  return (
    <DetailSection
      title="Pourquoi j'ai acheté"
      action={!editing && (
        <button type="button" className="mp-detail-edit-btn" onClick={startEdit}>✏️ Modifier</button>
      )}
    >
      {!editing ? (
        <div className="mp-detail-fields-grid">
          <Field label="Catalyseurs">
            {whyBought.catalyseurs?.length ? (
              <ul className="mp-detail-list">
                {whyBought.catalyseurs.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
            ) : <EmptyDoc />}
          </Field>
          <Field label="Valorisation à l'achat">{whyBought.valorisation || <EmptyDoc />}</Field>
          <Field label="Rôle dans le portefeuille">{whyBought.role_portefeuille || <EmptyDoc />}</Field>
        </div>
      ) : (
        <div className="mp-detail-edit-form">
          <label className="mp-detail-edit-label">
            Catalyseurs (ce qui devait se produire)
            <ListEditor items={catalyseurs} onChange={setCatalyseurs} placeholder="ex: utilisation capacité > 90%" />
          </label>
          <label className="mp-detail-edit-label">
            Valorisation à l'achat
            <textarea
              className="mp-detail-textarea" rows={2} value={valorisation}
              onChange={(e) => setValorisation(e.target.value)}
              placeholder="ex: P/E 12x vs médiane secteur 18x"
            />
          </label>
          <label className="mp-detail-edit-label">
            Rôle dans le portefeuille
            <textarea
              className="mp-detail-textarea" rows={2} value={rolePortefeuille}
              onChange={(e) => setRolePortefeuille(e.target.value)}
              placeholder="ex: diversification sectorielle, beta bas…"
            />
          </label>
          <EditActions
            onSave={handleSave} onCancel={() => setEditing(false)}
            pending={mutation.isPending} error={mutation.isError ? mutation.error?.message : null}
          />
        </div>
      )}
    </DetailSection>
  );
}

// Bloc B — signaux de vente, liste structurée (0..N sans cas particulier).
function SellSignalsSection({ ticker, sellSignals }) {
  const mutation = useUpdateThesis();
  const [editing, setEditing] = useState(false);
  const [rows, setRows] = useState([]);

  function startEdit() {
    setRows(
      sellSignals.length
        ? sellSignals.map((s) => ({ ...s }))
        : [{ libelle: '', statut: 'a_surveiller', note: '', date_maj: '' }],
    );
    setEditing(true);
  }

  function updateRow(i, field, value) {
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)));
  }

  function handleSave() {
    const cleaned = rows
      .filter((r) => r.libelle.trim())
      .map((r) => ({
        id: r.id, libelle: r.libelle.trim(), statut: r.statut,
        note: r.note || null, date_maj: r.date_maj || null,
      }));
    mutation.mutate(
      { ticker, body: { sell_signals: cleaned } },
      {
        onSuccess: () => { setEditing(false); pushToast({ msg: '✅ Signaux de vente mis à jour' }); },
        onError: (err) => pushToast({ msg: `❌ ${err?.message || 'Échec de la mise à jour'}`, type: 'error' }),
      },
    );
  }

  return (
    <DetailSection
      title="Signaux de vente"
      action={!editing && (
        <button type="button" className="mp-detail-edit-btn" onClick={startEdit}>✏️ Modifier</button>
      )}
    >
      {!editing ? (
        sellSignals.length === 0 ? <EmptyDoc>Aucun signal de vente documenté</EmptyDoc> : (
          <>
            <p className="mp-signal-legend">
              {Object.values(SIGNAL_STATUS_META).map((m) => (
                <span key={m.label} className="mp-signal-legend-item">{m.icon} {m.label}</span>
              ))}
            </p>
            <ul className="mp-signal-list">
              {sellSignals.map((s) => {
                const meta = SIGNAL_STATUS_META[s.statut] || {};
                return (
                  <li key={s.id} className="mp-signal-row">
                    <span
                      className="mp-signal-status-badge"
                      style={{
                        color: meta.color, background: meta.bg, borderColor: meta.border,
                        borderStyle: meta.borderStyle,
                        borderWidth: meta.borderStyle === 'double' ? '3px' : '1px',
                      }}
                      title={meta.hint}
                    >
                      {meta.icon} {meta.label}
                    </span>
                    <span className="mp-signal-libelle">{s.libelle}</span>
                    {s.note && <span className="mp-signal-note">— {s.note}</span>}
                    {s.date_maj && <span className="mp-price-sub mp-signal-date">évalué le {s.date_maj}</span>}
                  </li>
                );
              })}
            </ul>
          </>
        )
      ) : (
        <div className="mp-detail-edit-form">
          {rows.map((r, i) => (
            <div key={i} className="mp-signal-edit-row">
              <input
                className="mini-input" placeholder="Libellé du critère" value={r.libelle}
                onChange={(e) => updateRow(i, 'libelle', e.target.value)}
              />
              <select
                className="mini-input" value={r.statut} onChange={(e) => updateRow(i, 'statut', e.target.value)}
                title={SIGNAL_STATUS_META[r.statut]?.hint}
              >
                <option value="intact">✅ Pas de signal</option>
                <option value="a_surveiller">⚠️ À surveiller</option>
                <option value="declenche">🛑 Signal déclenché</option>
              </select>
              <input
                className="mini-input" placeholder="Note (optionnel)" value={r.note || ''}
                onChange={(e) => updateRow(i, 'note', e.target.value)}
              />
              <input
                className="mini-input" type="date" value={r.date_maj || ''}
                onChange={(e) => updateRow(i, 'date_maj', e.target.value)}
                title="Date de dernière évaluation de ce signal"
              />
              <button
                type="button" className="mp-detail-remove-btn" aria-label="Supprimer ce signal"
                onClick={() => setRows((rs) => rs.filter((_, idx) => idx !== i))}
              >
                ✕
              </button>
            </div>
          ))}
          <button
            type="button" className="mp-detail-add-btn"
            onClick={() => setRows((rs) => [...rs, { libelle: '', statut: 'a_surveiller', note: '', date_maj: '' }])}
          >
            + Ajouter un signal
          </button>
          <EditActions
            onSave={handleSave} onCancel={() => setEditing(false)}
            pending={mutation.isPending} error={mutation.isError ? mutation.error?.message : null}
          />
        </div>
      )}
    </DetailSection>
  );
}

// Bloc C — traçabilité des vérifications. `verification` est 100% éditoriale
// (voir contrainte non négociable, modules/my_portfolio_thesis.py) : ce
// composant ne fait qu'afficher/soumettre ce que l'utilisateur écrit, jamais
// de déduction depuis une autre donnée.
function VerificationSection({ ticker, verification }) {
  const mutation = useUpdateThesis();
  const [editing, setEditing] = useState(false);
  const [date, setDate] = useState('');
  const [verdict, setVerdict] = useState('');

  function startEdit() {
    setDate(new Date().toISOString().slice(0, 10));
    setVerdict('');
    setEditing(true);
  }

  function handleSave() {
    mutation.mutate(
      { ticker, body: { verification: { derniere_verification: date, verdict } } },
      {
        onSuccess: () => { setEditing(false); pushToast({ msg: '✅ Vérification enregistrée' }); },
        onError: (err) => pushToast({ msg: `❌ ${err?.message || 'Échec de la mise à jour'}`, type: 'error' }),
      },
    );
  }

  const days = verification.derniere_verification ? daysSince(verification.derniere_verification) : null;
  const stale = days !== null && days > VERIFICATION_STALE_DAYS;
  const history = verification.historique_verifications || [];

  return (
    <DetailSection
      title="Traçabilité des vérifications"
      action={!editing && (
        <button type="button" className="mp-detail-edit-btn" onClick={startEdit}>✅ Nouvelle vérification</button>
      )}
    >
      {verification.derniere_verification ? (
        <div className="mp-detail-fields-grid">
          <Field label="Dernière vérification">
            {verification.derniere_verification}
            <StaleBadge
              show={stale}
              title={`Non vérifiée depuis ${days} jours (seuil ${VERIFICATION_STALE_DAYS}j)`}
            />
          </Field>
          <Field label="Verdict">{verification.verdict}</Field>
        </div>
      ) : (
        <p className="mp-detail-empty">Aucune vérification enregistrée.</p>
      )}

      {editing && (
        <div className="mp-detail-edit-form" style={{ marginTop: verification.derniere_verification ? '0.8rem' : 0 }}>
          <label className="mp-detail-edit-label">
            Date de vérification
            <input className="mini-input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          <label className="mp-detail-edit-label">
            Verdict
            <textarea
              className="mp-detail-textarea" rows={2} value={verdict}
              onChange={(e) => setVerdict(e.target.value)}
              placeholder="ex: thèse intacte, T2 au-dessus des attentes"
            />
          </label>
          <EditActions
            onSave={handleSave} onCancel={() => setEditing(false)}
            pending={mutation.isPending} disabled={!verdict.trim()}
            error={mutation.isError ? mutation.error?.message : null}
          />
        </div>
      )}

      {history.length > 0 && (
        <details className="mp-detail-history">
          <summary>Historique des vérifications ({history.length})</summary>
          <ul className="mp-detail-history-list">
            {[...history].reverse().map((h, i) => (
              <li key={i}><strong>{h.date}</strong> — {h.verdict}</li>
            ))}
          </ul>
        </details>
      )}
    </DetailSection>
  );
}

export default function MyPortfolioTickerDetail({ position, driftThreshold, onBack }) {
  const p = position;

  return (
    <div className="mp-page animate-fade-in">
      <div className="mp-detail-breadcrumb">
        <button type="button" className="mp-detail-back-btn" onClick={onBack}>
          ← Retour
        </button>
        <nav className="mp-detail-crumbs" aria-label="Fil d'ariane">
          <button type="button" className="mp-detail-crumb-link" onClick={onBack}>Mon Portefeuille</button>
          <span className="mp-detail-crumb-sep">›</span>
          <span className="mp-detail-crumb-current">{p.ticker}</span>
        </nav>
      </div>

      <div className="mp-banner">
        <div className="mp-banner-icon">💼</div>
        <div>
          <h2 className="mp-banner-title">
            {p.ticker}
            {p.badge && <span className="mp-badge-pending" style={{ marginLeft: 8 }}>{p.badge}</span>}
          </h2>
          <p className="mp-banner-sub">
            Position du book personnel long terme — vue consolidée, aucune donnée recalculée ici.
          </p>
        </div>
      </div>

      <DetailSection title="Position & prix">
        <div className="mp-detail-fields-grid">
          <Field label="Prix actuel">
            {p.current_price != null ? `${p.current_price.toFixed(2)} / action` : '—'}
            {p.price_stale && <span className="mp-stale" title="Prix live indisponible — valeur de repli">●</span>}
            {p.price_as_of && (() => {
              const age = ageMinutes(p.price_as_of);
              const stale = age !== null && age > STALE_PRICE_AGE_MINUTES;
              return (
                <span className={`mp-price-sub mp-price-ts ${stale ? 'mp-price-ts-stale' : ''}`}>
                  {stale && '⚠ '}maj {fmtTimeAgo(p.price_as_of)}
                </span>
              );
            })()}
          </Field>
          <Field label="Actions détenues">{p.shares}</Field>
          <Field label="Prix d'entrée">
            {p.entry_price != null ? `${p.entry_price} ${p.currency || 'USD'}` : '— (pas encore ouverte)'}
          </Field>
          <Field label="Devise native">{p.currency || 'USD'}</Field>
          <Field label="Valeur actuelle">{fmtUsd(p.current_value)}</Field>
          <Field label="Montant cible">{fmtUsd(p.target_amount)}</Field>
          <Field label="P&L">
            {p.pnl_usd == null ? (
              <span style={{ color: 'var(--text-muted)' }}>—</span>
            ) : (
              <>
                <span className={pnlClass(p.pnl_usd)}>{fmtSignedUsd(p.pnl_usd)}</span>{' '}
                <span className="mp-price-sub" style={{ display: 'inline' }}>{fmtSignedPct(p.pnl_pct)}</span>
              </>
            )}
          </Field>
          <Field label="Poids cible vs réel">
            {p.target_weight_pct}% cible → <span className="mp-weight-real">{p.real_weight_pct}%</span> réel
          </Field>
          <Field label="Dérive">
            {p.is_deploying ? (
              <div className="mp-deploy">
                <div className="mp-deploy-bar">
                  <div className="mp-deploy-fill" style={{ width: `${Math.min(Math.max(p.deployment_pct, 0), 100)}%` }} />
                </div>
                <span className="mp-deploy-label">{p.deployment_pct}% déployé — DCA en cours</span>
              </div>
            ) : p.drift_pct != null ? (
              <span className={p.rebalance_alert ? 'mp-drift-alert' : ''}>
                {p.drift_pct > 0 ? '+' : ''}{p.drift_pct}% (seuil ±{driftThreshold}%)
              </span>
            ) : '—'}
          </Field>
        </div>
      </DetailSection>

      <WhyBoughtSection ticker={p.ticker} whyBought={p.why_bought || {}} />
      <SellSignalsSection ticker={p.ticker} sellSignals={p.sell_signals || []} />
      <VerificationSection ticker={p.ticker} verification={p.verification || {}} />

      <DetailSection title="Beta & corrélations (Upgrade 1)">
        <div className="mp-detail-fields-grid">
          <Field label="Beta déclaré">{Number.isFinite(p.beta) ? p.beta.toFixed(2) : '—'}</Field>
          <Field label="Beta recalculé (2 ans vs S&amp;P500)">
            {Number.isFinite(p.beta_recalculated) ? (
              <span className={p.beta_flag ? 'mp-beta-flag' : ''}>
                {p.beta_flag && '⚠ '}{p.beta_recalculated.toFixed(2)}
                {Number.isFinite(p.beta_diff_pct) && (
                  <span className="mp-price-sub" style={{ display: 'inline' }}>
                    {' '}(écart {p.beta_diff_pct > 0 ? '+' : ''}{p.beta_diff_pct}%)
                  </span>
                )}
              </span>
            ) : '— (pas encore calculé, job hebdo)'}
          </Field>
          <Field label="Corrélation moyenne">
            {Number.isFinite(p.avg_correlation) ? p.avg_correlation.toFixed(3) : '—'}
          </Field>
          <Field label="Corrélation vs référence">
            {Number.isFinite(p.correlation_vs_ref) ? p.correlation_vs_ref.toFixed(3) : '—'}
          </Field>
          <Field label="Alerte corrélation">
            {p.correlation_alert_triggered ? (
              <span className="mp-beta-flag">🔴 Déclenchée ({p.correlation_streak_weeks} semaine(s))</span>
            ) : 'Non déclenchée'}
          </Field>
          <Field label="Qualité de la donnée">{p.data_quality || '—'}</Field>
        </div>
      </DetailSection>

      <DetailSection title="Earnings (Upgrade 2)">
        {p.next_earnings_date ? (
          <div className="mp-detail-fields-grid">
            <Field label="Prochaine date">{p.next_earnings_date}</Field>
            <Field label="Jours restants">{p.earnings_days_until}</Field>
            <Field label="Source">{p.earnings_source || '—'}</Field>
            <Field label="Badge">{p.badge || '—'}</Field>
            {p.earnings_data_stale && (
              <Field label="Fraîcheur">
                <StaleBadge show title="Calendrier earnings indisponible depuis plus de 48h" />
                {' '}Donnée non rafraîchie depuis &gt;48h
              </Field>
            )}
          </div>
        ) : (
          <p className="mp-detail-empty">
            Aucune date d'earnings connue{p.earnings_data_stale ? ' — cache non rafraîchi depuis >48h' : ''}.
          </p>
        )}
      </DetailSection>

      <DetailSection title="Suggestion de rééquilibrage (Upgrade 3)">
        {p.rebalance_order ? (
          <div className="mp-detail-fields-grid">
            <Field label="Direction">
              <span className={p.rebalance_order.direction === 'BUY' ? 'mp-pnl-pos' : 'mp-pnl-neg'}>
                {p.rebalance_order.direction === 'BUY' ? 'Acheter' : 'Vendre'}
              </span>
            </Field>
            <Field label="Quantité estimée">~{p.rebalance_order.shares_native} actions</Field>
            <Field label="Montant">{fmtOrderAmount(p.rebalance_order)}</Field>
            <Field label="Dérive déclenchante">
              {p.drift_pct > 0 ? '+' : ''}{p.drift_pct}% (seuil ±{driftThreshold}%)
            </Field>
          </div>
        ) : (
          <p className="mp-detail-empty">
            {p.is_deploying
              ? 'Position en cours de déploiement (DCA) — pas de rééquilibrage tant que le seuil de déploiement n\'est pas atteint.'
              : p.rebalance_alert
                ? 'Alerte de dérive active mais ordre non calculable (prix stale ou FX indisponible).'
                : 'Dérive dans les limites — aucun rééquilibrage suggéré.'}
          </p>
        )}
      </DetailSection>

      <DetailSection title="Historique de prix">
        <PriceHistoryChart ticker={p.ticker} />
      </DetailSection>

      <DetailSection title="Journal de qualité d'exécution (Upgrade 4)">
        <ExecutionJournalForTicker ticker={p.ticker} />
      </DetailSection>
    </div>
  );
}
