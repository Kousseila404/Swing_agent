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


# ─────────────────────────────────────────────────────────────────
# Audit 2026-09-17 — tri par score, gate buy_signal, gate Risk, gate système
# ─────────────────────────────────────────────────────────────────

def _prop(ticker, titan, verdict="BUY", risk=70.0, **ctx_extra):
    ctx = {"titan_score": titan, "buy_signal": {"verdict": verdict}, "risk_score": risk}
    ctx.update(ctx_extra)
    return {"id": f"P-{ticker}", "ticker": ticker, "context": ctx}


def test_select_candidates_sorted_by_titan_desc():
    from modules.auto_approve import select_candidates
    pending = [_prop("LOW", 81.0), _prop("HIGH", 88.0), _prop("MID", 84.5)]
    got = [p["ticker"] for p, _ in select_candidates(pending, mode="legacy")]
    assert got == ["HIGH", "MID", "LOW"]


def test_buy_signal_gate_rejects_watch_and_skip():
    from modules.auto_approve import _qualifies
    assert _qualifies({"titan_score": 85.0, "buy_signal": {"verdict": "WATCH"}})[0] is False
    assert _qualifies({"titan_score": 85.0, "buy_signal": {"verdict": "FALLING_KNIFE"}})[0] is False
    assert _qualifies({"titan_score": 85.0, "buy_signal": {"verdict": "STRONG_BUY"}})[0] is True


def test_risk_gate_rejects_low_risk_pillar():
    """EIX (TITAN 72, Support ON 91, Piotroski 7) passait l'override avec Risk 26 → −23 %."""
    from modules.auto_approve import _qualifies
    ctx = {"titan_score": 72.1, "risk_score": 26.1, "f_score": 7,
           "support": {"level": "ON_SUPPORT", "score": 91.0}, "buy_signal": {"verdict": "BUY"}}
    ok, reason = _qualifies(ctx)
    assert ok is False and "Risk" in reason


def test_run_auto_approve_blocked_by_killswitch(monkeypatch):
    from modules import auto_approve
    monkeypatch.setattr("modules.tracker.killswitch.is_trading_allowed", lambda: False)
    called = []
    monkeypatch.setattr(auto_approve.proposals, "list_all", lambda status=None: called.append(1) or [])
    out = auto_approve.run_auto_approve()
    assert out["approved"] == 0 and "killswitch" in out["blocked"]
    assert called == []  # aucune lecture de la file quand les entrées sont gelées


# ─────────────────────────────────────────────────────────────────
# Mode basket (2026-09-17)
# ─────────────────────────────────────────────────────────────────

def test_basket_mode_ranks_and_filters():
    from modules.auto_approve import select_candidates
    pending = [_prop("A", 70.0, verdict="WATCH"), _prop("B", 85.0, verdict="BUY"),
               _prop("C", 60.0, verdict="FALLING_KNIFE"), _prop("D", 75.0, verdict="SKIP", risk=30.0),
               _prop("E", 72.0, verdict="WATCH")]
    ranks = {"A": 3, "B": 1, "C": 2, "D": 4, "E": 45}
    got = select_candidates(pending, ranks=ranks, mode="basket")
    assert [p["ticker"] for p, _ in got] == ["B", "A"]      # C : falling knife, D : Risk 30, E : rang 45
    assert "rang #1/" in got[0][1]


def test_basket_mode_unknown_rank_rejected():
    from modules.auto_approve import _qualifies_basket
    assert _qualifies_basket({"titan_score": 90.0}, None)[0] is False
    assert _qualifies_basket({"titan_score": 90.0}, 21)[0] is False
    assert _qualifies_basket({"titan_score": 65.0, "buy_signal": {"verdict": "WATCH"}}, 20)[0] is True
