"""Tests sentinel (audit 2026-11-18) — invariants de résultat."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from modules import sentinel as s


def _report(*checks):
    return s.Report(ts="t", status=max((c.status for c in checks), key=s._SEVERITY_RANK.__getitem__),
                    checks=list(checks))


def test_proposals_flow_crit_when_dead_for_weeks(monkeypatch, tmp_path):
    monkeypatch.setattr(s, "DATA", tmp_path)
    now = datetime(2026, 11, 18, 12, tzinfo=UTC)
    (tmp_path / "proposals.json").write_text(json.dumps([{"created_at": "2026-09-16T06:00:00Z"}]))
    (tmp_path / "proposals_last_refresh.json").write_text(json.dumps({"ok": False, "gates": [{"name": "x", "ok": True}]}))
    c = s.check_proposals_flow(now)
    assert c.status == s.CRIT
    assert c.value["business_days_since"] > 40


def test_proposals_flow_ok_when_recent(monkeypatch, tmp_path):
    monkeypatch.setattr(s, "DATA", tmp_path)
    now = datetime(2026, 11, 18, 12, tzinfo=UTC)
    recent = (now - timedelta(hours=6)).isoformat()
    (tmp_path / "proposals.json").write_text(json.dumps([{"created_at": recent}]))
    (tmp_path / "proposals_last_refresh.json").write_text(json.dumps({"ok": True, "gates": []}))
    assert s.check_proposals_flow(now).status == s.OK


def test_proposals_flow_closed_gate_is_not_a_crash(monkeypatch, tmp_path):
    """ok=False avec un gate fermé (régime, killswitch) = comportement voulu, pas CRIT."""
    monkeypatch.setattr(s, "DATA", tmp_path)
    now = datetime(2026, 11, 18, 12, tzinfo=UTC)
    (tmp_path / "proposals.json").write_text(json.dumps([{"created_at": (now - timedelta(days=1)).isoformat()}]))
    (tmp_path / "proposals_last_refresh.json").write_text(json.dumps({"ok": False, "gates": [{"name": "regime", "ok": False}]}))
    assert s.check_proposals_flow(now).status == s.OK


def test_should_alert_on_new_problem_then_dedupes():
    r = _report(s.Check("a", s.CRIT, "x"))
    assert s.should_alert(r, {}, 1000.0)
    state = {"fingerprint": r.fingerprint(), "status": s.CRIT, "alerted_at": 1000.0}
    assert not s.should_alert(r, state, 1000.0 + 3600)
    assert s.should_alert(r, state, 1000.0 + s.REALERT_SECONDS + 1)


def test_should_alert_on_recovery_only_once():
    green = _report(s.Check("a", s.OK, "x"))
    assert not s.should_alert(green, {}, 0.0)
    assert s.should_alert(green, {"fingerprint": "old", "status": s.CRIT}, 0.0)


def test_broken_check_does_not_hide_others(monkeypatch):
    def boom(*_a):
        raise RuntimeError("kaboom")
    for name in ("check_api_alive", "check_oom_kills", "check_host_resources"):
        monkeypatch.setattr(s, name, boom)
    r = s.run_checks()
    names = {c.name for c in r.checks}
    assert {"api_alive", "oom_kills", "host_resources", "proposals_flow"} <= names
