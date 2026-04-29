"""Tests modules/revisions_score.py — pilier Revisions."""
from __future__ import annotations

from modules.revisions_score import compute_revisions_pillar


def _row(**overrides):
    base = {
        "upgrades_30d": None,
        "downgrades_30d": None,
        "upgrades_90d": None,
        "downgrades_90d": None,
        "revisions_net_score": None,
        "earnings_beat_rate_8q": None,
        "earnings_surprise_avg_4q": None,
    }
    base.update(overrides)
    return base


def test_empty_input_returns_empty_dict():
    assert compute_revisions_pillar({}) == {}


def test_single_ticker_all_none_yields_neutral():
    out = compute_revisions_pillar({"AAA": _row()})
    assert out["AAA"]["revisions_score"] == 50.0
    assert out["AAA"]["revisions_data_quality"] == 0.0


def test_strong_bull_ranks_high():
    universe = {
        "BULL": _row(
            upgrades_90d=8, downgrades_90d=0, revisions_net_score=1.0,
            earnings_beat_rate_8q=1.0, earnings_surprise_avg_4q=10.0,
        ),
        "MID": _row(
            upgrades_90d=2, downgrades_90d=2, revisions_net_score=0.0,
            earnings_beat_rate_8q=0.5, earnings_surprise_avg_4q=0.0,
        ),
        "BEAR": _row(
            upgrades_90d=0, downgrades_90d=8, revisions_net_score=-1.0,
            earnings_beat_rate_8q=0.1, earnings_surprise_avg_4q=-5.0,
        ),
    }
    out = compute_revisions_pillar(universe)
    assert out["BULL"]["revisions_score"] > out["MID"]["revisions_score"]
    assert out["MID"]["revisions_score"] > out["BEAR"]["revisions_score"]
    # Components bull doivent toutes être hautes (DQ = 1.0 sur 4/4 composantes)
    assert out["BULL"]["revisions_data_quality"] == 1.0


def test_partial_data_renormalizes():
    """Si seulement beat_rate dispo, score doit refléter beat_rate uniquement."""
    universe = {
        "A": _row(earnings_beat_rate_8q=1.0),
        "B": _row(earnings_beat_rate_8q=0.0),
    }
    out = compute_revisions_pillar(universe)
    # A doit avoir DQ < 1, mais score reste calculable depuis 1 composante.
    assert out["A"]["revisions_data_quality"] == 0.25
    assert out["A"]["revisions_score"] >= out["B"]["revisions_score"]


def test_score_in_range_0_100():
    universe = {f"T{i}": _row(revisions_net_score=float(i) / 5 - 1.0)
                for i in range(10)}
    out = compute_revisions_pillar(universe)
    for v in out.values():
        assert 0.0 <= v["revisions_score"] <= 100.0
