# 02 · Stratégie « basket »

## En une phrase

Détenir en permanence les 20 meilleures actions de l'univers selon le score
TITAN (profil `equal_7`), à poids égaux, rotation quand un titre sort du
top-40 pendant 5 jours, rebalance mensuel des poids, stops catastrophe à
−35 % et trailing stop à partir de +15 %.

## Pourquoi cette stratégie (et pas le stock-picking à seuil 80)

L'audit du 2026-09-17 a montré que le compte paper faisait −1 % sur la fenêtre
live alors qu'un panier top-20 rebalancé faisait +18 % (V14.1) et que
l'univers équipondéré faisait +14 %. La perte ne venait pas du scoring mais
de l'exécution : 80 % du capital en cash (règle d'achat TITAN ≥ 80 trop
rare, budgets 2/run · 5/semaine), gagnants coupés à +3 % par un trailing trop
serré, achats à l'ouverture, et surtout aucun stop chez le broker (brackets
`DAY`).

Le Scoring Lab (fenêtre live 28/04 → 17/09/2026, 21 semaines, rebalance
hebdo, 10 bps) donne, à taille réelle de panier :

| Variante | Rendement | Hit | Max DD | Sharpe hebdo ann. |
|---|---|---|---|---|
| Composite V14.1 top-20 | +18,1 % | 62 % | −4,7 % | 2,9 |
| Univers équipondéré | +13,9 % | 76 % | −6,3 % | — |
| **equal_7 top-20 (retenu)** | **+34,3 %** | **76 %** | **−5,1 %** | **4,0** |
| equal_8 top-20 | +33,6 % | 81 % | −4,4 % | 4,3 |
| momentum_tilt top-15 | +42,7 % | 67 % | −8,5 % | 3,1 |
| Momentum seul top-20 | +57,8 % | 48 % | −20,3 % | — |

`equal_7` est retenu parce qu'il n'a **aucun paramètre ajusté** à la fenêtre
(poids égaux, exclusion des deux piliers à IC négatif mesurés en juin) et
qu'il domine V14.1 sur tous les axes. Les variantes concentrées (top-5,
momentum seul) rapportent plus mais avec des drawdowns doubles et une
dépendance à 2–3 valeurs mémoire/IA : ce n'est pas répétable.

**Limite assumée** : 21 semaines en bull market. Le forward-test shadow
(03-scoring) tranche à 3 mois.

## Règles, dans l'ordre d'exécution quotidien

1. **06:00 UTC — sélection.** `auto_proposer.plan_proposals` : gates système
   (killswitch, circuit breaker, régime BULL, fraîcheur univers/macro, cash,
   slots libres), puis `PortfolioManager` avec `weighting_method="equal"`,
   `max_holdings=20`, caps secteur 30 % / nom 10 % / ADTV, vol-target
   seulement si VIX ≥ 25. Filtres : montant ≥ 2 500 $, earnings à plus de
   7 jours, corrélation moyenne du panier < 0,55. Chaque proposition porte
   SL/TP (plancher −35 %, TP +100 %), buy_signal, support, prix cible.
2. **15:00–19:59 UTC, toutes les 30 min — `auto_approve`.**
   - *Rotation* (`basket_rotation`) : rang courant > 40 pendant 5 jours
     consécutifs → vente au marché (`Close_Reason=ROTATION`), sauf décision LT
     ADD_ON, max 3 par passage, jamais sous killswitch.
   - *Rebalance* (`basket_rebalance`, premier passage du mois) : lignes dont le
     poids s'écarte de 1/N de plus de 2 points → ordres ≥ 300 $, ventes
     d'abord.
   - *Achats* : propositions pending triées par rang ; qualifie si rang ≤ 20,
     verdict ∉ {FALLING_KNIFE, CHEAP_JUNK, EARNINGS_BLACKOUT, NO_DATA}, pilier
     Risk ≥ 40, pas déjà détenu, secteur non sur-exposé. Budgets 4/passage,
     20/semaine. L'ordre passe par le même endpoint que l'interface.
3. **Toutes les 2 min — `tracker`.** Voir 04 et 05 : fills, stops broker,
   trailing, time-exit (60 j sauf thèse intacte), killswitch, circuit breaker,
   décision LT.

## Paramètres

| Paramètre | Valeur | Où |
|---|---|---|
| `STRATEGY_MODE` | `basket` | `.env` / config |
| `TITAN_WEIGHT_PROFILE` | `equal_7` | `.env` |
| `BASKET_TOP_N` | 20 | config |
| `BASKET_EXIT_RANK` / `BASKET_EXIT_CONFIRM_DAYS` | 40 / 5 | config |
| `REBALANCE_BAND_PTS` / `REBALANCE_MIN_TRADE_USD` | 2 / 300 | config |
| `AUTO_APPROVE_MAX_PER_RUN` / `_PER_WEEK` | 4 / 20 | env (défauts basket) |
| `VOL_TARGET_VIX_MIN` | 25 | auto_proposer |
| `DEFAULT_MIN_PROPOSAL_USD` | 2 500 $ | auto_proposer |

Mode `legacy` (`STRATEGY_MODE=legacy`) : 15 lignes, HRP, règle TITAN ≥ 80 /
override, budgets 2/5 — conservé pour comparaison, non recommandé.

## Ce que la stratégie ne fait pas

- Pas de short, pas de levier, pas d'options. Le hedge baissier (`bear_hedge`,
  ETF SH à 10 % du book en BEAR confirmé 3 jours) est indépendant.
- Pas d'achat en régime BEAR/CRASH (gate régime), pas d'achat pendant un
  killswitch ou une pause du circuit breaker.
- Pas de re-pondération automatique du score : tout changement de profil
  passe par le Scoring Lab, le shadow et une décision humaine (11-decisions).

## Passage en réel

`LIVE_CAPITAL_FRACTION` (défaut 0,10) réduit le capital déployé quand
`ALPACA_BASE_URL` est l'URL live. Procédure recommandée : 2 mois de paper avec
journal fiable → réel à 10 % → comparaison paper vs réel dans le Cockpit
(slippage réel via `Slippage_Bps`) → montée par paliers.
