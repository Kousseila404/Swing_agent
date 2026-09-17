"""Tests — modules/scoring_lab.py + GET /api/scoring/lab."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from modules import api_core, scoring_lab

_TOKEN = "test-token-lab"


def test_live_stats_compounds_and_measures_drawdown():
    periods = [
        {"signal_date": "2026-01-01", "next_date": "2026-01-08", "portfolio_return": 0.10},  # bootstrap → ignoré
        {"signal_date": "2026-05-01", "next_date": "2026-05-08", "portfolio_return": 0.10},
        {"signal_date": "2026-05-08", "next_date": "2026-05-15", "portfolio_return": -0.05},
        {"signal_date": "2026-05-15", "next_date": "2026-05-22", "portfolio_return": 0.02},
    ]
    s = scoring_lab._live_stats(periods)
    assert s["periods"] == 3
    assert s["return_pct"] == round((1.10 * 0.95 * 1.02 - 1) * 100, 2)
    assert s["hit_rate"] == round(2 / 3, 3)
    assert s["max_drawdown_pct"] == -5.0
    assert s["worst_week_pct"] == -5.0
    assert s["start"] == "2026-05-01" and s["end"] == "2026-05-22"


def test_make_rank_fn_profile_and_reverse():
    snap = {"tickers": {
        "A": {"momentum_score": 90, "quality_score": 10, "titan_composite_score": 50},
        "B": {"momentum_score": 10, "quality_score": 90, "titan_composite_score": 70},
        "C": {"momentum_score": 50, "quality_score": 50, "titan_composite_score": 60},
    }}
    top_mom = scoring_lab.make_rank_fn(field="momentum_score")(snap, 2)
    assert [t for t, _ in top_mom] == ["A", "C"]
    bottom = scoring_lab.make_rank_fn(reverse=False)(snap, 1)
    assert [t for t, _ in bottom] == ["A"]
    prof = scoring_lab.make_rank_fn(weights={"momentum_score": 0.5, "quality_score": 0.5})(snap, 3)
    assert prof[0][1] == 50.0 and len(prof) == 3


def test_verdict_strength():
    rows = [
        {"id": "top20", "return_pct": 18.0}, {"id": "universe_ew", "return_pct": 14.0},
        {"id": "bottom20", "return_pct": 10.0}, {"id": "momentum_top20", "return_pct": 58.0},
    ]
    v = scoring_lab._verdict(rows)
    assert v["edge_vs_universe_pct"] == 4.0 and v["strength"] == "weak"
    assert len(v["messages"]) == 3


def test_scoring_lab_endpoint_404_then_200(monkeypatch, tmp_path):
    import api
    monkeypatch.setattr(api_core, "API_TOKEN", _TOKEN)
    monkeypatch.setattr(scoring_lab, "OUT_PATH", tmp_path / ".scoring_lab.json")
    client = TestClient(api.app)
    h = {"Authorization": f"Bearer {_TOKEN}"}
    assert client.get("/api/scoring/lab", headers=h).status_code == 404
    (tmp_path / ".scoring_lab.json").write_text(json.dumps({"rows": [], "verdict": {"strength": "none"}}))
    r = client.get("/api/scoring/lab", headers=h)
    assert r.status_code == 200 and r.json()["verdict"]["strength"] == "none"
