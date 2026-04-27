"""Tests des helpers recovery yfinance — dividend_yield + market_cap.

Audit 2026-04-23 : yfinance retournait sur 268 tickers un `dividendYield`
incohérent (ex: BAX=1.95 = dividend annuel en $, au lieu du yield décimal).
Ces helpers reconstruisent la valeur correcte avant cache.
"""
from __future__ import annotations

from data_providers.yfinance_provider import (
    _recover_dividend_yield,
    _recover_market_cap,
)


# ── _recover_dividend_yield ─────────────────────────────────────────────────

def test_div_yield_raw_in_bounds_returned_as_is():
    info = {"dividendYield": 0.0325}
    assert _recover_dividend_yield(info, price=150.0) == 0.0325


def test_div_yield_raw_out_of_bounds_recovered_from_rate():
    """BAX cas observé : dividendYield=1.95 (= $1.95 de dividende annuel)."""
    info = {"dividendYield": 1.95, "dividendRate": 1.95}
    # 1.95 / 100 = 0.0195 = 1.95 % → plausible
    result = _recover_dividend_yield(info, price=100.0)
    assert result is not None
    assert abs(result - 0.0195) < 1e-6


def test_div_yield_uses_trailing_rate_if_dividend_rate_missing():
    info = {"dividendYield": 1.50, "trailingAnnualDividendRate": 3.00}
    result = _recover_dividend_yield(info, price=100.0)
    assert result == 0.03


def test_div_yield_falls_back_to_trailing_yield():
    """Pas de dividendRate exploitable → trailingAnnualDividendYield si plausible."""
    info = {"dividendYield": 5.0, "trailingAnnualDividendYield": 0.045}
    result = _recover_dividend_yield(info, price=100.0)
    assert result == 0.045


def test_div_yield_all_sources_bad_returns_none():
    info = {"dividendYield": 10.0, "dividendRate": 500.0, "trailingAnnualDividendYield": 8.0}
    # dividendRate/price = 5.0 → toujours out of bounds (> 0.25)
    result = _recover_dividend_yield(info, price=100.0)
    assert result is None


def test_div_yield_no_price_no_recovery():
    info = {"dividendYield": 5.0, "dividendRate": 2.0}
    assert _recover_dividend_yield(info, price=None) is None


def test_div_yield_missing_everywhere_returns_none():
    info = {}
    assert _recover_dividend_yield(info, price=100.0) is None


def test_div_yield_zero_is_valid():
    info = {"dividendYield": 0.0}
    assert _recover_dividend_yield(info, price=100.0) == 0.0


def test_div_yield_negative_rejected():
    info = {"dividendYield": -0.05}
    # Recovery tentera dividendRate aussi, tout sera None.
    assert _recover_dividend_yield(info, price=100.0) is None


# ── _recover_market_cap ────────────────────────────────────────────────────

def test_mcap_reported_consistent_kept_as_is():
    info = {"marketCap": 1_000_000_000}
    # price × shares = 1.02B, divergence 2 % → tolérance OK.
    result = _recover_market_cap(info, price=100.0, shares=10_200_000)
    assert result == 1_000_000_000


def test_mcap_reported_wildly_off_replaced_by_computed():
    """Stock split non propagé au marketCap : on préfère price × shares."""
    info = {"marketCap": 1_000_000_000}
    # price × shares = 2B → divergence 50 % → on trust computed.
    result = _recover_market_cap(info, price=100.0, shares=20_000_000)
    assert result == 2_000_000_000


def test_mcap_missing_falls_back_to_computed():
    info = {}
    result = _recover_market_cap(info, price=50.0, shares=1_000_000)
    assert result == 50_000_000


def test_mcap_missing_and_no_price_returns_none():
    info = {}
    assert _recover_market_cap(info, price=None, shares=1_000_000) is None


def test_mcap_missing_and_no_shares_returns_none():
    info = {}
    assert _recover_market_cap(info, price=50.0, shares=None) is None


def test_mcap_negative_reported_falls_back_to_computed():
    info = {"marketCap": -100}
    result = _recover_market_cap(info, price=50.0, shares=1_000_000)
    assert result == 50_000_000


def test_mcap_no_cross_check_if_price_or_shares_missing():
    info = {"marketCap": 1_000_000_000}
    # Pas de price → on garde le reported tel quel (pas de cross-check possible).
    result = _recover_market_cap(info, price=None, shares=20_000_000)
    assert result == 1_000_000_000
