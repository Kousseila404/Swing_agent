"""Tests modules/correlation_check.py."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from modules.correlation_check import (
    CorrelationResult,
    compute_correlation,
    downsize_over_correlated,
)


class _StubProvider:
    name = "stub"

    def __init__(self, prices: dict[str, pd.Series]):
        self._prices = prices

    def get_daily_history_batch(
        self, tickers: list[str], days: int,
    ) -> dict[str, pd.Series | None]:
        return {t: self._prices.get(t) for t in tickers}

    def get_daily_history(self, ticker: str, days: int):
        return self._prices.get(ticker)


def _gen_correlated(seed: int, n: int = 80, base_drift: float = 0.0005):
    rng = np.random.default_rng(seed)
    # facteur commun très fort
    factor = rng.normal(0, 0.01, n)
    return factor + rng.normal(base_drift, 0.001, n)


def _gen_independent(seed: int, n: int = 80):
    rng = np.random.default_rng(seed)
    return rng.normal(0.0005, 0.015, n)


def _series_from_returns(returns) -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=len(returns), freq="B")
    prices = 100 * np.exp(np.cumsum(returns))
    return pd.Series(prices, index=idx)


def test_basket_too_small_returns_not_applied():
    res = compute_correlation(["A", "B"], _StubProvider({}))
    assert res.applied is False
    assert res.reason == "basket_too_small"


def test_no_provider_fail_open():
    res = compute_correlation(["A", "B", "C", "D"], None)
    assert res.applied is False
    assert res.reason == "no_market_provider"


def test_detects_high_correlation():
    common = _gen_correlated(42)
    prices = {
        "A": _series_from_returns(common + 0.0001),
        "B": _series_from_returns(common + 0.0002),
        "C": _series_from_returns(common + 0.0003),
        "D": _series_from_returns(common + 0.0004),
    }
    res = compute_correlation(list(prices.keys()), _StubProvider(prices),
                              max_avg_corr=0.4)
    assert res.applied is True
    assert res.avg_corr_basket is not None and res.avg_corr_basket > 0.7
    assert len(res.over_correlated) >= 3


def test_detects_low_correlation():
    prices = {
        "A": _series_from_returns(_gen_independent(1)),
        "B": _series_from_returns(_gen_independent(2)),
        "C": _series_from_returns(_gen_independent(3)),
        "D": _series_from_returns(_gen_independent(4)),
    }
    res = compute_correlation(list(prices.keys()), _StubProvider(prices),
                              max_avg_corr=0.55)
    assert res.applied is True
    assert res.avg_corr_basket is not None and res.avg_corr_basket < 0.4
    assert res.over_correlated == []


def test_downsize_over_correlated_redistributes():
    weights = {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}
    res = CorrelationResult(
        applied=True,
        avg_corr_basket=0.8,
        max_avg_corr_threshold=0.5,
        per_ticker_avg={"A": 0.85, "B": 0.85, "C": 0.20, "D": 0.20},
        over_correlated=["A", "B"],
        n_tickers=4,
    )
    new_w, diag = downsize_over_correlated(weights, res, haircut=0.5)
    assert diag["applied"] is True
    # A et B doivent être réduits.
    assert new_w["A"] < 0.25 and new_w["B"] < 0.25
    # C et D doivent recevoir le surplus.
    assert new_w["C"] > 0.25 and new_w["D"] > 0.25
    # Total doit rester ≈ 1.
    assert abs(sum(new_w.values()) - 1.0) < 1e-9


def test_downsize_no_over_correlated_passthrough():
    weights = {"A": 0.5, "B": 0.5}
    res = CorrelationResult(
        applied=True, avg_corr_basket=0.2, n_tickers=2,
    )
    new_w, diag = downsize_over_correlated(weights, res)
    assert new_w == weights
    assert diag["applied"] is False


def test_provider_failure_fail_open():
    class _FailingProvider:
        name = "fail"
        def get_daily_history_batch(self, tickers, days):
            raise RuntimeError("provider down")
        def get_daily_history(self, ticker, days):
            raise RuntimeError("nope")

    res = compute_correlation(["A", "B", "C", "D"], _FailingProvider())
    assert res.applied is False
    assert res.reason and "history_failed" in res.reason
