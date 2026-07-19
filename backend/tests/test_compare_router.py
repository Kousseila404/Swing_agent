"""Tests d'intégration — GET /api/compare (routers/peers.py).

Comparaison manuelle multi-tickers (Étape 4 roadmap) — distincte de
GET /api/peers/{ticker} (auto-sélection sector + market_cap).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import api
import routers.peers as peers_router


def _client() -> TestClient:
    return TestClient(api.app)


def _universe():
    return {
        "AAA": {"name": "Alpha", "sector": "Tech", "industry": "Software",
                "market_cap": 1e9, "trailing_pe": 10.0, "titan_composite_score": 70.0},
        "BBB": {"name": "Beta", "sector": "Healthcare", "industry": "Pharma",
                "market_cap": 5e9, "trailing_pe": 30.0, "titan_composite_score": 55.0},
    }


def test_compare_requires_at_least_two_tickers(monkeypatch):
    monkeypatch.setattr(peers_router, "get_scored_universe", lambda: _universe())
    r = _client().get("/api/compare", params={"tickers": "AAA"})
    assert r.status_code == 400


def test_compare_rejects_too_many_tickers(monkeypatch):
    monkeypatch.setattr(peers_router, "get_scored_universe", lambda: _universe())
    tickers = ",".join(f"T{i}" for i in range(10))
    r = _client().get("/api/compare", params={"tickers": tickers})
    assert r.status_code == 400


def test_compare_rejects_invalid_ticker(monkeypatch):
    monkeypatch.setattr(peers_router, "get_scored_universe", lambda: _universe())
    r = _client().get("/api/compare", params={"tickers": "AAA,BAD-TICKER"})
    assert r.status_code == 400


def test_compare_returns_503_when_universe_unavailable(monkeypatch):
    monkeypatch.setattr(peers_router, "get_scored_universe", lambda: {})
    r = _client().get("/api/compare", params={"tickers": "AAA,BBB"})
    assert r.status_code == 503


def test_compare_happy_path(monkeypatch):
    monkeypatch.setattr(peers_router, "get_scored_universe", lambda: _universe())
    r = _client().get("/api/compare", params={"tickers": "aaa, bbb"})
    assert r.status_code == 200
    body = r.json()
    assert body["requested"] == ["AAA", "BBB"]
    assert [row["ticker"] for row in body["rows"]] == ["AAA", "BBB"]
    assert body["missing"] == []
    assert body["median"]["trailing_pe"] == 20.0


def test_compare_reports_missing_tickers(monkeypatch):
    monkeypatch.setattr(peers_router, "get_scored_universe", lambda: _universe())
    r = _client().get("/api/compare", params={"tickers": "AAA,ZZZ"})
    assert r.status_code == 200
    body = r.json()
    assert body["missing"] == ["ZZZ"]
    assert [row["ticker"] for row in body["rows"]] == ["AAA"]
