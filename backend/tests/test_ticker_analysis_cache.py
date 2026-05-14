"""Tests pour le cache TTL price_action dans routers.ticker_analysis."""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from routers import ticker_analysis


@pytest.fixture(autouse=True)
def _clear_cache():
    ticker_analysis._clear_price_action_cache()
    yield
    ticker_analysis._clear_price_action_cache()


def _fake_history(rows=300):
    """Fabrique un DataFrame OHLCV suffisant pour MA200 + 252j 52w."""
    return pd.DataFrame({"Close": [100.0 + i for i in range(rows)]})


class TestPriceActionCache:
    def test_first_call_hits_load(self):
        """Premier appel : load_price_history est appelé."""
        with patch.object(
            ticker_analysis, "_load_price_history",
            return_value=_fake_history(),
        ) as mock_load:
            pa, hist = ticker_analysis._compute_price_action("AAPL")
        assert mock_load.call_count == 1
        assert pa["available"] is True
        assert hist is not None

    def test_second_call_uses_cache(self):
        """Deuxième appel dans la fenêtre TTL : pas de re-load."""
        with patch.object(
            ticker_analysis, "_load_price_history",
            return_value=_fake_history(),
        ) as mock_load:
            ticker_analysis._compute_price_action("AAPL")
            ticker_analysis._compute_price_action("AAPL")
            ticker_analysis._compute_price_action("AAPL")
        # Un seul load malgré 3 appels.
        assert mock_load.call_count == 1

    def test_different_tickers_get_separate_entries(self):
        """Le cache est keyé par ticker — pas de collision."""
        with patch.object(
            ticker_analysis, "_load_price_history",
            return_value=_fake_history(),
        ) as mock_load:
            ticker_analysis._compute_price_action("AAPL")
            ticker_analysis._compute_price_action("MSFT")
            ticker_analysis._compute_price_action("NVDA")
        assert mock_load.call_count == 3

    def test_ttl_expiry_triggers_reload(self, monkeypatch):
        """Après expiration TTL, on re-fetch."""
        # Force un TTL très court pour ce test.
        monkeypatch.setattr(ticker_analysis, "_PRICE_ACTION_TTL_SECONDS", 0)
        with patch.object(
            ticker_analysis, "_load_price_history",
            return_value=_fake_history(),
        ) as mock_load:
            ticker_analysis._compute_price_action("AAPL")
            ticker_analysis._compute_price_action("AAPL")
        assert mock_load.call_count == 2

    def test_lru_trim_when_max_exceeded(self, monkeypatch):
        """Si le cache dépasse max, on jette les 25 % plus anciens."""
        monkeypatch.setattr(ticker_analysis, "_PRICE_ACTION_CACHE_MAX", 4)
        with patch.object(
            ticker_analysis, "_load_price_history",
            return_value=_fake_history(),
        ):
            # Remplir 4 entrées → arrivée de la 5e doit déclencher trim de 1.
            for tk in ["A", "B", "C", "D"]:
                ticker_analysis._compute_price_action(tk)
            ticker_analysis._compute_price_action("E")
        # 4 - 1 (trim 25%) + 1 (nouveau) = 4 max
        assert len(ticker_analysis._PRICE_ACTION_CACHE) <= 4
        # E doit être présent
        assert "E" in ticker_analysis._PRICE_ACTION_CACHE

    def test_returns_unavailable_on_empty_history(self):
        """Si load_price_history retourne None → 'available': False, pas crash."""
        with patch.object(
            ticker_analysis, "_load_price_history", return_value=None,
        ):
            pa, hist = ticker_analysis._compute_price_action("FAKE")
        assert pa == {"available": False}
        assert hist is None
