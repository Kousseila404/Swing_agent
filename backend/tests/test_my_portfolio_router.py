"""Tests d'intégration — GET /api/my_portfolio (routers/my_portfolio.py).

Book personnel long terme, statique, hors moteur TITAN. Auth bypass via
ALLOW_UNAUTH=True pour isoler la logique de calcul (poids réel, dérive,
badge LNVGY).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import api
import routers.my_portfolio as my_portfolio_router
from modules import api_core


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(api_core, "API_TOKEN", "")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", True)
    return TestClient(api.app)


def test_my_portfolio_happy_path_prices_available(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", lambda t: 100.0)
    r = _client(monkeypatch).get("/api/my_portfolio")
    assert r.status_code == 200
    body = r.json()
    assert len(body["positions"]) == 10
    assert body["cash_reserve"]["amount"] == 200.0
    assert [w["ticker"] for w in body["watchlist"]] == ["SEZL"]
    # total = sum(shares * 100) + 200
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["current_value"] == round(2.338323 * 100, 2)
    assert bnp["price_stale"] is False


def test_my_portfolio_lnvgy_is_pending_with_badge_and_no_drift(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", lambda t: 50.0)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    lnvgy = next(p for p in body["positions"] if p["ticker"] == "LNVGY")
    assert lnvgy["is_pending"] is True
    assert lnvgy["current_value"] == 0.0
    assert lnvgy["drift_pct"] is None
    assert lnvgy["rebalance_alert"] is False
    assert lnvgy["badge"] == "⏳ En attente earnings 21/08"


def test_my_portfolio_price_fetch_failure_falls_back_to_target_amount(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", lambda t: None)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["price_stale"] is True
    assert bnp["current_value"] == 300.0  # fallback target_amount


def test_my_portfolio_rebalance_alert_fires_beyond_threshold(monkeypatch):
    # BNP.PA cible 15% ($300 sur ~$2000). On force son prix très haut pour
    # que son poids réel s'envole et dépasse la dérive tolérée de ±25%.
    def _price(ticker):
        return 100_000.0 if ticker == "BNP.PA" else 1.0
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _price)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["rebalance_alert"] is True
    assert bnp["drift_pct"] > 25.0


def test_my_portfolio_requires_auth_when_configured(monkeypatch):
    monkeypatch.setattr(api_core, "API_TOKEN", "secret-token")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", False)
    monkeypatch.setattr(my_portfolio_router, "_safe_price", lambda t: 100.0)
    client = TestClient(api.app)
    r = client.get("/api/my_portfolio")
    assert r.status_code == 401
