# Audit intégral — 2026-11-18

Audit en lecture seule mené par 11 agents en parallèle (ops, quant, chaîne de
trading, données, architecture/tests, frontend, sécurité, argent réel eToro,
risque/construction, performance API, produit/roadmap). **Aucun correctif
appliqué** : ce document stocke les constats pour traitement ultérieur.

Statut : 🔴 ouvert. Les numéros de ligne sont ceux du commit `faca7b8`.

---

## 0. Synthèse

**Verdict** : le logiciel est solide techniquement (1 441 tests verts, pas de
secret fuité, durcissements du 2026-09-17 présents) mais ne produit pas de
valeur mesurable :

- **TITAN n'a pas d'edge en live** : rank-IC 21 j = −0,048 (composite),
  −0,047 (equal_7), t ≈ −1 sur 6 périodes indépendantes (2026-04-22 → 2026-10).
  Top-20 − univers EW = −0,85 %/21 j avant coûts.
- **Shadow A/B 2026-09-17 → 2026-11-18** : equal_7 −7,5 %, v14_1 −6,3 %,
  momentum_tilt −9,0 %, momentum_only −12,4 % ; universe_ew **+3,3 %**,
  RSP +1,4 %, SPY −4,0 %.
- **54 % de l'historique est contaminé** (245/455 snapshots = bootstrap simfin
  avec fondamentaux figés à 2026) et le WFO l'utilise → son IC +0,041 est invalide.
- **Paper Alpaca** : 4 positions, ~91 % cash, 0 proposition depuis le 2026-09-16,
  9 trades clos depuis avril (6 W ≈ +4 %, 3 L ≈ −19 %), PnL réalisé −1 225 $.
- **Argent réel (eToro ~10 k$)** : 59 % en copy-trading, invisible pour l'app.
- L'ancienne affirmation « edge ~+4 pts vs univers EW » est **infirmée**.

### Liste P0 consolidée

| # | Problème | Preuve | Effort |
|---|---|---|---|
| 1 | OOM API : `_SNAP_CACHE` garde 455 snapshots décompressés (~2 Go) | `backend/modules/backtest.py:654-681`, `perf_benchmark.py:188` ; 4 OOM 16–18/11 | M |
| 2 | Warm benchmark 06:28 recalcule le backtest dans l'API chaque jour (cache invalidé par `n_snapshots`) → refresh proposals KO | `run_titan.sh` step 4b ; `titan_daily.log` rc=56/7 | S |
| 3 | Score insider faux : chaque Form 4 compté comme achat ; `filer_id` = agent de dépôt | `modules/sec_edgar.py:485-497` ; 134/510 tickers `insider_cluster_buying` | S neutraliser / M parser XML |
| 4 | WFO calibré sur snapshots bootstrap (look-ahead) | `modules/wfo_calibration.py:328` ; flag `_bootstrap_lookahead` jamais lu | S |
| 5 | Shadow/WFO calculent des rendements sur `current_price` figés (rotation par lots) | `shadow_portfolios.py:120`, `wfo_calibration.py:176` | S |
| 6 | Cache fondamentaux sans verrou inter-process (temp commun, copie mémoire périmée) | `modules/fundamentals_cache.py:50,114-142` | S |
| 7 | Sizing incompatible 10 k$ : 0 position dans la plupart des régimes ; capital codé 100 k$ | `auto_proposer.py:95,101` ; `portfolio/_vol_target.py:45-55` | S |
| 8 | Killswitch : gel permanent possible (reprise à 96 % du pic, sans timeout) | `tracker/killswitch.py:84,101-146` | S |
| 9 | CI rouge : mypy `type-var` | `modules/sentinel.py:141` | S |
| 10 | requirements ≠ venv prod (3 fichiers divergents) ; CI/Docker testent une autre pile | `requirements*.txt` | S |
| 11 | Frontend : 401 silencieux (Cockpit affiche « — » / « aucune proposition ») | `CockpitPage.jsx:134-143`, `api/client.js` | S |
| 12 | Accueil = paper seulement ; aucune action « aujourd'hui sur eToro » | `App.jsx:69`, `CockpitPage.jsx:34,192` | M |
| 13 | Copy-trading (59 % du compte) invisible ; poids du book faux d'un facteur ~5 | aucune occurrence mirror/copy dans `routers/`, `modules/my_portfolio*` | M |

---

## 1. Ops & fiabilité

- **Gate allocation (sentinel CRIT, 45 j ouvrés sans proposition)** — deux causes
  empilées, corrigées dans le code le 2026-11-18 mais **non appliquées** :
  - A : plancher 2 500 $ vs VIX 25,46 → vol-target ×0,51 × régime ×0,6 → 730–1 900 $/ligne.
    `DEFAULT_MIN_PROPOSAL_USD` abaissé à 500 (commit ee82021).
  - B : `KeyError: 'MU'` à `portfolio/_manager.py:539` (corrigé ddf0262).
  - Dry-run `plan_proposals()` : 16 propositions, n_kept=20, investi 24 955 $.
  - `proposals_last_refresh.json` : ran_at 2026-11-17T06:01Z ok=false ; `proposals.json` inchangé depuis le 17/09.
  - Action en attente : lancer un refresh réel.
- **OOM** : cgroup `MemoryMax=2G` (VM pas saturée : 2,6 Go dispo). Kills 18/11 06:29, 22:27, 22:46 ;
  OOM global VM 22:59 (conteneur docker voisin tué). Le sentinel n'en compte qu'1 sur 3.
  Correctifs : proposer en CLI depuis run_titan.sh (plus de dépendance au POST API),
  `MemoryHigh=1500M`, warm hors API, profiler (tracemalloc).
- **P1** run_titan.sh : 3 retries × 15 s ne couvrent pas un kill OOM ; « DONE … proposals KO »
  traité comme succès partiel → exit ≠ 0 + Telegram.
- **P1** Observabilité : aucune alerte sur `last_refresh_ok=false`, cash > 50 %, restarts systemd,
  RSS. Cron sentinel (ADR-018) **absent de la crontab**.
- **P1** git_auto_sync : commits chaque minute d'états intermédiaires ; `logs/git_auto_pull.log`
  12 Mo sans rotation ; `~/.git-credentials` token en clair (600). Débounce 5 min recommandé.
- **P2** Logs morts dans `backend/logs` (cron_scan, cron_morning, dashboard, strategy_research_*) ;
  thesis_review_bridge.log 2,2 Mo, telegram_inbox.log 1,1 Mo, api.log 1,8 Mo sans rotation ;
  pas de `flock` sur tracker/telegram_inbox/alpaca-sync. Disque 59 %.

## 2. Quant / edge

- **IC live** (210 snapshots 2026-04-22 → 11-18, seuls OOS) : 21 j −0,048 (t −0,99, IC>0 29 % des jours) ;
  63 j IC>0 6 % des jours ; 5 j t −0,01. Scripts : scratchpad session `ic.py`, `eq7.py`, `classify.py`.
- **Par pilier (21 j)** : Value +0,068 (t +0,75) ; Risk −0,074 ; Momentum −0,073. Aucun t ≥ 2.
- **Biais** : look-ahead (bootstrap `simfin_bootstrap.py:9-15`, ex. `snapshot_20210817`
  avec `fundamentals_period_end: 2026-04-30`) ; survivorship (S&P 500 actuel rétroactif ;
  EA, KVUE, WBD, BK, CTRA, AVB, EQR, PSKY, SATS, VYLR sans prix) ; data snooping
  (`scoring_lab.py:93-110` 14 variantes + 3 régimes) ; modèle changé pendant la fenêtre live (V14 → V14.1 → equal_7).
- **Sur-paramétrage** : 364 constantes numériques de module, ~110 littéraux dans
  `sector_metrics/_scoring.py` (1 535 l.), aucune validée OOS.
- Poids WFO non appliqués en live (`titan_weight_mode=None`) — heureusement.
- Prix cible : IC 0,127 in-sample seulement.
- Shadow : cash négatif (~−45 $) sur tous les profils (bug mineur).
- **À construire pour juger l'edge** : exclure bootstrap (S) ; prix ajustés journaliers pour shadow/WFO (S) ;
  tableau IC permanent net de coûts avec règle d'arrêt écrite (S) ; backtest 20 ans momentum 12-1 +
  quality (gross profitability) sur univers historique (M) ; fondamentaux PIT via SEC companyfacts (L).
- **Simplification suggérée** : TITAN en observation ; cœur eToro indiciel (RSP/SPY) + éventuelle
  poche quality+momentum mensuelle top-30.

## 3. Chaîne de trading (correction)

pytest : 1 441 passed, 126 s. **P0 : aucun.**

- **P1-1** Rebalance sans garde de séance ; 3 essais (15:00/15:30/16:00) un jour férié brûlent le mois,
  fenêtre sans stop à chaque essai — `basket_rebalance.py:116-148`, `broker_gateway.py:1211-1330`.
- **P1-2** Rotation : annule les jambes stop avant fill confirmé (7,5 s) → position sans stop —
  `broker_gateway.py:747,808`, `basket_rotation.py:134-160`.
- **P1-3** Rotation vend tout si l'univers scoré est partiel (ticker absent = hors panier) —
  `basket_rotation.py:62-75,106-113`. Ne pas incrémenter si couverture < 90 %.
- **P1-4** Double achat : `client_order_id` aléatoire (`broker_gateway.py:54-65`) ; timeout après
  acceptation → re-pending → re-soumission — `routers/proposals.py:871-910`. Id déterministe `SQ-<TICKER>-<id>`.
- **P1-5** Ordre envoyé mais absent du journal : `_log_to_csv` avale les exceptions — `broker_gateway.py:618,678`.
- **P1-6** Killswitch fail-open : `basket_rotation.py:99`, `basket_rebalance.py:122`, `auto_approve.py:145,152`,
  `is_trading_allowed` rend True si `trading_state.json` illisible.
- **P2** : `shadow_update_status` non protégé (`broker_gateway.py:829`) ; `open_tickers=set()` sur erreur
  (`routers/proposals.py:664`) ; proposition orpheline `approved` si `get_broker()` lève (`:873`) ;
  réalignement prix live sans plafond d'écart ni recalcul de size (`:783`) ; equity fallback 100 000
  si broker KO (`killswitch.py` ~197) ; gel bloque aussi les ventes de rotation (`basket_rotation.py:95`) ;
  `get_clock` en échec laisse passer (`broker_gateway.py:514`), pas de calendrier NYSE ;
  rebalance lit `equity_state` et non les positions broker (`basket_rebalance.py:93`).
- Tests manquants : rebalance marché fermé, rotation univers partiel, timeout+retry, `_log_to_csv` KO, state JSON corrompu.

## 4. Données

- Fraîcheur : 510 tickers, `fetched_at` médiane 4 j (max 7) ; period_end médiane 49 j.
  68 % ont les 18 champs cœur. Manquants : revisions_net_score 39 %, 5y div yield 37 %,
  dividends_paid 20 %, D/E 11 %. 32 tickers `dq_sanitize`. Source unique yfinance (510/510).
- **P0** insider (cf. §0 #3) ; garde-fou ventes `lt_exit_policy.py:431` ne peut jamais se déclencher ;
  booster `buy_signal.py:265-270` et kicker `_sizing_buffett.py:160+` contaminés.
- **P0** cache fondamentaux sans lock (cf. §0 #6) ; scheduler lançable depuis l'UI (`routers/universe.py:315`, `routers/data_health.py:363`).
- **P1** `universe.json` réécrit sans lock pendant 27 min par `finnhub_enrich.py:104`, `insider_enrich.py:99`.
- **P1** Écritures non atomiques : `equity_state.json` (`tracker/killswitch.py:360`), `macro_engine.py:76`, alert_cooldown (`tracker/evaluation.py:100-221`).
- **P1** `market.duckdb` absent → `read_ohlcv` toujours None → `yf.download` à chaque ATR/support/ticker_analysis.
- Dépendance yfinance : 17 modules ; FMP désactivé ; Polygon unitaire. Remplacements : SEC companyfacts
  (fondamentaux PIT, L), Alpaca bars IEX + Stooq (OHLCV, M), Tiingo ~10 $/mois en option.
- **P2** survivorship (delisted non réinjecté) ; journal CSV + miroir DuckDB ; `.archive` 43 Mo,
  `index.json.gz.bak`, 86 entrées cache > 7 j à purger ; `watchlist.duckdb` encore lu.

## 5. Architecture, dette, tests

- Backend : 67 k lignes (45,5 k code + 21,8 k tests), 84 modules plats, 8 sous-packages, 22 routers.
- **P1** 5 couches de sortie dans `tracker/evaluation.py::evaluate_trades` (~690 l., McCabe 93) sans arbitre.
- **P1** Modes basket + legacy (`STRATEGY_MODE`) partout ; `_hrp`, `_risk_parity`, `_sizing_buffett` legacy only.
- **P1** Couverture (`coverage.xml` du 13/07, périmé) : broker_gateway 51 %, killswitch 63 %, api_core 56 %,
  market_db 15 %, alerter 8 %, macro_engine 0 %, perf_metrics 8 % ; routers/api.py/tracker.py jamais mesurés ;
  seuil CI 55 % vs `.coveragerc` 60 %.
- **P1** 469 `except Exception`, 83 `except: pass`, 152 imports en fonction ; mypy laxiste hors `portfolio/`.
- **P2 code mort** : `indicators.py` (181), `price_target_calibration.py` (416, test seul),
  `simfin_bootstrap` (351), `universe_history_bootstrap` (358), façade `portfolio_engine.py`.
- **P2 dépendances mortes** : vectorbt, numba, llvmlite, optuna, QuantStats, pyarrow, tabulate, colorlog,
  structlog, schedule, vaderSentiment, bs4 (+ lxml/tenacity/yaml/curl_cffi à vérifier) ; jupyter/matplotlib/seaborn en dev.
- **P2** perf fragmenté (perf_metrics, perf_benchmark, performance_attribution, gap_attribution = 1 242 l.) ;
  risk / portfolio_risk / correlation_check se recouvrent.
- Complexité : evaluate_trades 93, `_score_universe` 52, compute_buy_signal 42, plan_proposals 41,
  calculate_allocations 35, lt_exit_policy.decide 34. `broker_gateway.py` 1 743 l.
- Docs : 3 arborescences (`documentation/`, `docs/`, `backend/docs/`), 31 .md, `docs/` époque swing.
- **Régime minceur estimé** : ~3 000–3 600 LOC (7–8 %).

## 6. Frontend & UX

- lint OK, Vitest 58/58 (utils seulement), build 2,7 s, chunk principal 71 kB gz, recharts 96 kB gz.
  7 routes. Orphelins : `TickerSpark.jsx`, `src/api/types.ts` (4 764 l. jamais importé).
- **P0** 401 silencieux ; accueil sans vrai argent ; signaux eToro non triés (EQT `declenche` au milieu).
  Proposition : carte « Aujourd'hui · eToro » (signal déclenché, dérive, earnings < 7 j, revue due ; « Rien à faire » sinon).
- **P1** « 10 positions » codé en dur (`MyPortfolioPage.jsx:156`, `nav.js:58`) ; 20 hooks et 9 fonctions client inutilisés ;
  11 `PAGE_PALETTE_HINTS` obsolètes ; ~20 endpoints jamais appelés par le front (à vérifier crons/Telegram avant suppression) ;
  script TradingView tiers dans l'origine qui détient le token, pas de CSP.
- **P2** accessibilité (tuiles sans clavier, modales sans role/focus trap), styles inline massifs,
  tableau 9 colonnes illisible mobile, recharts non lazy, aucun test de composant.
- Bruit : 5 pastilles système → 1 ; cartes shadow/rebalance → page Paper.

## 7. Sécurité

Sains : secrets en 600 et ignorés, aucun secret dans 461 commits, uvicorn sur 127.0.0.1, TLS nginx,
CORS strict, POST/PATCH/DELETE authentifiés fail-closed, Telegram filtré par chat_id, pas d'injection SQL/shell/path.
`npm audit` 0 vuln ; pip-audit non disponible. Visibilité du dépôt GitHub non vérifiée.

- **P1** Service sous `swing` avec `sudo NOPASSWD: ALL` + groupe docker ; `systemd-analyze security` 9.2 UNSAFE.
- **P1** GET non authentifiés exposés (nginx `auth_basic off` sur `/api/`) : watchlist, notes, price_alerts,
  news firehose (révèle les positions), veto-history, sector_benchmark/portfolio, jobs, audit/full, wfo.
- **P1** `git_auto_sync.sh` : dossiers non suivis non scannés (`:99`), motifs de secrets incomplets (`:66`).
- **P1** Auto-pull fail-open si CI ne répond pas (`:224`) ; réponse API GitHub interpolée dans un heredoc (`:166`).
- **P2** comparaisons de token non constantes (`api_core.py:116,143`) ; `VITE_API_TOKEN` dans le bundle JS ;
  pas de rate-limit ni validation de longueur ticker sur GET coûteux ; streamlit encore installé ;
  hook detect-secrets commenté.

## 8. Argent réel (eToro)

Faits au 18/11 : compte Trading 10 017 $ (cash 1 387 $, latent +559 $) ; cash EUR 1 220 € hors app ;
16 lignes directes (~3 000 $) ; 4 copies = 5 935 $ (leandrool 1 801, FundManagerZech 1 725,
robchamow 1 286, petelu 1 125).

- **P0** Copy invisible ; book dimensionné 2 000 $ + 200 $ de réserve vs ~10 k$ réels.
- **P0** Pas de synchro eToro automatique (book saisi à la main).
- **P0** Aucune action claire par ligne (signal EQT `declenche` sans conséquence affichée).
- Réconciliation : quantités/PRU OK sur 15 lignes ; KMAR.OL et SAGE CVR (reliquats) absents du book ;
  « LNVGY » (thèse) vs « 0992.HK » (eToro) ; TP NEM 1 042,69 $ (typo probable) à vérifier côté eToro.
- Lenovo 0992.HK résultats 19/11 ; MU earnings 15/12 ; corrélation ERO/MU 0,45.
- Risque agrégé directes : bêta 0,57, corrélation moyenne 0,08.
- **P1** perf comparée impossible proprement (snapshots par bloc + TWR vs SPY à construire) ;
  alerte d'allocation ignore copy et cash EUR ; SL de copie robchamow à 50 % non affiché.
- **P2** journal des décisions manuelles ; frais FX/overnight non suivis ; fiscalité CTO FR ; soldes multi-comptes.

## 9. Risque & construction

11 mécanismes de sortie : SL catastrophe 3,5σ plafonné −35 % (`_trade_levels.py:42,59`) ; TS 15 %/30 %
(`config.py:113-117`) ; TIMEOUT 60 j (`config.py:98`) ; lt_exit (informatif) ; thesis_stop (alerte) ;
rotation rang > 40 / 5 j ; rebalance 1/N mensuel ; killswitch −6 %/j ; circuit breaker −8/−12/−16 % ;
portfolio stop −15 %/60 j ; gate régime BULL + bear hedge SH 10 %.
Sorties mécaniques automatiques, sorties fondamentales manuelles : l'inverse du style LT.

Sizing (ligne = cash × régime × vol-target / 20) :

| VIX | Paper 91 k$ | eToro 10 k$ | eToro hors copy ~4,1 k$ |
|---|---|---|---|
| ≤ 15 | 4 553 $ | 500 $ | 205 $ |
| 20–25 | 3 415 $ | 375 $ | 154 $ |
| 25–30 | 1 393 $ | 153 $ | 63 $ |
| ≥ 30 ou S&P < EMA200 | 0 | 0 | 0 |

- **P0** gel permanent (cf. §0 #8) ; sizing 10 k$ (cf. §0 #7).
- **P1** rebalance renforce les perdants près du SL et vend les gagnants (`basket_rebalance.py:45`),
  cible 1/N sur N détenues (4) et non 20 ; ignore `ADD_ON_MIN_BUFFER_TO_SL` (`lt_exit_policy.py:80`).
- **P1** mode basket ignore la règle d'achat utilisateur (plancher 60 vs 80, WAIT_PULLBACK non bloquant, `auto_approve.py:62`).
- **P1** TS 15/30 et TIMEOUT 60 j contredisent l'horizon LT. Stops réels −35 % (MU à 2,5 % du SL) vs règle « −30 % max » d'avril.
- **P2** branche BEAR ×0,5 morte (`risk.py:164`) ; doc VIX < 25 vs code 30 (`macro_engine.py:538`) ;
  historique circuit breaker pollué par fallbacks 100 000.
- Alpha du bucket TITAN ≥ 80 sur 5 ans = 111 % de 2 tickers (audit 2026-08-16) → idiosyncratique.
- **Cadre minimal proposé** : 8–10 lignes EW avec fractions ; une seule bride (100 % si S&P > MA200, sinon 50 %) ;
  SL catastrophe −30 % seul + EXIT_THESIS ; portfolio stop = alerte (ou gel ≤ 20 j) ;
  rotation trimestrielle rang > 2N ; sizing sur equity réelle ; retirer bear_hedge, haircut corrélation, tilt Buffett.

## 10. Performance API

- **Incident pendant l'audit** : `GET /api/performance/benchmark` a fait passer l'API de 238 Mo à 2 056 Mo en 35,7 s
  (PID 2975 bloqué à MemoryMax, 660 Mo swap). Redémarrage `sudo systemctl restart swing-api` à faire par l'utilisateur.
- **P0** `_SNAP_CACHE` (cf. §0 #1) ; rechargement total au changement de mtime avec ancienne liste encore référencée (pic ×2).
- **P0** warm benchmark quotidien dans l'API (cf. §0 #2).
- **P1** `/api/performance/gap` relit les .gz à chaque requête (3,4 s) — `gap_attribution.py:144-163`.
- **P1** Jobs lancés par l'API (`api_core.launch_job`, `api_core.py:374`) partagent le cgroup 2 Go → `systemd-run --scope`.
- **P1** `--reload` était actif en prod jusqu'au 18/11 23:04 (8 955 fichiers scrutés, ~75 % d'un cœur) ; corrigé dans l'unit,
  `start.sh:21` le garde encore.
- **P1** scoring_lab du dimanche charge tous les snapshots (pic cron 1,9 Go).
- **P2** `AlpacaBroker._get_client` sans lock (15 clients créés en 1 s, `broker_gateway.py:428`) ;
  endpoints lents : recommendations 8,0 s / 1,4 s, universe 2,4 s (1,69 Mo, sans pagination),
  firehose 2,5 s, monitor/preview 1,8 s sans cache, my_portfolio 1,6 s (ThreadPoolExecutor par requête, `my_portfolio.py:256`).
- Sains : DuckDB en context managers, pas de lru_cache, tracker hors API, démarrage ~3 s, RSS repos 90–130 Mo.

## 11. Produit & roadmap

- 443 commits ; 342 entre juillet et septembre ; depuis octobre, uniquement des commits auto.
- 9 fonctionnalités « en attente de 20+ trades clos » (TS/SL, TP cap, thesis_stop, SL/TP Buffett, drift LT,
  support score, attribution scores d'entrée, data_confidence/insider kicker, recalibration SL/TP 2-3) :
  aucune validable avant 2027 au rythme actuel (dernier achat 2026-09-01, dernier trade clos 2026-09-23).
- Roadmap : n°2 (chaîne basket) en échec ; n°3 rebalance : 4/4 entrées `rebalance_log.jsonl` ok:false ;
  n°5–9 non commencés.
- ADR contredites : ADR-013 (anti cash-drag) — 91 % cash ; ADR-012 (equal_7) — shadow contraire ;
  ADR-018 (sentinel cron) — absent ; règle SL −30 % vs `_MAX_SL_PCT = 0.45` (`fundamentals_levels.py:53`).

### Roadmap 90 jours proposée

| Phase | Contenu | Critère de succès |
|---|---|---|
| Arrêter (S0–2) | Gel des nouveaux modules/piliers et des 9 calibrations en attente ; pas d'achats auto tant que TITAN < universe_ew | 0 commit feature hors plan |
| Stabiliser (S0–1) | P0 §0, cron sentinel, proposer hors API | ≥ 1 proposition/jour ouvré 4 semaines ; 0 CRIT > 24 h |
| Argent réel (S1–4) | Synchro eToro lecture seule (copy + cash EUR), carte « Aujourd'hui · eToro », journal unifié des décisions | 100 % des ordres eToro journalisés < 24 h |
| Simplifier (S2–6) | ≤ 3 règles de sortie ; KPI unique compte vs universe_ew vs SPY ; retrait mode legacy + code mort | Cockpit affiche l'écart chaque jour |
| Trancher (≈ 2026-12-17) | Fin des 3 mois de shadow : si equal_7 − universe_ew < 0, TITAN cesse de sélectionner ; ADR-019 | Décision écrite quel que soit le résultat |
| Mesurer (S4–12) | Backtest 20 ans momentum 12-1 + quality, univers historique, fondamentaux PIT SEC | IC/t-stat publiés avec règle d'arrêt |

---

## Annexes

- Fichiers de données cités : `backend/data/trade_journal.duckdb`, `shadow_portfolios.json`,
  `sentinel/latest.json`, `rebalance_log.jsonl`, `equity_state.json`, `wfo_history.jsonl`,
  `my_portfolio_thesis.json`, `my_portfolio_risk.json`.
- Infra : `/etc/systemd/system/swing-api.service`, `/etc/nginx/sites-enabled/swing.webcatalyste.fr`,
  `scripts/git_auto_sync.sh`, `run_titan.sh`.
