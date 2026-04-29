"""Tests modules/earnings_surprise.py."""
from __future__ import annotations

from modules.earnings_surprise import compute_earnings_surprise_score


def test_no_data_returns_insufficient():
    out = compute_earnings_surprise_score({})
    assert out.score is None
    assert out.level == "INSUFFICIENT_DATA"


def test_strong_beat_classification():
    out = compute_earnings_surprise_score({
        "earnings_beat_rate_8q": 1.0,
        "earnings_surprise_avg_4q": 15.0,
        "earnings_surprise_pct_last": 20.0,
    })
    assert out.level == "STRONG_BEAT"
    assert out.score >= 80


def test_strong_miss_classification():
    out = compute_earnings_surprise_score({
        "earnings_beat_rate_8q": 0.0,
        "earnings_surprise_avg_4q": -15.0,
        "earnings_surprise_pct_last": -20.0,
    })
    assert out.level == "STRONG_MISS"
    assert out.score <= 20


def test_inline_classification():
    out = compute_earnings_surprise_score({
        "earnings_beat_rate_8q": 0.5,
        "earnings_surprise_avg_4q": 0.0,
        "earnings_surprise_pct_last": 0.0,
    })
    assert out.level == "INLINE"
    assert 40 <= out.score <= 60


def test_partial_data_works():
    """Beat rate seul suffit pour produire un score."""
    out = compute_earnings_surprise_score({"earnings_beat_rate_8q": 0.75})
    assert out.score is not None
    assert out.level in ("BEAT", "STRONG_BEAT")


def test_to_dict_serialization():
    out = compute_earnings_surprise_score({
        "earnings_beat_rate_8q": 0.6,
        "earnings_surprise_avg_4q": 5.0,
    })
    d = out.to_dict()
    assert "score" in d and "level" in d and "beat_rate_8q" in d
    assert d["beat_rate_8q"] == 0.6
