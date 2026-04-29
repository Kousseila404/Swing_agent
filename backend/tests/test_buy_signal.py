"""Tests modules/buy_signal.py — verdict logic."""
from __future__ import annotations

from datetime import date, timedelta

from modules.buy_signal import compute_buy_signal


def _row(**overrides):
    base = {
        "titan_composite_score": 75.0,
        "quality_score": 70.0,
        "value_score": 60.0,
        "risk_score": 60.0,
        "momentum_score": 65.0,
        "revisions_score": 55.0,
        "growth_score": 50.0,
        "piotroski_score": 60.0,
        "f_score": 5,
        "earnings_beat_rate_8q": 0.5,
        "insider_cluster_buying": False,
        "insider_score": 50.0,
        "titan_tilt_flags": [],
        "next_earnings_date": (date.today() + timedelta(days=60)).isoformat(),
    }
    base.update(overrides)
    return base


def test_strong_buy_with_2_boosters():
    out = compute_buy_signal(_row(
        titan_composite_score=85.0,
        revisions_score=70.0,
        insider_cluster_buying=True,
        f_score=8,
    ))
    assert out.verdict == "STRONG_BUY"
    assert out.color == "success"
    assert out.sizing_pct == 0.20
    assert len(out.boosters_active) >= 2


def test_buy_with_one_booster():
    out = compute_buy_signal(_row(
        titan_composite_score=76.0,
        revisions_score=70.0,
    ))
    assert out.verdict == "BUY"
    assert out.color == "primary"
    assert out.sizing_pct == 0.13


def test_watch_when_no_booster():
    out = compute_buy_signal(_row(
        titan_composite_score=76.0,
        revisions_score=40.0,
        insider_cluster_buying=False,
        f_score=4,
        earnings_beat_rate_8q=0.4,
    ))
    assert out.verdict == "WATCH"
    assert out.sizing_pct == 0.0


def test_skip_when_titan_under_70():
    out = compute_buy_signal(_row(titan_composite_score=65.0))
    assert out.verdict == "SKIP"
    assert out.sizing_pct == 0.0


def test_earnings_blackout():
    soon = (date.today() + timedelta(days=3)).isoformat()
    out = compute_buy_signal(_row(
        titan_composite_score=85.0,
        next_earnings_date=soon,
        revisions_score=70.0,
        insider_cluster_buying=True,
    ))
    assert out.verdict == "EARNINGS_BLACKOUT"
    assert out.color == "danger"
    assert out.sizing_pct == 0.0


def test_earnings_near_reduces_size():
    near = (date.today() + timedelta(days=10)).isoformat()
    out = compute_buy_signal(_row(
        titan_composite_score=85.0,
        next_earnings_date=near,
        revisions_score=70.0,
        insider_cluster_buying=True,
        f_score=8,
    ))
    assert out.verdict == "STRONG_BUY"
    # Sizing réduit de 30% : 0.20 × 0.7 = 0.14
    assert abs(out.sizing_pct - 0.14) < 1e-3


def test_cheap_junk_rejected():
    out = compute_buy_signal(_row(
        titan_composite_score=78.0,
        titan_tilt_flags=["cheap_junk"],
    ))
    assert out.verdict == "CHEAP_JUNK"
    assert out.color == "danger"


def test_falling_knife_rejected():
    out = compute_buy_signal(_row(
        titan_composite_score=72.0,
        titan_tilt_flags=["falling_knife"],
    ))
    assert out.verdict == "FALLING_KNIFE"


def test_no_data_when_composite_missing():
    out = compute_buy_signal({})
    assert out.verdict == "NO_DATA"


def test_quality_too_low_demotes_to_watch():
    out = compute_buy_signal(_row(
        titan_composite_score=78.0,
        quality_score=40.0,
        revisions_score=70.0,
    ))
    assert out.verdict == "WATCH"


def test_to_dict_complete():
    d = compute_buy_signal(_row(titan_composite_score=85.0,
                                 revisions_score=70.0,
                                 insider_cluster_buying=True)).to_dict()
    assert "verdict" in d and "label" in d and "sizing_pct" in d
    assert "boosters_active" in d
    assert "reasons_pos" in d


def test_support_score_as_booster():
    out = compute_buy_signal(_row(
        titan_composite_score=76.0,
    ), support_score=85.0)
    assert "Support" in str(out.boosters_active)
    assert out.verdict == "BUY"
