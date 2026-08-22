"""Tests d'intégration — POST/GET /api/my_portfolio/executions (Upgrade 4).

Vérification live (TestClient(api.app), ASGI in-process) : un round-trip
POST puis GET prouve que le pipeline complet fonctionne (routing,
validation Pydantic, calcul du slippage, persistance CSV, agrégats) — pas
seulement la logique unitaire du module `my_portfolio_executions`, déjà
couverte par `test_my_portfolio_executions.py`.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import api
from modules import api_core
from modules import my_portfolio_executions as mpe


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(api_core, "API_TOKEN", "")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", True)
    return TestClient(api.app)


def _mock_market(monkeypatch, ref_price=64.02, resolution="daily_close"):
    monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (ref_price, resolution))
    monkeypatch.setattr(mpe, "get_historical_fx_rate", lambda pair, at: (None, False))


def test_post_execution_live_roundtrip_via_disk_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(mpe, "CSV_PATH", tmp_path / "execs.csv")
    monkeypatch.setattr(mpe, "CSV_LOCK_PATH", tmp_path / "execs.csv.lock")
    _mock_market(monkeypatch)
    client = _client(monkeypatch)

    payload = {
        "ticker": "CNC",
        "direction": "BUY",
        "shares": 3.27527,
        "fill_price_native": 67.17,
        "executed_at": "2026-08-19T09:50:00+02:00",
        "notes": "hors séance US",
    }
    r = client.post("/api/my_portfolio/executions", json=payload)
    assert r.status_code == 200
    posted = r.json()
    assert posted["Ticker"] == "CNC"
    assert posted["Primary_Exchange"] == "US"
    assert posted["Market_Open_At_Fill"] is False
    assert posted["Reference_Price_USD"] == 64.02
    assert posted["Slippage_Bps"] > 0

    r2 = client.get("/api/my_portfolio/executions")
    assert r2.status_code == 200
    body = r2.json()
    assert len(body["executions"]) == 1
    assert body["executions"][0]["Ticker"] == "CNC"
    assert body["summary"]["n_fills"] == 1
    assert body["summary"]["n_fills_outside_market"] == 1
    assert body["summary"]["total_slippage_usd"] > 0


def test_get_executions_empty_journal_before_first_post(monkeypatch, tmp_path):
    monkeypatch.setattr(mpe, "CSV_PATH", tmp_path / "execs.csv")
    monkeypatch.setattr(mpe, "CSV_LOCK_PATH", tmp_path / "execs.csv.lock")
    client = _client(monkeypatch)

    r = client.get("/api/my_portfolio/executions")
    assert r.status_code == 200
    body = r.json()
    assert body["executions"] == []
    assert body["summary"]["n_fills"] == 0
    assert body["summary"]["total_slippage_usd"] is None


def test_post_execution_rejects_naive_datetime(monkeypatch, tmp_path):
    monkeypatch.setattr(mpe, "CSV_PATH", tmp_path / "execs.csv")
    monkeypatch.setattr(mpe, "CSV_LOCK_PATH", tmp_path / "execs.csv.lock")
    _mock_market(monkeypatch)
    client = _client(monkeypatch)

    payload = {
        "ticker": "CNC",
        "direction": "BUY",
        "shares": 1.0,
        "fill_price_native": 67.17,
        "executed_at": "2026-08-19T09:50:00",
        "notes": "",
    }
    r = client.post("/api/my_portfolio/executions", json=payload)
    assert r.status_code == 400


def test_post_execution_rejects_invalid_direction(monkeypatch, tmp_path):
    monkeypatch.setattr(mpe, "CSV_PATH", tmp_path / "execs.csv")
    monkeypatch.setattr(mpe, "CSV_LOCK_PATH", tmp_path / "execs.csv.lock")
    _mock_market(monkeypatch)
    client = _client(monkeypatch)

    payload = {
        "ticker": "CNC",
        "direction": "HOLD",
        "shares": 1.0,
        "fill_price_native": 67.17,
        "executed_at": "2026-08-19T09:50:00+02:00",
    }
    r = client.post("/api/my_portfolio/executions", json=payload)
    assert r.status_code == 400


def test_post_execution_requires_auth_when_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(mpe, "CSV_PATH", tmp_path / "execs.csv")
    monkeypatch.setattr(mpe, "CSV_LOCK_PATH", tmp_path / "execs.csv.lock")
    monkeypatch.setattr(api_core, "API_TOKEN", "secret-token")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", False)
    client = TestClient(api.app)

    r = client.get("/api/my_portfolio/executions")
    assert r.status_code == 401
