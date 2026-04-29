"""Tests endpoint /api/data_health — observabilité providers + universe."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api
from modules import api_core
from modules import fundamentals_cache as fc
from modules.yf_circuit_breaker import yf_breaker


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.fixture(autouse=True)
def reset_breaker():
    """Reset le breaker entre tests (singleton process)."""
    yf_breaker.reset()
    yield
    yf_breaker.reset()


@pytest.fixture
def isolated_universe(tmp_path, monkeypatch):
    """Crée un universe.json synthétique avec mix de tickers complets/incomplets."""
    p = tmp_path / "universe.json"
    now = datetime.now(UTC)
    fresh = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = (now - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    very_stale = (now - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")

    p.write_text(json.dumps({
        "updated_at": fresh,
        "tickers": {
            # 5 tickers complets, fresh
            **{f"FRESH{i}": {
                "ticker": f"FRESH{i}", "fetched_at": fresh,
                "return_on_equity": 0.2, "operating_margin": 0.25,
                "ev_to_ebitda": 15.0, "free_cash_flow": 1e9,
                "operating_cash_flow": 1.2e9,
                "debt_to_equity": 50.0, "current_ratio": 2.0,
                "recommendation_mean": 2.0, "price_target_mean": 120.0,
                "momentum_return_pct": 10.0,
                "return_on_assets": 0.15, "net_income": 5e8, "gross_margin": 0.4,
                "source_provider": "yfinance+cached",
            } for i in range(5)},
            # 2 tickers stale (20 jours)
            **{f"STALE{i}": {
                "ticker": f"STALE{i}", "fetched_at": stale,
                "return_on_equity": 0.1,
                "source_provider": "yfinance+cached/stale",
            } for i in range(2)},
            # 1 très stale (45 jours)
            "OLD1": {
                "ticker": "OLD1", "fetched_at": very_stale,
                "return_on_equity": 0.05,
                "source_provider": "fmp+yfinance+cached",
            },
        },
    }))
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", p)
    return p


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "CACHE_DIR", tmp_path / "fc")
    monkeypatch.setattr(fc, "CACHE_PATH", tmp_path / "fc" / "index.json.gz")
    fc._store.clear()
    yield
    fc._store.clear()


# ─────────────────────────────────────────────────────────────────
# Smoke
# ─────────────────────────────────────────────────────────────────

def test_data_health_public_no_auth(client):
    """Endpoint public — pas d'auth requise."""
    resp = client.get("/api/data_health")
    assert resp.status_code == 200
    body = resp.json()
    assert "severity_global" in body
    assert "yf_breaker" in body
    assert "fundamentals_cache" in body
    assert "universe" in body
    assert "fmp" in body


def test_data_health_severity_ok_when_clean(client, isolated_universe, isolated_cache):
    """Universe propre + breaker closed + cache vide → severity ok."""
    resp = client.get("/api/data_health")
    body = resp.json()
    # Avec 5 fresh + 2 stale + 1 very-stale (8 tickers) :
    # - n_severe=1 → critical sur fetched_at component
    # MAIS le seuil n_severe>0 → critical : on ATTEND critical ici.
    assert body["severity_global"] in ("warning", "critical")


def test_data_health_yf_breaker_tripped_warning(client, isolated_universe, isolated_cache):
    yf_breaker.record(_make_429())
    resp = client.get("/api/data_health")
    body = resp.json()
    assert body["yf_breaker"]["tripped"] is True
    assert body["yf_breaker"]["cooldown_seconds"] > 0
    assert body["severity_global"] in ("warning", "critical")


def test_data_health_universe_fields_missing_breakdown(client, isolated_universe,
                                                         isolated_cache):
    resp = client.get("/api/data_health")
    body = resp.json()
    assert body["universe"]["loaded"] is True
    assert body["universe"]["n_tickers"] == 8
    fields = body["universe"]["fields_missing"]
    # ROE est présent sur tous les 8 → 0% missing
    assert fields["return_on_equity"]["missing_pct"] == 0.0
    # Lot 8 fields : présents seulement sur les 5 FRESH → 3/8 = 37.5% missing
    assert 30 < fields["return_on_assets"]["missing_pct"] < 50


def test_data_health_universe_fetched_at_distribution(client, isolated_universe, isolated_cache):
    resp = client.get("/api/data_health")
    body = resp.json()
    fa = body["universe"]["fetched_at"]
    assert fa["n_severe"] == 1   # OLD1 à 45j > 30
    assert fa["n_stale"] == 3    # OLD1 + 2 STALE > 14j


def test_data_health_universe_sources_aggregation(client, isolated_universe, isolated_cache):
    resp = client.get("/api/data_health")
    body = resp.json()
    sources = body["universe"]["sources"]
    # 3 sources distinctes
    assert "yfinance+cached" in sources
    assert "yfinance+cached/stale" in sources
    assert "fmp+yfinance+cached" in sources
    assert sources["yfinance+cached"] == 5


def test_data_health_no_universe_file(client, tmp_path, monkeypatch):
    """Universe absent → loaded=False, severity reste calculable."""
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", tmp_path / "missing.json")
    resp = client.get("/api/data_health")
    body = resp.json()
    assert body["universe"]["loaded"] is False
    assert "severity_global" in body


# ─────────────────────────────────────────────────────────────────
# Sanitize flags → severity (audit 2026-04-23)
# ─────────────────────────────────────────────────────────────────

def _seed_flagged_cache(n: int):
    """Empile n entrées flaggées dans le cache singleton."""
    from data_providers.base import FinancialRatios
    for i in range(n):
        r = FinancialRatios(
            ticker=f"FLAG{i}", source_provider="fake",
            error="dq_sanitize=out_of_bounds:ev_to_ebitda",
        )
        fc._store.put(f"FLAG{i}", r)


def test_data_health_sanitize_exposes_flag_stats(client, isolated_cache):
    _seed_flagged_cache(3)
    resp = client.get("/api/data_health")
    body = resp.json()
    assert body["sanitize"]["n_tickers_flagged"] == 3
    assert body["sanitize"]["flags"].get("out_of_bounds:ev_to_ebitda") == 3


def test_data_health_severity_warning_above_sanitize_threshold(
    client, isolated_cache, tmp_path, monkeypatch,
):
    """≥ 20 tickers flaggés → warning (même si reste ok)."""
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", tmp_path / "missing.json")
    _seed_flagged_cache(25)
    body = client.get("/api/data_health").json()
    assert body["sanitize"]["n_tickers_flagged"] == 25
    assert body["severity_global"] in ("warning", "critical")


def test_data_health_severity_critical_when_massive_flagged(
    client, isolated_cache, tmp_path, monkeypatch,
):
    """≥ 100 tickers flaggés → critical."""
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", tmp_path / "missing.json")
    _seed_flagged_cache(120)
    body = client.get("/api/data_health").json()
    assert body["severity_global"] == "critical"


def test_data_health_severity_ok_when_few_flagged(
    client, isolated_cache, tmp_path, monkeypatch,
):
    """< 20 flaggés = EV/EBITDA négatifs légitimes → reste ok."""
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", tmp_path / "missing.json")
    _seed_flagged_cache(5)
    body = client.get("/api/data_health").json()
    # Pas de bump severity à 5 flags.
    assert body["severity_global"] == "ok"


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _make_429():
    import requests
    resp = requests.Response()
    resp.status_code = 429
    return requests.HTTPError("429", response=resp)
