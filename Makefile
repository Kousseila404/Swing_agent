# SwingQuant TITAN — Makefile racine
# ─────────────────────────────────────────────────────────────────
# Cibles principales :
#   make test         — pytest backend (suite complète)
#   make test-fast    — pytest -x --ff (premier échec, prioritise les flaky)
#   make lint         — ruff backend
#   make types        — frontend openapi → ts (check drift)
#   make dev          — uvicorn reload + vite dev (deux terminaux)
#   make build        — frontend production build
#   make backup       — copie horodatée du trade journal
#   make ci-local     — reproduit la CI GitHub en local (lint+test+build)
#   make redeploy     — restart systemd swing-api.service (sudo)
# ─────────────────────────────────────────────────────────────────
SHELL := /usr/bin/env bash

ROOT       := $(shell pwd)
BACKEND    := $(ROOT)/backend
FRONTEND   := $(ROOT)/frontend
PY         := $(BACKEND)/venv/bin/python
PYTEST     := $(PY) -m pytest
RUFF       := $(PY) -m ruff
NPM        := npm --prefix $(FRONTEND)

.PHONY: help test test-fast test-cov lint types dev dev-backend dev-frontend \
        build backup ci-local redeploy clean install-dev

help:
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1;32m%-15s\033[0m %s\n", $$1, $$2}'

test: ## Suite pytest complète backend
	cd $(BACKEND) && $(PYTEST) tests/ -q

test-fast: ## pytest -x --ff (stoppe au premier échec, priorité flaky)
	cd $(BACKEND) && $(PYTEST) tests/ -x --ff -q

test-cov: ## pytest avec couverture (terminal + xml)
	cd $(BACKEND) && $(PYTEST) tests/ --cov=modules --cov-report=term --cov-report=xml

lint: ## Ruff lint backend (modules + tests + entrypoints)
	cd $(BACKEND) && $(RUFF) check modules/ tests/ api.py tracker.py

lint-fix: ## Ruff auto-fix
	cd $(BACKEND) && $(RUFF) check --fix modules/ tests/ api.py tracker.py

types: ## Frontend : régénère openapi→ts et fail si drift
	$(NPM) run check:types

dev-backend: ## uvicorn reload (dev only — la prod est systemd)
	cd $(BACKEND) && $(PY) -m uvicorn api:app --reload --host 0.0.0.0 --port 8000

dev-frontend: ## vite dev
	$(NPM) run dev

dev: ## Aide : lance backend + frontend dans deux terminaux séparés
	@echo "Lance dans 2 terminaux :"
	@echo "  make dev-backend"
	@echo "  make dev-frontend"

build: ## Frontend production build
	$(NPM) run build

backup: ## Backup atomique trade_journal.duckdb
	$(ROOT)/scripts/backup_journal.sh

ci-local: lint test build ## Reproduit la CI (lint + test + build)
	@echo "✓ CI locale verte"

redeploy: ## Restart systemd swing-api.service (nécessite sudo)
	sudo systemctl restart swing-api.service
	@sleep 1
	sudo systemctl status swing-api.service --no-pager | head -10

install-dev: ## (Ré)installe les dev deps dans le venv
	$(PY) -m pip install -r $(BACKEND)/requirements-dev.txt

git-creds: ## Régénère ~/.git-credentials depuis backend/.env (après rotation PAT)
	$(ROOT)/scripts/setup_git_creds.sh

clean: ## Supprime caches Python + frontend dist
	find $(BACKEND) -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf $(BACKEND)/.pytest_cache $(FRONTEND)/dist $(BACKEND)/coverage.xml
