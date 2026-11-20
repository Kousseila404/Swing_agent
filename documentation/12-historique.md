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

## 2026-11-06 → 11-20 — Audits, argent réel, fin de la sélection par score
- 11-06 : audit des pertes — proposer mort depuis le 09-18 (`KeyError`),
  SL/TS évalués sur des prix hors séance, aucun edge hors échantillon.
- 11-18 : architecture d'agents (sentinel de résultats + triage IA, 5 agents
  `sq-*`) ; audit intégral 11 agents (`audits/2026-11-18-audit-integral.md`) :
  IC TITAN live négatif, OOM `_SNAP_CACHE`, insider faux, copy-trading invisible.
- 11-20 après-midi : les 11 premiers P0 corrigés (OOM, warm hors API,
  insider, WFO sans bootstrap, prix shadow, lock fundamentals, sizing 10 k$,
  killswitch borné, CI, requirements, bannière 401).
- 11-20 soir (`audits/2026-11-20-audit-boost.md`) :
  - argent réel eToro visible (`etoro_sync`, carte Cockpit, ADR-019) ;
  - toutes les lectures `/api/*` authentifiées (ADR-020) ;
  - chaîne d'ordres P1-1/3/4/5/6 (séance, univers partiel, double achat,
    ordre non journalisé, killswitch fail-closed) ; sentinel +2 contrôles ;
  - TITAN V15 (value + filtre qualité) déployé puis **retiré** le soir même ;
  - recherche 6 agents (`audits/2026-11-20-recherche-scoring.md`) : aucun
    score de sélection n'a d'edge démontré après biais de survivance
    (+5,5 à +6,6 pts/an) ; bugs momentum 8M-1M, dividende ×100, seuil Risk ;
  - compte paper passé en **cœur-satellite** : 70 % RSP + 30 % momentum
    12-1, paramètres figés 24 mois (ADR-022).

## État au 2026-11-20 23:30 UTC
- Compte paper : 14 positions basket, equity ≈ 93,8 k$ ; SPY −5,5 % sous
  MM200 → premier rebalance cœur-satellite (lundi 11-23, 15-20 h UTC) =
  100 % RSP.
- Argent réel eToro ≈ 9,6 k$ : 58 % copy-trading, 27 % actions, 14 % cash.
- 23:28 UTC : API redémarrée (tout le code du 11-20 actif, vérifié) et
  crontab installée (sentinel 30 min + synchro eToro 22:30) ; ancienne
  crontab sauvegardée dans `~/.crontab.bak-20261120`.
- **Reste côté utilisateur** : clés API eToro (`ETORO_API_KEY`,
  `ETORO_USER_KEY` dans `backend/.env`) — sans elles le snapshot eToro reste
  celui du 11-20 et le sentinel passera en WARN après 72 h.
- **À surveiller lundi 11-23** (15-20 h UTC) : 1er rebalance cœur-satellite
  (100 % RSP, régime baissier) ; le WARN sentinel « deployment 38 % » doit
  disparaître ensuite.
- Suite : 1 502 tests verts, ruff/mypy/eslint propres.
