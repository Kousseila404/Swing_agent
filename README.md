# SwingQuant TITAN

[![CI](https://github.com/Kousseila404/Swing_agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Kousseila404/Swing_agent/actions/workflows/ci.yml)

Système quantamental long-terme. Backend FastAPI (Python 3.12) + frontend
React 19 (Vite). Scoring TITAN 6 piliers (Quality / Value / Risk / Sector /
Momentum / Piotroski), sizing risk-parity (HRP en canary), exécution Alpaca
brackets avec gates fail-closed.

## Stack

- **Backend** — FastAPI, DuckDB, yfinance/FMP/Polygon adapters, ~28 k LOC, 1386 tests.
- **Frontend** — React 19, React Query, Recharts, openapi-typescript pour les types.
- **Prod** — `swing-api.service` (systemd) sur VM unique. Cron quotidien `run_titan.sh` à 06:00.

## Quick start

```bash
git clone https://github.com/Kousseila404/Swing_agent.git swingquant
cd swingquant

# Backend
python3.12 -m venv backend/venv
backend/venv/bin/pip install -r backend/requirements-dev.txt

# Frontend
npm --prefix frontend ci

# Tout vérifier en local (= CI GitHub)
make ci-local        # ruff + pytest+cov + frontend build
make test            # pytest seul
make dev-backend     # uvicorn reload
make dev-frontend    # vite dev
```

## Commandes utiles

| Cible | Effet |
|---|---|
| `make ci-local` | Reproduit la CI complète |
| `make test` / `test-fast` / `test-cov` | Pytest variants |
| `make lint` / `lint-fix` | Ruff backend |
| `make types` | OpenAPI → TypeScript drift check |
| `make backup` | Backup horodaté du trade journal |
| `make redeploy` | Restart `swing-api.service` (sudo) |

Le runbook complet (deploy, backup/restore, cron, checklist) vit dans
[`DEPLOY.md`](DEPLOY.md).

## Layout

```
backend/
  api.py                    # FastAPI composition (thin)
  routers/                  # 21 routers HTTP
  modules/                  # logique métier
    portfolio/              # sizing, risk parity, HRP, sector cap
    tracker/                # killswitch, evaluation, market hours
    sector_metrics/         # scoring sectoriel
  data_providers/           # ABC + FMP/Polygon/yfinance/Stooq + composite fallback
  tests/                    # 1386 tests pytest
frontend/
  src/api/                  # client + types générés depuis OpenAPI
  src/components/           # React 19 pages
  src/utils/                # helpers + tests Vitest
scripts/
  backup_journal.sh         # backup atomique horodaté
.github/workflows/ci.yml    # ruff + mypy + pytest+cov 60% + eslint + vitest + build + docker smoke run
docker-compose.yml          # dev/CI reproductibilité
DEPLOY.md                   # runbook ingénierie
```

## Tests & qualité

- **1386 tests pytest**, coverage **64.5 %** (seuil CI **60 %**, durcir vers 70 %).
- **mypy** sur tout le backend (0 erreur), strict sur `modules/portfolio/*` (`mypy.ini`).
- **ruff** + **eslint** (bloquants en CI) + **pre-commit** (optionnel).
- L'image Docker est démarrée en CI et doit répondre sur `/api/status`.

Avant un changement risqué, voir la checklist `DEPLOY.md` § 8.
