"""Tests smoke — décomposition tracker.py en modules/tracker/ (Lot 4).

Vérifie que :
  - Tous les sous-modules s'importent sans erreur circulaire.
  - run_cycle() sur un CSV vide : pas de crash, heartbeat="idle" écrit.
  - run_cycle() quand trading bloqué : heartbeat="blocked".
  - tracker.py expose toujours le CLI entry point (main, run_cycle).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import tracker
from modules.tracker import cycle, evaluation, killswitch, market, state
from modules.utils import CSV_SCHEMA


def _isolate_tracker_state(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Redirige tous les paths persistants tracker vers tmp_path."""
    csv = tmp_path / "trade_journal.csv"
    heartbeat = tmp_path / "tracker_heartbeat.json"
    equity = tmp_path / "equity_state.json"
    trading = tmp_path / "trading_state.json"
    cb_state = tmp_path / "circuit_breaker_state.json"
    cooldown = tmp_path / "alert_cooldown.json"

    # Redirige state.py paths
    monkeypatch.setattr(state, "EQUITY_STATE_PATH", equity)
    monkeypatch.setattr(state, "TRADING_STATE_PATH", trading)
    monkeypatch.setattr(state, "CB_STATE_PATH", cb_state)
    monkeypatch.setattr(state, "HEARTBEAT_PATH", heartbeat)
    monkeypatch.setattr(state, "ALERT_COOLDOWN_PATH", cooldown)

    # Les submodules importent les CONSTANTES — monkey-patche aussi leurs refs
    monkeypatch.setattr(killswitch, "EQUITY_STATE_PATH", equity)
    monkeypatch.setattr(killswitch, "TRADING_STATE_PATH", trading)
    monkeypatch.setattr(evaluation, "ALERT_COOLDOWN_PATH", cooldown)

    # Redirige CSV_PATH + CSV_LOCK_PATH
    from modules import utils as _utils
    monkeypatch.setattr(_utils, "CSV_PATH", csv)
    monkeypatch.setattr(_utils, "CSV_LOCK_PATH", tmp_path / "trade_journal.csv.lock")
    monkeypatch.setattr(evaluation, "CSV_PATH", csv)
    monkeypatch.setattr(evaluation, "CSV_LOCK_PATH", tmp_path / "trade_journal.csv.lock")

    # Reset le singleton CB (le test précédent a pu le laisser initialisé)
    state.set_circuit_breaker(None)
    return csv, heartbeat, equity, trading


def test_tracker_package_imports():
    """Tous les sous-modules s'importent sans erreur."""
    assert callable(cycle.run_cycle)
    assert callable(killswitch.is_trading_allowed)
    assert callable(evaluation.evaluate_trades)
    assert callable(market.get_current_price)
    assert state.logger.name == "Tracker"


def test_tracker_py_exposes_cli():
    """tracker.py reste le point d'entrée CLI (run_cycle + main)."""
    assert callable(tracker.run_cycle)
    assert callable(tracker.main)
    # LOOP_INTERVAL exposé pour aider les tests/scripts externes
    assert tracker.LOOP_INTERVAL == 300


def test_run_cycle_empty_journal_writes_idle_heartbeat(monkeypatch, tmp_path: Path):
    """CSV vide → heartbeat=idle, pas de crash."""
    csv, heartbeat, _, _ = _isolate_tracker_state(monkeypatch, tmp_path)
    pd.DataFrame(columns=CSV_SCHEMA).to_csv(csv, index=False)

    cycle.run_cycle()

    assert heartbeat.exists()
    hb = json.loads(heartbeat.read_text())
    assert hb["status"] == "idle"


def test_run_cycle_blocked_trading_writes_blocked_heartbeat(monkeypatch, tmp_path: Path):
    """trading_state.json avec blocked=True (date du jour) → heartbeat=blocked, early return."""
    csv, heartbeat, _, trading = _isolate_tracker_state(monkeypatch, tmp_path)
    pd.DataFrame(columns=CSV_SCHEMA).to_csv(csv, index=False)
    trading.write_text(json.dumps({
        "blocked": True, "date": date.today().isoformat(),
    }))

    cycle.run_cycle()

    hb = json.loads(heartbeat.read_text())
    assert hb["status"] == "blocked"


def test_backward_compat_read_circuit_breaker_state(monkeypatch, tmp_path: Path):
    """L'API publique read_circuit_breaker_state est toujours exposée et fail-open."""
    _isolate_tracker_state(monkeypatch, tmp_path)
    result = state.read_circuit_breaker_state()
    assert result == {"is_paused": False, "size_multiplier": 1.0, "peak_equity": 0.0}
