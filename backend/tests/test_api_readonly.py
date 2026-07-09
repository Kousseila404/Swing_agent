"""Tests d'intégration des endpoints GET read-only — contrat de forme.

Vérifie que chaque endpoint retourne une réponse valide (schema + keys
critiques) y compris quand les caches sont absents / vides (fail-open).
On monkey-patche les paths sur des tmp_path pour éviter de polluer
data/ (les fixtures prod tournent indépendamment des tests).
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import api
from modules import api_core


_TEST_TOKEN = "test-readonly-token"
_AUTH_HEADERS = {"Authorization": f"Bearer {_TEST_TOKEN}"}


def _redirect_paths_to_tmp(monkeypatch, tmp_path: Path) -> None:
    """Redirige tous les caches JSON critiques vers tmp_path (état vide)."""
    monkeypatch.setattr(api_core, "API_TOKEN", _TEST_TOKEN)
    for attr, name in [
        ("EQUITY_PATH", "equity_state.json"),
        ("TRADING_PATH", "trading_state.json"),
        ("MACRO_PATH", "macro_state.json"),
        ("MACRO_CALENDAR_PATH", "macro_calendar.json"),
        ("CSV_PATH", "trade_journal.csv"),
        ("CSV_LOCK_PATH", "trade_journal.csv.lock"),
        ("UNIVERSE_QUANTAMENTAL_PATH", "universe.json"),
        ("TRACKER_HEARTBEAT_PATH", "tracker_heartbeat.json"),
        ("TRACKER_PID_PATH", "tracker.pid"),
    ]:
        monkeypatch.setattr(api_core, attr, tmp_path / name)

    # Redirige aussi les paths utilisés par modules.duckdb_journal (lecture du
    # journal via DuckDB + fallback CSV) pour isoler /performance_metrics.
    # Les defaults de read_journal_df sont gelés à la def — on les remplace
    # directement via __defaults__ pour que l'appel sans args pointe vers tmp.
    from modules import duckdb_journal, utils
    tmp_csv = tmp_path / "trade_journal.csv"
    tmp_db = tmp_path / "trade_journal.duckdb"
    monkeypatch.setattr(duckdb_journal, "CSV_PATH", tmp_csv)
    monkeypatch.setattr(duckdb_journal, "DUCKDB_PATH", tmp_db)
    monkeypatch.setattr(utils, "CSV_PATH", tmp_csv)
    monkeypatch.setattr(duckdb_journal.read_journal_df, "__defaults__", (tmp_csv, tmp_db))


# ─────────────────────────────────────────────────────────────────
# /api/status
# ─────────────────────────────────────────────────────────────────

def test_status_returns_200_with_defaults(monkeypatch, tmp_path: Path):
    """Caches absents → /status retourne INITIAL_CAPITAL + 0 positions + BEAR."""
    _redirect_paths_to_tmp(monkeypatch, tmp_path)
    resp = TestClient(api.app).get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["open_positions"] == 0
    assert body["trading_blocked"] is False
    assert body["account_equity"] == api_core.INITIAL_CAPITAL
    assert body["regime"] == "UNKNOWN"


def test_status_with_equity_and_macro(monkeypatch, tmp_path: Path):
    """Equity custom + macro BULL → /status remonte correctement les valeurs."""
    _redirect_paths_to_tmp(monkeypatch, tmp_path)
    (tmp_path / "equity_state.json").write_text(json.dumps({
        "starting_equity": 100_000, "current_equity": 98_500,
        "open_positions": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
    }))
    (tmp_path / "macro_state.json").write_text(json.dumps({
        "confirmed_regime": "BULL_MARKET", "vix": 17.5,
    }))

    body = TestClient(api.app).get("/api/status").json()
    assert body["open_positions"] == 2
    assert body["account_equity"] == 98500.0
    assert body["regime"] == "BULL_MARKET"
    assert body["vix"] == 17.5
    # 1.5% drawdown from 100k → 98.5k
    assert body["daily_drawdown_pct"] == 1.5


def test_status_blocked_when_killswitch_active(monkeypatch, tmp_path: Path):
    _redirect_paths_to_tmp(monkeypatch, tmp_path)
    (tmp_path / "trading_state.json").write_text(json.dumps({"blocked": True}))
    body = TestClient(api.app).get("/api/status").json()
    assert body["trading_blocked"] is True
    assert body["nuclear_stop"] is True


# ─────────────────────────────────────────────────────────────────
# /api/macro
# ─────────────────────────────────────────────────────────────────

def test_macro_fallback_when_file_missing(monkeypatch, tmp_path: Path):
    _redirect_paths_to_tmp(monkeypatch, tmp_path)
    body = TestClient(api.app).get("/api/macro").json()
    assert body["regime"] == "UNKNOWN"
    assert "introuvable" in body["error"]


def test_macro_happy_path(monkeypatch, tmp_path: Path):
    _redirect_paths_to_tmp(monkeypatch, tmp_path)
    (tmp_path / "macro_state.json").write_text(json.dumps({
        "confirmed_regime": "BULL_MARKET",
        "vix": 18.2,
        "sp500": 5600.0,
        "ema200": 5400.0,
        "allowed_directions": ["LONG"],
    }))
    body = TestClient(api.app).get("/api/macro").json()
    assert body["regime"] == "BULL_MARKET"
    assert body["vix"] == 18.2
    assert body["sp500"] == 5600.0


# ─────────────────────────────────────────────────────────────────
# /api/portfolio
# ─────────────────────────────────────────────────────────────────

def test_portfolio_empty_journal(monkeypatch, tmp_path: Path):
    _redirect_paths_to_tmp(monkeypatch, tmp_path)
    body = TestClient(api.app).get("/api/portfolio", headers=_AUTH_HEADERS).json()
    assert body["journal"] == []
    assert body["open_positions"] == []
    assert body["closed_trades"] == []
    assert body["stats"]["total_trades"] == 0
    assert body["stats"]["win_rate"] == 0


# ─────────────────────────────────────────────────────────────────
# /api/equity_curve
# ─────────────────────────────────────────────────────────────────

def test_equity_curve_empty_journal(monkeypatch, tmp_path: Path):
    _redirect_paths_to_tmp(monkeypatch, tmp_path)
    body = TestClient(api.app).get("/api/equity_curve", headers=_AUTH_HEADERS).json()
    assert body["curve"] == []
    assert body["initial_equity"] == api_core.INITIAL_CAPITAL
    assert body["final_equity"] == api_core.INITIAL_CAPITAL


# ─────────────────────────────────────────────────────────────────
# /api/performance_metrics
# ─────────────────────────────────────────────────────────────────

def test_performance_metrics_empty_journal(monkeypatch, tmp_path: Path):
    _redirect_paths_to_tmp(monkeypatch, tmp_path)
    body = TestClient(api.app).get("/api/performance_metrics", headers=_AUTH_HEADERS).json()
    assert body["total_closed"] == 0
    assert body["wins"] == 0
    assert body["losses"] == 0
    assert body["capital"] == api_core.INITIAL_CAPITAL


# ─────────────────────────────────────────────────────────────────
# /api/macro_calendar
# ─────────────────────────────────────────────────────────────────

def test_macro_calendar_empty(monkeypatch, tmp_path: Path):
    _redirect_paths_to_tmp(monkeypatch, tmp_path)
    body = TestClient(api.app).get("/api/macro_calendar").json()
    assert body["events"] == []
    assert body["in_blackout"] is False
    assert body["next_event"] is None


def test_macro_calendar_with_upcoming_fomc(monkeypatch, tmp_path: Path):
    from datetime import date, timedelta
    _redirect_paths_to_tmp(monkeypatch, tmp_path)
    future = (date.today() + timedelta(days=3)).isoformat()
    (tmp_path / "macro_calendar.json").write_text(json.dumps({
        "events": [{"date": future, "type": "FOMC", "label": "FOMC Meeting"}],
    }))
    body = TestClient(api.app).get("/api/macro_calendar").json()
    assert len(body["events"]) == 1
    assert body["events"][0]["type"] == "FOMC"
    assert body["events"][0]["upcoming"] is True


# ─────────────────────────────────────────────────────────────────
# /api/jobs/{id} — endpoint conservé pour le polling UniverseManagerPage.
# Les autres endpoints jobs (liste) + /logs, /live_feed, /score_universe
# ont été retirés avec la purge ControlPanel (2026-04-24).
# ─────────────────────────────────────────────────────────────────

def test_jobs_get_nonexistent_returns_404(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(api_core, "JOBS_DIR", tmp_path / ".jobs")
    resp = TestClient(api.app).get("/api/jobs/deadbeef")
    assert resp.status_code == 404
