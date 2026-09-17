# 13 · Glossaire

**ADD_ON / TRIM / EXIT_*** — actions de la couche fondamentale
(`lt_exit_policy`) : renforcer, alléger, sortir (thèse cassée, survalorisation,
catastrophe). Informatives, sauf exemption de rotation pour ADD_ON.

**ADTV** — volume moyen quotidien en dollars ; cap de 5 % par position.

**Attribution de l'écart** — décomposition compte − panier en cash drag,
slippage, stops, sélection/timing (`gap_attribution`).

**Basket (mode)** — stratégie panier top-N équipondéré avec rotation et
rebalance (`STRATEGY_MODE=basket`).

**Bracket** — ordre Alpaca à trois jambes : entrée + stop (SL) + limite (TP).
Les jambes doivent être **GTC** pour survivre à la clôture.

**Buy signal** — verdict par ticker (`STRONG_BUY`, `BUY`, `WATCH`, `SKIP`,
`FALLING_KNIFE`, `CHEAP_JUNK`, `EARNINGS_BLACKOUT`, `NO_DATA`) calculé par le
proposer à partir du score, du support, des earnings et de la confiance.

**Cash drag** — coût d'opportunité du capital non investi : (1 − investi) ×
rendement du panier.

**Circuit breaker** — réduction progressive des nouvelles entrées selon le
drawdown vs plus haut roulant (−8/−12/−16 %).

**Composite / TITAN score** — moyenne pondérée des piliers (profil de poids),
0–100.

**Data confidence** — score de qualité des données d'un ticker (coverage ×
fraîcheur × sanité).

**Fenêtre live** — période depuis le 2026-04-22 où les snapshots de scoring
sont matérialisés chaque jour (pas de look-ahead). Seule base valable pour
mesurer le composite.

**Freeze** — mode du killswitch : gel des nouvelles entrées sans vendre.

**GTC / DAY** — durée de validité d'un ordre : jusqu'à annulation / jusqu'à
la clôture du jour.

**HRP** — Hierarchical Risk Parity, pondération par clusters de corrélation
(mode legacy). En mode basket : 1/N.

**Hit rate** — part des périodes (semaines) à rendement positif.

**IC (Information Coefficient)** — corrélation de rang entre un score et le
rendement futur ; > 0 = prédictif, ≈ 0 = bruit.

**Journal** — `trade_journal.csv`, une ligne par position (source de vérité).

**Killswitch** — frein journalier (−6 % d'equity dans la journée).

**Limite marketable** — ordre limite placé légèrement au-delà du marché
(ask × 1,003) : fill quasi immédiat, protège des pics.

**Look-ahead** — biais d'un backtest qui utilise une information
indisponible à la date de décision (ex. fondamentaux d'aujourd'hui sur des
snapshots reconstruits).

**OCO** — One-Cancels-Other : stop + limite de sortie liés ; l'exécution
d'un annule l'autre.

**Panier théorique** — top-20 rebalancé chaque semaine, 1/N, 10 bps ; la
référence à battre dans le Cockpit.

**Profil de poids** — jeu de pondérations des piliers (`v14_1`, `equal_7`,
`equal_8`, `momentum_tilt`), choisi par `TITAN_WEIGHT_PROFILE`.

**Publication lag** — délai (5 j) entre la date d'un snapshot et son usage
dans le backtest, pour ne pas classer avec des fondamentaux pas encore
publiés.

**Rang** — position d'un ticker dans l'univers trié par composite (1 =
meilleur) ; pilote achats (≤ 20) et rotation (> 40).

**Rebalance** — retour mensuel des poids vers 1/N (bande ±2 pts).

**Régime** — BULL / BEAR / CRASH_PANIC selon S&P vs MA200 et VIX, confirmé
3 jours.

**Rotation** — vente d'une ligne restée hors du top-40 pendant 5 jours.

**Scoring Lab** — batterie de backtests comparant tailles de panier,
piliers, profils et filtres de régime sur la fenêtre live.

**Shadow portfolio** — portefeuille virtuel valorisé chaque jour pour un
profil de scoring (forward-test A/B).

**Sharpe hebdo annualisé** — moyenne / écart-type des rendements
hebdomadaires × √52.

**Slippage (bps)** — écart fill réel vs prix proposé, en points de base.

**Snapshot** — copie gz du scoring de l'univers à une date.

**Stop catastrophe** — jambe stop à −35 % (borne), filet anti-accident.

**Sweep** — passe du tracker qui corrige `Entry` au fill réel et remplace
les limites non fillées.

**Trailing stop** — stop remonté avec le prix, actif à partir de +15 %,
verrouillant 30 % du gain.

**Univers** — S&P 500 + Nasdaq-100 filtrés (capitalisation, données), ~490–590 tickers.

**Vol-target** — mise à l'échelle du book pour viser 18 % de volatilité
annualisée ; seulement si VIX ≥ 25 en mode basket.
