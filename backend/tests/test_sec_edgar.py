"""Tests modules/sec_edgar.py — focus pillar score + parsing logic."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

from modules import sec_edgar
from modules.sec_edgar import (
    InsiderActivity,
    compute_insider_pillar_score,
    fetch_insider_activity,
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


# ─────────────────────────────────────────────────────────────────
# fetch_insider_activity — extraction 8-K (Étape 9 roadmap)
# ─────────────────────────────────────────────────────────────────

def _mock_submissions_payload(forms, dates, accessions):
    return {"filings": {"recent": {
        "form": forms, "filingDate": dates, "accessionNumber": accessions,
    }}}


def test_fetch_insider_activity_extracts_most_recent_8k():
    payload = _mock_submissions_payload(
        forms=["8-K", "4", "8-K", "10-Q"],
        dates=["2026-06-01", "2026-06-15", "2026-07-10", "2026-05-01"],
        accessions=["0001-26-000001", "0001-26-000002", "0001-26-000003", "0001-26-000004"],
    )
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=payload), \
         patch.object(sec_edgar, "_read_cache", return_value=None), \
         patch.object(sec_edgar, "_write_cache"):
        activity = fetch_insider_activity("AAA", use_cache=False)
    # Le 8-K du 2026-07-10 est plus récent que celui du 2026-06-01.
    assert activity.most_recent_8k_date == "2026-07-10"
    assert activity.error is None


def test_fetch_insider_activity_no_8k_present():
    payload = _mock_submissions_payload(
        forms=["4", "10-Q"],
        dates=["2026-06-15", "2026-05-01"],
        accessions=["0001-26-000002", "0001-26-000004"],
    )
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=payload), \
         patch.object(sec_edgar, "_read_cache", return_value=None), \
         patch.object(sec_edgar, "_write_cache"):
        activity = fetch_insider_activity("BBB", use_cache=False)
    assert activity.most_recent_8k_date is None


def test_fetch_insider_activity_8ka_amendment_also_counted():
    payload = _mock_submissions_payload(
        forms=["8-K/A", "4"],
        dates=["2026-07-05", "2026-06-15"],
        accessions=["0001-26-000005", "0001-26-000002"],
    )
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=payload), \
         patch.object(sec_edgar, "_read_cache", return_value=None), \
         patch.object(sec_edgar, "_write_cache"):
        activity = fetch_insider_activity("CCC", use_cache=False)
    assert activity.most_recent_8k_date == "2026-07-05"


# ─────────────────────────────────────────────────────────────────
# fetch_insider_activity — extraction 10-K/10-Q (Étape 11 roadmap)
# ─────────────────────────────────────────────────────────────────

def test_fetch_insider_activity_extracts_most_recent_10k_and_10q():
    payload = _mock_submissions_payload(
        forms=["10-K", "4", "10-Q", "10-Q", "8-K"],
        dates=["2026-02-01", "2026-06-15", "2026-05-01", "2026-07-10", "2026-04-01"],
        accessions=[
            "0001-26-000001", "0001-26-000002", "0001-26-000003",
            "0001-26-000004", "0001-26-000005",
        ],
    )
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=payload), \
         patch.object(sec_edgar, "_read_cache", return_value=None), \
         patch.object(sec_edgar, "_write_cache"):
        activity = fetch_insider_activity("AAA", use_cache=False)
    assert activity.most_recent_10k_date == "2026-02-01"
    # Le 10-Q du 2026-07-10 est plus récent que celui du 2026-05-01.
    assert activity.most_recent_10q_date == "2026-07-10"
    assert activity.most_recent_8k_date == "2026-04-01"


def test_fetch_insider_activity_no_10k_10q_present():
    payload = _mock_submissions_payload(
        forms=["4", "8-K"],
        dates=["2026-06-15", "2026-04-01"],
        accessions=["0001-26-000002", "0001-26-000005"],
    )
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=payload), \
         patch.object(sec_edgar, "_read_cache", return_value=None), \
         patch.object(sec_edgar, "_write_cache"):
        activity = fetch_insider_activity("BBB", use_cache=False)
    assert activity.most_recent_10k_date is None
    assert activity.most_recent_10q_date is None


def test_fetch_insider_activity_10k_10q_amendments_also_counted():
    payload = _mock_submissions_payload(
        forms=["10-K/A", "10-Q/A", "4"],
        dates=["2026-03-01", "2026-06-01", "2026-06-15"],
        accessions=["0001-26-000006", "0001-26-000007", "0001-26-000002"],
    )
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=payload), \
         patch.object(sec_edgar, "_read_cache", return_value=None), \
         patch.object(sec_edgar, "_write_cache"):
        activity = fetch_insider_activity("CCC", use_cache=False)
    assert activity.most_recent_10k_date == "2026-03-01"
    assert activity.most_recent_10q_date == "2026-06-01"
