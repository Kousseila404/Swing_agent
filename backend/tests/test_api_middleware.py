"""Middlewares transverses de api.py — CORS + security headers.

Contrat :
  - Le preflight CORS autorise toutes les méthodes réellement exposées par
    les routers (PATCH/PUT/DELETE inclus — watchlist, thesis, review queue).
    Régression 2026-09 : allow_methods limité à GET/POST rendait ces
    endpoints inutilisables cross-origin (préflight refusé) alors qu'ils
    marchaient derrière le proxy Vite same-origin.
  - Les headers de sécurité sont posés sur toute réponse HTTP, y compris
    les erreurs, et Cache-Control private uniquement sous /api/.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api

_ORIGIN = "http://localhost:5173"


@pytest.fixture
def client() -> TestClient:
    return TestClient(api.app)


@pytest.mark.parametrize("method", ["PATCH", "PUT", "DELETE", "POST"])
def test_cors_preflight_allows_mutating_methods(client: TestClient, method: str):
    resp = client.options(
        "/api/watchlist/AAPL",
        headers={
            "Origin": _ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code == 200
    allowed = {m.strip() for m in resp.headers["access-control-allow-methods"].split(",")}
    assert method in allowed
    assert resp.headers["access-control-allow-origin"] == _ORIGIN


def test_security_headers_on_api_response(client: TestClient):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "same-origin"
    assert resp.headers["cache-control"] == "private, max-age=0"


def test_security_headers_on_error_response(client: TestClient):
    resp = client.get("/api/jobs/not-a-valid-id")
    assert resp.status_code == 400
    assert resp.headers["x-frame-options"] == "DENY"


def test_no_private_cache_control_outside_api(client: TestClient):
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers.get("cache-control") != "private, max-age=0"
