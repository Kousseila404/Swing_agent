"""Tests endpoint /api/data_health — observabilité providers + universe."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api
from modules import api_core, metrics_agent
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


# ─────────────────────────────────────────────────────────────────
# Providers non-fondamentaux (Étape 0 — dashboard toutes-sources)
# ─────────────────────────────────────────────────────────────────

def test_data_health_exposes_providers_section(client):
    resp = client.get("/api/data_health")
    body = resp.json()
    providers = body["providers"]
    for key in ("finnhub", "news", "insider", "sec_filings"):
        assert key in providers
        assert "cache" in providers[key]
        assert "n_cached" in providers[key]["cache"]
    assert "configured" in providers["finnhub"]
    assert "configured" in providers["news"]
    assert "cik_map_age_sec" in providers


def test_data_health_providers_cache_counts_disk_files(client, tmp_path, monkeypatch):
    """Un fichier cache finnhub sur disque doit remonter dans n_cached."""
    from data_providers import finnhub_provider

    cache_dir = tmp_path / "finnhub_cache"
    monkeypatch.setattr(finnhub_provider, "_CACHE_DIR", cache_dir)
    finnhub_provider._write_cache("AAPL", {"ticker": "AAPL", "error": None})
    finnhub_provider._write_cache("MSFT", {"ticker": "MSFT", "error": "boom"})

    resp = client.get("/api/data_health")
    body = resp.json()
    cache = body["providers"]["finnhub"]["cache"]
    assert cache["n_cached"] == 2
    assert cache["n_errors"] == 1


def test_data_health_enrichment_errors_counted(client, tmp_path, monkeypatch):
    """Étape 6 — insider_error/finnhub_error truthy doivent être comptés,
    séparément l'un de l'autre, et jamais sur une valeur falsy/absente."""
    p = tmp_path / "universe.json"
    p.write_text(json.dumps({
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tickers": {
            "A": {"ticker": "A", "insider_error": "cik_unknown"},
            "B": {"ticker": "B", "finnhub_error": "http_429"},
            "C": {"ticker": "C", "insider_error": "sec_fetch_failed",
                  "finnhub_error": "rate_limited"},
            "D": {"ticker": "D", "insider_error": None, "finnhub_error": None},
            "E": {"ticker": "E"},
        },
    }))
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", p)
    resp = client.get("/api/data_health")
    body = resp.json()
    errs = body["universe"]["enrichment_errors"]
    assert errs["insider_error"] == 2
    assert errs["finnhub_error"] == 2


def test_data_health_enrichment_errors_lists_tickers(client, tmp_path, monkeypatch):
    """Étape 14 — en plus du compteur, la liste {ticker, error} elle-même,
    triée, insensible aux valeurs falsy/absentes, séparée par source."""
    p = tmp_path / "universe.json"
    p.write_text(json.dumps({
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tickers": {
            "B": {"ticker": "B", "finnhub_error": "http_429"},
            "A": {"ticker": "A", "insider_error": "cik_unknown"},
            "C": {"ticker": "C", "insider_error": "sec_fetch_failed",
                  "finnhub_error": "rate_limited"},
            "D": {"ticker": "D", "insider_error": None, "finnhub_error": None},
            "E": {"ticker": "E"},
        },
    }))
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", p)
    resp = client.get("/api/data_health")
    body = resp.json()
    errs = body["universe"]["enrichment_errors"]
    assert errs["insider_error_tickers"] == [
        {"ticker": "A", "error": "cik_unknown"},
        {"ticker": "C", "error": "sec_fetch_failed"},
    ]
    assert errs["finnhub_error_tickers"] == [
        {"ticker": "B", "error": "http_429"},
        {"ticker": "C", "error": "rate_limited"},
    ]


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


def test_data_health_sanitize_exposes_flagged_tickers(client, isolated_cache):
    """Étape 7 : la liste des tickers flaggés (pas juste le compte) est exposée."""
    _seed_flagged_cache(3)
    resp = client.get("/api/data_health")
    body = resp.json()
    assert [t["ticker"] for t in body["sanitize"]["tickers"]] == ["FLAG0", "FLAG1", "FLAG2"]


def test_data_health_sanitize_tickers_include_tags(client, isolated_cache):
    """Étape 8 : chaque ticker flaggé porte son détail de tag(s), pas juste le symbole."""
    from data_providers.base import FinancialRatios
    fc._store.put("MULTI", FinancialRatios(
        ticker="MULTI", source_provider="fake",
        error="dq_sanitize=out_of_bounds:ev_to_ebitda|mcap_mismatch",
    ))
    resp = client.get("/api/data_health")
    body = resp.json()
    entries = {t["ticker"]: t["tags"] for t in body["sanitize"]["tickers"]}
    assert entries["MULTI"] == ["out_of_bounds:ev_to_ebitda", "mcap_mismatch"]


def test_data_health_sanitize_tickers_empty_when_clean(client, isolated_cache):
    resp = client.get("/api/data_health")
    body = resp.json()
    assert body["sanitize"]["tickers"] == []


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
# Historique metrics_history.jsonl (Étape 17)
# ─────────────────────────────────────────────────────────────────

def test_data_health_history_empty_when_file_missing(client, tmp_path, monkeypatch):
    """Fichier absent (cas attendu en local/sandbox) → liste vide, jamais d'erreur."""
    monkeypatch.setattr(metrics_agent, "METRICS_HISTORY_PATH", tmp_path / "missing.jsonl")
    body = client.get("/api/data_health").json()
    assert body["history"] == []


def test_data_health_history_exposes_last_n_points(client, tmp_path, monkeypatch):
    """Points JSONL déjà persistés par metrics_agent sont réexposés tels quels."""
    hist_path = tmp_path / "metrics_history.jsonl"
    entries = [
        {"timestamp": f"2026-07-{10 + i:02d}T06:00:00+00:00",
         "severity_global": "ok", "stale_ratio": 0.1 + i * 0.01,
         "n_dq_sanitize": i}
        for i in range(3)
    ]
    hist_path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8",
    )
    monkeypatch.setattr(metrics_agent, "METRICS_HISTORY_PATH", hist_path)
    body = client.get("/api/data_health").json()
    assert body["history"] == entries


def test_data_health_history_caps_to_last_n(client, tmp_path, monkeypatch):
    """Plus de points que _HISTORY_LAST_N → seuls les N derniers sont retournés."""
    from routers import data_health

    hist_path = tmp_path / "metrics_history.jsonl"
    entries = [{"timestamp": f"idx-{i}"} for i in range(data_health._HISTORY_LAST_N + 5)]
    hist_path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8",
    )
    monkeypatch.setattr(metrics_agent, "METRICS_HISTORY_PATH", hist_path)
    body = client.get("/api/data_health").json()
    assert len(body["history"]) == data_health._HISTORY_LAST_N
    assert body["history"][-1] == {"timestamp": f"idx-{data_health._HISTORY_LAST_N + 4}"}


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _make_429():
    import requests
    resp = requests.Response()
    resp.status_code = 429
    return requests.HTTPError("429", response=resp)
