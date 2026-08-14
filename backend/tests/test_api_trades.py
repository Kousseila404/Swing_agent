"""Tests endpoints POST /api/trade/* — validations de contrat.

Couvre :
  - /trade/add : validations LONG/SHORT (SL/TP relatifs à Entry), ticker vide,
    prix/size ≤ 0, calcul RR, persistance CSV.
  - /trade/close : ticker sans position OPEN → 404, clôture valide.

Auth bypass via ALLOW_UNAUTH=True pour isoler la logique métier.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api
from modules import api_core


@pytest.fixture(autouse=True)
def _bypass_auth_and_isolate_csv(monkeypatch, tmp_path: Path):
    """Désactive l'auth et redirige la CSV vers tmp_path pour tous les tests."""
    monkeypatch.setattr(api_core, "API_TOKEN", "")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", True)
    csv_path = tmp_path / "trade_journal.csv"
    lock_path = tmp_path / "trade_journal.csv.lock"
    monkeypatch.setattr(api_core, "CSV_PATH", csv_path)
    monkeypatch.setattr(api_core, "CSV_LOCK_PATH", lock_path)
    # Stub le shadow_insert/update DuckDB (on valide uniquement la CSV).
    #
    # Audit 2026-08-14 — régression trouvée en prod : `routers/trades.py` fait
    # `from modules.duckdb_journal import shadow_insert, shadow_update_status`,
    # ce qui lie SA PROPRE référence locale au moment de l'import. Patcher
    # `duckdb_journal.shadow_insert` (le module source) ne change PAS cette
    # référence déjà liée dans `routers.trades` — le endpoint continuait
    # d'appeler la VRAIE fonction, qui écrivait dans le VRAI
    # data/trade_journal.duckdb de prod à chaque run de la suite de tests
    # (4 lignes TSLA fantômes trouvées et nettoyées ce jour-là). Fix : patcher
    # la référence dans le module qui l'appelle réellement, `routers.trades`.
    import routers.trades as trades_router
    monkeypatch.setattr(trades_router, "shadow_insert", lambda *a, **k: None)
    monkeypatch.setattr(trades_router, "shadow_update_status", lambda *a, **k: None)
    return csv_path


# ─────────────────────────────────────────────────────────────────
# /api/trade/add — validations
# ─────────────────────────────────────────────────────────────────

def _valid_long_payload() -> dict:
    return {
        "ticker": "AAPL", "direction": "LONG", "entry": 100.0,
        "stop_loss": 95.0, "take_profit": 115.0, "size": 10,
    }


def test_trade_add_long_happy_path(_bypass_auth_and_isolate_csv: Path):
    client = TestClient(api.app)
    resp = client.post("/api/trade/add", json=_valid_long_payload())
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # RR = (115-100)/(100-95) = 3.0
    assert "RR 3.00" in resp.json()["message"]
    # CSV écrite
    df = pd.read_csv(_bypass_auth_and_isolate_csv, dtype=str)
    assert len(df) == 1
    assert df.iloc[0]["Ticker"] == "AAPL"
    assert df.iloc[0]["Direction"] == "LONG"
    assert df.iloc[0]["Status"] == "OPEN"


def test_fixture_patches_the_reference_trades_router_actually_calls(_bypass_auth_and_isolate_csv: Path):
    """Régression 2026-08-14 : `routers/trades.py` fait
    `from modules.duckdb_journal import shadow_insert, shadow_update_status`,
    ce qui lie SA PROPRE référence locale à l'import. Patcher
    `duckdb_journal.shadow_insert` (le module source, comme le faisait
    l'ancienne version du fixture) ne change PAS cette référence déjà liée —
    le endpoint continuait d'appeler la VRAIE fonction, qui a écrit 4 lignes
    TSLA fantômes dans le VRAI data/trade_journal.duckdb de prod à chaque run
    de la suite ce jour-là. Ce test échoue si le fixture régresse vers le
    mauvais module cible."""
    import routers.trades as trades_router
    from modules import duckdb_journal
    assert trades_router.shadow_insert is not duckdb_journal.shadow_insert
    assert trades_router.shadow_update_status is not duckdb_journal.shadow_update_status


def test_trade_add_short_happy_path(_bypass_auth_and_isolate_csv: Path):
    client = TestClient(api.app)
    payload = {
        "ticker": "tsla", "direction": "SHORT", "entry": 200.0,
        "stop_loss": 210.0, "take_profit": 180.0, "size": 5,
    }
    resp = client.post("/api/trade/add", json=payload)
    assert resp.status_code == 200
    # RR = (200-180)/(210-200) = 2.0
    assert "RR 2.00" in resp.json()["message"]
    df = pd.read_csv(_bypass_auth_and_isolate_csv, dtype=str)
    assert df.iloc[0]["Ticker"] == "TSLA"  # upper().strip()


def test_trade_add_empty_ticker_400():
    payload = _valid_long_payload() | {"ticker": "   "}
    resp = TestClient(api.app).post("/api/trade/add", json=payload)
    assert resp.status_code == 400
    assert "Ticker vide" in resp.json()["detail"]


def test_trade_add_negative_price_400():
    payload = _valid_long_payload() | {"entry": -10.0}
    resp = TestClient(api.app).post("/api/trade/add", json=payload)
    assert resp.status_code == 400
    assert "Prix invalide" in resp.json()["detail"]


def test_trade_add_zero_size_400():
    payload = _valid_long_payload() | {"size": 0}
    resp = TestClient(api.app).post("/api/trade/add", json=payload)
    assert resp.status_code == 400
    assert "Size" in resp.json()["detail"]


def test_trade_add_long_sl_above_entry_400():
    """LONG : SL doit être < Entry."""
    payload = _valid_long_payload() | {"stop_loss": 105.0}
    resp = TestClient(api.app).post("/api/trade/add", json=payload)
    assert resp.status_code == 400
    assert "SL" in resp.json()["detail"] and "LONG" in resp.json()["detail"]


def test_trade_add_long_tp_below_entry_400():
    """LONG : TP doit être > Entry."""
    payload = _valid_long_payload() | {"take_profit": 95.0}
    resp = TestClient(api.app).post("/api/trade/add", json=payload)
    assert resp.status_code == 400
    assert "TP" in resp.json()["detail"] and "LONG" in resp.json()["detail"]


def test_trade_add_short_sl_below_entry_400():
    """SHORT : SL doit être > Entry."""
    payload = {
        "ticker": "TSLA", "direction": "SHORT", "entry": 200.0,
        "stop_loss": 190.0, "take_profit": 180.0, "size": 5,
    }
    resp = TestClient(api.app).post("/api/trade/add", json=payload)
    assert resp.status_code == 400
    assert "SL" in resp.json()["detail"] and "SHORT" in resp.json()["detail"]


def test_trade_add_short_tp_above_entry_400():
    """SHORT : TP doit être < Entry."""
    payload = {
        "ticker": "TSLA", "direction": "SHORT", "entry": 200.0,
        "stop_loss": 210.0, "take_profit": 220.0, "size": 5,
    }
    resp = TestClient(api.app).post("/api/trade/add", json=payload)
    assert resp.status_code == 400
    assert "TP" in resp.json()["detail"] and "SHORT" in resp.json()["detail"]


# ─────────────────────────────────────────────────────────────────
# /api/trade/close
# ─────────────────────────────────────────────────────────────────

def test_trade_close_no_open_position_404(_bypass_auth_and_isolate_csv: Path):
    """Pas de position OPEN pour ce ticker → 404."""
    # CSV vide mais on doit quand-même la créer pour que pandas lise
    _bypass_auth_and_isolate_csv.write_text("Ticker,Status\n")
    resp = TestClient(api.app).post(
        "/api/trade/close",
        json={"ticker": "AAPL", "exit_price": 105.0, "result": "WIN"},
    )
    assert resp.status_code == 404
    assert "Aucune position OPEN" in resp.json()["detail"]


def test_trade_close_happy_path(_bypass_auth_and_isolate_csv: Path):
    """Ajoute un trade puis le clôture → status + exit_price persistés."""
    client = TestClient(api.app)
    client.post("/api/trade/add", json=_valid_long_payload())

    resp = client.post(
        "/api/trade/close",
        json={"ticker": "AAPL", "exit_price": 110.0, "result": "WIN"},
    )
    assert resp.status_code == 200
    df = pd.read_csv(_bypass_auth_and_isolate_csv, dtype=str)
    assert df.iloc[0]["Status"] == "WIN"
    assert float(df.iloc[0]["Exit_Price"]) == 110.0
    assert df.iloc[0]["Exit_Date"]  # non-vide
