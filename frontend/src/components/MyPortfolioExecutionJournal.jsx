// MyPortfolioExecutionJournal — journal de qualité d'exécution (Upgrade 4).
//
// `my_portfolio` n'a aucune intégration eToro : ce formulaire est une
// saisie manuelle assistée d'un fill déjà exécuté, pas une capture
// automatique (voir docs/UPGRADES_MY_PORTFOLIO.md, Upgrade 4). Le calcul
// du prix de référence historique + slippage se fait entièrement côté
// backend (modules/my_portfolio_executions.py) ; ce composant se contente
// d'assister la saisie de `executed_at` sans ambiguïté de fuseau — cas
// limite explicite de la spec ("erreur de fuseau à la saisie") — en
// affichant l'heure convertie dans les 3 fuseaux de référence avant envoi.

import { useMemo, useState } from 'react';

import { useLogExecution, useMyPortfolioExecutions } from '../hooks/useApi';
import { zonedWallClockToUtc } from '../utils/timezone';

const REFERENCE_ZONES = [
  { id: 'Europe/Paris', label: 'Paris' },
  { id: 'America/New_York', label: 'New York' },
  { id: 'Asia/Hong_Kong', label: 'Hong Kong' },
  { id: 'UTC', label: 'UTC' },
];

function fmtUsd(n) {
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : '—';
}

function fmtSignedUsd(n) {
  if (!Number.isFinite(n)) return '—';
  const sign = n > 0 ? '+' : n < 0 ? '−' : '';
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}

const INITIAL_FORM = {
  ticker: '', direction: 'BUY', shares: '', fillPrice: '',
  date: '', time: '', timeZone: 'Europe/Paris', notes: '',
};

export default function MyPortfolioExecutionJournal({ tickers }) {
  const q = useMyPortfolioExecutions();
  const logExecution = useLogExecution();
  const [form, setForm] = useState(INITIAL_FORM);
  const [submitError, setSubmitError] = useState(null);

  const utcInstant = useMemo(
    () => zonedWallClockToUtc(form.date, form.time, form.timeZone),
    [form.date, form.time, form.timeZone]
  );

  const selectedCurrency = tickers.find(t => t.ticker === form.ticker)?.currency || 'USD';

  function set(field) {
    return e => setForm(f => ({ ...f, [field]: e.target.value }));
  }

  function handleSubmit(e) {
    e.preventDefault();
    setSubmitError(null);
    if (!utcInstant) {
      setSubmitError('Date/heure d\'exécution requises');
      return;
    }
    logExecution.mutate(
      {
        ticker: form.ticker,
        direction: form.direction,
        shares: parseFloat(form.shares),
        fill_price_native: parseFloat(form.fillPrice),
        executed_at: utcInstant.toISOString(),
        notes: form.notes,
      },
      {
        onSuccess: (res) => {
          if (res.ok === false) {
            setSubmitError(res.detail || 'Échec de l\'enregistrement');
            return;
          }
          setForm(INITIAL_FORM);
        },
        onError: () => setSubmitError('Échec de l\'enregistrement (réseau)'),
      }
    );
  }

  const data = q.data || {};
  const executions = data.executions || [];
  const summary = data.summary || {};

  return (
    <div className="mp-card mp-exec-card">
      <div className="mp-exec-header">
        <h3 className="mp-exec-title">📓 Journal de qualité d'exécution</h3>
        <p className="mp-exec-sub">
          Saisie manuelle assistée d'un fill (aucune intégration eToro) — calcule le
          prix de référence historique et le slippage réel vs marché.
        </p>
      </div>

      <form className="mp-exec-form" onSubmit={handleSubmit}>
        <div className="mp-exec-form-row">
          <label>
            Ticker
            <select className="mini-input" value={form.ticker} onChange={set('ticker')} required>
              <option value="" disabled>—</option>
              {tickers.map(t => <option key={t.ticker} value={t.ticker}>{t.ticker}</option>)}
            </select>
          </label>
          <label>
            Devise
            <input className="mini-input" value={selectedCurrency} disabled />
          </label>
          <label>
            Direction
            <select className="mini-input" value={form.direction} onChange={set('direction')}>
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
          </label>
          <label>
            Shares
            <input
              className="mini-input" type="number" step="any" min="0"
              value={form.shares} onChange={set('shares')} required
            />
          </label>
          <label>
            Prix payé (devise native)
            <input
              className="mini-input" type="number" step="any" min="0"
              value={form.fillPrice} onChange={set('fillPrice')} required
            />
          </label>
        </div>

        <div className="mp-exec-form-row">
          <label>
            Date d'exécution
            <input className="mini-input" type="date" value={form.date} onChange={set('date')} required />
          </label>
          <label>
            Heure d'exécution
            <input className="mini-input" type="time" value={form.time} onChange={set('time')} required />
          </label>
          <label>
            Fuseau de saisie
            <select className="mini-input" value={form.timeZone} onChange={set('timeZone')}>
              {REFERENCE_ZONES.map(z => <option key={z.id} value={z.id}>{z.label}</option>)}
            </select>
          </label>
          <label className="mp-exec-notes-field">
            Notes
            <input className="mini-input" value={form.notes} onChange={set('notes')} placeholder="libre" />
          </label>
        </div>

        {utcInstant && (
          <div className="mp-exec-tz-preview">
            <span className="mp-exec-tz-preview-label">Vérifier avant envoi :</span>
            {REFERENCE_ZONES.map(z => (
              <span key={z.id} className="mp-exec-tz-chip">
                {z.label} : {utcInstant.toLocaleString('fr-FR', { timeZone: z.id, dateStyle: 'short', timeStyle: 'short' })}
              </span>
            ))}
          </div>
        )}

        {submitError && <div className="mp-exec-error">{submitError}</div>}

        <button type="submit" className="btn btn-primary" disabled={logExecution.isPending}>
          {logExecution.isPending ? 'Enregistrement…' : 'Enregistrer le fill'}
        </button>
      </form>

      <div className="mp-exec-summary-tile">
        <span className="mp-tile-label">Coût d'exécution cumulé</span>
        <span className={`mp-tile-value ${Number(summary.total_slippage_usd) > 0 ? 'mp-pnl-neg' : summary.total_slippage_usd < 0 ? 'mp-pnl-pos' : ''}`}>
          {summary.total_slippage_usd == null ? '—' : fmtSignedUsd(-summary.total_slippage_usd)}
        </span>
        {summary.n_fills > 0 && (
          <small>{summary.n_fills_outside_market} fill(s) hors séance sur {summary.n_fills}</small>
        )}
      </div>

      {executions.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table className="mp-table">
            <thead>
              <tr>
                <th>Date/Heure</th>
                <th>Ticker</th>
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
                  <td className="mp-ticker">{row.Ticker}</td>
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
      )}

      <p className="mp-exec-hours-reminder">
        Horaires (heure de Paris) — US : 15h30–22h00 · Euronext Paris : 09h00–17h30 ·
        HKEX : 03h30–06h00 et 07h00–10h00 (pause déjeuner incluse).
      </p>
    </div>
  );
}
