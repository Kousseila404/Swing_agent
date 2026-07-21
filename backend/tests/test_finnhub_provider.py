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
            return rec_payload, None
        if "earnings" in endpoint and "calendar" not in endpoint:
            return earnings_payload, None
        if "calendar/earnings" in endpoint:
            return cal_payload, None
        if "price-target" in endpoint:
            return pt_payload, None
        return None, None

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
    assert data.error is None  # tous les endpoints ont répondu, même vide


def test_handles_endpoint_failures_gracefully():
    """Si tous les endpoints renvoient (None, None) — "0 résultat légitime",
    pas un échec —, on retourne FinnhubData neutre sans `error`."""
    p = FinnhubProvider(api_key="test")
    with patch("data_providers.finnhub_provider._fetch_json", return_value=(None, None)), \
         patch("data_providers.finnhub_provider._read_cache", return_value=None), \
         patch("data_providers.finnhub_provider._write_cache"):
        data = p.get_revisions_and_earnings("XXX", use_cache=False)
    assert data.ticker == "XXX"
    assert data.upgrades_30d is None
    assert data.next_earnings_date is None
    assert data.error is None


def test_populates_error_on_explicit_fetch_failure():
    """Un échec HTTP/réseau explicite (429, timeout...) doit être distingué
    d'une absence légitime de données — Étape 5 roadmap."""
    def stub(endpoint, params, key):
        if "recommendation" in endpoint:
            return None, "rate_limited"
        return None, None

    p = FinnhubProvider(api_key="test")
    with patch("data_providers.finnhub_provider._fetch_json", side_effect=stub), \
         patch("data_providers.finnhub_provider._read_cache", return_value=None), \
         patch("data_providers.finnhub_provider._write_cache"):
        data = p.get_revisions_and_earnings("YYY", use_cache=False)
    assert data.error == "recommendation:rate_limited"


def test_error_accumulates_across_endpoints():
    """Plusieurs endpoints en échec sur le même ticker → error concatène les
    causes au lieu d'écraser la première."""
    def stub(endpoint, params, key):
        if "recommendation" in endpoint:
            return None, "http_500"
        if "price-target" in endpoint:
            return None, "fetch_failed"
        return None, None

    p = FinnhubProvider(api_key="test")
    with patch("data_providers.finnhub_provider._fetch_json", side_effect=stub), \
         patch("data_providers.finnhub_provider._read_cache", return_value=None), \
         patch("data_providers.finnhub_provider._write_cache"):
        data = p.get_revisions_and_earnings("ZZZ", use_cache=False)
    assert data.error == "recommendation:http_500; price_target:fetch_failed"


def _grade_time(y, m, d):
    """Unix epoch (UTC, minuit) — même convention que `gradeTime` Finnhub."""
    import calendar
    from datetime import datetime as _dt
    return calendar.timegm(_dt(y, m, d).timetuple())


def test_upgrade_downgrade_nominative_parsing():
    """Étape 13 roadmap : /stock/upgrade-downgrade doit être réellement
    appelé et son contenu mappé vers `analyst_actions`, trié du plus récent
    au plus ancien."""
    ud_payload = [
        {"symbol": "AAPL", "gradeTime": _grade_time(2026, 5, 1), "company": "Barclays",
         "fromGrade": "Overweight", "toGrade": "Equal-Weight", "action": "down"},
        {"symbol": "AAPL", "gradeTime": _grade_time(2026, 7, 15), "company": "Morgan Stanley",
         "fromGrade": "Equal-Weight", "toGrade": "Overweight", "action": "up"},
    ]

    def stub(endpoint, params, key):
        if "upgrade-downgrade" in endpoint:
            return ud_payload, None
        return None, None

    p = FinnhubProvider(api_key="test")
    with patch("data_providers.finnhub_provider._fetch_json", side_effect=stub), \
         patch("data_providers.finnhub_provider._read_cache", return_value=None), \
         patch("data_providers.finnhub_provider._write_cache"):
        data = p.get_revisions_and_earnings("AAPL", use_cache=False)

    assert data.error is None
    assert len(data.analyst_actions) == 2
    # Trié du plus récent au plus ancien.
    assert data.analyst_actions[0] == {
        "date": "2026-07-15", "firm": "Morgan Stanley", "action": "up",
        "from_grade": "Equal-Weight", "to_grade": "Overweight",
    }
    assert data.analyst_actions[1]["firm"] == "Barclays"


def test_upgrade_downgrade_skips_malformed_items():
    """Items sans gradeTime/company/action exploitable sont ignorés sans
    faire échouer le parsing des autres."""
    ud_payload = [
        {"symbol": "AAPL", "company": "Barclays", "fromGrade": "Buy", "toGrade": "Hold", "action": "down"},
        {"symbol": "AAPL", "gradeTime": _grade_time(2026, 6, 1), "fromGrade": "Buy", "toGrade": "Hold", "action": "down"},
        {"symbol": "AAPL", "gradeTime": _grade_time(2026, 6, 2), "company": "Barclays", "fromGrade": "Buy", "toGrade": "Hold"},
        {"symbol": "AAPL", "gradeTime": _grade_time(2026, 6, 3), "company": "UBS",
         "fromGrade": "Hold", "toGrade": "Buy", "action": "up"},
    ]

    def stub(endpoint, params, key):
        if "upgrade-downgrade" in endpoint:
            return ud_payload, None
        return None, None

    p = FinnhubProvider(api_key="test")
    with patch("data_providers.finnhub_provider._fetch_json", side_effect=stub), \
         patch("data_providers.finnhub_provider._read_cache", return_value=None), \
         patch("data_providers.finnhub_provider._write_cache"):
        data = p.get_revisions_and_earnings("AAPL", use_cache=False)

    assert len(data.analyst_actions) == 1
    assert data.analyst_actions[0]["firm"] == "UBS"


def test_upgrade_downgrade_error_accumulates_with_other_endpoints():
    def stub(endpoint, params, key):
        if "upgrade-downgrade" in endpoint:
            return None, "rate_limited"
        return None, None

    p = FinnhubProvider(api_key="test")
    with patch("data_providers.finnhub_provider._fetch_json", side_effect=stub), \
         patch("data_providers.finnhub_provider._read_cache", return_value=None), \
         patch("data_providers.finnhub_provider._write_cache"):
        data = p.get_revisions_and_earnings("AAPL", use_cache=False)

    assert data.error == "upgrade_downgrade:rate_limited"
    assert data.analyst_actions == []


def test_upgrade_downgrade_caps_at_10_most_recent():
    ud_payload = [
        {"symbol": "AAPL", "gradeTime": _grade_time(2020, 1, i + 1), "company": f"Firm{i}",
         "fromGrade": "Hold", "toGrade": "Buy", "action": "up"}
        for i in range(1, 15)
    ]

    def stub(endpoint, params, key):
        if "upgrade-downgrade" in endpoint:
            return ud_payload, None
        return None, None

    p = FinnhubProvider(api_key="test")
    with patch("data_providers.finnhub_provider._fetch_json", side_effect=stub), \
         patch("data_providers.finnhub_provider._read_cache", return_value=None), \
         patch("data_providers.finnhub_provider._write_cache"):
        data = p.get_revisions_and_earnings("AAPL", use_cache=False)

    assert len(data.analyst_actions) == 10
    # Le plus récent (jour 15) doit être en tête.
    assert data.analyst_actions[0]["firm"] == "Firm14"


def test_upgrade_downgrade_empty_list_is_not_an_error():
    """Ticker sans couverture analyste nominative — liste vide légitime, pas
    un échec (même philosophie que les autres endpoints, Étape 5)."""
    def stub(endpoint, params, key):
        if "upgrade-downgrade" in endpoint:
            return [], None
        return None, None

    p = FinnhubProvider(api_key="test")
    with patch("data_providers.finnhub_provider._fetch_json", side_effect=stub), \
         patch("data_providers.finnhub_provider._read_cache", return_value=None), \
         patch("data_providers.finnhub_provider._write_cache"):
        data = p.get_revisions_and_earnings("AAPL", use_cache=False)

    assert data.error is None
    assert data.analyst_actions == []
