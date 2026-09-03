"""Tests — scripts/publish_thesis_review_context.py.

build_context/render_body sont des fonctions pures (aucun accès réseau) —
testées sans mock GitHub. `publish()` (réseau) est testé séparément avec
`requests.patch` mocké.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from modules import my_portfolio_thesis as mpt
from modules import thesis_review_queue as trq
from scripts import publish_thesis_review_context as publish_mod


@pytest.fixture(autouse=True)
def _isolate_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(mpt, "STORE_PATH", tmp_path / "my_portfolio_thesis.json")
    monkeypatch.setattr(mpt, "_LOCK_PATH", tmp_path / "my_portfolio_thesis.json.lock")
    monkeypatch.setattr(trq, "STORE_PATH", tmp_path / "thesis_review_queue.json")
    monkeypatch.setattr(trq, "_LOCK_PATH", tmp_path / "thesis_review_queue.json.lock")


def test_build_context_empty_state():
    ctx = publish_mod.build_context()
    assert ctx["ticker"] == "BNP.PA"
    assert ctx["verification"]["derniere_verification"] is None
    assert ctx["recent_review_queue"] == []
    assert "published_at" in ctx


def test_build_context_reflects_verification():
    mpt.update_thesis("BNP.PA", verification={"derniere_verification": "2026-08-01", "verdict": "thèse intacte"})
    ctx = publish_mod.build_context()
    assert ctx["verification"]["derniere_verification"] == "2026-08-01"
    assert ctx["verification"]["verdict"] == "thèse intacte"


def test_build_context_last_three_queue_entries_only():
    for i in range(5):
        trq.add_entry("BNP.PA", execution_type="light", findings=[], run_date=f"2026-0{i + 1}-01")
    ctx = publish_mod.build_context()
    assert len(ctx["recent_review_queue"]) == 3
    assert [e["date"] for e in ctx["recent_review_queue"]] == ["2026-03-01", "2026-04-01", "2026-05-01"]


def test_render_body_contains_markers_and_valid_json():
    import json
    ctx = publish_mod.build_context()
    body = publish_mod.render_body(ctx)
    assert publish_mod._MARKER_START in body
    assert publish_mod._MARKER_END in body
    start = body.index("```json\n") + len("```json\n")
    end = body.index("\n```", start)
    parsed = json.loads(body[start:end])
    assert parsed == ctx


def test_publish_sends_patch_with_rendered_body(monkeypatch):
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

    def _fake_patch(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse()

    monkeypatch.setattr(publish_mod.requests, "patch", _fake_patch)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    result = publish_mod.publish()

    assert captured["url"] == f"https://api.github.com/repos/{publish_mod.REPO}/issues/{publish_mod.ISSUE_NUMBER}"
    assert captured["headers"]["Authorization"] == "Bearer fake-token"
    assert publish_mod._MARKER_START in captured["json"]["body"]
    assert result["ticker"] == "BNP.PA"


def test_publish_raises_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        publish_mod.publish()
