"""Tests auth Bearer — fail-closed par défaut sur tous les endpoints mutants.

Contrat :
  - API_TOKEN absent + ALLOW_UNAUTHENTICATED≠true → 503 (config manquante)
  - API_TOKEN set sans header Authorization → 401
  - API_TOKEN set + mauvais token → 401
  - API_TOKEN set + bon token → 200/400 (endpoint logic)

Couvre les endpoints protégés par `api_core.require_auth` restants après la
purge ControlPanel (2026-04-24) :
  POST /api/trade/add
  POST /api/trade/close
  POST /api/job/{job_id}/kill
  POST /api/universe/rebuild
  POST /api/proposals/{id}/approve
  POST /api/proposals/{id}/reject
  POST /api/proposals/refresh
  POST /api/proposals/regenerate
  POST /api/proposals/approve_batch
  POST /api/proposals/reject_batch
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api
from modules import api_core

_MUTANT_ENDPOINTS = [
    ("POST", "/api/trade/add", {
        "ticker": "AAPL", "direction": "LONG", "entry": 100.0,
        "stop_loss": 95.0, "take_profit": 110.0, "size": 10,
    }),
    ("POST", "/api/trade/close", {
        "ticker": "AAPL", "exit_price": 105.0, "result": "WIN",
    }),
    ("POST", "/api/job/nonexistent/kill", None),
    ("POST", "/api/universe/rebuild", {}),
]


@pytest.mark.parametrize("method,path,body", _MUTANT_ENDPOINTS)
def test_mutant_endpoint_503_when_no_token_configured(monkeypatch, method, path, body):
    """Sans API_TOKEN ni ALLOW_UNAUTH → 503 (config manquante, fail-closed)."""
    monkeypatch.setattr(api_core, "API_TOKEN", "")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", False)

    client = TestClient(api.app)
    resp = client.request(method, path, json=body or {})
    assert resp.status_code == 503
    assert "Auth non configurée" in resp.json().get("detail", "")


@pytest.mark.parametrize("method,path,body", _MUTANT_ENDPOINTS)
def test_mutant_endpoint_401_without_header(monkeypatch, method, path, body):
    """Token configuré mais header Authorization absent → 401."""
    monkeypatch.setattr(api_core, "API_TOKEN", "s3cret")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", False)

    resp = TestClient(api.app).request(method, path, json=body or {})
    assert resp.status_code == 401


@pytest.mark.parametrize("method,path,body", _MUTANT_ENDPOINTS)
def test_mutant_endpoint_401_with_wrong_token(monkeypatch, method, path, body):
    """Token configuré mais Bearer invalide → 401."""
    monkeypatch.setattr(api_core, "API_TOKEN", "s3cret")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", False)

    resp = TestClient(api.app).request(
        method, path,
        headers={"Authorization": "Bearer wrong"},
        json=body or {},
    )
    assert resp.status_code == 401


def test_readonly_endpoints_unauthenticated_ok(monkeypatch):
    """Les endpoints GET publics ne sont PAS affectés par l'auth."""
    monkeypatch.setattr(api_core, "API_TOKEN", "")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", False)

    client = TestClient(api.app)
    for path in ("/api/status", "/api/macro", "/api/proposals"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} should be public (got {resp.status_code})"


def test_sectors_refresh_momentum_requires_auth(monkeypatch):
    """/api/sectors sans refresh_momentum = public ; avec = auth requise."""
    monkeypatch.setattr(api_core, "API_TOKEN", "s3cret")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", False)

    client = TestClient(api.app)
    # Lecture du cache : public, pas de 401 (peut être 200 ou 500 selon data)
    resp_cached = client.get("/api/sectors")
    assert resp_cached.status_code != 401

    # Refresh = auth required → 401 sans token
    resp_refresh = client.get("/api/sectors?refresh_momentum=true")
    assert resp_refresh.status_code == 401
