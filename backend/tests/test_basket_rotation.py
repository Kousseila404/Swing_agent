"""Tests — modules/basket_rotation.py (logique pure)."""
from __future__ import annotations

from modules import basket_rotation as br


def test_update_streaks_counts_consecutive_days_and_resets():
    st = {"streaks": {}, "last_date": None}
    st = br.update_streaks(st, ["A", "B"], {"A": 10, "B": 55}, exit_rank=40, today="2026-09-17")
    assert st["streaks"] == {"A": 0, "B": 1}
    # même jour : pas de double comptage
    assert br.update_streaks(st, ["A", "B"], {"A": 10, "B": 55}, 40, "2026-09-17")["streaks"] == {"A": 0, "B": 1}
    st = br.update_streaks(st, ["A", "B"], {"A": 50, "B": 60}, 40, "2026-09-18")
    assert st["streaks"] == {"A": 1, "B": 2}
    st = br.update_streaks(st, ["A", "B"], {"A": 5, "B": 60}, 40, "2026-09-19")
    assert st["streaks"] == {"A": 0, "B": 3}   # A revenu dans le panier → reset


def test_update_streaks_unknown_rank_counts_as_out_and_drops_closed():
    st = br.update_streaks({"streaks": {"GONE": 4}, "last_date": None}, ["X"], {}, 40, "2026-09-17")
    assert st["streaks"] == {"X": 1}


def test_plan_exits_respects_confirm_exempt_and_cap():
    st = {"streaks": {"A": 5, "B": 7, "C": 2, "D": 6, "E": 9}}
    assert br.plan_exits(st, confirm_days=5, exempt={"E"}, max_exits=2) == ["B", "D"]
    assert br.plan_exits(st, confirm_days=10) == []


def test_run_rotation_skips_when_not_basket(monkeypatch):
    import config
    monkeypatch.setattr(config, "STRATEGY_MODE", "legacy", raising=False)
    assert br.run_rotation(dry_run=True) == {"skipped": "STRATEGY_MODE != basket"}
