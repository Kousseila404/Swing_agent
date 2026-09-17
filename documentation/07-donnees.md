# 07 · Données

Tout vit sous `backend/data/` (non versionné, sauvegardé par `scripts/backup_journal.sh`).

## Fichiers d'état

| Fichier | Rôle | Écrit par |
|---|---|---|
| `trade_journal.csv` | **Source de vérité** des trades (schéma ci-dessous) | broker (entrée), tracker (fusion par clé), sync, rebuild |
| `trade_journal.duckdb` | miroir analytique, rejoué après chaque écriture CSV | `duckdb_journal.sync_from_csv` |
| `equity_state.json` | equity, PnL réalisé/latent, positions live (dashboard) | tracker, chaque cycle |
| `trading_state.json` | killswitch (`blocked`, `peak_equity`, date) | killswitch, portfolio_stop, Telegram `/pause` |
| `circuit_breaker_state.json` | peak roulant 60 j, pause, multiplicateur, `last_reading_date` | tracker (1 lecture/jour) |
| `tracker_heartbeat.json` | dernier cycle (`ok` / `idle` / `blocked`) | tracker |
| `alert_cooldown.json` | anti-spam alertes (SL proximity, thèse, LT, throttles) | tracker |
| `proposals.json`, `proposals_audit.jsonl` | file de propositions + audit des décisions | proposer, approve/reject, auto-approve, Telegram |
| `proposals_last_refresh.json` | gates + diagnostics du dernier refresh | proposer |
| `universe.json` | univers avec fondamentaux (488–590 tickers) | universe_scheduler |
| `.universe_history/snapshot_YYYYMMDD.json.gz` | scoring quotidien (backtests) | universe_history |
| `.fundamentals_cache/`, `finnhub_cache/`, `sec_cache/` | caches providers (24 h) | providers |
| `macro_state.json` | régime, VIX, S&P, MA200 | macro_engine |
| `.basket_rotation_state.json` | séries « hors panier » par ticker | basket_rotation |
| `.basket_rebalance_state.json`, `rebalance_log.jsonl` | dernier rebalance, ordres exécutés | basket_rebalance, broker |
| `shadow_portfolios.json` | NAV quotidienne des profils shadow | shadow_portfolios |
| `.scoring_lab.json` | résultats du lab | scoring_lab |
| `.perf_benchmark_cache.json` | panier théorique (12 h) | perf_benchmark |
| `.ndx100_cache.json` | dernière liste Nasdaq-100 réussie | universe_engine |
| `.telegram_offset.json` | curseur getUpdates | telegram_inbox |
| `my_portfolio_*.json`, `thesis_review_queue.json` | book personnel « Mon Portefeuille » | routines dédiées |

## Schéma du journal (`modules/utils.CSV_SCHEMA`, 34 colonnes)

| Colonne | Sens |
|---|---|
| `Date` | horodatage d'entrée (heure locale du process = UTC) |
| `Ticker`, `Direction` | symbole, `LONG` (SHORT non utilisé) |
| `Entry` | prix d'entrée **réel** après sweep (prix proposé avant) |
| `Stop_Loss`, `Initial_SL`, `Take_Profit` | niveaux courants / initial |
| `Size`, `RR` | quantité, ratio rendement/risque à l'entrée |
| `Status` | `OPEN` → `WIN` / `LOSS` / `CANCELED` / `EMERGENCY_CLOSED` |
| `Exit_Price`, `Exit_Date`, `Close_Reason` | `SL_HIT`, `TP_HIT`, `TRAILING_STOP`, `TIMEOUT`, `ROTATION`, `BROKER_SYNC`, `ENTRY_EXPIRED`, `EMERGENCY_DD` |
| `Last_TS_Update`, `Last_TS_Mode` | trailing stop (1×/jour, `ATR` / `PCT`) |
| `Order_ID`, `Signal`, `Sector` | parent Alpaca, `AUTO_PROPOSAL` / `ALPACA_IMPORT` / manuel, secteur |
| `Reco_Entry`, `Slippage_Bps` | prix proposé, slippage réel |
| `*_Entry` (Titan, Quality, Value, Risk, Momentum, Piotroski, Growth, F_Score, Tilt_Flags, Confidence) | scores capturés à l'entrée (attribution) |
| `Last_LT_Action`, `Last_LT_Date`, `Last_LT_Severity` | dernière décision LT |

Règles d'écriture : toujours sous `FileLock` ; le tracker ne remplace que
les lignes qu'il connaît (clé `Order_ID`, sinon `Date+Ticker`) ; les cellules
vides sont `""` (attention : `pandas` les lit en `NaN`, utiliser
`broker_gateway._cell`).

## Providers de données

Cascade `data_providers/` : FMP (fondamentaux, 402 = endpoint premium
ignoré) → Polygon (snapshot prix) → yfinance (fallback, breaker 429 avec reset
10 min) → Stooq. Finnhub : révisions, earnings, news. SEC EDGAR : Form 4.
Nasdaq-100 : API `api.nasdaq.com` (primaire) → Wikipedia → cache.
`/api/data_health` expose breaker, âge des caches, champs hors bornes.

## Sauvegardes et reconstruction

- `scripts/backup_journal.sh` (06:30) : CSV + DuckDB + equity_state horodatés,
  sha256, dans `backups/`.
- `scripts/rebuild_journal_from_alpaca.py` : reconstruit le journal depuis
  Alpaca + `proposals.json` (SL/TP/scores) + ancien CSV (métadonnées).
- `scripts/backfill_entry_scores.py` : re-remplit les scores d'entrée depuis
  les snapshots.
