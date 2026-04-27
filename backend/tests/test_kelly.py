"""Tests kelly_rolling — sizing dynamique bas la performance récente."""
from __future__ import annotations

import pandas as pd
import pytest

from modules.risk import kelly_rolling


def _mk_trades(records):
    """records: list of (status, entry, exit, direction)."""
    return pd.DataFrame(records, columns=["Status", "Entry", "Exit_Price", "Direction"])


class TestKellyRolling:
    def test_insufficient_trades_returns_default(self):
        df = _mk_trades([("WIN", 100.0, 105.0, "LONG")] * 3)
        result = kelly_rolling(df, min_trades=10)
        assert result == pytest.approx(0.0025)  # config.RISK_PER_TRADE

    def test_empty_dataframe_returns_default(self):
        df = _mk_trades([])
        assert kelly_rolling(df) == pytest.approx(0.0025)

    def test_all_wins_increases_size(self):
        df = _mk_trades([("WIN", 100.0, 110.0, "LONG")] * 15)
        result = kelly_rolling(df, min_trades=10)
        assert result > 0.0025
        assert result <= 0.01  # plafonné à 1%

    def test_mixed_profitable_positive_edge(self):
        # 12 wins @ +10%, 8 losses @ -5% — edge clairement positif
        records = [("WIN", 100.0, 110.0, "LONG")] * 12 + [("LOSS", 100.0, 95.0, "LONG")] * 8
        df = _mk_trades(records)
        result = kelly_rolling(df, min_trades=10, fraction=0.25)
        assert 0.001 <= result <= 0.01

    def test_negative_edge_reduces_size(self):
        # 3 wins @ +5%, 17 losses @ -10% — edge négatif
        records = [("WIN", 100.0, 105.0, "LONG")] * 3 + [("LOSS", 100.0, 90.0, "LONG")] * 17
        df = _mk_trades(records)
        result = kelly_rolling(df, min_trades=10)
        # Doit être réduit en deçà du défaut (0.0025 * 0.5 = 0.00125)
        assert result < 0.0025

    def test_result_is_bounded(self):
        df = _mk_trades([("WIN", 100.0, 200.0, "LONG")] * 20)
        result = kelly_rolling(df, min_trades=10)
        assert 0.001 <= result <= 0.01

    def test_handles_short_direction(self):
        # SHORT gagnant : entry 100, exit 90 → +10%
        records = [("WIN", 100.0, 90.0, "SHORT")] * 12 + [("LOSS", 100.0, 105.0, "SHORT")] * 8
        df = _mk_trades(records)
        result = kelly_rolling(df, min_trades=10)
        assert 0.001 <= result <= 0.01
