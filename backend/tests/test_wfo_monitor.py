"""Tests unitaires — modules/wfo_monitor.py

Couvre :
  • Append historique JSONL + lecture last_n.
  • Détection dégradation (2 runs consécutifs sous seuil).
  • Pas d'alerte si moins de 2 ok runs.
  • Fail-open Telegram (alerte échoue → run réussit).
  • Statut "skipped" quand WFO lève (historique trop court).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from modules import wfo_monitor


@pytest.fixture(autouse=True)
def _isolate_history(monkeypatch, tmp_path: Path):
    """Redirige le chemin du JSONL d'historique vers tmp_path."""
    target = tmp_path / "wfo_history_test.jsonl"
    monkeypatch.setattr(wfo_monitor, "WFO_HISTORY_PATH", target)
    return target


@pytest.fixture
def captured_alerts(monkeypatch):
    """Intercepte les appels Telegram du monitor (qui import depuis alerter
    via `from modules.alerter import _send_telegram_message`)."""
    sent: list[str] = []

    def _fake_send(text: str, *_a, **_kw) -> None:
        sent.append(text)

    import modules.alerter as alerter_mod
    monkeypatch.setattr(alerter_mod, "_send_telegram_message", _fake_send)
    return sent


@pytest.fixture
def patch_wfo(monkeypatch):
    """Helper pour mocker `run_walk_forward` avec une `WfoResult` fabriquée.

    Retourne une fonction qui prend `avg_ic_test` et configure le mock.
    """
    def _set(avg_ic_test: float, n_folds: int = 1, raise_value_error: bool = False):
        if raise_value_error:
            def _fake(*a, **k):
                raise ValueError("historique trop court")
            monkeypatch.setattr(wfo_monitor.wfo_calibration,
                                "run_walk_forward", _fake)
            return

        def _fake(*_a, **_kw):
            from modules.wfo_calibration import WfoResult
            return WfoResult(
                n_folds=n_folds,
                pillars=list(wfo_monitor.wfo_calibration.PILLARS),
                folds=[],
                avg_weights={p: 1.0 / len(wfo_monitor.wfo_calibration.PILLARS)
                             for p in wfo_monitor.wfo_calibration.PILLARS},
                avg_ic_test=avg_ic_test,
                diagnostics={"n_snapshots": 50, "n_pairs": 30,
                             "n_skipped_lag": 0, "train_days": 60,
                             "test_days": 20, "publication_lag_days": 90,
                             "grid_step": 0.05,
                             "date_range": {"start": "2026-01-01", "end": "2026-04-01"}},
            )
        monkeypatch.setattr(wfo_monitor.wfo_calibration,
                            "run_walk_forward", _fake)
    return _set


# ─────────────────────────────────────────────────────────────────
# History append + read
# ─────────────────────────────────────────────────────────────────

def test_append_and_read_history_round_trip():
    """Un append produit une ligne lisible par `_read_history`."""
    entry = {"timestamp": "2026-04-27T00:00:00+00:00",
             "status": "ok", "avg_ic_test": 0.05}
    wfo_monitor._append_history(entry)
    out = wfo_monitor._read_history()
    assert out == [entry]


def test_read_history_respects_last_n():
    """`last_n` doit retourner les N dernières lignes seulement."""
    for i in range(5):
        wfo_monitor._append_history({"i": i, "status": "ok"})
    out = wfo_monitor._read_history(last_n=3)
    assert [e["i"] for e in out] == [2, 3, 4]


def test_read_history_returns_empty_when_no_file():
    """Pas de fichier → liste vide (pas une exception)."""
    assert wfo_monitor._read_history() == []


# ─────────────────────────────────────────────────────────────────
# run_monitor — chemins ok / skipped
# ─────────────────────────────────────────────────────────────────

def test_run_monitor_status_skipped_when_wfo_raises(patch_wfo, captured_alerts):
    """ValueError dans run_walk_forward (historique court) → status=skipped,
    pas d'alerte (ce n'est pas une dégradation, juste une absence de data)."""
    patch_wfo(0.0, raise_value_error=True)

    summary = wfo_monitor.run_monitor(persist=False)
    assert summary["status"] == "skipped"
    assert "historique" in summary["reason"].lower()
    assert captured_alerts == []

    # L'entrée doit quand même être appendée à l'historique pour audit.
    history = wfo_monitor._read_history()
    assert len(history) == 1
    assert history[0]["status"] == "skipped"


def test_run_monitor_writes_history_with_avg_ic_test(patch_wfo, captured_alerts):
    """Run nominal → entry status=ok avec avg_ic_test rempli."""
    patch_wfo(0.05)
    summary = wfo_monitor.run_monitor(persist=False)

    assert summary["status"] == "ok"
    assert summary["avg_ic_test"] == 0.05
    assert summary["degraded"] is False  # 0.05 > seuil 0.02

    history = wfo_monitor._read_history()
    assert len(history) == 1
    assert history[0]["avg_ic_test"] == 0.05


# ─────────────────────────────────────────────────────────────────
# Détection dégradation
# ─────────────────────────────────────────────────────────────────

def test_no_alert_with_single_low_run(patch_wfo, captured_alerts):
    """1 seul run sous seuil → pas d'alerte (besoin de 2 consécutifs)."""
    patch_wfo(0.01)  # < 0.02
    summary = wfo_monitor.run_monitor(persist=False)
    assert summary["degraded"] is False
    assert captured_alerts == []


def test_alert_fires_on_two_consecutive_low_runs(patch_wfo, captured_alerts):
    """2 runs sous seuil consécutifs → alerte Telegram."""
    patch_wfo(0.01)
    wfo_monitor.run_monitor(persist=False)
    summary2 = wfo_monitor.run_monitor(persist=False)
    assert summary2["degraded"] is True
    assert len(captured_alerts) == 1
    assert "Dégradation" in captured_alerts[0]
    assert "0.01" in captured_alerts[0] or "+0.0100" in captured_alerts[0]


def test_no_alert_when_recovery(patch_wfo, captured_alerts):
    """Run faible suivi d'un bon run → pas d'alerte (pas de dégradation persistante)."""
    patch_wfo(0.01)
    wfo_monitor.run_monitor(persist=False)
    patch_wfo(0.08)  # bon run
    summary = wfo_monitor.run_monitor(persist=False)
    assert summary["degraded"] is False
    assert captured_alerts == []


def test_skipped_runs_dont_break_degradation_detection(patch_wfo, captured_alerts):
    """`status=skipped` ne compte pas dans la fenêtre 2-runs : seuls les
    runs ok sont considérés (sinon l'historique court ferait du faux alarme)."""
    patch_wfo(0.01)
    wfo_monitor.run_monitor(persist=False)
    patch_wfo(0.0, raise_value_error=True)  # skip
    wfo_monitor.run_monitor(persist=False)
    captured_alerts.clear()
    patch_wfo(0.01)
    summary = wfo_monitor.run_monitor(persist=False)
    # Les 2 runs ok sont sous seuil → dégradation attendue.
    assert summary["degraded"] is True
    assert len(captured_alerts) == 1


def test_alert_skipped_when_alert_false(patch_wfo, captured_alerts):
    """`alert=False` court-circuite Telegram même sur dégradation détectée."""
    patch_wfo(0.01)
    wfo_monitor.run_monitor(persist=False)
    summary = wfo_monitor.run_monitor(persist=False, alert=False)
    assert summary["degraded"] is True
    assert captured_alerts == [], "alert=False doit bloquer l'envoi"


# ─────────────────────────────────────────────────────────────────
# Fail-open
# ─────────────────────────────────────────────────────────────────

def test_run_monitor_failopen_when_telegram_raises(patch_wfo, monkeypatch):
    """Si l'envoi Telegram lève, le run doit quand même retourner un
    summary avec status=ok (pas de RuntimeError remontant)."""
    patch_wfo(0.01)
    wfo_monitor.run_monitor(persist=False)

    import modules.alerter as alerter_mod

    def _explode(*_a, **_kw):
        raise RuntimeError("Telegram offline")
    monkeypatch.setattr(alerter_mod, "_send_telegram_message", _explode)

    summary = wfo_monitor.run_monitor(persist=False)
    # Doit avoir détecté dégradation, tenté l'alerte, mais ne pas crash.
    assert summary["status"] == "ok"
    assert summary["degraded"] is True
