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
import { fmtSignedPct } from '../utils/format';

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
                <tr key={p.ticker} className={p.rebalance_alert ? 'mp-row-alert' : ''}>
                  <td>
                    <div className="mp-ticker-cell">
                      <span className="mp-ticker">{p.ticker}</span>
                      {p.badge && <span className="mp-badge-pending">{p.badge}</span>}
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
                      <span
                        className="mp-drift-alert"
                        title={`Rééquilibrage suggéré — dérive ${p.drift_pct > 0 ? '+' : ''}${p.drift_pct}% vs poids cible (seuil ±${driftThreshold}%)`}
                      >
                        ⚠ {p.drift_pct > 0 ? '+' : ''}{p.drift_pct}%
                      </span>
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
                  <td className="mp-value-cell">{Number.isFinite(p.beta) ? p.beta.toFixed(2) : '—'}</td>
                  <td className="mp-reason">{p.reason}</td>
                  <td className="mp-sell">{p.sell_signal}</td>
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
