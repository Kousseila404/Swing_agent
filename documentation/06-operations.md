# 06 · Opérations

## Planning (UTC — le cron Debian ignore `CRON_TZ`)

Fichier de référence : `deploy/crontab.txt` ; installer avec
`crontab deploy/crontab.txt`. NYSE : 13:30–20:00 UTC (été), 14:30–21:00 (hiver).

| Heure UTC | Tâche | Commande |
|---|---|---|
| toutes les 1 min | sync git (commit + push + pull ff-only) | `scripts/git_auto_sync.sh` |
| */2, 12–22 h, lun–ven | tracker positions | `backend/tracker.py` |
| */2, 12–22 h | commandes Telegram | `python -m modules.telegram_inbox` |
| 03:00 | rotation des logs (daily, 30 j) | `logrotate` |
| 05:00 / 05:30 lun | Mon Portefeuille : earnings / risque | `main.py --refresh-my-portfolio-earnings` / `--recompute-portfolio-risk` |
| 05:05 / 05:10 | pont GitHub revue de thèse | `scripts.publish_thesis_review_context` / `ingest_thesis_review_comments` |
| 06:00 | **pipeline TITAN** | `run_titan.sh` |
| 06:30 | backup journal | `scripts/backup_journal.sh` |
| 07:30 lun–ven | digest Telegram | `main.py --daily-digest` |
| 13:00 lun–ven | health check Telegram | `main.py --health-check` |
| */15, 13–21 h, lun–ven | sync Alpaca (filet) | `main.py --alpaca-sync` |
| */30, 15–19 h, lun–ven | **rotation · rebalance · achats** | `main.py --auto-approve-proposals` |
| 21:35 lun–ven | monitor LT post-clôture | `scripts.run_monitor_alerts` |

## `run_titan.sh` (06:00 UTC), étapes

0. `macro_engine` (VIX, régime) ;
1. `universe_scheduler --budget 100 --min-age-days 7` (fondamentaux, rotation hebdo) ; `insider_enrich` (SEC, 15 min max) ; `finnhub_enrich` (45 min max) ;
2. warm `/api/portfolio/recommendations` ;
3. `universe_history` (snapshot du jour), export calibration, `wfo_monitor` (le 1er), `metrics_agent`, `price_target_snapshot`, **`shadow_portfolios`**, **`portfolio_stop`**, `scoring_lab` (dimanche ou si absent), warm `/api/performance/benchmark` ;
4. `POST /api/proposals/refresh` (propositions du jour) ; `bear_hedge`.

Logs : `logs/titan_daily.log` (racine), `backend/logs/*.log` (un par cron,
rotation quotidienne, 30 jours).

## Déploiement

- Prod = `swing-api.service` (systemd, `uvicorn --reload`). **Tout commit sur
  `main` est un déploiement** : le sync git tire toutes les minutes et
  uvicorn recharge. Ne jamais pousser une suite rouge.
- Changement de `.env` (profil de scoring, budgets) : `make redeploy`
  (restart systemd, sudo). Les crons lisent `.env` à chaque exécution.
- Frontend : `vite build --watch` tourne en continu ; `dist/` est servi par
  l'API. Types : `npm --prefix frontend run gen:types` après tout changement
  d'endpoint (drift bloquant en CI).

## Runbook incidents

| Symptôme | Vérifier | Action |
|---|---|---|
| Cockpit : « Tracker silencieux » | `backend/logs/tracker.log`, `/tmp/swing_tracker.lock` orphelin | relancer `python tracker.py` ; le lock orphelin est remplacé automatiquement si le PID est mort |
| Position sans stop broker (`/api/portfolio/protection`) | tracker actif ? Alpaca répond ? | un cycle tracker ré-arme ; sinon `AlpacaBroker().ensure_protective_stops(rows)` en console |
| Journal ≠ Alpaca | `python -m scripts.rebuild_journal_from_alpaca` (dry-run) | `--apply` si le diff est compris (backup automatique) |
| Aucune proposition | gates dans `data/proposals_last_refresh.json` | régime ? cash ? slots ? univers frais ? |
| Telegram 400 | texte HTML non échappé | tout texte libre passe par `html.escape` |
| yfinance 429 | `/api/data_health` (breaker) | attendre le reset auto (10 min) ; FMP/Polygon prennent le relais |
| Disque > 90 % | `df -h`, `backend/logs`, `data/.universe_history` | logrotate, purge des exports de calibration |
| Killswitch actif à tort | `data/trading_state.json` | `/resume` sur Telegram ou `_set_trading_blocked(False)` |

## Commandes utiles

```bash
make test            # suite backend
make lint            # ruff + mypy
make ci-local        # lint + test + build frontend
make backup          # backup horodaté du journal
make redeploy        # restart API (sudo)
python -m modules.scoring_lab --quick      # 4 variantes, ~30 s
python -m modules.basket_rotation           # dry-run rotation
python -m modules.basket_rebalance          # dry-run rebalance (--force --apply pour exécuter)
python -m modules.portfolio_stop            # état du stop de portefeuille
python -m modules.shadow_portfolios         # valorise les portefeuilles shadow
```

## Telegram

Sortant : alertes (clôtures, décisions LT, stops ré-armés, rotation,
rebalance, killswitch), digest 07:30, health check. Entrant (`/status`,
`/approve T`, `/reject T`, `/pause`, `/resume`, `/help`) : seul le chat
`TELEGRAM_CHAT_ID` est écouté ; les approbations passent par les mêmes gates
que l'interface ; aucune vente directe par Telegram.
