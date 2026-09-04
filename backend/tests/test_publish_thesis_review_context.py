"""Tests — scripts/publish_thesis_review_context.py.

build_context/render_body sont des fonctions pures (aucun accès réseau) —
testées sans mock GitHub. `publish()` (réseau) est testé séparément avec
`requests.patch` mocké.

Contexte multi-ticker (généralisé 2026-09-04, historiquement BNP.PA seul) :
`build_context()` couvre désormais toutes les positions de
`my_portfolio_data.POSITIONS`, chacune avec sa propre `verification`,
`recent_review_queue` et `reference_metrics`.
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
from modules.my_portfolio_data import POSITIONS
from scripts import publish_thesis_review_context as publish_mod


@pytest.fixture(autouse=True)
def _isolate_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(mpt, "STORE_PATH", tmp_path / "my_portfolio_thesis.json")
    monkeypatch.setattr(mpt, "_LOCK_PATH", tmp_path / "my_portfolio_thesis.json.lock")
    monkeypatch.setattr(trq, "STORE_PATH", tmp_path / "thesis_review_queue.json")
    monkeypatch.setattr(trq, "_LOCK_PATH", tmp_path / "thesis_review_queue.json.lock")


def test_tickers_matches_positions():
    assert publish_mod.TICKERS == [p["ticker"] for p in POSITIONS]


def test_build_context_covers_every_position():
    ctx = publish_mod.build_context()
    assert set(ctx["tickers"].keys()) == {p["ticker"] for p in POSITIONS}
    assert "published_at" in ctx


def test_build_context_empty_state_per_ticker():
    ctx = publish_mod.build_context()
    fmx = ctx["tickers"]["FMX"]
    assert fmx["verification"]["derniere_verification"] is None
    assert fmx["recent_review_queue"] == []
    assert isinstance(fmx["reference_metrics"], list)


def test_build_context_includes_reference_metrics():
    ctx = publish_mod.build_context()
    bnp_metrics = ctx["tickers"]["BNP.PA"]["reference_metrics"]
    assert any(m["label"] == "Ratio CET1" for m in bnp_metrics)


def test_build_context_reflects_verification():
    mpt.update_thesis("BNP.PA", verification={"derniere_verification": "2026-08-01", "verdict": "thèse intacte"})
    ctx = publish_mod.build_context()
    assert ctx["tickers"]["BNP.PA"]["verification"]["derniere_verification"] == "2026-08-01"
    assert ctx["tickers"]["BNP.PA"]["verification"]["verdict"] == "thèse intacte"
    # Une autre position n'est pas affectée par la mise à jour de BNP.PA.
    assert ctx["tickers"]["FMX"]["verification"]["derniere_verification"] is None


def test_build_context_last_three_queue_entries_only_per_ticker():
    for i in range(5):
        trq.add_entry("BNP.PA", execution_type="light", findings=[], run_date=f"2026-0{i + 1}-01")
    trq.add_entry("FMX", execution_type="light", findings=[], run_date="2026-01-15")
    ctx = publish_mod.build_context()
    assert len(ctx["tickers"]["BNP.PA"]["recent_review_queue"]) == 3
    assert [e["date"] for e in ctx["tickers"]["BNP.PA"]["recent_review_queue"]] == ["2026-03-01", "2026-04-01", "2026-05-01"]
    assert len(ctx["tickers"]["FMX"]["recent_review_queue"]) == 1


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
    assert set(result["tickers"].keys()) == {p["ticker"] for p in POSITIONS}


def test_publish_raises_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        publish_mod.publish()
