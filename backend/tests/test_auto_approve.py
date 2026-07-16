"""Tests unitaires — modules/auto_approve.py

Couvre la règle d'achat manuelle (memory user_buy_rule_manual) :
    TITAN >= 80 (nominal)
    OU TITAN in [70, 80) + Support ON (>=90) + Piotroski F-score >= 7
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from modules.auto_approve import _qualifies, _recent_auto_approve_count


def test_qualifies_nominal_titan_80():
    ok, reason = _qualifies({"titan_score": 82.0})
    assert ok
    assert "nominal" in reason


def test_qualifies_exactly_80_is_nominal():
    ok, _ = _qualifies({"titan_score": 80.0})
    assert ok


def test_rejects_titan_below_70():
    ok, _ = _qualifies({"titan_score": 65.0, "support": {"level": "ON_SUPPORT", "score": 95}, "f_score": 9})
    assert not ok


def test_override_70_80_with_strong_support_and_piotroski():
    ok, reason = _qualifies({
        "titan_score": 75.0,
        "support": {"level": "ON_SUPPORT", "score": 92},
        "f_score": 7,
    })
    assert ok
    assert "override" in reason


def test_override_rejected_if_support_off():
    ok, _ = _qualifies({
        "titan_score": 75.0,
        "support": {"level": "OFF_SUPPORT", "score": 92},
        "f_score": 9,
    })
    assert not ok


def test_override_rejected_if_support_score_below_90():
    ok, _ = _qualifies({
        "titan_score": 75.0,
        "support": {"level": "ON_SUPPORT", "score": 85},
        "f_score": 9,
    })
    assert not ok


def test_override_rejected_if_piotroski_below_7():
    ok, _ = _qualifies({
        "titan_score": 75.0,
        "support": {"level": "ON_SUPPORT", "score": 95},
        "f_score": 6,
    })
    assert not ok


def test_missing_titan_score_rejected():
    ok, reason = _qualifies({})
    assert not ok
    assert "manquant" in reason


def test_recent_auto_approve_count_filters_by_decided_by_and_window(tmp_path, monkeypatch):
    audit_path = tmp_path / "proposals_audit.jsonl"
    now = datetime.now(UTC)
    records = [
        {"ts": (now - timedelta(days=1)).isoformat(), "event": "executed", "decided_by": "auto_approve_bot"},
        {"ts": (now - timedelta(days=10)).isoformat(), "event": "executed", "decided_by": "auto_approve_bot"},  # hors fenêtre
        {"ts": (now - timedelta(days=1)).isoformat(), "event": "executed", "decided_by": "user"},  # pas le bot
        {"ts": (now - timedelta(days=1)).isoformat(), "event": "rejected", "decided_by": "auto_approve_bot"},  # pas "executed"
        {"ts": (now - timedelta(hours=2)).isoformat(), "event": "executed", "decided_by": "auto_approve_bot"},
    ]
    audit_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    import modules.proposals as proposals
    monkeypatch.setattr(proposals, "PROPOSALS_AUDIT_PATH", audit_path)

    assert _recent_auto_approve_count(days=7.0) == 2
