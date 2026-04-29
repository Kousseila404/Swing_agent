"""Tests modules/sec_edgar.py — focus pillar score + parsing logic."""
from __future__ import annotations

from datetime import date

from modules.sec_edgar import (
    InsiderActivity,
    compute_insider_pillar_score,
)


def test_compute_pillar_score_neutral_on_empty():
    a = InsiderActivity(ticker="AAA")
    out = compute_insider_pillar_score(a)
    assert out["score"] == 50.0
    assert out["data_quality"] == 0.0


def test_compute_pillar_score_with_error():
    a = InsiderActivity(ticker="AAA", error="cik_unknown")
    out = compute_insider_pillar_score(a)
    assert out["score"] == 50.0
    assert out["reason"] == "cik_unknown"


def test_pillar_score_bullish_signals_boost():
    """Calibration Lot 17 stricte : +15 (distinct≥8) +10 (cluster) +5 (≥15/90j) = +30 → 80."""
    a = InsiderActivity(
        ticker="BBB",
        buy_count_30d=10,
        distinct_insiders_buying_30d=8,
        cluster_buying=True,
        buy_count_90d=15,
        n_filings_scanned=15,
    )
    out = compute_insider_pillar_score(a)
    assert out["score"] == 80.0
    assert out["data_quality"] == 1.0


def test_pillar_score_moderate_distinct():
    """5 insiders distincts → +8 mais pas +15."""
    a = InsiderActivity(
        ticker="MMM",
        buy_count_30d=6,
        distinct_insiders_buying_30d=5,
        cluster_buying=False,
        buy_count_90d=8,
        n_filings_scanned=8,
    )
    out = compute_insider_pillar_score(a)
    # 50 + 8 (distinct≥5) = 58
    assert out["score"] == 58.0


def test_pillar_score_caps_at_100():
    a = InsiderActivity(
        ticker="CCC",
        buy_count_30d=30,
        distinct_insiders_buying_30d=15,
        cluster_buying=True,
        buy_count_90d=50,
        n_filings_scanned=80,
    )
    out = compute_insider_pillar_score(a)
    assert out["score"] <= 100.0


def test_pillar_score_heavy_selling_penalty():
    a = InsiderActivity(
        ticker="DDD",
        buy_count_30d=1,
        sell_count_30d=10,
        n_filings_scanned=11,
    )
    out = compute_insider_pillar_score(a)
    # 50 base, sell > 2× buy and sell ≥ 3 → -10 → 40
    assert out["score"] == 40.0


def test_pillar_score_components_exposed():
    a = InsiderActivity(
        ticker="EEE",
        buy_count_30d=3,
        sell_count_30d=1,
        distinct_insiders_buying_30d=2,
        cluster_buying=False,
        n_filings_scanned=5,
    )
    out = compute_insider_pillar_score(a)
    c = out["components"]
    assert c["buy_count_30d"] == 3
    assert c["sell_count_30d"] == 1
    assert c["distinct_insiders_30d"] == 2
    assert c["cluster_buying"] is False
