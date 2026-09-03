// StaleBadge — badge circulaire "?" de fraîcheur, partagé entre earnings
// (48h, ProposalsPage/MyPortfolioPage) et vérification de thèse (90j,
// MyPortfolioTickerDetail) pour rester visuellement cohérent : même forme,
// même logique (affiché seulement quand `show` est vrai, jamais de valeur
// déduite — c'est juste "cette donnée n'a pas été rafraîchie récemment").

export default function StaleBadge({ show, title, symbol = '?' }) {
  if (!show) return null;
  return (
    <span className="mp-stale-badge" title={title}>
      {symbol}
    </span>
  );
}
