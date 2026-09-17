# 15 · Configuration (générée depuis backend/config.py)

Les variables d'environnement se placent dans `backend/.env` (jamais versionné). 
Après modification d'une valeur lue par l'API, `make redeploy`. Les crons relisent `.env` à chaque exécution.

| Constante | Variable d'env | Valeur / défaut | Note |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `TELEGRAM_BOT_TOKEN` | `os.getenv("TELEGRAM_BOT_TOKEN")` | |
| `TELEGRAM_CHAT_ID` | `TELEGRAM_CHAT_ID` | `os.getenv("TELEGRAM_CHAT_ID")` |  |
| `FRONTEND_URL` | `FRONTEND_URL` | `os.getenv("FRONTEND_URL", "").rstrip("/")` | URL publique du frontend déployé (ex: https://titan.ton-domaine.com/). Optionnel — sert uniquement à construire un lien cliquable dans le digest Telegram (Étape |
| `LLM_API_KEY` | `ANTHROPIC_API_KEY` | `os.getenv("ANTHROPIC_API_KEY")` | Anthropic (Claude) — analyse IA des candidats quantamentaux |
| `LLM_MODEL` |  | `"claude-sonnet-4-6"` |  |
| `ENABLE_AI_ANALYSIS` |  | `True` |  |
| `BROKER_MODE` | `BROKER_MODE` | `os.getenv("BROKER_MODE", "paper")` | |
| `ALPACA_API_KEY` | `ALPACA_API_KEY` | `os.getenv("ALPACA_API_KEY")` |  |
| `ALPACA_SECRET_KEY` | `ALPACA_SECRET_KEY` | `os.getenv("ALPACA_SECRET_KEY")` |  |
| `ALPACA_BASE_URL` | `ALPACA_BASE_URL` | `os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")` | Paper  : https://paper-api.alpaca.markets Live   : https://api.alpaca.markets |
| `ACCOUNT_SIZE` |  | `100_000` | |
| `RISK_PER_TRADE` |  | `0.0025` | Risque par trade utilisé comme valeur par défaut dans kelly_rolling(). |
| `MACRO_INDEX` |  | `"^GSPC"` | |
| `VIX_INDEX` |  | `"^VIX"` |  |
| `VIX_BULL_MAX` |  | `30.0` |  |
| `VIX_PANIC_MIN` |  | `35.0` |  |
| `REGIME_CONFIRMATION_DAYS` |  | `3` |  |
| `SLIPPAGE_PCT` |  | `0.0005` | |
| `MAX_HOLDING_DAYS` |  | `60` | Durée max de détention avant time exit forcé (WIN/LOSS selon PnL instantané). 60j = 2 mois, horizon moyen de convergence d'une thèse fondamentale. |
| `TRAILING_STOP_ACTIVATION_PCT` |  | `15.0` | Mode % fixe (fallback si pas d'OHLCV) : Audit 2026-05-12 — activation 15 → 8 %, lock 25 → 40 %. V4.1 LT (15 %/25 %) était trop conservateur pour des positions L |
| `TRAILING_STOP_ACTIVATION_RATIO` |  | `0.5` | si TP_distance × RATIO > 15 %) |
| `TRAILING_STOP_LOCK_PCT` |  | `0.30` | TP +100 % → 50 % → capé à 15 %. |
| `TRAILING_STOP_ATR_ACTIVATION_MULT` |  | `4.0` |  Mode ATR-adaptatif (prioritaire si OHLCV dispo) : ATR calculé sur 14 j → court terme ; on compense par un multiplicateur large pour coller à un hold LT. Audit  |
| `TRAILING_STOP_ATR_TRAIL_MULT` |  | `4.0` |  |
| `BEAR_HEDGE_ENABLED` |  | `True` | |
| `BEAR_HEDGE_TICKER` |  | `"SH"` |  |
| `BEAR_HEDGE_PCT_OF_BOOK` |  | `0.10` |  |
| `BEAR_HEDGE_MIN_DAYS` |  | `3` |  |
| `KILLSWITCH_ACTION` | `KILLSWITCH_ACTION` | `os.getenv("KILLSWITCH_ACTION", "freeze")` | |
| `MAX_DAILY_DRAWDOWN_PCT` | `MAX_DAILY_DRAWDOWN_PCT` | `float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "6.0"))` |  |
| `CB_ROLLING_WINDOW_DAYS` |  | `60` | Circuit breaker progressif (modules/risk.DrawdownCircuitBreaker) : le peak est désormais échantillonné **1×/jour** (pas 1×/cycle 2 min, cf. cycle.py) sur une fe |
| `CB_DD_REDUCE_75_PCT` |  | `-8.0` |  |
| `CB_DD_REDUCE_50_PCT` |  | `-12.0` |  |
| `CB_DD_PAUSE_PCT` |  | `-16.0` |  |
| `CB_PAUSE_DAYS` |  | `5` |  |
| `STRATEGY_MODE` | `STRATEGY_MODE` | `os.getenv("STRATEGY_MODE", "basket")` | |
| `BASKET_TOP_N` | `BASKET_TOP_N` | `int(os.getenv("BASKET_TOP_N", "20"))` |  |
| `BASKET_EXIT_RANK` | `BASKET_EXIT_RANK` | `int(os.getenv("BASKET_EXIT_RANK", "40"))` |  |
| `BASKET_EXIT_CONFIRM_DAYS` | `BASKET_EXIT_CONFIRM_DAYS` | `int(os.getenv("BASKET_EXIT_CONFIRM_DAYS", "5"))` |  |
| `BASKET_MAX_EXITS_PER_RUN` |  | `3` |  |
| `EXEC_ENTRY_TYPE` | `EXEC_ENTRY_TYPE` | `os.getenv("EXEC_ENTRY_TYPE", "limit")` | Exécution (2026-09-17) : entrée en limite marketable (ask × 1,003), gate de spread 0,5 %, remplacement par un market bracket après 60 min sans fill. |
| `EXEC_LIMIT_OFFSET_PCT` | `EXEC_LIMIT_OFFSET_PCT` | `float(os.getenv("EXEC_LIMIT_OFFSET_PCT", "0.3"))` |  |
| `EXEC_MAX_SPREAD_PCT` | `EXEC_MAX_SPREAD_PCT` | `float(os.getenv("EXEC_MAX_SPREAD_PCT", "0.5"))` |  |
| `EXEC_LIMIT_MAX_AGE_MIN` | `EXEC_LIMIT_MAX_AGE_MIN` | `int(os.getenv("EXEC_LIMIT_MAX_AGE_MIN", "60"))` |  |
| `REBALANCE_BAND_PTS` | `REBALANCE_BAND_PTS` | `float(os.getenv("REBALANCE_BAND_PTS", "2.0"))` | Rebalance des poids vers 1/N (mensuel, premier jour de bourse) si l'écart d'une ligne dépasse REBALANCE_BAND_PTS points de poids. |
| `REBALANCE_MIN_TRADE_USD` | `REBALANCE_MIN_TRADE_USD` | `float(os.getenv("REBALANCE_MIN_TRADE_USD", "300"))` |  |
| `PORTFOLIO_STOP_DD_PCT` | `PORTFOLIO_STOP_DD_PCT` | `float(os.getenv("PORTFOLIO_STOP_DD_PCT", "-15"))` | Stop de portefeuille : drawdown 60 séances de l'equity ≤ −15 % → gel des entrées. |
| `PORTFOLIO_STOP_WINDOW_D` |  | `60` |  |
| `LIVE_CAPITAL_FRACTION` | `LIVE_CAPITAL_FRACTION` | `float(os.getenv("LIVE_CAPITAL_FRACTION", "0.10"))` | Passage progressif en réel : fraction du capital déployée quand le broker est en URL live (1.0 = tout). Sans effet en paper. |
| `LOG_FILE` |  | `"logs/agent.log"` | |
| `LOG_LEVEL` |  | `"INFO"` |  |

## Variables d'environnement hors config.py

| Variable | Lue par | Rôle |
|---|---|---|
| `API_TOKEN` | api_core | Bearer token de l'API (frontend : localStorage `api_token`) |
| `TITAN_WEIGHT_PROFILE` | sector_metrics/_scoring | profil de poids (`v14_1`, `equal_7`, `equal_8`, `momentum_tilt`) |
| `AUTO_APPROVE_MAX_PER_RUN` / `_PER_WEEK` | auto_approve | budgets d'achats (défauts basket 4 / 20) |
| `VETO_COOLDOWN_DAYS`, `WIN_COOLDOWN_DAYS`, `PROPOSAL_TTL_HOURS`, `PENDING_MAX_AGE_DAYS` | proposals | cooldowns et TTL de la file |
| `FMP_API_KEY`, `FINNHUB_API_KEY`, `MARKET_DATA_API_KEY` (Polygon) | data_providers | clés providers |
| `GITHUB_TOKEN`, `GITHUB_USER`, `THESIS_REVIEW_TOKEN` | pont revue de thèse | routines cloud |
| `HEALTHCHECKS_PING_URL` | run_titan.sh | ping externe optionnel |
| `LOG_JSON` | log | logs JSON une ligne |
| `JOURNAL_READ_BACKEND=csv` | duckdb_journal | force la lecture CSV |
| `SWINGQUANT_TEST_GUARD=1` | tests | garde-fou données prod |
