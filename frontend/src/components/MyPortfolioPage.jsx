// MyPortfolioPage — book personnel long terme, 10 positions manuelles.
//
// Entièrement indépendant du moteur TITAN : pas de scoring, pas de
// proposals, pas de veto auto. Endpoint /api/my_portfolio calcule le
// poids réel (prix live × actions détenues) vs le poids cible fixé par
// l'utilisateur.
//
// Deux états mutuellement exclusifs par position (is_deploying) :
//   - sous le seuil de déploiement (deployment_threshold_pct, 70% par
//     défaut) : barre de progression "X$ / Y$ déployé" — DCA en cours,
//     pas de dérive calculée (LNVGY à 0 part en est le cas extrême).
//   - au-dessus : alerte de rééquilibrage classique si la dérive dépasse
//     ±drift_threshold_pct (25% par défaut).
//
// P&L (pnl_usd/pnl_pct) est un axe de tracking SÉPARÉ, calculé depuis le
// prix d'entrée réel (entry_price) — "combien j'ai gagné/perdu", pas
// "suis-je sur ma cible d'allocation". N'affecte jamais poids/dérive.
//
// Identité visuelle volontairement distincte de PortfolioPage/ProposalsPage
// (accent cuivre/or, classes `mp-*` dans index.css) pour ne jamais laisser
// croire que ces positions sont notées ou décidées par TITAN.

import ApiErrorBanner from './common/ApiErrorBanner';
import { PageSkeleton } from './common/Skeleton';
import { useMyPortfolio } from '../hooks/useApi';
import { ageMinutes, fmtSignedPct, fmtTimeAgo } from '../utils/format';

// Au-delà de ce seuil, "dernière mise à jour" bascule en alerte visible —
// signe qu'un ticker ne se rafraîchit plus normalement plutôt qu'un simple
// jour férié/weekend sur une place étrangère (fenêtre de tolérance 3j).
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

// Le montant USD est toujours affiché entre parenthèses pour rester
// comparable entre les lignes du book (voir docs/UPGRADES_MY_PORTFOLIO.md,
// Upgrade 3), en plus du montant natif quand la devise n'est pas USD.
function fmtOrderAmount(order) {
  const usd = fmtSignedUsd(order.amount_usd);
  if (order.currency === 'USD') return usd;
  const sign = order.amount_native > 0 ? '+' : order.amount_native < 0 ? '−' : '';
  const native = `${sign}${Math.abs(order.amount_native).toFixed(2)} ${order.currency}`;
  return `${native} / ${usd}`;
}

// Signal de vente composite (Upgrade 1) : `correlation_alert_triggered` et
// `beta_flag` déclenchent chacun indépendamment le badge rouge (texte
// original BNP.PA "Beta >0.8 OU corrélation >0.40" — voir cas limite
// "signal composite" dans docs/UPGRADES_MY_PORTFOLIO.md).
function isRiskAlert(p) {
  return Boolean(p.correlation_alert_triggered || p.beta_flag);
}

export default function MyPortfolioPage() {
  const q = useMyPortfolio();

  if (q.isLoading) {
    return <PageSkeleton tiles={4} blockHeight={360} rows={6} />;
  }

  if (q.isError) {
    return (
      <ApiErrorBanner
        msg={q.error?.message || 'Erreur de chargement — Mon Portefeuille'}
        onRetry={() => q.refetch()}
      />
    );
  }

  const data = q.data || {};
  const positions = data.positions || [];
  const cash = data.cash_reserve || {};
  const watchlist = data.watchlist || [];
  const totalValue = data.total_value ?? 0;
  const totalPnlUsd = data.total_pnl_usd ?? 0;
  const driftThreshold = data.drift_threshold_pct ?? 25;
  const alertCount = positions.filter(p => p.rebalance_alert).length;
  const deployingCount = positions.filter(p => p.is_deploying).length;
  const risk = data.risk_snapshot || null;

  return (
    <div className="mp-page animate-fade-in">
      <div className="mp-banner">
        <div className="mp-banner-icon">💼</div>
        <div>
          <h2 className="mp-banner-title">Mon Portefeuille — book personnel long terme</h2>
          <p className="mp-banner-sub">
            10 positions choisies à la main, allocation manuelle fixe.
            {' '}<strong>Indépendant du moteur TITAN</strong> — pas de scoring quantamental,
            pas de proposals, pas de veto automatique sur ces lignes.
          </p>
        </div>
      </div>

      <div className="mp-tiles">
        <div className="mp-tile">
          <span className="mp-tile-label">Valeur totale</span>
          <span className="mp-tile-value">{fmtUsd(totalValue)}</span>
        </div>
        <div className="mp-tile">
          <span className="mp-tile-label">Réserve cash</span>
          <span className="mp-tile-value">
            {fmtUsd(cash.amount)} <small>({cash.target_weight_pct}% cible)</small>
          </span>
        </div>
        <div className={`mp-tile ${alertCount > 0 ? 'mp-tile-alert' : ''}`}>
          <span className="mp-tile-label">Dérive &gt; ±{driftThreshold}%</span>
          <span className="mp-tile-value">{alertCount}</span>
        </div>
        <div className="mp-tile">
          <span className="mp-tile-label">En cours de déploiement</span>
          <span className="mp-tile-value">{deployingCount}</span>
        </div>
        <div className="mp-tile">
          <span className="mp-tile-label">P&amp;L global</span>
          <span className={`mp-tile-value ${pnlClass(totalPnlUsd)}`}>
            {fmtSignedUsd(totalPnlUsd)}
          </span>
        </div>
      </div>

      {risk && (
        <div className="mp-card mp-risk-card">
          <div className="mp-risk-tiles">
            <div className="mp-tile">
              <span className="mp-tile-label">Beta portefeuille</span>
              <span className="mp-tile-value">
                {Number.isFinite(risk.portfolio_beta) ? risk.portfolio_beta.toFixed(2) : '—'}
              </span>
            </div>
            <div className="mp-tile">
              <span className="mp-tile-label">Corrélation moy. pondérée</span>
              <span className="mp-tile-value">
                {Number.isFinite(risk.avg_weighted_correlation) ? risk.avg_weighted_correlation.toFixed(2) : '—'}
              </span>
            </div>
            <div className="mp-tile">
              <span className="mp-tile-label">Ratio de diversification</span>
              <span className="mp-tile-value">
                {Number.isFinite(risk.diversification_ratio) ? risk.diversification_ratio.toFixed(2) : '—'}
              </span>
            </div>
            <div className="mp-tile">
              <span className="mp-tile-label">Dernier recalcul</span>
              <span className="mp-tile-value mp-risk-recalc-date" title={risk.last_recalc_date || ''}>
                {fmtTimeAgo(risk.last_recalc_date)}
              </span>
              {risk.n_tickers_missing > 0 && (
                <small>{risk.n_tickers_missing} ticker(s) sans données</small>
              )}
            </div>
          </div>
          {risk.most_correlated_pairs?.length > 0 && (
            <div className="mp-risk-pairs">
              <span className="mp-risk-pairs-label">Paires les plus corrélées :</span>
              {risk.most_correlated_pairs.map(pair => (
                <span key={`${pair.a}-${pair.b}`} className="mp-risk-pair">
                  {pair.a} ↔ {pair.b} : {pair.corr.toFixed(2)}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="mp-card">
        <div style={{ overflowX: 'auto' }}>
          <table className="mp-table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Poids cible</th>
                <th>Poids réel</th>
                <th>Montant cible</th>
                <th>Valeur actuelle</th>
                <th>P&amp;L</th>
                <th>Beta</th>
                <th>Raison d'achat</th>
                <th>Signal de vente</th>
              </tr>
            </thead>
            <tbody>
              {positions.map(p => (
                <tr
                  key={p.ticker}
                  className={[
                    p.rebalance_alert && 'mp-row-alert',
                    isRiskAlert(p) && 'mp-row-risk-alert',
                  ].filter(Boolean).join(' ')}
                >
                  <td>
                    <div className="mp-ticker-cell">
                      <span className="mp-ticker">{p.ticker}</span>
                      {p.badge && <span className="mp-badge-pending">{p.badge}</span>}
                      {p.earnings_data_stale && (
                        <span
                          className="mp-earnings-stale"
                          title="Calendrier earnings indisponible depuis plus de 48h"
                        >
                          ?
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="mp-value-cell">{p.target_weight_pct}%</td>
                  <td>
                    <span className="mp-weight-real">{p.real_weight_pct}%</span>
                    {p.is_deploying ? (
                      <div className="mp-deploy">
                        <div className="mp-deploy-bar">
                          <div
                            className="mp-deploy-fill"
                            style={{ width: `${Math.min(Math.max(p.deployment_pct, 0), 100)}%` }}
                          />
                        </div>
                        <span className="mp-deploy-label">
                          {fmtUsd(p.current_value)} / {fmtUsd(p.target_amount)} déployé — {p.deployment_pct}%
                        </span>
                      </div>
                    ) : p.rebalance_alert && (
                      <>
                        <span
                          className="mp-drift-alert"
                          title={`Rééquilibrage suggéré — dérive ${p.drift_pct > 0 ? '+' : ''}${p.drift_pct}% vs poids cible (seuil ±${driftThreshold}%)`}
                        >
                          ⚠ {p.drift_pct > 0 ? '+' : ''}{p.drift_pct}%
                        </span>
                        {p.rebalance_order && (
                          <span className={`mp-rebalance-order ${p.rebalance_order.direction === 'BUY' ? 'mp-pnl-pos' : 'mp-pnl-neg'}`}>
                            → {p.rebalance_order.direction === 'BUY' ? 'Acheter' : 'Vendre'} ~{p.rebalance_order.shares_native} actions
                            {' '}({fmtOrderAmount(p.rebalance_order)})
                          </span>
                        )}
                      </>
                    )}
                  </td>
                  <td className="mp-value-cell">{fmtUsd(p.target_amount)}</td>
                  <td className="mp-value-cell">
                    {fmtUsd(p.current_value)}
                    {p.price_stale && (
                      <span className="mp-stale" title="Prix live indisponible — valeur de repli = montant cible">●</span>
                    )}
                    {Number.isFinite(p.current_price) && (
                      <span className="mp-price-sub">{p.current_price.toFixed(2)} / action</span>
                    )}
                    {p.price_as_of && (() => {
                      const age = ageMinutes(p.price_as_of);
                      const stale = age !== null && age > STALE_PRICE_AGE_MINUTES;
                      return (
                        <span
                          className={`mp-price-sub mp-price-ts ${stale ? 'mp-price-ts-stale' : ''}`}
                          title={`Prix daté du ${new Date(p.price_as_of).toLocaleString('fr-FR')}${stale ? ' — ne se rafraîchit peut-être plus correctement' : ''}`}
                        >
                          {stale && '⚠ '}maj {fmtTimeAgo(p.price_as_of)}
                        </span>
                      );
                    })()}
                  </td>
                  <td className="mp-value-cell">
                    {p.pnl_usd == null ? (
                      <span style={{ color: 'var(--text-muted)' }}>—</span>
                    ) : (
                      <>
                        <span className={pnlClass(p.pnl_usd)}>{fmtSignedUsd(p.pnl_usd)}</span>
                        <span className="mp-price-sub">{fmtSignedPct(p.pnl_pct)}</span>
                      </>
                    )}
                  </td>
                  <td className="mp-value-cell">
                    {Number.isFinite(p.beta) ? p.beta.toFixed(2) : '—'}
                    {Number.isFinite(p.beta_recalculated) && (
                      <span
                        className={`mp-price-sub mp-beta-recalc ${p.beta_flag ? 'mp-beta-flag' : ''}`}
                        title={`Beta recalculé sur 2 ans vs S&P500${Number.isFinite(p.beta_diff_pct) ? `, écart de ${p.beta_diff_pct > 0 ? '+' : ''}${p.beta_diff_pct}% avec le beta déclaré` : ''}.`}
                      >
                        {p.beta_flag && '⚠ '}βʳᵉᶜᵃˡᶜ {p.beta_recalculated.toFixed(2)}
                      </span>
                    )}
                  </td>
                  <td className="mp-reason">{p.reason}</td>
                  <td className="mp-sell">{isRiskAlert(p) ? `🔴 ${p.sell_signal}` : p.sell_signal}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="mp-cash-row">
                <td>💵 Réserve cash</td>
                <td>{cash.target_weight_pct}%</td>
                <td>{cash.real_weight_pct}%</td>
                <td className="mp-value-cell">{fmtUsd(cash.amount)}</td>
                <td className="mp-value-cell">{fmtUsd(cash.amount)}</td>
                <td>—</td>
                <td>—</td>
                <td colSpan={2}>Non investie — tampon de sécurité / opportunités futures</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>

      {watchlist.length > 0 && (
        <div className="mp-card mp-watchlist-card">
          <h3 className="mp-watchlist-title">👁 Watchlist — 0% (pas détenu)</h3>
          {watchlist.map(w => (
            <div key={w.ticker} className="mp-watchlist-item">
              <span className="mp-watchlist-ticker">{w.ticker}</span>
              <span className="mp-watchlist-note">{w.note}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
