"""Tests data_providers/finnhub_provider.py — focus parsing.

On mock _fetch_json pour éviter les appels réseau réels.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from data_providers.finnhub_provider import FinnhubProvider


def test_provider_requires_api_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    assert FinnhubProvider.is_configured() is False
    with pytest.raises(ValueError, match="FINNHUB_API_KEY"):
        FinnhubProvider()


def test_provider_with_explicit_key():
    p = FinnhubProvider(api_key="test_key_123")
    assert p._api_key == "test_key_123"


def test_recommendation_parsing():
    rec_payload = [
        {"period": "2026-04-01", "strongBuy": 14, "buy": 23, "hold": 15, "sell": 2, "strongSell": 0},
        {"period": "2026-03-01", "strongBuy": 14, "buy": 22, "hold": 16, "sell": 2, "strongSell": 0},
        {"period": "2026-02-01", "strongBuy": 14, "buy": 21, "hold": 17, "sell": 2, "strongSell": 0},
        {"period": "2026-01-01", "strongBuy": 14, "buy": 21, "hold": 16, "sell": 2, "strongSell": 0},
    ]
    earnings_payload = [
        {"surprisePercent": 4.19, "actual": 1.65, "estimate": 1.58, "period": "2026-Q1"},
        {"surprisePercent": 3.0,  "actual": 1.50, "estimate": 1.46, "period": "2025-Q4"},
        {"surprisePercent": 2.5,  "actual": 1.40, "estimate": 1.37, "period": "2025-Q3"},
        {"surprisePercent": 3.6,  "actual": 1.35, "estimate": 1.30, "period": "2025-Q2"},
    ]
    cal_payload = {"earningsCalendar": [{"date": "2026-07-29", "epsEstimate": 1.76}]}
    pt_payload = {"targetMean": 220.5}

    def stub(endpoint, params, key):
        if "recommendation" in endpoint:
            return rec_payload
        if "earnings" in endpoint and "calendar" not in endpoint:
            return earnings_payload
        if "calendar/earnings" in endpoint:
            return cal_payload
        if "price-target" in endpoint:
            return pt_payload
        return None

    p = FinnhubProvider(api_key="test")
    with patch("data_providers.finnhub_provider._fetch_json", side_effect=stub), \
         patch("data_providers.finnhub_provider._read_cache", return_value=None), \
         patch("data_providers.finnhub_provider._write_cache"):
        data = p.get_revisions_and_earnings("AAPL", use_cache=False)

    assert data.upgrades_30d == 1   # bull went from 36 → 37
    assert data.downgrades_30d == 0
    assert data.upgrades_90d == 2   # bull went from 35 → 37
    assert data.downgrades_90d == 0
    assert data.revisions_net_score == 1.0
    assert data.next_earnings_date == "2026-07-29"
    assert data.next_earnings_eps_estimate == 1.76
    assert data.target_price_consensus == 220.5
    assert data.earnings_beat_rate_8q == 1.0  # 4/4 positives
    assert abs(data.earnings_surprise_avg_4q - 3.3225) < 1e-6


def test_handles_endpoint_failures_gracefully():
    """Si tous les endpoints renvoient None, on retourne FinnhubData neutre."""
    p = FinnhubProvider(api_key="test")
    with patch("data_providers.finnhub_provider._fetch_json", return_value=None), \
         patch("data_providers.finnhub_provider._read_cache", return_value=None), \
         patch("data_providers.finnhub_provider._write_cache"):
        data = p.get_revisions_and_earnings("XXX", use_cache=False)
    assert data.ticker == "XXX"
    assert data.upgrades_30d is None
    assert data.next_earnings_date is None
