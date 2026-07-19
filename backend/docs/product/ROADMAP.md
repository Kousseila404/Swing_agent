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

### Étape 0 — Fondation data — PAS COMMENCÉE
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
- Généraliser `data_confidence.py` à toutes les sources, pas seulement
  fondamentaux. **Attention en l'implémentant** : ce module alimente déjà
  `_sizing_buffett.apply_buffett_tilt` et `lt_exit_policy.decide`
  (inhibition EXIT_VALUATION) — donc de la logique de sizing/exit réelle.
  Généraliser la formule sans changer le comportement des tickers déjà
  couverts (fondamentaux) demande un jugement humain sur comment isoler le
  nouveau facteur multi-source ; ne pas reformuler l'existant à la volée.
  **BLOCKED (2026-07-18):** vérifié en code — `confidence_score` gate
  directement `EXIT_VALUATION` et `confidence_drop`/WARN dans
  `lt_exit_policy.decide` (`lt_exit_policy.py:344-427`) et pondère le tilt
  de sizing dans `_sizing_buffett.apply_buffett_tilt`
  (`_sizing_buffett.py:154-155`). Généraliser la formule multiplicative de
  `compute_confidence` pour absorber finnhub/insider/SEC/news sans
  définition humaine de comment isoler ce nouveau facteur risquerait de
  faire glisser silencieusement les scores de confiance — donc le
  sizing/exit — sur des tickers déjà en prod. Reste exactement le jugement
  humain que ce bullet demande déjà ; un agent autonome ne tranche pas ce
  choix. Prochain run : passer au bullet suivant (`/api/data_health`
  toutes-sources) tant que celui-ci n'est pas débloqué par l'utilisateur.
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

### Étape 0bis — Agent d'analyse des chiffres (local, pas cloud) — PAS COMMENCÉE
Découverte en configurant l'automatisation : un agent cloud n'a accès ni aux
secrets `.env`, ni à l'API live, ni au bot Telegram — seulement à un clone
Git. Donc ce n'est PAS une 2e routine cloud, c'est une fonctionnalité à
coder et faire tourner via le cron local existant (`run_titan.sh`, qui a
déjà accès à tout) :
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

### Étape 1 — Qualifier le signal (fraîcheur + segmentation + narratif) — PAS COMMENCÉE
Exploite `universe_history` (déjà en place, zéro nouvelle infra) :
- Delta de score / flip de verdict vs N jours en arrière.
- Segmentation en 3 catégories de conviction plutôt qu'un tri plat :
  🔥 Nouveau signal (verdict vient de changer) / ⭐ Confirmé (stable, fort)
  / 👁 Surveillance (proche du seuil, pas encore actionnable).
- **Narratif généré** par ticker : synthèse en une phrase du "pourquoi"
  (Piotroski, momentum, support, earnings, tilt) au lieu de badges bruts —
  tous les ingrédients existent déjà dans `context`, c'est de la synthèse.
- **Fraîcheur causale** : quand le verdict change, croiser avec
  `earnings_surprise.py`/`insider_enrich.py`/`revisions_score.py` pour dire
  *pourquoi* ça a bougé, pas juste que ça a bougé.

### Étape 2 — Condenser l'UI Proposals — PAS COMMENCÉE
- Vue "résumé" (3-5 top picks en cartes) au-dessus de la table détaillée,
  table repliable pour les power-users.
- Réduire les colonnes par défaut ; le détail existe déjà via
  `TickerAnalysisModal`, pas besoin de le dupliquer en colonnes.

### Étape 3 — Proactivité réelle — PAS COMMENCÉE
- Le digest Telegram devient un vrai call-to-action (lien direct vers la
  proposition), pas une info à côté.
- Notifications ciblées sur signal **nouveau/changé** uniquement — pas de
  répétition du même score statique jour après jour.

### Étape 4 — Exploration (si besoin après 1-3) — PAS COMMENCÉE
- Screener multi-critères sauvegardable.
- Vue comparaison multi-tickers dédiée (au-delà de `PeerComparison`
  existant, à auditer avant de dupliquer).

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
