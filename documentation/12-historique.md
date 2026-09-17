# 12 · Historique du projet

Chronologie condensée ; le détail commit par commit est dans
[CHANGELOG.md](../CHANGELOG.md) et l'historique git (les commits `auto: sync`
sont le déploiement continu).

## 2026-03 → 04 — Swing Agent (héritage)
- Bot swing court terme (RSI, chandelier, optimizer, Streamlit). Stats
  archivées dans `data/.archive/`.

## 2026-04-17 → 04-24 — Pivot TITAN
- 17 : hardening phase 1 (auth fail-closed, CI, schémas), Streamlit remplacé
  par React/FastAPI (11 onglets).
- 20 : blindage données (breaker yfinance, adapters FMP/Polygon), perf
  frontend (code-splitting), cron `run_titan.sh`.
- 22 : tracker découpé en package, split portfolio/sector_metrics, TITAN
  V14 → V14.1 (Piotroski, PEG, momentum 6 M), historisation des snapshots,
  backtest top-N, premiers ordres Alpaca (NEM, EQT, CF, INCY).
- 24 : fusion reco → propositions (veto humain), market-hours gate,
  scoring V14.1, purge des stats swing.

## 2026-04-27 → 05-12 — Long terme « Buffett »
- Support score, SL/TP recalibrés (−30 % / +100 %), couche LT (thèse,
  catastrophe floor, valuation), alertes thèse Telegram, data confidence.
- 05-07 : audit scoring (24 défauts, 18 patchés). 05-12 : trailing 8 %/40 %,
  bear hedge SH, seuils VIX alignés.

## 2026-06 → 08 — Exploitation et mesures
- 06-05 : diagnostic IC par pilier (momentum et revisions seuls robustes).
- 07-09 : 3 trades (NEM, CF, EOG) coupés par trailing à +2–4 %.
- 07-16 : disque plein (cause des lenteurs), sync git bidirectionnel,
  auto-approve TITAN ≥ 80 + digest.
- 08-14 : audit asymétrie TS/SL (WIN +3 % / LOSS −16 %), fuite tests → DuckDB
  prod corrigée, escalade des propositions récurrentes.
- 08-16 : audit edge du composite (concentration SNDK/MU) → statu quo achat.
- 08-17 → 09-01 : 6 achats auto (MU, CF, EIX, EOG, HAS, NEM), tous à 13:30
  UTC (ouverture), brackets DAY → stops perdus le soir même.

## 2026-09-17 — Audit intégral et refonte (une journée)
- Matin : audit complet (`documentation/audits/2026-09-17-audit-integral.md`) :
  4 bugs P0, cron en mauvais fuseau, cash drag 80 %, compte −1 % vs SPY +7,5 %
  vs panier +18 %.
- Après-midi : Lots 1→6 livrés — brackets GTC + ré-armement (6 stops posés
  automatiquement), journal fusionné et reconstruit depuis Alpaca (PnL
  réalisé −169 $ au lieu de −591 $), sync bornée, crontab UTC installé,
  auto-approve durci, sizing sans vol-target en régime calme, trailing
  15 %/30 %, killswitch freeze, tests isolés, Telegram échappé, cockpit
  (benchmark, protection, santé), Scoring Lab.
- Soir : stratégie **basket** (`equal_7`, top-20, rotation, budgets), UI
  réduite à 7 pages, Nasdaq-100 via API officielle, ADD_ON gaté par rang/Risk.
- Nuit : exécution intelligente (limite marketable, spread, sweep), rebalance
  1/N mensuel, stop de portefeuille, shadow portfolios A/B, attribution de
  l'écart, variantes de régime au lab, commandes Telegram, cache des
  snapshots (backtest 19 s → 0,7 s), ce dossier de documentation.

## État au 2026-09-18 00:00 UTC
- Compte paper : 6 positions protégées (2 OCO + 4 stops GTC), 14 lignes
  journal alignées Alpaca, 0 proposition pending (régénération à 06:00 en
  1/N), rotation armée (EIX rang 48, jour 1/5).
- Suite : 1 428 tests verts, lint/typage propres, frontend construit.
- Premier passage complet de la nouvelle chaîne : 2026-09-18, 06:00 UTC
  (propositions) puis 15:00 UTC (achats).
