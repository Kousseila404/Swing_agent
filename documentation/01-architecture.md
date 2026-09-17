# 01 · Architecture

## Vue d'ensemble

SwingQuant TITAN est un système d'investissement long terme sur actions US
(S&P 500 + Nasdaq-100), entièrement automatisé de la sélection à l'exécution,
avec une interface de pilotage. Une seule VM (Ubuntu, UTC), un seul compte
broker (Alpaca, paper aujourd'hui).

```
                 ┌──────────────── run_titan.sh (cron 06:00 UTC) ────────────────┐
                 │ macro → univers (fondamentaux) → scoring TITAN → snapshot      │
                 │ → prix cibles → shadow portfolios → portfolio stop → lab (dim.)│
                 │ → propositions (top-20, 1/N)                                   │
                 └───────────────────────────┬────────────────────────────────────┘
                                             │ data/proposals.json
   auto_approve (cron 15-19h UTC) ───────────┤   rotation (rang > 40, 5 j) → ventes
                                             │   rebalance 1/N (mensuel)
                                             │   achats : rang ≤ 20, gates, budgets
                                             ▼
                              POST /api/proposals/approve_batch
                                             │
                                   AlpacaBroker.submit_order
                              (limite marketable + bracket GTC)
                                             │
   tracker.py (cron 2 min, 12-22h UTC) ──────┤   sync fills · sweep entrées
                                             │   ré-armement stops · SL/TS/time-exit
                                             │   killswitch · circuit breaker · LT policy
                                             ▼
                 data/trade_journal.csv (source de vérité) + trade_journal.duckdb (miroir)
                                             │
                   FastAPI (systemd swing-api) ──► React (Cockpit, Propositions, …)
                                             │
                                Telegram (alertes + commandes entrantes)
```

## Processus

| Processus | Lancement | Rôle |
|---|---|---|
| `swing-api.service` | systemd, `uvicorn api:app --reload` | API HTTP + service du frontend ; recharge à chaque changement de fichier (topologie dev choisie par l'utilisateur) |
| `run_titan.sh` | cron 06:00 UTC | Pipeline quotidien (voir 06-operations) |
| `tracker.py` | cron toutes les 2 min | Surveillance et protection des positions |
| `main.py --auto-approve-proposals` | cron 30 min, 15–19 h UTC | Rotation, rebalance, achats |
| `main.py --alpaca-sync` | cron 15 min en séance | Réconciliation broker (filet) |
| `modules.telegram_inbox` | cron 2 min | Commandes Telegram |
| `scripts/git_auto_sync.sh` | cron 1 min | Commit + push + pull ff-only : le dépôt distant est le déploiement |

## Modules backend (backend/modules)

**Chaîne de données**
- `universe_engine.py` — construction de l'univers (S&P 500 Wikipedia, Nasdaq-100 API Nasdaq → fallback), fondamentaux via `data_providers/` (FMP, Polygon, yfinance en cascade).
- `universe_scheduler.py` — rafraîchit un budget de tickers par jour (100, rotation hebdo).
- `sector_metrics/` — scoring TITAN (`_scoring.py` : piliers, profils de poids, composite), `get_scored_universe()`.
- `universe_history.py` — snapshot quotidien gz du scoring (source du backtest).
- `macro_engine.py` — régime (S&P vs MA200, VIX).

**Décision**
- `auto_proposer.py` — gates système (killswitch, régime, fraîcheur, cash) → `portfolio/_manager.py` (sélection, pondération `equal` en mode basket, caps secteur/nom/ADTV, vol-target si VIX ≥ 25) → propositions avec SL/TP (`portfolio/_trade_levels.py`), buy_signal, support, prix cible.
- `auto_approve.py` — mode basket : rang ≤ 20 ; mode legacy : TITAN ≥ 80 / override.
- `basket_rotation.py`, `basket_rebalance.py` — ventes par rang, poids 1/N.
- `lt_exit_policy.py`, `thesis_stop.py`, `fundamentals_levels.py` — couche fondamentale (HOLD / ADD_ON / TRIM / EXIT).

**Exécution et protection**
- `broker_gateway.py` — `PaperBroker` (CSV) et `AlpacaBroker` (ordres réels, brackets GTC, sweep, ré-armement, réconciliation, `adjust_position`).
- `tracker/` — `cycle.py` (orchestration), `evaluation.py` (SL/TS/time-exit, journal fusionné), `killswitch.py`, `market.py`.
- `risk.py` — circuit breaker progressif ; `portfolio_stop.py` — drawdown global.

**Mesure**
- `backtest.py` — top-N hebdo (cache snapshots), `scoring_lab.py`, `perf_benchmark.py`, `gap_attribution.py`, `shadow_portfolios.py`.

**Interface et alertes**
- `routers/` — un routeur par domaine ; `routers/performance.py` = endpoints cockpit.
- `alerter.py`, `daily_digest.py`, `telegram_inbox.py`.

## Frontend (frontend/src)

React 19 + React Query + Recharts, build Vite servi par l'API. Sept pages
(voir 09-frontend). Le fichier `config/nav.js` est la source de vérité de la
navigation ; `hooks/useApi.js` et `api/client.js` encapsulent tous les appels.

## Sources de vérité

| Donnée | Source de vérité | Miroir / cache |
|---|---|---|
| Positions et fills | Alpaca | `trade_journal.csv` (réconcilié, reconstruisible via `scripts/rebuild_journal_from_alpaca.py`) |
| Journal de trades | `data/trade_journal.csv` | `trade_journal.duckdb` (rejoué après chaque écriture) |
| Univers scoré | `data/universe.json` + `.universe_history/` | cache RAM API |
| Propositions | `data/proposals.json` (+ `proposals_audit.jsonl`) | — |
| Configuration | `backend/config.py` + `backend/.env` (jamais versionné) | — |
