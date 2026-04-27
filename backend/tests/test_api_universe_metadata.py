"""Tests des métadonnées /api/universe (W8) — fraîcheur + is_incomplete."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import api
from modules import api_core


def _write_universe(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _fresh_iso(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def test_fresh_universe_is_not_stale(monkeypatch, tmp_path: Path):
    path = tmp_path / "universe.json"
    _write_universe(path, {
        "version": 1,
        "updated_at": _fresh_iso(days_ago=0.1),
        "tickers": {
            "AAPL": {"market_cap": 3e12, "sector": "Technology"},
            "MSFT": {"market_cap": 3.2e12, "sector": "Technology"},
        },
    })
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", path)

    client = TestClient(api.app)
    resp = client.get("/api/universe")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_stale"] is False
    assert body["stale_days"] is not None and body["stale_days"] < 1
    assert body["last_updated_timestamp"] is not None
    assert body["staleness_threshold_days"] == 7
    assert body["incomplete_count"] == 0
    assert body["incomplete_ratio"] == 0.0


def test_stale_universe_flagged(monkeypatch, tmp_path: Path):
    path = tmp_path / "universe.json"
    _write_universe(path, {
        "updated_at": _fresh_iso(days_ago=15),
        "tickers": {"AAPL": {"market_cap": 3e12, "sector": "Technology"}},
    })
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", path)

    resp = TestClient(api.app).get("/api/universe")
    body = resp.json()
    assert body["is_stale"] is True
    assert body["stale_days"] > 7


def test_incomplete_ticker_flagged(monkeypatch, tmp_path: Path):
    path = tmp_path / "universe.json"
    _write_universe(path, {
        "updated_at": _fresh_iso(),
        "tickers": {
            "AAPL":   {"market_cap": 3e12, "sector": "Technology"},
            "GHOST":  {"market_cap": None, "sector": "Technology"},
            "ORPHAN": {"market_cap": 1e10, "sector": None},
        },
    })
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", path)

    resp = TestClient(api.app).get("/api/universe")
    body = resp.json()
    tickers = body["tickers"]
    assert tickers["AAPL"]["is_incomplete"] is False
    assert tickers["GHOST"]["is_incomplete"] is True
    assert tickers["ORPHAN"]["is_incomplete"] is True
    assert body["incomplete_count"] == 2
    assert body["incomplete_ratio"] == round(2 / 3, 4)


def test_missing_updated_at_is_not_flagged_stale(monkeypatch, tmp_path: Path):
    """Un rebuild sans timestamp ne doit PAS remonter is_stale=True (évite faux positifs)."""
    path = tmp_path / "universe.json"
    _write_universe(path, {"tickers": {"AAPL": {"market_cap": 3e12, "sector": "Tech"}}})
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", path)

    resp = TestClient(api.app).get("/api/universe")
    body = resp.json()
    assert body["is_stale"] is False
    assert body["stale_days"] is None
    assert body["last_updated_timestamp"] is None


def test_corrupted_universe_returns_503(monkeypatch, tmp_path: Path):
    path = tmp_path / "universe.json"
    path.write_text("{this is not json")
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", path)

    client = TestClient(api.app, raise_server_exceptions=False)
    resp = client.get("/api/universe")
    assert resp.status_code == 503
    assert resp.json()["error"] == "corrupted_cache"
