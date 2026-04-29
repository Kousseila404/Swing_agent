"""Tests modules/dividend_safety.py."""
from __future__ import annotations

from modules.dividend_safety import compute_dividend_safety


def test_no_dividend_returns_no_dividend_level():
    out = compute_dividend_safety({"dividend_yield": 0.0})
    assert out.score is None
    assert out.level == "NO_DIVIDEND"


def test_no_dividend_yield_none():
    out = compute_dividend_safety({})
    assert out.level == "NO_DIVIDEND"


def test_very_safe_classification():
    out = compute_dividend_safety({
        "dividend_yield": 0.025,
        "payout_ratio": 0.30,
        "free_cash_flow": 10_000_000_000.0,
        "dividends_paid": 2_500_000_000.0,
        "five_year_avg_dividend_yield": 0.025,
    })
    assert out.level == "VERY_SAFE"
    assert out.score >= 80


def test_unsafe_payout_high_no_fcf_cover():
    out = compute_dividend_safety({
        "dividend_yield": 0.08,
        "payout_ratio": 1.20,
        "free_cash_flow": 100_000_000.0,
        "dividends_paid": 500_000_000.0,
        "five_year_avg_dividend_yield": 0.03,
    })
    # payout > 100% + spike yield + cover < 1 → score bas.
    assert out.score is not None
    assert out.level in ("UNSAFE", "RISKY", "MODERATE")


def test_partial_data():
    """Seulement payout dispo → score calculé sur 1 axis."""
    out = compute_dividend_safety({
        "dividend_yield": 0.04,
        "payout_ratio": 0.50,
    })
    assert out.score is not None
    assert out.level in ("MODERATE", "SAFE", "VERY_SAFE", "RISKY")


def test_serialization():
    out = compute_dividend_safety({
        "dividend_yield": 0.04, "payout_ratio": 0.45,
    })
    d = out.to_dict()
    assert "score" in d and "level" in d and "components" in d
