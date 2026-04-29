"""
Tests des contrats data_providers : factory, FMP, Polygon, fallback YFinance.

Ne tape aucune API réelle — les providers HTTP sont mockés via requests.Session.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data_providers import (
    FinancialRatios,
    FMPProvider,
    PolygonProvider,
    ProviderQuotaExceeded,
    ProviderUnavailable,
    get_providers,
)
from data_providers.base import FundamentalProviderBase, MarketDataProviderBase

# ── Helpers mock HTTP ──────────────────────────────────────────────────────

def _mock_response(status: int, json_payload):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_payload
    r.text = str(json_payload)[:100]
    return r


# ═══════════════════════════════════════════════════════════════════════════
# FMPProvider
# ═══════════════════════════════════════════════════════════════════════════

def test_fmp_requires_api_key():
    """Pas de clé → ValueError immédiate (fail-fast à l'instanciation)."""
    with pytest.raises(ValueError, match="api_key requis"):
        FMPProvider(api_key="")


def test_fmp_get_financial_ratios_happy_path():
    """Payloads calqués sur l'API /stable/ (post-migration août 2025)."""
    session = MagicMock()
    session.get.side_effect = [
        _mock_response(200, [{
            "companyName": "Apple Inc",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "country": "US", "currency": "USD", "exchange": "NASDAQ",
            "marketCap": 3_000_000_000_000.0,
            "price": 200.0, "beta": 1.2,
        }]),
        _mock_response(200, [{
            "priceToEarningsRatioTTM": 28.5,
            "priceToEarningsGrowthRatioTTM": 2.1,
            "priceToBookRatioTTM": 40.0,
            "dividendYieldTTM": 0.005,
            "operatingProfitMarginTTM": 0.30,
            "netProfitMarginTTM": 0.25,
            "debtToEquityRatioTTM": 1.5,
            "currentRatioTTM": 1.1, "quickRatioTTM": 0.9,
            "freeCashFlowPerShareTTM": 6.5,
            "operatingCashFlowPerShareTTM": 7.2,
        }]),
        _mock_response(200, [{
            "returnOnEquityTTM": 1.45,
            "evToEBITDATTM": 22.0,
            "evToSalesTTM": 7.5,
        }]),
    ]

    p = FMPProvider(api_key="fake_key", session=session)
    r = p.get_financial_ratios("AAPL")

    assert r.ticker == "AAPL"
    assert r.name == "Apple Inc"
    assert r.sector == "Technology"
    assert r.market_cap == 3_000_000_000_000.0
    assert r.current_price == 200.0
    assert r.return_on_equity == pytest.approx(1.45)
    assert r.debt_to_equity == pytest.approx(1.5)
    assert r.ev_to_ebitda == pytest.approx(22.0)
    # FCF absolu = FCF/share × shares (mktCap/price = 15e9)
    assert r.free_cash_flow == pytest.approx(6.5 * 15_000_000_000)
    assert r.source_provider == "fmp"
    assert r.error is None


def test_fmp_quota_exceeded_on_429():
    """HTTP 429 → ProviderQuotaExceeded (le caller doit arrêter la pipeline)."""
    session = MagicMock()
    session.get.return_value = _mock_response(429, {})
    p = FMPProvider(api_key="fake_key", session=session)
    with pytest.raises(ProviderQuotaExceeded):
        p.get_financial_ratios("AAPL")


def test_fmp_daily_quota_cap():
    """Compteur local → ProviderQuotaExceeded dès que daily_quota est atteint."""
    session = MagicMock()
    p = FMPProvider(api_key="fake_key", daily_quota=2, session=session)
    # Les deux premiers calls réservent leur slot sans émettre vraiment (on
    # intercepte avec un payload bidon — on ne teste que le compteur).
    session.get.return_value = _mock_response(200, [{}])
    p._reserve_call()
    p._reserve_call()
    with pytest.raises(ProviderQuotaExceeded, match="Quota journalier"):
        p._reserve_call()


def test_fmp_empty_payload_returns_error_ratios():
    """Payload vide → FinancialRatios avec .error, pas de raise."""
    session = MagicMock()
    session.get.return_value = _mock_response(200, [])
    p = FMPProvider(api_key="fake_key", session=session)
    r = p.get_financial_ratios("ZZZZ")
    assert r.ticker == "ZZZZ"
    assert r.error == "FMP empty payload"


def test_fmp_unavailable_on_401():
    """Clé invalide (401) → ProviderUnavailable (pas Quota — le caller doit pas attendre)."""
    session = MagicMock()
    session.get.return_value = _mock_response(401, {"error": "bad key"})
    p = FMPProvider(api_key="fake_key", session=session)
    r = p.get_financial_ratios("AAPL")
    # Unavailable catché localement → ratios.error set
    assert r.error and "unavailable" in r.error


def test_fmp_402_skips_subsequent_calls_to_same_path():
    """Endpoint premium (402) → path blacklisté pour la session, pas de quota
    consommé ni de WARNING log répété sur les tickers suivants."""
    session = MagicMock()
    # /profile OK pour les 2 tickers, /ratios-ttm + /key-metrics-ttm = 402.
    # Premier ticker : 3 calls HTTP (profile, ratios-402, keymetrics-402).
    # Deuxième ticker : 1 seul call HTTP (profile — les 2 autres sont skippés).
    def _dispatch(url, params=None, timeout=None):
        if "/profile" in url:
            return _mock_response(200, [{"companyName": "X", "price": 10.0, "marketCap": 1e9}])
        return _mock_response(402, {"error": "premium only"})

    session.get.side_effect = _dispatch
    p = FMPProvider(api_key="fake_key", session=session)

    p.get_financial_ratios("AAA")
    calls_after_first = session.get.call_count  # 3
    p.get_financial_ratios("BBB")
    calls_after_second = session.get.call_count  # 3 + 1 = 4

    assert calls_after_first == 3
    assert calls_after_second == 4, (
        f"2e ticker devrait skip ratios-ttm + key-metrics-ttm "
        f"(+1 call /profile = 4 total), got {calls_after_second}"
    )
    assert "/ratios-ttm" in p._premium_blocked
    assert "/key-metrics-ttm" in p._premium_blocked
    # Quota consommé : 3 (premier ticker) + 1 (profile du 2e) = 4.
    assert p.calls_today == 4


# ═══════════════════════════════════════════════════════════════════════════
# PolygonProvider
# ═══════════════════════════════════════════════════════════════════════════

def test_polygon_requires_api_key():
    with pytest.raises(ValueError, match="api_key requis"):
        PolygonProvider(api_key="")


def test_polygon_get_daily_history_happy_path():
    """Parse correctement le payload /v2/aggs et retourne une pd.Series."""
    bars = [
        {"t": 1_700_000_000_000, "c": 100.0},
        {"t": 1_700_086_400_000, "c": 101.0},
        {"t": 1_700_172_800_000, "c": 102.5},
    ]
    session = MagicMock()
    session.get.return_value = _mock_response(200, {"results": bars})

    p = PolygonProvider(api_key="fake", rate_limit_per_minute=None, session=session)
    s = p.get_daily_history("AAPL", days=10)

    assert s is not None
    assert len(s) == 3
    assert s.iloc[-1] == 102.5
    assert s.name == "AAPL"


def test_polygon_empty_results_returns_none():
    session = MagicMock()
    session.get.return_value = _mock_response(200, {"results": []})
    p = PolygonProvider(api_key="fake", rate_limit_per_minute=None, session=session)
    assert p.get_daily_history("ZZZZ", days=10) is None


def test_polygon_429_raises_quota_exceeded():
    session = MagicMock()
    session.get.return_value = _mock_response(429, {})
    p = PolygonProvider(api_key="fake", rate_limit_per_minute=None, session=session)
    with pytest.raises(ProviderQuotaExceeded):
        p.get_daily_history("AAPL", days=10)


def test_polygon_batch_propagates_quota():
    """Si un call du batch hit 429, la ProviderQuotaExceeded remonte — pas de retry silencieux."""
    session = MagicMock()
    # Premier OK, second 429.
    session.get.side_effect = [
        _mock_response(200, {"results": [{"t": 1, "c": 10.0}]}),
        _mock_response(429, {}),
    ]
    p = PolygonProvider(api_key="fake", rate_limit_per_minute=None, session=session)
    with pytest.raises(ProviderQuotaExceeded):
        p.get_daily_history_batch(["AAPL", "MSFT"], days=10)


def test_polygon_batch_isolated_errors():
    """Une ProviderUnavailable locale sur un ticker ne tue pas les autres."""
    session = MagicMock()
    session.get.side_effect = [
        _mock_response(500, {}),  # AAPL → unavailable, ticker fail
        _mock_response(200, {"results": [{"t": 1, "c": 50.0}]}),  # MSFT OK
    ]
    p = PolygonProvider(api_key="fake", rate_limit_per_minute=None, session=session)
    out = p.get_daily_history_batch(["AAPL", "MSFT"], days=10)
    assert out["AAPL"] is None
    assert out["MSFT"] is not None and out["MSFT"].iloc[-1] == 50.0


# ═══════════════════════════════════════════════════════════════════════════
# Factory get_providers()
# ═══════════════════════════════════════════════════════════════════════════

def test_factory_uses_fmp_when_key_present_and_enabled():
    """FMP_ENABLED=true requis depuis Lot 11 (free tier inutile par défaut)."""
    with patch.dict(os.environ, {"FMP_API_KEY": "xxx", "FMP_ENABLED": "true",
                                  "MARKET_DATA_API_KEY": ""}, clear=False):
        fundamental, market = get_providers()
        # Lot 11 : wrappé par CachedFundamentalProvider → name composite
        assert fundamental.name == "fmp+yfinance+cached"
        assert market.name == "yfinance"


def test_factory_skips_fmp_when_enabled_false_default():
    """Sans FMP_ENABLED, FMP est skippé même si la clé est set (free tier inutile)."""
    with patch.dict(os.environ, {"FMP_API_KEY": "xxx", "MARKET_DATA_API_KEY": ""},
                     clear=False):
        # Force unset FMP_ENABLED pour le test
        os.environ.pop("FMP_ENABLED", None)
        fundamental, market = get_providers()
        # Pas de FMP → yfinance direct, toujours wrappé par cache
        assert fundamental.name == "yfinance+cached"


def test_factory_skips_fmp_when_enabled_explicitly_false():
    with patch.dict(os.environ, {"FMP_API_KEY": "xxx", "FMP_ENABLED": "false",
                                  "MARKET_DATA_API_KEY": ""}, clear=False):
        fundamental, _ = get_providers()
        assert fundamental.name == "yfinance+cached"


def test_factory_uses_polygon_when_key_present():
    with patch.dict(os.environ, {"FMP_API_KEY": "", "MARKET_DATA_API_KEY": "yyy"},
                     clear=False):
        fundamental, market = get_providers()
        assert fundamental.name == "yfinance+cached"
        # Polygon est wrappé avec YF fallback pour get_latest_prices (403 snapshot
        # en free tier). Le primary reste Polygon (daily history).
        assert market.name == "polygon+yfinance"


def test_factory_yfinance_only_when_no_keys():
    with patch.dict(os.environ, {"FMP_API_KEY": "", "MARKET_DATA_API_KEY": ""},
                     clear=False):
        fundamental, market = get_providers()
        assert fundamental.name == "yfinance+cached"
        assert market.name == "yfinance"


def test_factory_both_providers_when_both_keys_present_and_fmp_enabled():
    with patch.dict(os.environ, {"FMP_API_KEY": "x", "FMP_ENABLED": "true",
                                  "MARKET_DATA_API_KEY": "y"}, clear=False):
        fundamental, market = get_providers()
        assert fundamental.name == "fmp+yfinance+cached"
        assert market.name == "polygon+yfinance"


# ═══════════════════════════════════════════════════════════════════════════
# Intégration moteur TITAN — injection FakeProvider
# ═══════════════════════════════════════════════════════════════════════════

class _FakeFundamental(FundamentalProviderBase):
    """Provider déterministe pour tester UniverseEngine sans réseau."""
    name = "fake_fundamental"

    def get_financial_ratios(self, ticker: str) -> FinancialRatios:
        return FinancialRatios(
            ticker=ticker,
            name=f"{ticker} Corp",
            sector="Technology",
            market_cap=50_000_000_000.0,
            current_price=100.0,
            return_on_equity=0.25,
            source_provider=self.name,
            fetched_at="2026-04-20T00:00:00Z",
        )


class _FakeMarket(MarketDataProviderBase):
    name = "fake_market"

    def get_daily_history(self, ticker, days):
        idx = pd.date_range("2025-10-01", periods=130, freq="B")
        return pd.Series(range(100, 100 + 130), index=idx, dtype=float)

    def get_daily_history_batch(self, tickers, days):
        return {t: self.get_daily_history(t, days) for t in tickers}


def test_universe_engine_injection_bypasses_yfinance():
    """UniverseEngine(FakeFundamental, FakeMarket) ne touche jamais yfinance."""
    from modules.universe_engine import UniverseEngine

    engine = UniverseEngine(
        fundamental_provider=_FakeFundamental(),
        market_provider=_FakeMarket(),
    )
    # On patche _collect_tickers pour éviter les appels Wikipedia.
    with patch("modules.universe_engine._collect_tickers") as mock_collect:
        mock_collect.return_value = {"AAPL": ["sp500"], "MSFT": ["sp500"]}
        payload = engine.build(indices=("sp500",), workers=2, progress_every=999)

    assert payload["stats"]["n_kept"] == 2
    assert "AAPL" in payload["tickers"]
    aapl = payload["tickers"]["AAPL"]
    assert aapl["sector"] == "Technology"
    # Momentum injecté depuis la série monotone croissante du FakeMarket
    assert aapl["momentum_return_pct"] is not None
    assert aapl["momentum_return_pct"] > 0
