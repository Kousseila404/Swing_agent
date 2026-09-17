# 10 · Tests et qualité

## Chiffres (2026-09-17, soir)

- Backend : **1 428 tests** pytest (`backend/tests/`), ~70 s ; couverture ≈ 65 %
  (seuil CI 60 %). mypy : 0 erreur sur 137 fichiers (strict sur
  `modules/portfolio`). ruff : 0.
- Frontend : 58 tests Vitest, ESLint bloquant, build Vite.
- CI GitHub (`.github/workflows/ci.yml`) : ruff + mypy + pytest + cov + eslint
  + vitest + build + smoke Docker (`/api/status`) + drift OpenAPI.

## Garde-fous « ne jamais toucher la prod »

Le dépôt de travail **est** la prod (sync git + `--reload`). Les tests
tournent donc à côté des données réelles :

- `tests/conftest.py` : fixture autouse `_isolate_trade_journal` redirige
  `CSV_PATH` / `CSV_LOCK_PATH` / `DUCKDB_PATH` de tous les modules qui en
  portent une copie vers un fichier temporaire ; `_isolate_universe_history`
  fait de même pour les snapshots.
- `modules/duckdb_journal.py` : chemins résolus **à l'appel** (`db_path=None`),
  pas à l'import (un default lié à l'import ignorait le monkeypatch : c'est
  ainsi que pytest écrivait dans la DB prod).
- `SWINGQUANT_TEST_GUARD=1 pytest` : hash de `trade_journal.csv`,
  `trade_journal.duckdb`, `proposals.json` avant/après la session ; exit 3 si
  modifiés. À activer en CI ; sur la VM, le tracker peut légitimement écrire
  pendant la suite (faux positif possible).
- Loggers : le handler fichier de `modules/log.py` et du tracker est
  désactivé sous pytest (avant : 250 lignes CRITICAL factices par run dans
  `agent.log`).

## Tests de non-régression clés

| Fichier | Protège |
|---|---|
| `test_broker_protection_audit_2026_09_17.py` | bracket GTC, ré-armement (OCO, fallback, orphelin, qty), sync (grâce, vieux fills, CANCELED), fusion journal |
| `test_improvements_2026_09_17.py` | rebalance 1/N, stop de portefeuille, shadow, attribution, Telegram |
| `test_auto_approve.py`, `test_basket_rotation.py` | mode basket (rang, verdicts, Risk, killswitch), rotation |
| `test_cockpit_router.py`, `test_scoring_lab.py` | endpoints cockpit, lab, rank_fn, verdict |
| `test_tracker_*.py` | SL/TP/TS (15 %/30 %), killswitch freeze (−6 %), cycle |
| `test_auto_proposer_gates.py`, `test_portfolio_*` | gates, sizing, caps |

## Comment tester un changement sensible

1. Écrire le test qui **échoue** sur le bug (reproduire avec un faux client
   Alpaca : voir `_FakeClient` dans les tests broker).
2. `make lint && make test`.
3. Pour le broker : dry-run sur le compte paper
   (`python -m modules.basket_rotation`, `--alpaca-test`, un cycle
   `python tracker.py`) et lecture de `/api/portfolio/protection`.
4. Commit (le sync déploie) ; surveiller `backend/logs/tracker.log` et le
   Cockpit pendant deux cycles.

## Dette connue

- Couverture 65 % : les routeurs « Mon Portefeuille » et les providers
  réseau sont peu testés.
- Pas de test end-to-end navigateur du frontend (build + lint seulement).
- Les tests réseau (yfinance, Alpaca) sont mockés ; un changement d'API
  externe n'est détecté qu'en prod (logs + `/api/system/health`).
