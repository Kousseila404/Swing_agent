"""Tests data_providers/stooq_provider.py — focus parsing + erreurs réseau."""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from data_providers.stooq_provider import (
    StooqProvider,
    _parse_csv,
    _stooq_ticker,
)


def test_stooq_ticker_format():
    assert _stooq_ticker("AAPL") == "aapl"
    assert _stooq_ticker("BRK-B") == "brk-b"
    assert _stooq_ticker("BRK.B") == "brk-b"


def test_parse_csv_valid():
    raw = (
        "Date,Open,High,Low,Close,Volume\n"
        "2024-01-02,148.21,149.13,148.05,148.93,55020900\n"
        "2024-01-03,149.00,150.00,148.50,149.50,52000000\n"
    )
    s = _parse_csv(raw)
    assert s is not None
    assert len(s) == 2
    assert s.iloc[0] == 148.93
    assert s.iloc[1] == 149.50


def test_parse_csv_empty():
    assert _parse_csv("") is None
    assert _parse_csv("garbage,not,csv") is None


def test_parse_csv_skips_invalid_close():
    raw = (
        "Date,Open,High,Low,Close,Volume\n"
        "2024-01-02,148,149,148,nan,500\n"
        "2024-01-03,149,150,148.5,149.5,520\n"
    )
    s = _parse_csv(raw)
    assert s is not None
    assert len(s) == 1
    assert s.iloc[0] == 149.5


def test_provider_throttle():
    """Le provider applique son throttle (mais 0 dans le test pour speed)."""
    p = StooqProvider(min_delay_seconds=0.0)
    p._last_call = 0.0
    p._throttle()  # no-op


def test_get_daily_history_handles_fetch_failure():
    p = StooqProvider(min_delay_seconds=0.0)
    with patch("data_providers.stooq_provider._fetch_csv", return_value=None):
        result = p.get_daily_history("AAPL", 60)
    assert result is None


def test_get_daily_history_returns_series():
    p = StooqProvider(min_delay_seconds=0.0)
    fake_csv = (
        "Date,Open,High,Low,Close,Volume\n"
        "2024-01-02,148.21,149.13,148.05,148.93,55020900\n"
        "2024-01-03,149.00,150.00,148.50,149.50,52000000\n"
    )
    with patch("data_providers.stooq_provider._fetch_csv", return_value=fake_csv):
        result = p.get_daily_history("AAPL", 60)
    assert isinstance(result, pd.Series)
    assert len(result) == 2


def test_batch_aborts_after_consecutive_failures():
    """10 fails consécutifs → ProviderUnavailable."""
    from data_providers.base import ProviderUnavailable
    p = StooqProvider(min_delay_seconds=0.0)
    with patch("data_providers.stooq_provider._fetch_csv", return_value=None):
        try:
            p.get_daily_history_batch([f"T{i}" for i in range(15)], 60)
            raise AssertionError("Should have raised ProviderUnavailable")
        except ProviderUnavailable:
            pass  # expected
