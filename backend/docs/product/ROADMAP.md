# SwingQuant TITAN — Ligne directrice produit

Document vivant. On l'update à chaque étape franchie (pas un audit figé).
Objectif : transformer TITAN en vraie appli perso de qualification de signaux
d'investissement LT (inspiration Seeking Alpha), en itérant étape par étape,
avec validation utilisateur avant chaque implémentation.

## Vision

Pas juste un moteur de scoring consultable — une appli qui **qualifie** les
signaux (pourquoi celui-là, pourquoi maintenant) et qui **pousse** l'info
plutôt que d'attendre que l'utilisateur vienne la chercher.

## État des lieux (2026-07-18)

Le logiciel est déjà substantiel, pas un MVP :
- Backend : 63 endpoints, 58 modules (scoring 9 piliers, support score,
  thesis_stop, buy_signal, price_alerts, insider, SEC filings, news,
  sector benchmark, peer comparison, correlation check, HRP weighting…)
- Frontend : 16 pages React 19 + TanStack Query, `TickerAnalysisModal`
  factsheet 11 sections (breakdown TITAN, fundamentals, valuation, quality,
  health, growth, analystes, price action, support, drift, flags)
- Automations existantes : cron quotidien (universe refresh + macro), digest
  Telegram matinal, `titan_alerts`, alertes `thesis_stop` et `price_alerts`
- Historisation des scores en place (`universe_history.ticker_history()`)
  → réutilisable pour tout ce qui touche à la fraîcheur/l'évolution d'un
  signal, pas besoin de nouvelle infra data.

Donc le vrai gap n'est pas "il manque des données ou des features" — c'est
**la qualification et la mise en avant du signal**.

## Diagnostic — les deux frictions remontées

### 1. "L'onglet Proposals n'est pas qualitatif, c'est le même signal tout le temps"

Root cause identifiée dans `auto_proposer.py` (`plan_proposals`, ~L793-798) :

- Le ranking est un **simple tri par `titan_score` décroissant**. Aucune
  notion de fraîcheur, de changement d'état, ou de segmentation par
  conviction.
- L'univers est rescoré par rotation budgétée (~100 tickers/jour, cf. mémoire
  `cron_budget_tuning`) → cycle complet ~5 jours ouvrés. Les scores du top
  bougent donc à peine d'un refresh à l'autre : **les mêmes tickers
  ressortent en tête quasi tous les jours**, ce qui donne l'impression d'un
  signal figé — parce que c'en est un.
- Les signaux qualitatifs existent déjà (verdict `buy_signal`, badge
  `support`, tilt flags QARP/GARP/consistent/cheap-junk/falling-knife,
  `revisions_score`, earnings blackout) mais ils sont :
  - uniquement **affichés**, jamais utilisés pour réordonner ou segmenter ;
  - noyés dans une table dense de 14 colonnes — pas de lecture "pourquoi
    celui-là plutôt qu'un autre" en un coup d'œil.
- Aucun signal de **nouveauté** : un ticker qui vient de passer
  `WATCH → STRONG_BUY` n'est pas distingué d'un ticker en `STRONG_BUY`
  depuis 3 semaines. Le deuxième cas est du bruit répété, le premier est
  l'info qui a de la valeur.

### 2. "Trop long, je veux être plus proactif"

- Flow actuel = 100% pull : ouvrir la page → paramétrer capital/max
  holdings/mode → cliquer "Générer un plan" → lire un tableau dense →
  cocher → approuver.
- Le digest Telegram matinal et `titan_alerts` existent mais sont
  **déconnectés** de ce flow — c'est de l'info passive, pas un call-to-action
  qui ramène directement à la décision.
- Pas de "résumé 3 lignes" avant de plonger dans le détail — l'utilisateur
  doit parcourir toute la table pour juger s'il y a quelque chose de neuf.

### 3. "La donnée elle-même n'est peut-être pas fiable"

Vérifié en live (2026-07-18, pas une supposition) :
- **100% des fondamentaux viennent de yfinance gratuit** — `FMP_ENABLED=false`
  par défaut (`data_providers/__init__.py`), le tier gratuit FMP ne sert que
  `/profile`. Aucune redondance de source.
- **Bug de mesure de fraîcheur** : `fundamentals_period_end`
  (`data_providers/yfinance_provider.py:142-177`) est calculé depuis les
  états financiers **annuels** (`tk.balance_sheet`/`tk.financials`), jamais
  trimestriels, malgré un commentaire ailleurs qui dit "Q-latest". Résultat
  live : 77/461 tickers (17%) taggués stale, dont AAPL à 287j — artefact
  cyclique du mode de calcul, pas un vrai trou de donnée à chaque fois.
- **33/461 tickers actuellement `dq_sanitize`** (valeurs brutes aberrantes
  clampées). Historique du projet : audit du 2026-04-23 a trouvé 310/516
  tickers avec valeurs aberrantes à un moment donné — la fragilité de la
  source gratuite est un vécu répété, pas hypothétique.
- `data_confidence.py` et `/api/data_health` ne couvrent que les
  fondamentaux — rien sur finnhub/insider/SEC/news. Chacune de ces sources
  a sa propre logique de cache ad hoc, pas le pattern Provider partagé.
- Décision payant vs gratuit : **différée**, prise avec des chiffres réels
  (taux de panne mesuré) plutôt qu'à l'instinct — cf. Étape 0bis.

## Roadmap — étape par étape

On implémente une étape, on valide en usage réel, puis on passe à la
suivante. Pas de gros refactor big-bang. Ordre : 0 → 0bis → 1 → 2 → 3 → 4.

### Étape 0 — Fondation data — [FAIT 2026-07-20]
- [FAIT 2026-07-18] Corriger le calcul de fraîcheur fondamentaux : ajout de
  `_latest_quarterly_period_end()` (`yfinance_provider.py`), qui lit
  `tk.quarterly_balance_sheet`/`quarterly_financials` en plus des annuels.
  `fundamentals_period_end` retient désormais la période la plus récente
  (annuelle ou trimestrielle) — `fundamentals_period_end_y1` reste
  volontairement annuel (référentiel Piotroski Y/Y inchangé). Corrige le
  faux-stale type AAPL@287j causé par l'ancien calcul annuel-only.
- [FAIT 2026-07-18] Factorisé le cache disque TTL ad hoc dupliqué 5x
  (`finnhub_provider`, `sec_edgar` insider/filings/CIK-map, `finnhub_news`)
  dans `data_providers/_disk_cache.py` (`read_json_cache`/`write_json_cache`).
  Chaque module garde son propre répertoire/TTL/clé — comportement inchangé
  (mêmes TTL, mêmes chemins de cache, fail-open préservé), seule la mécanique
  lecture/écriture JSON+TTL est partagée. Pas de changement de contrat
  `Provider ABC` : ces sources enrichissent `universe.json`, elles ne
  produisent pas de `FinancialRatios` — les forcer dans
  `FundamentalProviderBase` aurait touché le pipeline de scoring, hors scope
  de ce refactor pur.
- [FAIT 2026-07-20] Généraliser `data_confidence.py` au-delà des
  fondamentaux, débloqué sur mécanisme explicitement validé par
  l'utilisateur (2026-07-20) après proposition concrète : nouveau facteur
  multiplicatif `multi_source` dans `compute_confidence`, **neutre
  (×1.00) par défaut** sur tous les tickers déjà couverts — aucune
  régression de comportement sur le fondamentaux-only existant, comme
  exigé par le blocage initial. Ne pénalise (×0.85, même magnitude que les
  pénalités sanity existantes) que sur un signal d'échec **sans
  ambiguïté** déjà calculé ailleurs dans le pipeline — jamais sur une
  simple absence de données. Source branchée : `insider_error` (SEC Form
  4 — `"cik_unknown"`/`"sec_fetch_failed"`, distinct du cas normal "0
  filing trouvé, insiders calmes"), jusqu'ici calculé par
  `compute_insider_pillar_score` mais jamais persisté dans `universe.json`
  — `insider_enrich._enrich_one` l'écrit désormais explicitement.
  Finnhub (revisions/earnings) et news restent **hors scope** : ni l'un ni
  l'autre n'a de signal d'échec par-ticker propre aujourd'hui (finnhub =
  fail-open silencieux, un champ `None` peut vouloir dire "pas de data"
  comme "endpoint cassé" ; news/filings SEC 10-K/Q = fetch à la demande,
  jamais persisté dans `universe.json`, donc invisible sans I/O réseau que
  ce module s'interdit). Les ajouter demandera d'abord de leur donner un
  signal d'échec aussi propre que celui d'insider — pas un nouveau
  jugement d'isolation, juste de l'instrumentation supplémentaire.
  Tests : 3 dans `test_data_confidence.py` (neutre si absent, non
  pénalisé si juste "calme", pénalisé si erreur explicite) + 4 dans
  `test_insider_enrich.py` (propagation succès/échec/cik_unknown/calme).
- [FAIT 2026-07-19] Unifié `/api/data_health` en tableau de bord
  toutes-sources : nouvelle section `providers` (finnhub enrich, finnhub
  news, insider SEC Form 4, SEC filings, CIK map), cache stats agrégées
  via un nouveau helper générique `dir_cache_stats()` dans `_disk_cache.py`
  (n_cached/ages/n_errors par source, `configured` pour finnhub/news).
  Aucune modification de la logique de fetch/enrichissement des sources —
  uniquement de l'agrégation en lecture des caches disque existants.
  Pas de changement de `severity_global` : les seuils d'alerte sur ces
  nouvelles sources demandent un calibrage sur données réelles d'usage,
  pas de valeur arbitraire — à faire dans un futur run une fois un
  historique observé (cf. Étape 0bis `metrics_history.jsonl`). UI :
  nouvelle card "Sources enrichissement" dans `DataHealthPage.jsx`.
- Zéro coût — refactor pur, aucune souscription/API payante. **Ne jamais**
  activer un tier payant automatiquement : toujours différé à l'utilisateur
  avec des chiffres réels.

### Étape 0bis — Agent d'analyse des chiffres (local, pas cloud) — [FAIT 2026-07-19]
Découverte en configurant l'automatisation : un agent cloud n'a accès ni aux
secrets `.env`, ni à l'API live, ni au bot Telegram — seulement à un clone
Git. Donc ce n'est PAS une 2e routine cloud, c'est une fonctionnalité à
coder et faire tourner via le cron local existant (`run_titan.sh`, qui a
déjà accès à tout) :

[FAIT 2026-07-19] Implémenté `modules/metrics_agent.py`, branché comme
step 3c non-bloquant dans `run_titan.sh` (après `wfo_monitor`, avant le
refresh des propositions) :
- Lit `/api/data_health` (severity, staleness fondamentaux, dq_sanitize)
  via HTTP local (l'API est déjà up à ce stade du cron) — même source de
  vérité que le dashboard, aucun seuil dupliqué. Lit aussi
  `wfo_history.jsonl` (via `wfo_monitor`), `universe_history.list_snapshots()`
  et le journal de trades (`evaluation.load_journal()` +
  `perf_metrics.compute_metrics()`) pour le compte WIN/LOSS clos. Les seuils
  de progression (60 snapshots, 20 trades clos, 3 folds WFO, IC 0.02/0.05)
  sont repris tels quels de `audit_summary._TH` / du commentaire existant
  dans `wfo_monitor.py` — aucune valeur inventée.
- Calcule la tendance vs le run précédent (staleness, dq_sanitize) et
  envoie un digest Telegram via le canal `alerter` existant, à chaque run
  (fail-open si Telegram ou l'API sont indisponibles — jamais de crash du
  cron).
- Persiste chaque constat dans `data/metrics_history.jsonl` (append,
  même pattern que `wfo_history.jsonl` déjà utilisé — gitignored, mémoire
  durable côté VPS uniquement, Telegram étant éphémère).
- **Garde-fou respecté** : le module ne touche jamais
  `modules/sector_metrics/_scoring.py`. Si un run WFO produit un jour un
  IC significatif (seuil IC > 0.05, repris du commentaire déjà présent
  dans `wfo_monitor.py`), il écrit au plus une proposition markdown
  (poids OOS vs poids prod) dans `data/wfo_proposals/`, idempotente par
  timestamp de run WFO — jamais d'auto-application sur le code de scoring.
  Avec `n_folds=0` actuellement, ce chemin ne se déclenche pas encore.
- Tests : `backend/tests/test_metrics_agent.py` (14 tests — historique
  JSONL, fail-open réseau/Telegram, tendance affichée, écriture/idempotence
  de la proposition WFO).
- Nouveau module qui lit `/api/data_health` + stats `universe.json`
  (breakdown source_provider, ratio staleness, count `dq_sanitize`) et
  calcule une tendance dans le temps (le fix Étape 0 a-t-il fait baisser le
  ratio stale, le taux de panne yfinance monte/descend).
- **Volet backtest/WFO** (vérifié en live le 2026-07-18 — état réel, pas une
  supposition) : 87 snapshots historiques (seuil ≥60 atteint ✓), mais
  seulement **8 trades clos** (4 WIN/4 LOSS, seuil ≥20 loin d'être atteint)
  et `wfo_calibration.py` a tourné 2 fois avec **n_folds=0 / avg_ic_test=0.0**
  les deux fois — aucune validation statistique des poids TITAN n'existe à
  ce jour. Le digest doit suivre cette progression (folds, IC, trades clos
  vs seuils) à chaque run. **Interdiction stricte** : cet agent ne modifie
  JAMAIS les poids/logique de `sector_metrics/_scoring.py` ou tout autre
  code de scoring — avec 8 trades, tout pattern trouvé serait du bruit
  statistique, pas un signal. Le jour où WFO produit enfin des folds avec
  un IC significatif, l'agent rédige une **proposition écrite** (fichier
  markdown, pas de commit sur le code de scoring) que l'utilisateur relit
  et applique lui-même s'il est d'accord. Jamais d'auto-application sur la
  logique qui pilote de l'argent réel.
- **Persistance obligatoire** : chaque run **append** son constat structuré
  (JSON : date, ratio staleness par source, dq_sanitize count, n_folds WFO,
  avg_ic_test, n_trades clos win/loss) dans `data/metrics_history.jsonl`
  (même pattern que `wfo_history.jsonl` déjà utilisé) — Telegram est éphémère
  et ne suffit pas pour juger une tendance sur plusieurs semaines (ex : la
  décision payant/gratuit a besoin d'un historique consultable, pas du
  dernier message). Le digest Telegram reste le canal de notification
  proactive, le JSONL est la mémoire durable.
- Digest envoyé via le mécanisme Telegram existant (réutiliser le chemin de
  `daily_digest.py`/`alerter.py`, pas en recréer un).
- Branché comme step supplémentaire dans `run_titan.sh` (déjà cron quotidien
  — pas besoin d'un nouveau crontab).
- Purement lecture/analyse — ne modifie jamais le code ni l'état trading.

### Étape 1 — Qualifier le signal (fraîcheur + segmentation + narratif) — [FAIT 2026-07-19]
[FAIT 2026-07-19] Nouveau module `modules/signal_qualification.py`, branché
en lecture seule dans `auto_proposer.plan_proposals` (`context.qualification`,
fail-open — une erreur de qualification ne fait jamais perdre une
proposition) :
- Delta de score TITAN + flip de verdict `buy_signal` vs N jours en arrière
  (défaut 5j, aligné sur le cycle de rescoring budgété ~5j ouvrés), via
  `universe_history.ticker_history()` — zéro nouvelle infra, comme prévu.
  Limite documentée dans le module : `buy_signal` n'étant pas historisé, le
  verdict "N jours en arrière" est une RECONSTITUTION (`compute_buy_signal`
  rejoué sur le snapshot historique, sans le bloc `support` de l'époque).
- Segmentation en 3 catégories de conviction (`classify_conviction`) :
  🔥 Nouveau signal (verdict actionnable + changé) / ⭐ Confirmé (actionnable,
  stable — ou historique insuffisant, jamais affiché "nouveau" sans preuve)
  / 👁 Surveillance (verdict WATCH). Nouveau mode de tri "Conviction" côté
  `ProposalsPage.jsx` (badge + tooltip narratif), en plus de score/poids/buy_zone.
- **Narratif généré** (`build_narrative`) : synthèse une-phrase, pure
  formatage des ingrédients déjà dans `context` (Piotroski, momentum,
  support, revisions, earnings, tilt flags) — aucun nouveau calcul de score.
- **Fraîcheur causale** (`causal_reasons`) : quand le verdict a changé,
  croise `earnings_surprise.py`/insider (`insider_cluster_buying`)/
  `revisions_score` entre l'instantané de référence et aujourd'hui.
- Tests : `test_signal_qualification.py` (26) + 2 dans
  `test_auto_proposer_gates.py` (intégration + fail-open dédié).
- Aucune modification du verdict `buy_signal`, du sizing ou de la logique
  d'exit — lecture seule sur `universe_history`/`scored_universe`.

### Étape 2 — Condenser l'UI Proposals — [FAIT 2026-07-19]
[FAIT 2026-07-19] `ProposalsPage.jsx` : nouvelle section "🎯 Top picks du
jour" (jusqu'à 5 cartes, classées par conviction puis score TITAN — même
classement que le tri "Conviction" de l'Étape 1, filtre `pending`
uniquement), affichée au-dessus de la table. Chaque carte reprend le badge
buy-signal, le badge conviction et le narratif une-phrase déjà calculés par
`signal_qualification.py`, plus une checkbox reliée au même état de
sélection que la table (sélectionner depuis une carte suffit pour
approuver ensuite via l'ActionBar, sans ouvrir la table).
- Table détaillée rendue repliable (bouton "▾/▸ Table détaillée (N)"),
  expanded par défaut pour ne pas casser l'habitude d'approbation
  existante — le power-user peut la replier une fois les top picks
  suffisants.
- Colonnes "Tendance" (sparkline `TickerSpark`) et "Mom 6M" retirées de la
  table : doublons du chart prix (compact + TradingView) et du
  `momentum_6m_pct` déjà affichés dans `TickerAnalysisModal` (accessible en
  un clic sur le ticker).
- Aucun changement de logique trading/risk/sizing — travail de présentation
  pur sur `ProposalsPage.jsx`.

### Étape 3 — Proactivité réelle — [FAIT 2026-07-19]
[FAIT 2026-07-19] `modules/daily_digest.py` (`build_digest_text`) ne liste
plus le top-N par `titan_score` (quasi figé d'un jour à l'autre vu le cycle
de rescoring budgété ~5j, cf. diagnostic ci-dessus) mais uniquement les
propositions en `context.qualification.conviction == "new_signal"` (Étape 1
— verdict actionnable qui vient de changer), avec leur narratif une-phrase
déjà calculé par `signal_qualification.py`. `qualification=None` (fail-open)
n'est jamais compté "nouveau" sans preuve — même garde-fou qu'Étape 1.
S'il n'y a rien de neuf, le digest le dit explicitement au lieu de répéter
le même trio statique.
- Lien cliquable ajouté vers l'onglet Propositions (`#/proposals`) via une
  nouvelle variable optionnelle `FRONTEND_URL` (`config.py`, vide par
  défaut — aucune URL publique de prod n'existait dans la config avant ce
  run ; le digest fonctionne sans lien si elle n'est pas renseignée par
  l'utilisateur).
- Scope volontairement limité au digest quotidien (`daily_digest.py`),
  celui explicitement visé par le diagnostic ("digest Telegram matinal
  déconnecté du flow"). La notification `_notify_new_proposals`
  (`routers/proposals.py`, déclenchée à chaque refresh) n'est pas touchée —
  elle a déjà sa propre logique de "nouvelles propositions" au sens DB, pas
  du ressort de cette étape.
- Tests : `backend/tests/test_daily_digest.py` (7 tests — filtrage
  new_signal, fail-open qualification=None, lien présent/absent selon
  FRONTEND_URL, envoi Telegram fail-open).
- Aucune modification de logique trading/risk/sizing — présentation/
  notification pure.

### Étape 4 — Exploration (si besoin après 1-3) — [FAIT 2026-07-20]
[FAIT 2026-07-20] Screener multi-critères sauvegardable, débloqué sur
instruction explicite de l'utilisateur (2026-07-20 — "fais ce qui te
semble plus logique et opti") après lui avoir rappelé les 3 axes
d'ambiguïté (critères / lieu de persistance / UI). Choix retenu :
- **Critères** : panneau de seuils combinables en ET logique sur les
  champs déjà exposés par `/api/universe` (TITAN composite, 6 piliers
  Quality/Value/Risk/Sentiment/Momentum/Piotroski via `f_score`, market
  cap, forward P/E) — aucun champ backend nouveau, aucun endpoint dédié.
  S'ajoute aux filtres secteur/buy/status déjà présents dans
  `UniverseManagerPage.jsx`, pas une nouvelle page.
- **Persistance** : réutilise l'infra déjà en place plutôt qu'un nouveau
  mécanisme — auto-persist localStorage existant (reload ne perd pas la
  vue) + `PresetBar`/`utils/presets.js` déjà wiré scope `"universe"` pour
  la sauvegarde nommée (mêmes composants que le filtre secteur/buy
  existant, juste un payload étendu).
- **UI** : panneau repliable "🎛 Screener" au-dessus de la table, cohérent
  avec le style des autres contrôles de la page (pas de maquette séparée).
- Vérifié en local : lint + 45 tests vitest + build clean, et exercice
  navigateur réel (Playwright headless) — panneau ouvre/filtre
  correctement (ex : TITAN≥80 réduit 6→3 tickers), bouton Reset, et
  sauvegarde d'un preset nommé → reload → application du preset restaure
  bien les seuils et re-filtre (round-trip localStorage confirmé).
- Aucun changement backend, aucune logique trading/risk touchée — travail
  de présentation pur sur `UniverseManagerPage.jsx`.
- [FAIT 2026-07-19] Vue comparaison multi-tickers dédiée : nouvel endpoint
  `GET /api/compare` (`modules/peer_comparison.build_compare_table`, 2 à 8
  tickers choisis librement par l'utilisateur, pas de contrainte
  sectorielle) + page `ComparePage.jsx` (route `#/compare`, nav
  "Découverte"). Audit de `PeerComparison.jsx` fait : il reste dédié à
  l'auto-sélection sector/market_cap autour d'UN target (inchangé, toujours
  utilisé dans `TickerAnalysisModal`) — pas un doublon, cas d'usage
  différent (sélection libre vs auto). Config KPI/coloration commune
  extraite dans `utils/kpiCompare.js` pour éviter la duplication entre les
  deux vues. Débloqué sur instruction explicite de l'utilisateur
  (2026-07-19) : ambiguïté résolue en scope minimal (une seule des deux
  features du bullet), le screener restant `PAS COMMENCÉE` en tant que pas
  séparé (scope plus large : persistance des critères, UI de filtres — à
  spécifier avant implémentation).

### Étape 5 — Signal d'échec Finnhub pour `multi_source` — [FAIT 2026-07-20]
[FAIT 2026-07-20] Implémenté sur le patron exact décrit ci-dessous :
`_fetch_json` (`data_providers/finnhub_provider.py`) retourne désormais
`(payload, error)` — `error` est `None` même sur un payload vide/légitimement
absent (ticker sans couverture analyste), et n'est renseigné que sur un
échec réseau/HTTP explicite (`http_<code>`, `rate_limited` sur 429,
`fetch_failed` sur exception réseau). `get_revisions_and_earnings` accumule
les échecs des 4 endpoints (préfixés par endpoint, concaténés par `; ` si
plusieurs) dans `FinnhubData.error`. `finnhub_enrich.py` persiste ce champ
sous `finnhub_error` dans `universe.json`, **toujours réécrit** (même à
`None`) pour qu'un ticker "guérisse" au run suivant si l'échec était
transitoire — même mécanique que `insider_error` (Étape 0).
`data_confidence._multi_source_factor` pénalise `finnhub_error` truthy avec
la même magnitude (×0.85) et la même philosophie neutre-par-défaut que
`insider_error` ; les deux pénalités sont indépendantes et cumulatives (un
ticker en échec sur les deux sources tombe à ×0.7225, pas juste la pire des
deux). Docstring module mis à jour (les deux sources sont désormais
"branchées", plus seulement insider).
Hors scope conservé : `news` et les filings SEC 10-K/Q (toujours fetchés à
la demande, jamais persistés dans `universe.json`), comme documenté
initialement.
Tests : 4 nouveaux dans `test_finnhub_provider.py` (tuple `(payload, error)`,
distinction échec/absence légitime, concatenation multi-endpoints) + 3 dans
`test_finnhub_enrich.py` (succès sans erreur, propagation d'échec,
guérison au run suivant) + 4 dans `test_data_confidence.py` (absent neutre,
0-résultat légitime non pénalisé, pénalisé si erreur explicite, cumul avec
insider_error).
Zéro coût, zéro nouvelle source, aucune logique trading/risk touchée — pur
renforcement de la fiabilité de la couche data existante, comme prévu.

Preuve concrète relevée en code (pas une hypothèse), directement dans le
prolongement de la limite déjà documentée par Étape 0 dans
`data_confidence.py` (docstring `_multi_source_factor`, L17-36) : *"Finnhub
(revisions/earnings) et news n'ont pas encore de signal d'échec par-ticker
persisté (fail-open silencieux — un champ `None` peut aussi bien dire 'pas
d'info Finnhub' que 'endpoint en échec')"*.

Vérifié en lisant le code du provider : `FinnhubData` (`data_providers/
finnhub_provider.py`) a bien un champ `error: str | None = None` dans son
dataclass, mais il n'est **jamais assigné nulle part** dans
`get_revisions_and_earnings`/`_fetch_json` — chaque échec HTTP (timeout,
429 rate-limit, HTTPError, exception réseau) `return None` silencieusement,
indiscernable d'un ticker qui n'a légitimement aucune donnée. Côté
`modules/finnhub_enrich.py`, `n_errors` ne compte que les crashes non
gérés (`except Exception`), jamais ces échecs fail-open, et le champ
`fdata.error` n'est de toute façon jamais lu ni persisté dans
`universe.json`.

Implémenter demanderait, sur le même patron que `insider_enrich._enrich_one`
→ `insider_error` (Étape 0) :
1. Faire distinguer à `_fetch_json`/`get_revisions_and_earnings` un échec
   réseau/HTTP explicite (429, timeout, HTTPError, exception) d'un "0
   résultat légitime", et peupler `FinnhubData.error` en conséquence.
2. `finnhub_enrich.py` persiste ce signal dans `universe.json` (nouveau
   champ `finnhub_error`, même mécanique que `insider_error`).
3. Étendre `_multi_source_factor` (`modules/data_confidence.py`) pour
   pénaliser `finnhub_error` truthy avec la même magnitude (×0.85) et la
   même philosophie neutre-par-défaut que `insider_error` — aucune
   régression sur le comportement fondamentaux-only existant.
- Hors scope de cette étape : `news` (fetch à la demande, jamais persisté
  dans `universe.json` — persister nécessiterait une décision de design
  séparée, pas juste de l'instrumentation) et les filings SEC 10-K/Q,
  comme déjà noté par Étape 0.
- Zéro coût, zéro nouvelle source, aucune logique trading/risk touchée —
  pur renforcement de la fiabilité de la couche data existante.

### Étape 6 — Visibilité des compteurs `insider_error`/`finnhub_error` dans `/api/data_health` — [FAIT 2026-07-20]
[FAIT 2026-07-20] Implémenté exactement sur le patron décrit ci-dessous :
`_universe_inventory()` (`routers/data_health.py`) compte désormais les
tickers avec `insider_error`/`finnhub_error` truthy dans `universe.json` et
les expose sous `universe.enrichment_errors.{insider_error,finnhub_error}`
(même pattern que le comptage `sources` déjà présent, deux compteurs
indépendants). Côté UI, `DataHealthPage.jsx` affiche une ligne "Tickers en
échec (universe.json)" dans les blocs Finnhub et SEC insider de la card
"🌐 Sources enrichissement" existante — pas de nouvelle card, colorée en
warning si > 0. Aucun changement de `_global_severity()`/nouveau seuil
d'alerte, comme prévu (calibrage différé à un historique réel via
`data/metrics_history.jsonl`, Étape 0bis).
Tests : 1 nouveau dans `test_data_health.py` (comptage correct, insensible
aux valeurs `None`/absentes, séparé par source).
Zéro coût, zéro nouvelle source, aucune logique trading/risk touchée — pur
complément d'observabilité.

Preuve concrète relevée en code (pas une hypothèse) : `modules/insider_enrich.py`
(L47) persiste `insider_error` et `modules/finnhub_enrich.py` (L97) persiste
`finnhub_error` par ticker dans `universe.json` — les deux champs sont déjà
lus par `data_confidence._multi_source_factor` (Étape 0 et Étape 5) pour
pénaliser la confiance d'un ticker. Mais aucun des deux n'est agrégé nulle
part : `routers/data_health.py::_universe_inventory()` ne compte que les
champs de `_CRITICAL_FIELDS` (% manquant) et la répartition `source_provider`
— pas ces deux signaux d'échec. Côté UI, `DataHealthPage.jsx` (card "🏷
Sources enrichissement", ~L298) n'affiche que les stats de cache disque
(`n_cached`/`ages`/`n_errors` via `dir_cache_stats()`), pas de compte de
tickers actuellement en `insider_error`/`finnhub_error` truthy dans
`universe.json`. Résultat : le dashboard "toutes-sources" construit
spécifiquement par l'Étape 0 pour donner de la visibilité sur les sources
non-fondamentales reste aveugle aux deux signaux d'échec que l'Étape 0 et
l'Étape 5 viennent d'introduire — impossible aujourd'hui de voir "12
tickers actuellement pénalisés par finnhub_error" sans lire le JSON brut.

Implémenter demanderait : dans `_universe_inventory()` (ou un petit helper
dédié), compter les tickers avec `insider_error`/`finnhub_error` truthy
(même pattern que le comptage `sources` déjà présent), exposer ces deux
compteurs dans le payload `/api/data_health`, et les afficher comme deux
nombres simples dans la card "Sources enrichissement" existante de
`DataHealthPage.jsx` (pas de nouvelle card).

Hors scope explicite : ne touche pas `_global_severity()` / n'ajoute aucun
nouveau seuil d'alerte — le calibrage de seuils sur ces sources est
volontairement différé jusqu'à un historique réel observé via
`data/metrics_history.jsonl` (Étape 0bis), comme déjà noté par Étape 0. Ce
step-ci est un pur ajout de comptage/visibilité, aucune nouvelle logique de
sévérité.

Zéro coût, zéro nouvelle source, aucune logique trading/risk touchée — pur
complément d'observabilité sur la couche data déjà en place.

### Étape 7 — Lister les tickers `dq_sanitize` dans `/api/data_health` — [FAIT 2026-07-20]
[FAIT 2026-07-20] Implémenté exactement sur le patron décrit ci-dessous :
`list_flagged_tickers()` (déjà calculée par `fundamentals_cache.py`, même
source que `cache_sanitize_stats()`) est désormais incluse dans le payload
`GET /api/data_health` sous `sanitize.tickers` (liste triée de symboles).
Côté UI, `DataHealthPage.jsx` (card "🧹 Qualité données") affiche un bouton
repliable "▸ Tickers (N)" sous le tableau des tags existant — chips au clic,
cohérent avec le style déjà en place, pas de nouvelle card.
Tests : 2 nouveaux dans `test_data_health.py` (liste correcte quand des
tickers sont flaggés, liste vide quand le cache est propre).
Vérifié : suite backend complète (1010 tests, 3 échecs pré-existants sans
lien — confirmés en échec identique sur le commit de base avant ce
changement, environnement sandbox : chemin `/nonexistent/...` writable en
root, mapping secteur différent) + build frontend clean + 45 tests vitest +
lint clean.
Aucun changement à la logique de sanitization elle-même ni au comportement
du bouton "Rafraîchir", aucun nouveau seuil de severity — pur ajout de
lecture/présentation, comme prévu.

Preuve concrète relevée en code (pas une hypothèse) : `modules/fundamentals_cache.py`
(`list_flagged_tickers()`, L364) calcule déjà la liste triée des tickers
avec un flag `dq_sanitize=` actif (valeurs aberrantes clampées), mais cette
fonction n'est utilisée **que** par `routers/data_health.py::refresh_flagged_tickers`
(L297, endpoint POST déclenché par le bouton "refresh" de la page) — jamais
retournée par le GET `/api/data_health` lui-même. Ce dernier n'expose que
l'agrégat `cache_sanitize_stats()` (compteur par tag, ex.
`out_of_bounds:ev_to_ebitda: 12`), sans jamais dire **quels tickers**.
Côté UI, `DataHealthPage.jsx` (card "🧹 Qualité données", L210-237) affiche
le nombre total de tickers flaggés et le tableau des tags avec leur count,
mais aucune liste de symboles nulle part dans le frontend (confirmé :
`dq_sanitize` n'apparaît dans `frontend/src` que dans les types générés
`api/openapi.json`/`api/types.ts`, jamais dans un composant). Résultat :
un utilisateur qui voit "33 tickers flaggés" sur le dashboard n'a aucun
moyen de savoir lesquels sans lire le cache JSON brut sur le VPS ou
cliquer aveuglément sur "Rafraîchir" — alors que la donnée existe déjà
en mémoire côté backend.

Implémenter demanderait : inclure `list_flagged_tickers()` (déjà calculée,
même source que `cache_sanitize_stats()`) dans le payload `/api/data_health`
sous `sanitize.tickers` (liste de symboles), puis afficher ces symboles
dans `DataHealthPage.jsx` — par exemple une liste de chips repliable sous
le tableau des tags existant (cohérent avec le style déjà en place, pas de
nouvelle card). Pur ajout de lecture/présentation : aucun nouveau calcul,
aucune nouvelle source, aucun changement de `severity_global` ou de seuil
d'alerte.

Hors scope explicite : ne change rien à la logique de sanitization
elle-même (`data_validation.sanitize_ratios`), ni au comportement du bouton
"Rafraîchir" (`refresh_flagged_tickers`) — uniquement de la visibilité en
lecture sur une liste déjà calculée.

Zéro coût, zéro nouvelle source, aucune logique trading/risk touchée — pur
complément d'observabilité sur la couche data déjà en place.

### Étape 8 — Afficher le(s) tag(s) `dq_sanitize` par ticker (pas juste la liste) — [FAIT 2026-07-20]
[FAIT 2026-07-20] Implémenté exactement sur le patron décrit ci-dessous :
nouvelle fonction `list_flagged_tickers_with_tags()` (`modules/
fundamentals_cache.py`), qui réutilise le même parsing que
`cache_sanitize_stats()` (`err.partition("dq_sanitize=")` puis
`split("|")`) mais associé au ticker plutôt que sommé globalement. `GET
/api/data_health` (`routers/data_health.py`) expose désormais
`sanitize.tickers` comme une liste d'objets `{ticker, tags}` (au lieu de
simples symboles) — `list_flagged_tickers()` elle-même n'a pas changé de
signature, `refresh_flagged_tickers` continue à l'utiliser telle quelle.
Côté UI, `DataHealthPage.jsx` affiche les tags en `title=` (tooltip au
survol) sur chaque chip existant — pas de nouveau composant.
Tests : 4 nouveaux dans `test_fundamentals_cache.py` (vide, tag simple,
multi-tags, exclusion des tickers propres) + 1 nouveau dans
`test_data_health.py` (détail des tags exposé par l'endpoint) + le test
existant `test_data_health_sanitize_exposes_flagged_tickers` adapté à la
nouvelle forme `{ticker, tags}`.
Vérifié : suite backend complète (1012 tests, mêmes 3 échecs
pré-existants sans lien que l'Étape 7 — confirmés identiques sur le
commit de base avant ce changement, environnement sandbox) + build
frontend clean + 45 tests vitest. `npm run check:types` non concluant
dans ce sandbox : diff déjà présent sur la baseline avant tout changement
(dérive de génération OpenAPI liée aux versions de dépendances du
sandbox, confirmée en stashant les changements et en relançant sur
`origin/main` tel quel) — non lié à ce step, `sanitize.tickers` n'étant
de toute façon pas typé (l'endpoint n'a pas de `response_model`).
Aucun changement à `sanitize_ratios`, au bouton "Rafraîchir", ni à
`severity_global` — pur enrichissement de présentation, comme prévu.

Preuve concrète relevée en code (pas une hypothèse) : l'Étape 7 a exposé
`sanitize.tickers` (liste triée de symboles) via `list_flagged_tickers()`
(`modules/fundamentals_cache.py:364-376`), affichée en chips repliables dans
`DataHealthPage.jsx` (~L239-260). Mais cette fonction ne retourne que le
symbole — elle jette l'information de tag au passage (`if "dq_sanitize=" in
err: flagged.append(ticker)`, L374-375). Le tag lui-même (ex.
`out_of_bounds:ev_to_ebitda`, `mcap_mismatch`) existe déjà par entrée de
cache et est déjà parsé ticker par ticker dans `cache_sanitize_stats()`
(même fichier, L379-407 : `_, _, tag_part = err.partition("dq_sanitize=");
tag_part.split("|")`) — mais cette fonction ne fait que sommer les tags
**globalement** (`flags_count[tag] += 1`), sans jamais associer un tag à
son ticker dans le payload retourné. Résultat vécu : le dashboard dit "33
tickers flaggés" + un tableau agrégé "out_of_bounds:ev_to_ebitda: 12", et
depuis l'Étape 7 la liste des 33 symboles — mais un utilisateur qui clique
sur le chip "AAPL" ne peut toujours pas savoir *pourquoi* AAPL est flaggé
(quel ratio, quelle raison) sans aller lire le cache JSON brut sur le VPS.
C'est exactement le même type de trou que celui qu'a comblé l'Étape 7
(liste sans détail), une itération plus loin (détail par ticker).

Implémenter demanderait : réutiliser le même parsing déjà fait dans
`cache_sanitize_stats()` (`err.partition("dq_sanitize=")` puis
`split("|")`) pour construire, dans `list_flagged_tickers()` ou un helper
voisin, un mapping `{ticker: [tags]}` plutôt qu'une simple liste de
symboles ; exposer ça sous `sanitize.tickers` en remplaçant les strings
par des objets `{ticker, tags}` (ou une structure équivalente qui reste
rétro-compatible avec le rendu existant) dans `routers/data_health.py`
(~L245-247) ; côté `DataHealthPage.jsx`, afficher les tags au survol/clic
de chaque chip (tooltip `title=` suffit, cohérent avec le style léger déjà
en place — pas besoin d'une nouvelle card ni d'un nouveau composant).
Aucun nouveau calcul : le tag est déjà présent dans `entry["ratios"]["error"]`
pour chaque ticker du cache, seule la structure de sortie change.

Hors scope explicite : ne touche pas à `sanitize_ratios`
(`data_validation.py`) ni au bouton "Rafraîchir" (`refresh_flagged_tickers`)
— pur enrichissement de présentation d'une donnée déjà calculée, comme
l'Étape 7. Aucun nouveau seuil de `severity_global`.

Zéro coût, zéro nouvelle source, aucune logique trading/risk touchée — pur
complément d'observabilité sur la couche data déjà en place.

### Étape 9 — Croiser les 8-K (événements matériels SEC) dans le narratif causal — PAS COMMENCÉE

Preuve concrète relevée en code (pas une hypothèse) : `causal_reasons()`
(`modules/signal_qualification.py:220-255`) construit le "pourquoi maintenant"
d'un signal en croisant exactement 3 sources entre l'instantané de référence
et aujourd'hui — `earnings_surprise` (L233-241), `insider_cluster_buying`
(L243-246), `revisions_score` (L248-253). Aucune trace d'événement matériel
SEC (dépôt 8-K) dans cette liste, alors que c'est l'un des catalyseurs
"pourquoi maintenant" les plus directs qui existent (rachat d'action annoncé,
changement de direction, guidance révisée, fusion/acquisition — tout ce qui
déclenche un 8-K). C'est exactement le type de trou que la Vision du document
cible ("pourquoi celui-là, pourquoi maintenant").

Ce qui rend l'ajout bon marché : la donnée est déjà fetchée quotidiennement
pour tout l'univers, sans nouvel appel réseau. `insider_enrich.py` appelle
`sec_edgar.fetch_insider_activity()` pour chaque ticker (déjà branché en
cron), qui télécharge et parse `data.sec.gov/submissions/CIK{cik}.json` —
mais la boucle de parsing (`sec_edgar.py:425-447`) filtre `if form not in
("4", "4/A"): continue` (L426) et jette silencieusement tous les autres
types de filing présents dans le même payload déjà en mémoire, y compris les
8-K. Extraire en plus la date du 8-K le plus récent dans cette même boucle
ne coûte aucune requête SEC supplémentaire — pure extraction d'une donnée
déjà téléchargée et parsée.

(Vérifié aussi : `sec_edgar.fetch_recent_filings()`, utilisé uniquement à la
demande par `routers/sec_filings.py`, calcule déjà `days_ago` par filing y
compris pour les 8-K — confirme que la donnée existe et est bien formée,
seulement jamais persistée ni utilisée pour la qualification.)

Implémenter demanderait :
1. `sec_edgar.InsiderActivity` : ajouter un champ `most_recent_8k_date: str
   | None`, peuplé dans la même boucle `fetch_insider_activity()` qui itère
   déjà `forms`/`dates` (pas de nouveau fetch, juste une 2e condition dans
   la boucle existante).
2. `insider_enrich._enrich_one` : persister ce champ dans `universe.json`
   (même mécanique que `insider_error`/`insider_most_recent`).
3. `signal_qualification.causal_reasons` : ajouter une 4e comparaison —
   si un 8-K est apparu entre l'instantané de référence et aujourd'hui,
   ajouter une raison du type "Événement matériel déposé (8-K, {date})".
   Même garde-fou que les 3 sources existantes : lecture seule sur des
   champs déjà dans `scored_row`/`universe_history`, aucun nouveau calcul
   de score, aucun impact sur `compute_buy_signal`/sizing/exit.

Hors scope explicite : ne touche pas au pilier Insider
(`compute_insider_pillar_score`) ni à aucun poids de scoring — uniquement
une extraction de donnée déjà fetchée (data-provider layer) et un ajout de
raison narrative (présentation/qualification layer), comme les étapes
précédentes.

Zéro coût, zéro nouvelle source, aucune logique trading/risk touchée — pur
enrichissement du narratif causal déjà en place (Étape 1).

## Notes de méthode

- Aucune étape ne touche à la logique trading/risk (gates, killswitch,
  sizing) — c'est strictement de la couche qualification/présentation du
  signal, pas de nouvelle prise de décision automatisée.
- Chaque étape se ferme par un test manuel de l'utilisateur en conditions
  réelles avant de passer à la suivante.

## Automatisation (2026-07-18)

Une routine cloud ("SwingQuant Roadmap Builder") exécute ce roadmap étape
par étape en autonome pendant que l'utilisateur est déconnecté du VPS —
même mécanique que "SwingQuant CI Auto-Fix" déjà en prod : push direct sur
`main`, auto-pull + auto-restart côté VPS, aucune action manuelle de
déploiement nécessaire. 1 étape par déclenchement (pas tout d'un coup) pour
rester revue-able. Garde-fous codés dans la routine :
- Jamais de logique trading/risk/ordre/killswitch/sizing.
- Jamais de souscription/API payante activée automatiquement.
- Si tests rouges après 2 tentatives, ou si l'étape nécessiterait de toucher
  au trading/risk : la routine s'arrête et écrit une note `BLOCKED` ici au
  lieu de forcer.
- Une fois 0→4 fait, la routine passe en mode "suivi" (health check léger,
  pas de nouvelle scope inventée seule).
