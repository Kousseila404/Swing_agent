"""Tests unitaires — modules/metrics_agent.py (Étape 0bis roadmap).

Couvre :
  • Append/lecture historique JSONL (round-trip, last_n).
  • run_agent — collecte + persistance + digest Telegram (fail-open réseau).
  • Tendance vs run précédent affichée dans le digest.
  • Proposition WFO markdown écrite seulement si IC significatif, jamais
    de modification de code, idempotente (pas de doublon).
  • alert=False / persist=False respectés.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import requests

from modules import metrics_agent


@pytest.fixture(autouse=True)
def _isolate_paths(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(metrics_agent, "METRICS_HISTORY_PATH",
                         tmp_path / "metrics_history_test.jsonl")
    monkeypatch.setattr(metrics_agent, "WFO_PROPOSAL_DIR",
                         tmp_path / "wfo_proposals")


@pytest.fixture
def captured_alerts(monkeypatch):
    sent: list[str] = []

    def _fake_send(text: str, *_a, **_kw) -> None:
        sent.append(text)

    import modules.alerter as alerter_mod
    monkeypatch.setattr(alerter_mod, "_send_telegram_message", _fake_send)
    return sent


@pytest.fixture
def stub_sources(monkeypatch):
    """Neutralise les I/O externes (HTTP data_health, journal trades, WFO,
    snapshots) avec des valeurs de contrôle par défaut, surchargeables."""
    state = {
        "health": {
            "severity_global": "ok",
            "universe": {"n_tickers": 400, "fetched_at": {"n_stale": 40, "n_severe": 5}},
            "sanitize": {"n_tickers_flagged": 10},
        },
        "trades": {"wins": 4, "losses": 4, "total_closed": 8},
        "wfo_history": [],
        "snapshots": list(range(87)),
    }

    monkeypatch.setattr(metrics_agent, "_data_health_snapshot",
                         lambda api_url: state["health"])
    monkeypatch.setattr(metrics_agent, "_trades_snapshot",
                         lambda: state["trades"])
    monkeypatch.setattr(metrics_agent.wfo_monitor, "_read_history",
                         lambda last_n=1: state["wfo_history"])
    monkeypatch.setattr(metrics_agent.universe_history, "list_snapshots",
                         lambda: state["snapshots"])
    return state


# ─────────────────────────────────────────────────────────────────
# Historique JSONL
# ─────────────────────────────────────────────────────────────────

def test_append_and_read_history_round_trip():
    entry = {"timestamp": "2026-07-19T00:00:00+00:00", "stale_ratio": 0.1}
    metrics_agent._append_history(entry)
    assert metrics_agent._read_history() == [entry]


def test_read_history_respects_last_n():
    for i in range(5):
        metrics_agent._append_history({"i": i})
    out = metrics_agent._read_history(last_n=3)
    assert [e["i"] for e in out] == [2, 3, 4]


def test_read_history_returns_empty_when_no_file():
    assert metrics_agent._read_history() == []


# ─────────────────────────────────────────────────────────────────
# run_agent — collecte nominale
# ─────────────────────────────────────────────────────────────────

def test_run_agent_persists_expected_fields(stub_sources, captured_alerts):
    entry = metrics_agent.run_agent()

    assert entry["severity_global"] == "ok"
    assert entry["n_tickers"] == 400
    assert entry["stale_ratio"] == pytest.approx(40 / 400)
    assert entry["n_severe_stale"] == 5
    assert entry["n_dq_sanitize"] == 10
    assert entry["n_snapshots"] == 87
    assert entry["trades"] == {"wins": 4, "losses": 4, "total_closed": 8}
    assert entry["wfo_proposal_written"] is None

    history = metrics_agent._read_history()
    assert len(history) == 1
    assert history[0]["stale_ratio"] == entry["stale_ratio"]

    assert len(captured_alerts) == 1
    text = captured_alerts[0]
    assert "10.0%" in text  # 40/400 staleness
    assert "8" in text and "4W/4L" in text
    assert "87/60" in text


def test_run_agent_failopen_when_data_health_unreachable(monkeypatch, stub_sources, captured_alerts):
    """L'API n'est pas up (cron avant démarrage, etc.) → pas de crash, entrée
    dégradée mais complète."""
    monkeypatch.setattr(metrics_agent, "_data_health_snapshot",
                         lambda api_url: {"error": "connection refused"})

    entry = metrics_agent.run_agent()
    assert entry["stale_ratio"] is None
    assert entry["n_tickers"] == 0
    assert len(captured_alerts) == 1
    assert "indisponible" in captured_alerts[0]


def test_data_health_snapshot_failopen_on_connection_error(monkeypatch):
    """Le vrai `_data_health_snapshot` ne lève jamais — fail-open avec un
    dict `{"error": ...}` si l'API locale est injoignable."""
    def _raise(*_a, **_kw):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(requests, "get", _raise)

    out = metrics_agent._data_health_snapshot("http://localhost:8000")
    assert "error" in out


def test_run_agent_alert_false_skips_telegram(stub_sources, captured_alerts):
    metrics_agent.run_agent(alert=False)
    assert captured_alerts == []


def test_run_agent_persist_false_skips_history(stub_sources, captured_alerts):
    metrics_agent.run_agent(persist=False)
    assert metrics_agent._read_history() == []


def test_run_agent_trend_shown_vs_previous_run(stub_sources, captured_alerts):
    metrics_agent.run_agent()  # baseline : stale_ratio 0.1, dq 10
    captured_alerts.clear()

    stub_sources["health"] = {
        "severity_global": "ok",
        "universe": {"n_tickers": 400, "fetched_at": {"n_stale": 80, "n_severe": 5}},
        "sanitize": {"n_tickers_flagged": 25},
    }
    metrics_agent.run_agent()

    assert len(captured_alerts) == 1
    assert "↗️" in captured_alerts[0]  # staleness ET dq_sanitize ont augmenté


# ─────────────────────────────────────────────────────────────────
# Proposition WFO — seulement si IC significatif, jamais de code touché
# ─────────────────────────────────────────────────────────────────

def test_no_wfo_proposal_when_no_history(stub_sources, captured_alerts):
    stub_sources["wfo_history"] = []
    entry = metrics_agent.run_agent()
    assert entry["wfo_proposal_written"] is None
    assert not metrics_agent.WFO_PROPOSAL_DIR.exists()


def test_no_wfo_proposal_when_ic_below_threshold(stub_sources, captured_alerts):
    stub_sources["wfo_history"] = [
        {"timestamp": "2026-07-19T06:00:00+00:00", "status": "ok",
         "n_folds": 3, "avg_ic_test": 0.03, "avg_weights": {}},
    ]
    entry = metrics_agent.run_agent()
    assert entry["wfo_proposal_written"] is None


def test_wfo_proposal_written_when_ic_significant(monkeypatch, stub_sources, captured_alerts):
    stub_sources["wfo_history"] = [
        {"timestamp": "2026-07-19T06:00:00+00:00", "status": "ok",
         "n_folds": 5, "avg_ic_test": 0.08,
         "avg_weights": {"quality_score": 0.3, "value_score": 0.7}},
    ]
    monkeypatch.setattr(metrics_agent.audit_summary, "_load_wfo", lambda: None)

    entry = metrics_agent.run_agent()

    assert entry["wfo_proposal_written"] is not None
    path = Path(entry["wfo_proposal_written"])
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "0.08" in content or "+0.0800" in content
    assert "quality_score" in content
    assert "aucun code de scoring" in content.lower()

    # Message de digest doit mentionner la proposition.
    assert "proposition de poids" in captured_alerts[0].lower()


def test_wfo_proposal_idempotent_no_duplicate(monkeypatch, stub_sources, captured_alerts):
    stub_sources["wfo_history"] = [
        {"timestamp": "2026-07-19T06:00:00+00:00", "status": "ok",
         "n_folds": 5, "avg_ic_test": 0.08, "avg_weights": {}},
    ]
    monkeypatch.setattr(metrics_agent.audit_summary, "_load_wfo", lambda: None)

    first = metrics_agent.run_agent()
    files_after_first = list(metrics_agent.WFO_PROPOSAL_DIR.glob("*.md"))
    assert len(files_after_first) == 1

    second = metrics_agent.run_agent()
    files_after_second = list(metrics_agent.WFO_PROPOSAL_DIR.glob("*.md"))
    assert len(files_after_second) == 1  # pas de doublon pour le même run WFO
    assert second["wfo_proposal_written"] is None  # déjà écrit, rien de neuf


# ─────────────────────────────────────────────────────────────────
# Fail-open Telegram
# ─────────────────────────────────────────────────────────────────

def test_run_agent_failopen_when_telegram_raises(monkeypatch, stub_sources):
    import modules.alerter as alerter_mod

    def _explode(*_a, **_kw):
        raise RuntimeError("Telegram offline")
    monkeypatch.setattr(alerter_mod, "_send_telegram_message", _explode)

    entry = metrics_agent.run_agent()
    assert entry["n_tickers"] == 400  # le run se termine normalement
