# SwingQuant — Changelog

Historique des changements significatifs côté moteur scoring TITAN, providers
de données et orchestrateur portfolio.

Format inspiré de [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased] — audit intégral + Lots 1→6 (2026-09-17)

Rapport : [`docs/AUDIT_INTEGRAL_2026-09-17.md`](./docs/AUDIT_INTEGRAL_2026-09-17.md).

#### Fixed — P0 sécurité du capital

- **Brackets Alpaca `DAY` → `GTC`** (`broker_gateway.submit_order`). Les
  jambes SL/TP expiraient à la clôture le jour du fill (100 % des jambes
  depuis juillet, `canceled_at = 20:0x UTC`) : 6 positions ouvertes sans
  aucun stop broker. `client_order_id` déterministe `SQ-<TICKER>-…`.
- **Ré-armement automatique des stops** (`AlpacaBroker.ensure_protective_stops`,
  appelé à chaque cycle tracker) : toute position OPEN sans jambe STOP
  ouverte reçoit un stop GTC (OCO stop+TP si TP connu), au SL journal ou au
  plancher catastrophe −35 %. Réalignement si le stop broker dérive > 1 %
  du journal. `update_stop_loss` crée le stop s'il n'existe plus.
- **Race lost-update du journal** (`tracker.evaluation.save_journal`) :
  fusion par clé (`Order_ID` / Date+Ticker) avec l'état disque au lieu de
  réécrire toute la vue chargée en début de cycle. Les appends de l'API
  pendant un cycle ne sont plus perdus. Miroir DuckDB rejoué après chaque
  écriture, co-localisé avec le CSV.
- **Réconciliation broker** (`sync_fills_from_alpaca`) : fenêtre de grâce
  30 min, parent relu par `Order_ID`, fills de sortie filtrés (`after`
  entrée, side opposé, `filled_at > entrée`). Plus de clôture d'un trade
  neuf avec un vieux fill (CF 17/08 clôturé au fill du 27/07).
- **Journal reconstruit** depuis l'historique Alpaca
  (`scripts/rebuild_journal_from_alpaca.py`) : 14 lignes cohérentes avec le
  broker (6 OPEN, 5 WIN, 2 LOSS, 1 CANCELED), PnL réalisé −169 $ (au lieu
  de −591 $ faussés par 3 doublons). SL/TP/scores restaurés sur HAS/NEM/CF/EOG.
- `import_positions_to_csv` : récupère SL/TP depuis les jambes broker, sinon
  plancher catastrophe + WARNING ; `Signal=ALPACA_IMPORT`.

#### Changed — exécution & risque (Lots 2–4)

- **Crontab de référence** `deploy/crontab.txt` en UTC explicite (cron
  Debian ignore `CRON_TZ`) : auto-approve 15:00–19:59 UTC (jamais dans
  l'enchère d'ouverture), sync toute la séance, monitor post-clôture,
  tracker 12–22 h UTC. **À installer manuellement** : `crontab deploy/crontab.txt`.
- **Auto-approve** : candidats triés par TITAN décroissant, gate
  `buy_signal ∈ {STRONG_BUY, BUY}`, gate pilier Risk ≥ 40, refus si
  killswitch/circuit breaker, budgets via `AUTO_APPROVE_MAX_PER_RUN/WEEK`.
- **Sizing** : vol-targeting seulement si VIX ≥ 25 (`VOL_TARGET_VIX_MIN`),
  `DEFAULT_MAX_HOLDINGS` 20 → 15, `DEFAULT_MIN_PROPOSAL_USD` 250 → 2 500 $.
- **Trailing stop LT** : activation 8 → 15 %, lock 40 → 30 %, ATR 4×/4×.
- **Killswitch** : `KILLSWITCH_ACTION=freeze` (gel des entrées, pas de
  liquidation), seuil −4 → −6 %/jour ; le tracker continue de surveiller
  les positions quand le killswitch est actif.
- **Circuit breaker** : échantillonné 1×/jour (fenêtre 60 séances), seuils
  −8/−12/−16 % (paramétrés `CB_*` dans config).

#### Added — cockpit (Lot 6)

- `modules/perf_benchmark.py` + `GET /api/performance/benchmark` : compte
  Alpaca vs SPY vs panier TITAN top-N théorique (fenêtre live, sans
  look-ahead), rebasés à 100, cache 12 h.
- `GET /api/portfolio/protection` (stop broker par position, drift) et
  `GET /api/system/health` (tracker, killswitch, CB, crons).
- Frontend : page **Cockpit** (route par défaut) — KPI, graphique
  benchmark, positions × protection × décision LT, à décider, santé.
  Styles dédiés `styles/cockpit.css`.

#### Fixed — fiabilité (Lot 5)

- Telegram HTTP 400 : échappement HTML des narratifs (`TITAN 69 < 70`).
- pytest n'écrit plus dans les données prod : defaults DuckDB paresseux,
  fixture autouse d'isolation du journal, garde-fou `SWINGQUANT_TEST_GUARD=1`,
  logger fichier désactivé sous pytest.
- logrotate quotidien, 30 jours (preuves conservées).
- NDX100 : cache + fallback quand le scrape Wikipedia échoue (WARNING au
  lieu d'ERROR quotidien).

#### Tests

- +24 tests (protection broker, sync, fusion journal, auto-approve gates,
  killswitch freeze, cockpit). Suite : 1410, garde-fou data prod actif.

---

## [Unreleased] — branche `claude/sweet-faraday-j0cdxp`

### Audit infra/sécurité/CI (2026-09-17)

Audit intégral hors moteur scoring (aucun poids ni logique de scoring touché).

#### Fixed

- **`backend/Dockerfile`** — installait `requirements.txt` (freeze legacy sans
  `fastapi`/`uvicorn`/`psutil`) : l'image se construisait mais ne pouvait pas
  démarrer. Passe sur `requirements-runtime.txt`, utilisateur non-root
  `app`, `HEALTHCHECK` sur `/api/status`. La CI démarre désormais le
  conteneur et exige une réponse `/api/status` (avant : build seul).
- **`api.py` CORS** — `allow_methods` limité à `GET, POST` alors que les
  routers exposent `PATCH` (thesis, review queue), `PUT` (notes) et `DELETE`
  (watchlist, alertes) consommés par le frontend : préflight refusé dès que
  le frontend n'est pas servi same-origin (proxy Vite). Ajout
  `PUT/PATCH/DELETE/OPTIONS` + tests `test_api_middleware.py`.
- **`frontend/src/api/{openapi.json,types.ts}`** — régénérés : 5 endpoints
  `my_portfolio` (price_history, executions, thesis, review queue) manquaient
  dans les types TypeScript.
- **`tests/test_duckdb_journal.py::test_fail_open_on_bad_path`** — échouait
  en root (chemin absolu créable) ; utilise un fichier régulier comme
  parent, échec garanti quel que soit l'utilisateur.

#### Changed — performance

- **`api.py`** — middleware security headers réécrit en ASGI pur (plus de
  `BaseHTTPMiddleware` : pas de buffering/task group par requête, compatible
  streaming et `StaticFiles`).
- **`modules/tracker/market.py`** — `is_market_hours()` cache 60 s la réponse
  `get_clock()` Alpaca : avant, 1 appel réseau **par ticker et par cycle**
  tracker (toutes les 2 min) pour une valeur qui ne change qu'à l'open/close.

#### Security — dépendances (pip-audit / npm audit)

- `fastapi 0.115.0 → 0.141.1` (starlette 0.38.6 → 1.6.0, 8 CVE),
  `requests 2.32.5 → 2.34.2`, `filelock 3.19.1 → 3.20.3`,
  `python-dotenv 1.2.1 → 1.2.3`, `lxml 6.0.2 → 6.1.3`,
  `pyarrow 21.0.0 → 25.0.1`, `curl_cffi 0.13.0 → 0.16.3` (requiert
  `yfinance 1.2.0 → 1.7.0`). Suite complète verte après bump.
- Frontend : lockfile régénéré, 12 vulnérabilités dev-only → 0.
  `eslint-plugin-react-hooks 7.0.1 → 7.1.1` : 5 violations corrigées
  (setState dans `useEffect` → pattern "adjust state during render" dans
  `CommandPalette`/`PresetBar`, id de toast impur → compteur `useRef`) ;
  règle `preserve-manual-memoization` (React Compiler, non utilisé)
  désactivée explicitement.

#### CI

- ESLint bloquant (était `continue-on-error`, la base est propre).
- Seuil couverture `55 % → 60 %` (mesuré 64.5 %).
- `make lint` aligné sur la CI (`ruff check .` + mypy, au lieu d'un
  sous-ensemble de dossiers).

#### Tests

- 1386/1386 (baseline 1377 + 1 échec sandbox → +9 nouveaux : CORS
  préflight ×4, security headers ×3, cache clock Alpaca, journal).

---

## [Unreleased] — branche `claude/titan-hardening-2026-05-07`

### Audit & Hardening TITAN (2026-05-07)

Audit complet du moteur scoring → 24 défauts identifiés, 18 patchés, 6 reportés
au monitoring. Documentation: [`backend/docs/titan/`](./backend/docs/titan/).

#### Added — Diagnostics scoring

Nouveaux champs exposés dans le payload `_score_universe` (consommés par
`/api/universe`, `auto_proposer`, frontend) :

- `fundamentals_report_age_days` — âge depuis `fundamentals_period_end` (Q-latest).
- `fundamentals_report_age_days_y1` — âge depuis `fundamentals_period_end_y1`.
- `value_unprofitable_flags` — `["ev_ebitda_negative"]`, `["forward_pe_negative"]`, ou `[]`.
- `cross_provider_divergence` — `["trailing_pe=15.0/22.5", ...]` quand FMP/YF divergent > 5 %.
- `structural_gaps` — fields non-applicables au secteur mais effectivement absents.
- `sector_relative_fallback` — `True` si fallback global déclenché (secteur < 12 tickers).
- `titan_weight_mode="renormalized"` — nouveau mode quand un pilier est dynamiquement exclu.

Référence: [`scoring_payload_reference.md`](./backend/docs/titan/scoring_payload_reference.md).

#### Added — Gates & validations

- **`fundamentals_cache.py`** — bornes dures `PERIOD_END_MAX_AGE_DAYS=200`,
  `PERIOD_END_Y1_MAX_AGE_DAYS=450`. Tag `report_stale=` propagé via
  `source_provider="/stale_report"`.
- **`auto_proposer.py`** — nouveau gate `_gate_fundamentals_staleness`
  (`FUNDAMENTALS_STALE_MAX_RATIO=0.30`). Bloque les recommandations si > 30 %
  des tickers sont stale.
- **`_scoring.py`** — gate hard `market_cap > 0` ajouté en amont du DQ filter.

#### Changed — Comportement scoring

- **Clipping composite réordonné** ([#6](./backend/docs/titan/audit_2026-05-07.md#bug-6))
  — `clip((composite + tilt) × dq_coef)` au lieu de `clip(composite + tilt) × dq_coef`.
  Préserve les tilts pour DQ < 1.
- **Piotroski sector-aware** ([#10](./backend/docs/titan/audit_2026-05-07.md#bug-10))
  — `f_score_max=5` Financials (skip f5/f6/f7/f9), `=6` Real Estate (skip f6/f7/f9),
  `=9` ailleurs. Effet observable: PRU/AIG/TRV/WFC +3 à +7 pts.
- **Renormalisation dynamique des poids** ([#17](./backend/docs/titan/audit_2026-05-07.md#bug-17))
  — quand un pilier est entièrement vide (count=0), il est exclu et les
  poids des piliers restants sont renormalisés.
- **Tilt cap absolu `±8`** ([#13](./backend/docs/titan/audit_2026-05-07.md#bug-13))
  — empêche l'empilement (LEN/KHC/HPQ remontent +8).
- **Magnitudes tilts atténuées** —
  `cheap_junk: -10 → -7`, `falling_knife: -6 → -4` (atténuation double-counting
  avec piliers Q/V/M).
- **Consistency stricter** — `_CONSISTENCY_MIN_PILLARS_USED: 6 → 7` (bloque
  faux positif sur tickers FMP-only).
- **DQ-coef plus discriminant** — `_DQ_MIN_COEF: 0.95 → 0.92` (8 pts spread
  vs 1.5 pts).

#### Changed — Data providers

- **`FinancialRatios.cross_provider_divergence`** — nouveau champ optionnel.
  Propagé du `FallbackFundamentalProvider._merge` jusqu'au scoring (avant: log
  WARNING uniquement).
- **`UniverseTicker.cross_provider_divergence`** — propagation idem.

#### Tests

- 884/884 passent (baseline et post-patch identiques).
- Aucun test n'a été modifié.
- Voir [`hardening_2026-05-07.md` § Tests](./backend/docs/titan/hardening_2026-05-07.md#tests).

#### Reported — non patchés (6)

Reportés au [`monitoring_plan.md`](./backend/docs/titan/monitoring_plan.md) :

1. Pondérations piliers non calibrées OOS (#1) — exige WFO convergent.
2. WFO calibration cassée (#2) — exige 60+ jours d'historique.
3. Zéro trades clos pour attribution (#3) — exige temps qui passe.
4. Historique snapshots < 60 jours (#4) — exige cron quotidien à accumuler.
5. Look-ahead bias backtest (#5) — patch ponctuel `--lag-days ≥ 30` planifié.
6. Revisions + Insider opaques (#16) — recalibration dépendant de #1-3.

#### Files

```
backend/data_providers/_fallback.py        +9 -3
backend/data_providers/base.py             +6 -0
backend/modules/auto_proposer.py          +73 -0
backend/modules/fundamentals_cache.py     +40 -7
backend/modules/sector_metrics/_scoring.py +362 -64
backend/modules/universe_engine.py         +4 -0
backend/docs/titan/README.md                       (new)
backend/docs/titan/audit_2026-05-07.md             (new)
backend/docs/titan/hardening_2026-05-07.md         (new)
backend/docs/titan/monitoring_plan.md              (new)
backend/docs/titan/scoring_payload_reference.md    (new)
CHANGELOG.md                                       (new)
```

---

### Audit data + TITAN, fix propositions orphelines (2026-08-16)

Session d'audit du système de data (mise à jour, fraîcheur, sécurité), suivie
d'un backtest cross-sectionnel 5 ans pour évaluer si le score TITAN justifie
plus d'automatisation côté achat. Documentation:
[`backend/docs/titan/audit_titan_2026-08-16.md`](./backend/docs/titan/audit_titan_2026-08-16.md).

#### Fixed — Bug propositions orphelines

- **`proposals.py::update_status`** — nouvelle transition `approved → pending`
  (auparavant seule `approved → executed` existait). Sans elle, un rejet
  broker retryable (ex: NYSE fermée à 09:00, avant l'ouverture 09:30)
  laissait la proposition bloquée en `approved` pour toujours — jamais
  exécutée, jamais revue.
- **`routers/proposals.py::approve_proposals_batch`** — sur rejet broker,
  la proposition revient maintenant en `pending` (avec `rejection_reason`)
  au lieu de rester orpheline en `approved`. Corrige un bug réel observé en
  prod : CF/TPR/HAS recréés en doublon chaque jour depuis le 18/07 (11
  propositions orphelines trouvées et nettoyées manuellement, aucune
  n'avait `order_id` — aucun impact capital).

#### Added — Filtre TITAN<60 (audit_titan_2026-08-16.md)

- **`auto_proposer.py`** — nouvelle constante `TITAN_AUTO_REJECT_FLOOR=60.0`.
  Un candidat avec `titan_score < 60` n'est plus jamais proposé (filtre à la
  génération, pas un rejet a posteriori). Backtest cross-sectionnel 5 ans
  (361 snapshots) : bucket `<60` a un alpha démeané négatif à tous horizons
  et un taux de stop-loss touché 3× plus élevé que le bucket `≥80` (11.2%
  vs 3.8%), sur N=143978 réparti sur ~490 tickers — signal robuste, non
  concentré.
- **Décision explicite de NE PAS élargir `auto_approve.py`** — le bucket
  `≥80` a un alpha moyen positif mais **111% porté par 2 tickers sur 5 ans**
  (SNDK+MU, supercycle mémoire/IA), sur seulement 9 tickers ayant jamais
  atteint ce score. Pas un pattern généralisable ni automatisable côté achat.
  Règle `auto_approve.py` (TITAN≥80, cap 2/run 5/semaine) inchangée.

#### Ops — sécurité scripts (hors scope moteur scoring, notée ici faute de mieux)

- **`scripts/setup_github_deploy.sh`** — token GitHub ne peut plus être
  passé en argument CLI (exposition via `ps aux`/historique shell). Lu
  exclusivement depuis `backend/.env`.
- **`scripts/git_auto_sync.sh`** — garde-fou anti-secret ajouté avant tout
  auto-commit (tokens GitHub/AWS, clés privées PEM, fichiers `.env`
  trackés). Le script committe/push chaque minute sans revue humaine du
  contenu ; ce filtre couvre le scénario "secret ajouté par erreur poussé
  en <1min".
- **`/home/swing/data/`** (ancien système, mort depuis avril) archivé en
  `/home/swing/data.ARCHIVED_dead_since_2026-04/` — aucune référence
  trouvée nulle part, aucun impact.

#### Tests

- 1124/1124 passent (baseline 1103 + 21 nouveaux : garde-fou proposals,
  filtre TITAN floor, revert-to-pending sur rejet broker).
- ruff + mypy strict clean sur tous les fichiers touchés.

#### Reported — non implémenté (délibérément)

1. Compteur monitoring `n_unique_tickers_qualifying_80` (alerte
   anti-sur-confiance sur la concentration) — recommandé par l'audit,
   pas encore câblé.
2. Sizing Kelly fractionné calibré sur la distribution réelle — l'audit
   dit explicitement de ne pas le faire avant un split out-of-sample
   (2021-2024 train / 2025-2026 test), sinon overfitting déguisé.

#### Files

```
backend/modules/auto_proposer.py                    +14 -0
backend/modules/proposals.py                         +6 -3
backend/routers/proposals.py                        +16 -3
backend/tests/test_auto_proposer_gates.py            +46 -0
backend/tests/test_proposals_router.py               +34 -0
backend/docs/titan/audit_titan_2026-08-16.md               (new)
scripts/setup_github_deploy.sh                        +8 -4
scripts/git_auto_sync.sh                             +32 -1
CHANGELOG.md                                          +64 -0
```
