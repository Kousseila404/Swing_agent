# 08 · Référence API (générée depuis openapi.json)

72 routes. Authentification : `Authorization: Bearer <API_TOKEN>` (backend/.env). 
Régénérer : `npm --prefix frontend run gen:types`.

| Méthode | Route | Tag | Résumé |
|---|---|---|---|
| GET | `/api/attribution` | attribution | Get Attribution |
| GET | `/api/audit/full` | audit | Get Audit Full |
| POST | `/api/backtest/quick` | history | Backtest Quick |
| GET | `/api/calendar` | calendar | Get Calendar |
| GET | `/api/compare` | peers | Compare |
| GET | `/api/data_health` | data_health | Get Data Health |
| POST | `/api/data_health/refresh_flagged` | data_health | Refresh Flagged Tickers |
| GET | `/api/delisted` | audit | Get Delisted |
| GET | `/api/equity_curve` | portfolio | Get Equity Curve |
| GET | `/api/history/snapshots` | history | List Snapshots Endpoint |
| GET | `/api/history/ticker/{ticker}` | history | Ticker History Endpoint |
| POST | `/api/job/{job_id}/kill` | jobs | Kill Job |
| GET | `/api/jobs/{job_id}` | jobs | Get Job |
| GET | `/api/lt_decision` | monitor | Lt Decision All |
| GET | `/api/lt_decision/{ticker}` | monitor | Lt Decision One |
| GET | `/api/macro` | macro | Get Macro |
| GET | `/api/macro_calendar` | system | Get Macro Calendar |
| GET | `/api/market_status` | system | Get Market Status |
| GET | `/api/monitor/preview` | monitor | Preview Alerts |
| POST | `/api/monitor/run` | monitor | Run Alerts |
| GET | `/api/my_portfolio` | my_portfolio | Get My Portfolio |
| GET | `/api/my_portfolio/executions` | my_portfolio | Get Executions |
| POST | `/api/my_portfolio/executions` | my_portfolio | Post Execution |
| GET | `/api/my_portfolio/{ticker}/price_history` | my_portfolio | Get My Portfolio Price History |
| PATCH | `/api/my_portfolio/{ticker}/thesis` | my_portfolio | Patch Thesis |
| POST | `/api/my_portfolio/{ticker}/thesis_review_queue` | my_portfolio | Post Review Entry |
| GET | `/api/my_portfolio/{ticker}/thesis_review_queue` | my_portfolio | Get Review Queue |
| PATCH | `/api/my_portfolio/{ticker}/thesis_review_queue/{entry_id}` | my_portfolio | Patch Review Entry Status |
| GET | `/api/news/portfolio/firehose` | news | Portfolio News Firehose |
| GET | `/api/news/{ticker}` | news | Get News |
| PUT | `/api/notes/{note_id}` | watchlist | Put Note |
| DELETE | `/api/notes/{note_id}` | watchlist | Delete Note Endpoint |
| GET | `/api/notes/{ticker}` | watchlist | Get Notes |
| POST | `/api/notes/{ticker}` | watchlist | Post Note |
| GET | `/api/peers/{ticker}` | peers | Peers |
| GET | `/api/performance/benchmark` | cockpit | Get Performance Benchmark |
| GET | `/api/performance/gap` | cockpit | Get Performance Gap |
| GET | `/api/performance_metrics` | portfolio | Get Performance Metrics |
| GET | `/api/portfolio` | portfolio | Get Portfolio |
| POST | `/api/portfolio/backfill_entry_scores` | portfolio | Post Backfill Entry Scores |
| GET | `/api/portfolio/protection` | cockpit | Get Portfolio Protection |
| GET | `/api/portfolio/recommendations` | portfolio | Get Portfolio Recommendations |
| GET | `/api/price_alerts` | watchlist | Get Price Alerts |
| POST | `/api/price_alerts` | watchlist | Post Price Alert |
| GET | `/api/price_alerts/stats` | watchlist | Get Price Alerts Stats |
| DELETE | `/api/price_alerts/{alert_id}` | watchlist | Delete Price Alert |
| GET | `/api/proposals` | proposals | List Proposals |
| POST | `/api/proposals/approve_batch` | proposals | Approve Proposals Batch |
| POST | `/api/proposals/manual` | proposals | Manual Proposal |
| POST | `/api/proposals/refresh` | proposals | Refresh Proposals |
| POST | `/api/proposals/regenerate` | proposals | Regenerate Proposals |
| POST | `/api/proposals/reject_batch` | proposals | Reject Proposals Batch |
| GET | `/api/proposals/veto-history` | proposals | Get Veto History |
| POST | `/api/proposals/{proposal_id}/approve` | proposals | Approve Proposal |
| POST | `/api/proposals/{proposal_id}/reject` | proposals | Reject Proposal |
| GET | `/api/rebalance/preview` | cockpit | Get Rebalance Preview |
| GET | `/api/scoring/lab` | cockpit | Get Scoring Lab |
| GET | `/api/sec_filings/{ticker}` | sec_filings | Get Sec Filings |
| GET | `/api/sector_benchmark/portfolio` | sector_benchmark | Benchmark Portfolio Endpoint |
| GET | `/api/sector_benchmark/{ticker}` | sector_benchmark | Benchmark Ticker Endpoint |
| GET | `/api/sectors` | sectors | Get Sectors |
| GET | `/api/sectors/{sector}` | sectors | Get Sector Detail |
| GET | `/api/shadow` | cockpit | Get Shadow |
| GET | `/api/status` | system | Get Status |
| GET | `/api/system/health` | cockpit | Get System Health |
| GET | `/api/thesis_status` | monitor | Thesis Status All |
| GET | `/api/ticker_analysis/{ticker}` | ticker_analysis | Ticker Analysis |
| GET | `/api/titan_alerts` | watchlist | Get Titan Alerts |
| POST | `/api/titan_alerts` | watchlist | Post Titan Alert |
| DELETE | `/api/titan_alerts/{alert_id}` | watchlist | Delete Titan Alert |
| POST | `/api/trade/add` | trades | Add Trade |
| POST | `/api/trade/close` | trades | Close Trade |
| GET | `/api/universe` | universe | Get Quantamental Universe |
| POST | `/api/universe/rebuild` | universe | Rebuild Quantamental Universe |
| GET | `/api/watchlist` | watchlist | Get Watchlist |
| POST | `/api/watchlist` | watchlist | Post Watchlist |
| DELETE | `/api/watchlist/{ticker}` | watchlist | Delete Watchlist |
| GET | `/api/wfo` | audit | Get Wfo Weights |
| GET | `/api/wfo/history` | audit | Get Wfo History |
