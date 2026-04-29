"""Tests modules/simfin_bootstrap.py — walk-back logic + edge cases."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from modules.simfin_bootstrap import _ticker_snapshot_at_date


def _gen_history(n_days: int, start_price: float = 100.0, drift: float = 0.0005):
    rng = np.random.default_rng(42)
    rets = rng.normal(drift, 0.015, n_days)
    prices = start_price * np.exp(np.cumsum(rets))
    idx = pd.date_range(end=date.today() - timedelta(days=1), periods=n_days, freq="B")
    return pd.Series(prices, index=idx, name="Close")


def test_snapshot_with_full_history():
    history = _gen_history(300)
    target_date = history.index[-1].date()
    base = {"ticker": "TEST", "sector": "Tech", "current_price": 0}
    snap = _ticker_snapshot_at_date(base, history, target_date)
    assert snap is not None
    assert snap["current_price"] > 0
    assert snap["momentum_return_pct"] is not None
    assert snap["momentum_volatility_pct"] is not None
    assert snap["momentum_high_52w_ratio"] is not None
    assert 0 <= snap["momentum_high_52w_ratio"] <= 1.0


def test_snapshot_short_history_uses_fallback_momentum():
    history = _gen_history(150)
    target_date = history.index[-1].date()
    base = {"ticker": "T", "current_price": 0}
    snap = _ticker_snapshot_at_date(base, history, target_date)
    assert snap is not None
    # 150 jours → fallback 6M-1M (≥ 126 days disponibles)
    assert snap["momentum_return_pct"] is not None


def test_snapshot_too_short_history_returns_none():
    history = _gen_history(10)
    target_date = history.index[-1].date()
    snap = _ticker_snapshot_at_date({"ticker": "T"}, history, target_date)
    assert snap is None


def test_snapshot_walks_back_in_time():
    """Le snapshot au jour J-100 doit refléter le prix au jour J-100, pas le prix actuel."""
    history = _gen_history(300)
    target_date = (history.index[-100]).date()
    snap = _ticker_snapshot_at_date({"ticker": "T"}, history, target_date)
    assert snap is not None
    expected_price = float(history[history.index <= pd.Timestamp(target_date)].iloc[-1])
    assert snap["current_price"] == round(expected_price, 4)


def test_snapshot_none_for_history_with_zero_price():
    """Si la dernière barre est ≤ 0 → None."""
    base = pd.Series([100.0] * 30 + [0.0],
                     index=pd.date_range(end=date.today() - timedelta(days=1),
                                         periods=31, freq="B"))
    target_date = base.index[-1].date()
    snap = _ticker_snapshot_at_date({"ticker": "T"}, base, target_date)
    assert snap is None
