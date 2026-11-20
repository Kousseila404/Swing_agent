"""Gardes de la chaîne de trading — audit 2026-11-18 §3 (P1-1, P1-3, P1-6)."""
from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

import config
from modules import auto_approve, basket_rebalance, basket_rotation


@pytest.fixture(autouse=True)
def _basket_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STRATEGY_MODE", "basket", raising=False)
    monkeypatch.setattr(basket_rebalance, "STATE_PATH", tmp_path / "rebal.json")
    monkeypatch.setattr(basket_rotation, "STATE_PATH", tmp_path / "rot.json", raising=False)


def _ks(raise_exc: bool = False, allowed: bool = True):
    def f() -> bool:
        if raise_exc:
            raise RuntimeError("state KO")
        return allowed
    return f


# ── P1-6 : killswitch illisible = fail-closed ────────────────────────────────

def test_auto_approve_refuses_when_killswitch_unreadable(monkeypatch):
    import modules.tracker.killswitch as ks
    monkeypatch.setattr(ks, "is_trading_allowed", _ks(raise_exc=True))
    ok, why = auto_approve._system_allows_entries()
    assert ok is False and "illisible" in why


def test_rebalance_skips_when_killswitch_unreadable(monkeypatch):
    import modules.tracker.killswitch as ks
    monkeypatch.setattr(ks, "is_trading_allowed", _ks(raise_exc=True))
    r = basket_rebalance.run_rebalance(force=True)
    assert r["skipped"] == "killswitch illisible"


def test_rotation_skips_when_killswitch_unreadable(monkeypatch):
    import modules.tracker.killswitch as ks
    monkeypatch.setattr(ks, "is_trading_allowed", _ks(raise_exc=True))
    assert basket_rotation.run_rotation()["skipped"] == "killswitch illisible"


# ── P1-1 : rebalance hors séance ne consomme pas de tentative ────────────────

class _Broker:
    def __init__(self, is_open: Any):
        self._open = is_open
        self.calls: list[tuple[str, int]] = []

    def market_status(self) -> dict:
        if isinstance(self._open, Exception):
            raise self._open
        return {"is_open": self._open}

    def adjust_position(self, ticker: str, delta: int) -> dict:
        self.calls.append((ticker, delta))
        return {"ok": True, "ticker": ticker}


@pytest.mark.parametrize("is_open,reason", [(False, "marché fermé"), (RuntimeError("x"), "horloge marché illisible")])
def test_rebalance_market_closed_does_not_burn_attempt(monkeypatch, is_open, reason):
    import modules.broker_gateway as bg
    import modules.tracker.killswitch as ks
    monkeypatch.setattr(ks, "is_trading_allowed", _ks())
    broker = _Broker(is_open)
    monkeypatch.setattr(bg, "get_broker", lambda: broker)
    monkeypatch.setattr(basket_rebalance, "preview", lambda: {"orders": [
        {"ticker": "AAA", "delta_qty": 3, "from_pct": 3.0, "to_pct": 5.0, "usd": 500}]})
    r = basket_rebalance.run_rebalance(force=True)
    assert r["skipped"] == reason
    assert broker.calls == []
    assert basket_rebalance._load_state().get("attempts") in (None, 0)


def test_rebalance_market_open_executes(monkeypatch):
    import modules.broker_gateway as bg
    import modules.tracker.killswitch as ks
    monkeypatch.setattr(ks, "is_trading_allowed", _ks())
    broker = _Broker(True)
    monkeypatch.setattr(bg, "get_broker", lambda: broker)
    monkeypatch.setattr(basket_rebalance, "preview", lambda: {"orders": [
        {"ticker": "AAA", "delta_qty": 3, "from_pct": 3.0, "to_pct": 5.0, "usd": 500}]})
    monkeypatch.setattr("modules.alerter._send_telegram_message", lambda *a, **k: None, raising=False)
    r = basket_rebalance.run_rebalance(force=True)
    assert broker.calls == [("AAA", 3)] and r["month_done"] is True


# ── P1-3 : univers partiel → rotation suspendue ──────────────────────────────

def test_coverage_ok_median_baseline():
    assert basket_rotation.coverage_ok([], 10) == (True, None)
    assert basket_rotation.coverage_ok([500, 505, 498], 460) == (True, 500)
    assert basket_rotation.coverage_ok([500, 505, 498], 300) == (False, 500)


def test_rotation_suspended_on_partial_universe(monkeypatch):
    import modules.sector_metrics as sm
    import modules.tracker.killswitch as ks
    monkeypatch.setattr(ks, "is_trading_allowed", _ks())
    saved: dict = {"streaks": {"AAA": 4}, "last_date": "2026-01-01", "universe_sizes": [500] * 10}
    monkeypatch.setattr(basket_rotation, "_load_state", lambda: dict(saved))
    writes: list = []
    monkeypatch.setattr(basket_rotation, "_save_state", lambda s: writes.append(s))
    monkeypatch.setattr(sm, "get_scored_universe", lambda: {f"T{i}": {} for i in range(200)})
    r = basket_rotation.run_rotation()
    assert r["skipped"] == "univers partiel" and r["baseline"] == 500
    assert writes == []  # séries intactes


def test_rotation_records_universe_size(monkeypatch):
    import modules.lt_exit_policy as lt
    import modules.sector_metrics as sm
    import modules.tracker.evaluation as ev
    import modules.tracker.killswitch as ks
    monkeypatch.setattr(ks, "is_trading_allowed", _ks())
    monkeypatch.setattr(basket_rotation, "_load_state", lambda: {"streaks": {}, "last_date": None})
    writes: list = []
    monkeypatch.setattr(basket_rotation, "_save_state", lambda s: writes.append(dict(s)))
    monkeypatch.setattr(sm, "get_scored_universe", lambda: {"AAA": {}, "BBB": {}})
    monkeypatch.setattr(lt, "compute_ranks", lambda scored: {"AAA": 1, "BBB": 2})
    monkeypatch.setattr(ev, "load_journal", lambda: pd.DataFrame({"Ticker": ["AAA"], "Status": ["OPEN"]}))
    r = basket_rotation.run_rotation(dry_run=True)
    assert r["to_exit"] == []
    assert writes[-1]["universe_sizes"] == [2]
