// MyPortfolioTickerDetail — page détail d'une ligne "Mon Portefeuille" (book
// perso LT, hors moteur TITAN). Route `#/my_portfolio/<ticker>`.
//
// Ne recalcule RIEN : consolide dans une seule vue ce que
// `/api/my_portfolio` (position, thèse/signal de vente, beta/corrélations —
// Upgrade 1, earnings — Upgrade 2, rééquilibrage — Upgrade 3), le journal
// d'exécution (Upgrade 4, filtré côté client par ticker) et le nouvel
// endpoint `/api/my_portfolio/{ticker}/price_history` (seule donnée pas
// déjà exposée par la liste — réutilise le même fetch yfinance que le
// calcul beta/corrélation) exposent déjà.

import { useState } from 'react';
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { useMyPortfolioExecutions, useMyPortfolioPriceHistory } from '../../hooks/useApi';
import { ageMinutes, fmtSignedPct, fmtTimeAgo } from '../../utils/format';

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

function DetailSection({ title, children }) {
  return (
    <div className="mp-card mp-detail-section">
      <h3 className="mp-detail-section-title">{title}</h3>
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

      <DetailSection title="Thèse d'achat & signal de vente">
        <div className="mp-detail-fields-grid">
          <Field label="Thèse d'achat">
            {p.reason || <em style={{ color: 'var(--text-muted)' }}>Aucune thèse documentée</em>}
          </Field>
          <Field label="Signal de vente">
            {p.sell_signal ? (
              <>
                {(p.correlation_alert_triggered || p.beta_flag) && '🔴 '}
                {p.sell_signal}
              </>
            ) : <em style={{ color: 'var(--text-muted)' }}>Aucun signal de vente documenté</em>}
          </Field>
        </div>
      </DetailSection>

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
                <span className="mp-earnings-stale-text">⚠ Donnée non rafraîchie depuis &gt;48h</span>
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
