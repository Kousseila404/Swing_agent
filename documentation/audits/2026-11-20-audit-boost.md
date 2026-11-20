# Audit « boost » — 2026-11-20

Suite de l'[audit intégral du 2026-11-18](2026-11-18-audit-integral.md). Objectif :
vérifier ce qui a été corrigé, mesurer l'état réel (paper + argent réel) et
classer les leviers restants par valeur.

## 0. Synthèse

| Axe | État 18/11 | État 20/11 |
|---|---|---|
| P0 audit (13) | 13 ouverts | **13 traités** (#1–#11 le 20/11 après-midi, #12–#13 le soir) |
| Proposer | mort depuis 09-18 | 2 propositions le 20/11, 14/20 positions déployées |
| API en prod | OOM 2×/semaine | **tourne encore l'ancien code (démarrée 06:15)** — correctifs OOM non chargés |
| Lectures `/api/*` | 30 GET sans auth, exposés via nginx | **fermés** (sauf 2 sondes cron) |
| Chaîne d'ordres P1-1…P1-6 | 6 ouverts | P1-1, P1-3, P1-4, P1-5, P1-6 **corrigés** ; P1-2 ouvert |
| Argent réel eToro | invisible | snapshot + carte Cockpit + réconciliation |
| Edge TITAN | IC live −0,048 | shadow equal_7 −8,2 % vs EW +2,3 % ; excès hebdo t = −1,8 |

**Verdict** : la plomberie est maintenant saine. Le facteur limitant est la
**valeur** (aucun edge mesuré sur le paper, et l'argent réel est piloté
surtout par 4 copy-traders sous SPY sur 1 an), pas la fiabilité.

## 1. Corrigé dans cette session

1. **Argent réel visible (P0 #12/#13)** — `modules/etoro_sync.py`, `routers/etoro.py`,
   carte « Argent réel · eToro » en tête du Cockpit (ADR-019).
2. **Lectures authentifiées (§7 P1)** — middleware `_ReadAuthMiddleware` (api.py) :
   tout GET `/api/*` exige le token complet ou le token de revue, sauf
   `/api/market_status` et `/api/data_health` (sondes cron). Comparaisons de
   token en temps constant (`hmac.compare_digest`, §7 P2). Les tests ne
   dépendent plus du vrai `API_TOKEN` de `.env` (conftest).
3. **P1-6 killswitch fail-closed** chez les appelants (rotation, rebalance,
   auto-approve) — une exception n'autorise plus les ordres.
4. **P1-1 rebalance hors séance** — marché fermé / horloge KO → report sans
   consommer une des 3 tentatives du mois.
5. **P1-3 rotation sur univers partiel** — suspendue si l'univers scoré
   < 90 % de la médiane des 10 dernières tailles.
6. **P1-4 double achat** — `client_order_id` = `SQ-<TICKER>-P<proposal>-<jour>` :
   Alpaca rejette le doublon du jour.
7. **P1-5 ordre non journalisé** — trace `data/unjournaled_orders.jsonl` + alerte Telegram.
8. **Shadow `equal_7_sector_cap`** — même score que equal_7, 3 titres max par
   secteur, pour mesurer l'effet de la concentration (voir §3).
9. Lint CI : `price_panel.py` (B023).

Tests : 1 441 → **1 479**, ruff et mypy verts.

## 2. Exploitation

- **API à redémarrer (bloquant)** : `swing-api` est active depuis
  2026-11-20 06:15 UTC, donc *avant* les correctifs OOM (`_SNAP_CACHE`,
  warm hors API), la route eToro et l'auth des lectures. Le warm benchmark de
  06:12 a encore expiré (180 s) et le sentinel compte 1 OOM / 24 h.
- **Crontab** : sentinel + synchro eToro absents de la crontab installée
  (diff purement additif avec `deploy/crontab.txt`).
- **Clés API eToro** absentes → snapshot figé au 20/11 21:51 jusqu'à la
  prochaine ingestion manuelle.

## 3. Quant / paper

Shadow 2026-09-17 → 2026-11-20 (NAV base 100 000 $, 10 bps de frais) :

| Profil | Total | Excès hebdo vs EW | t (12 sem.) |
|---|---|---|---|
| universe_ew | **+2,2 %** | — | — |
| v14_1 | −6,8 % | −0,78 % | −1,38 |
| equal_7 | −8,2 % | −0,89 % | −1,77 |
| momentum_tilt | −9,7 % | −1,02 % | −1,85 |
| momentum_only | −13,7 % | −1,33 % | −1,43 |

- Tous les profils TITAN battus par l'univers équipondéré ; pas encore
  significatif à 5 % (t ≈ −1,8, n = 12), mais cohérent avec l'IC live négatif.
- **Concentration** : panier equal_7 = 6 Énergie + 5 Tech sur 20 (APA, OXY,
  COP, EOG, MPC, VLO, PSX…). Le décrochage du 05→08/11 (−4,5 % en 4 séances)
  est un choc sectoriel, pas idiosyncratique. Le proposer live a un plafond
  30 % par secteur ; le panier shadow/rotation n'en avait aucun → profil
  `equal_7_sector_cap` ajouté (mesure seulement, aucun changement live).
- Recommandation : **ne pas changer le live avant le 2026-12-17**, mais
  préparer la décision dès maintenant. Si equal_7 reste < universe_ew, basculer
  le paper sur un cœur EW/indiciel + satellites TITAN plafonnés par secteur,
  plutôt que d'ajuster encore les poids.

## 4. Argent réel (eToro, ~9,6 k$)

| Bloc | Valeur | Part | Remarque |
|---|---|---|---|
| Copy-trading (4) | 5 630 $ | 58 % | 4 books actions longs, 100 % investis, biais tech |
| Actions directes (14 + 2 reliquats) | 2 618 $ | 27 % | PnL latent ≈ −8 % sur le coût |
| Cash | 1 387 $ | 14 % | |

Copy-traders sur 1 an (profil public eToro) vs **SPY +6,9 %, QQQ +4,4 %** :

| Trader | Gain 1 an | Risque | Max DD | Stop copie | Poids compte |
|---|---|---|---|---|---|
| leandrool | −3,0 % | 4 | −21 % | 5 % | 17,7 % |
| FundManagerZech | +5,3 % | 4 | −11 % | 5 % | 17,2 % |
| robchamow | −2,1 % | 4 | −16 % | **50 %** | 12,4 % |
| petelu | +6,5 % | 5 | −21 % | 10 % | 11,2 % |

- Les 4 font moins bien que SPY sur 12 mois, et leurs gains moyens de
  copieurs sont négatifs. Ils restent bons sur 5 ans (+62 % à +326 %), donc
  pas de conclusion définitive sur un an, mais **58 % du capital paie des
  gérants pour une exposition proche d'un indice actions**.
- Stops de copie à 5 % (leandrool, Zech) : sortie forcée sur un simple
  repli de marché, au pire moment ; à 50 % (robchamow) : quasi aucune
  protection. Incohérent entre traders.
- Action prioritaire actuelle : **EQT** (signal « TITAN < 60 » déclenché, −18,6 %).

## 5. Leviers restants, par valeur

| # | Levier | Pourquoi | Effort |
|---|---|---|---|
| 1 | Redémarrer l'API + installer la crontab | Active tous les correctifs du 20/11 | utilisateur, 1 min |
| 2 | Clés API eToro | Snapshot quotidien automatique → historique réel | utilisateur, 5 min |
| 3 | **Performance réelle (TWR) vs SPY** par bloc (direct / chaque copy) | Seule mesure qui dit si l'argent réel est bien géré ; dépôts/retraits via `depositSummary`/`withdrawalSummary` des mirrors | M |
| 4 | Décision TITAN au 2026-12-17 sur critères écrits d'avance (seuil t, écart vs EW, effet du plafond sectoriel) | Éviter le biais de « encore un réglage » | S |
| 5 | Revue des stops de copie (règle unique, p. ex. 20–25 %) | Cohérence du risque sur 58 % du capital | utilisateur |
| 6 | P1-2 rotation : annulation des jambes stop avant fill confirmé | Fenêtre sans stop (paper) | M |
| 7 | Sentinel : invariant « snapshot eToro > 3 j » et « unjournaled_orders non vide » | Rendre visibles les deux nouveaux angles morts | S |
| 8 | Dette : 5 couches de sortie sans arbitre, 469 `except Exception`, dépendances mortes | Lisibilité / risque de régression | L |

Aucune recommandation ci-dessus ne passe d'ordre : les décisions sur le
compte eToro restent manuelles.
