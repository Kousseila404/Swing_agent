"""
Tests unitaires pour FallbackMarketProvider.

Couvre le bug "cascade prix stale" du 2026-04-22 : Polygon free tier bloque
/v2/snapshot (403), le provider retourne price=None pour tous, et sans le
fallback YF les SL/TP seraient calibrés sur des prix jusqu'à plusieurs jours
stale. On valide aussi le nouveau mode bypass (use_primary_for_snapshot=False)
ajouté suite à l'audit du 2026-04-23.
"""
from __future__ import annotations

import time

import pandas as pd
import pytest

from data_providers._market_fallback import FallbackMarketProvider
from data_providers.base import (
    LatestPrice,
    MarketDataProviderBase,
    ProviderError,
    ProviderUnavailable,
)


class _StubProvider(MarketDataProviderBase):
    """Provider déterministe : réponse configurable par ticker, compteur d'appels."""

    def __init__(self, name: str, prices: dict[str, float | None], *,
                 raise_exc: Exception | None = None):
        self.name = name
        self._prices = prices
        self._raise = raise_exc
        self.calls: list[list[str]] = []

    def get_daily_history(self, ticker: str, days: int):
        return None

    def get_daily_history_batch(self, tickers, days):
        return {t: None for t in tickers}

    def get_latest_prices(self, tickers):
        self.calls.append(list(tickers))
        if self._raise is not None:
            raise self._raise
        now = time.time()
        return {
            t: LatestPrice(ticker=t, price=self._prices.get(t), fetched_at_epoch=now,
                           source=self.name, as_of=None)
            for t in tickers
        }


# ── Mode classique (use_primary_for_snapshot=True) ─────────────────────────

def test_fallback_primary_all_good_no_fallback_call():
    primary = _StubProvider("primary", {"A": 10.0, "B": 20.0})
    fb = _StubProvider("fb", {"A": 99.0, "B": 99.0})
    composite = FallbackMarketProvider(primary, fb)

    out = composite.get_latest_prices(["A", "B"])

    assert out["A"].price == 10.0
    assert out["B"].price == 20.0
    assert fb.calls == []  # fallback jamais sollicité


def test_fallback_primary_all_none_triggers_fallback_full():
    """Reproduit le bug Polygon 403 : primary retourne price=None partout."""
    primary = _StubProvider("primary", {"A": None, "B": None})
    fb = _StubProvider("fb", {"A": 10.0, "B": 20.0})
    composite = FallbackMarketProvider(primary, fb)

    out = composite.get_latest_prices(["A", "B"])

    assert out["A"].price == 10.0
    assert out["B"].price == 20.0
    assert fb.calls == [["A", "B"]]  # fallback a été appelé avec les 2


def test_fallback_primary_partial_fills_gaps_only():
    primary = _StubProvider("primary", {"A": 10.0, "B": None, "C": 30.0})
    fb = _StubProvider("fb", {"B": 20.0})
    composite = FallbackMarketProvider(primary, fb)

    out = composite.get_latest_prices(["A", "B", "C"])

    assert out["A"].price == 10.0
    assert out["B"].price == 20.0
    assert out["C"].price == 30.0
    assert fb.calls == [["B"]]  # fallback uniquement sur B


def test_fallback_primary_crashes_provider_error_full_fallback():
    primary = _StubProvider("primary", {}, raise_exc=ProviderUnavailable("503"))
    fb = _StubProvider("fb", {"A": 10.0, "B": 20.0})
    composite = FallbackMarketProvider(primary, fb)

    out = composite.get_latest_prices(["A", "B"])

    assert out["A"].price == 10.0
    assert out["B"].price == 20.0
    assert fb.calls == [["A", "B"]]


def test_fallback_primary_crashes_unexpected_full_fallback():
    primary = _StubProvider("primary", {}, raise_exc=RuntimeError("boom"))
    fb = _StubProvider("fb", {"A": 10.0})
    composite = FallbackMarketProvider(primary, fb)

    out = composite.get_latest_prices(["A"])

    assert out["A"].price == 10.0
    assert fb.calls == [["A"]]


def test_fallback_fallback_crashes_returns_primary_as_is():
    primary = _StubProvider("primary", {"A": 10.0, "B": None})
    fb = _StubProvider("fb", {}, raise_exc=ProviderUnavailable("yf down"))
    composite = FallbackMarketProvider(primary, fb)

    out = composite.get_latest_prices(["A", "B"])

    assert out["A"].price == 10.0
    assert out["B"].price is None  # pas de prix à combler, mais on ne crash pas


def test_fallback_fallback_returns_also_none_merges_keep_none():
    primary = _StubProvider("primary", {"A": None})
    fb = _StubProvider("fb", {"A": None})
    composite = FallbackMarketProvider(primary, fb)

    out = composite.get_latest_prices(["A"])

    # Aucun provider n'a de prix → price reste None mais l'entry existe.
    assert out["A"].price is None


def test_fallback_fallback_returns_zero_or_negative_ignored():
    """Garde-fou : un prix ≤ 0 côté fallback ne doit pas écraser un None primary."""
    primary = _StubProvider("primary", {"A": None})
    fb = _StubProvider("fb", {"A": 0.0})
    composite = FallbackMarketProvider(primary, fb)

    out = composite.get_latest_prices(["A"])
    assert out["A"].price is None  # 0.0 rejeté


def test_fallback_empty_tickers_short_circuit():
    primary = _StubProvider("primary", {})
    fb = _StubProvider("fb", {})
    composite = FallbackMarketProvider(primary, fb)

    assert composite.get_latest_prices([]) == {}
    assert primary.calls == []
    assert fb.calls == []


# ── Mode bypass (use_primary_for_snapshot=False, audit 2026-04-23) ─────────

def test_inverted_mode_yf_first_polygon_backup():
    """use_primary_for_snapshot=False : yfinance (fallback) prioritaire,
    Polygon (primary) sert de backup sur les holes — vraie cascade inversée."""
    primary = _StubProvider("primary", {"A": 10.0})  # polygon (backup ici)
    fb = _StubProvider("fb", {"A": 15.0})            # yfinance (primary routing)
    composite = FallbackMarketProvider(primary, fb, use_primary_for_snapshot=False)

    out = composite.get_latest_prices(["A"])

    # YF a répondu → polygon (backup) jamais touché.
    assert out["A"].price == 15.0
    assert primary.calls == []
    assert fb.calls == [["A"]]


def test_inverted_mode_yf_fails_polygon_backup_kicks_in():
    """Résilience : si yfinance trip, Polygon prend le relais."""
    primary = _StubProvider("primary", {"A": 10.0, "B": 20.0})  # polygon backup
    fb = _StubProvider("fb", {}, raise_exc=ProviderUnavailable("YF breaker"))
    composite = FallbackMarketProvider(primary, fb, use_primary_for_snapshot=False)

    out = composite.get_latest_prices(["A", "B"])
    # YF a crashé → Polygon a été appelé en fallback.
    assert out["A"].price == 10.0
    assert out["B"].price == 20.0
    assert primary.calls == [["A", "B"]]


def test_inverted_mode_yf_partial_polygon_fills_holes():
    """yfinance partiel (B None) → Polygon comble B seulement."""
    primary = _StubProvider("primary", {"B": 22.0})   # polygon
    fb = _StubProvider("fb", {"A": 10.0, "B": None})  # yfinance
    composite = FallbackMarketProvider(primary, fb, use_primary_for_snapshot=False)

    out = composite.get_latest_prices(["A", "B"])
    assert out["A"].price == 10.0  # YF OK
    assert out["B"].price == 22.0  # Polygon backup
    assert fb.calls == [["A", "B"]]
    assert primary.calls == [["B"]]  # ciblé sur B uniquement


# ── Pass-through : daily history délégué au primary ────────────────────────

def test_daily_history_unitary_always_delegated_to_primary():
    """get_daily_history (unitaire) reste Polygon quelle que soit la config batch."""
    primary = _StubProvider("primary", {})
    fb = _StubProvider("fb", {})
    series = pd.Series([100.0, 101.0])
    primary.get_daily_history = lambda t, d: series  # type: ignore[assignment]
    fb_series = pd.Series([999.0])
    fb.get_daily_history = lambda t, d: fb_series  # type: ignore[assignment]

    composite = FallbackMarketProvider(
        primary, fb,
        use_primary_for_snapshot=False,
        use_primary_for_daily_batch=False,
    )
    # Call unitaire : toujours primary.
    assert composite.get_daily_history("AAPL", 30) is series


def test_daily_history_batch_primary_when_flag_true():
    primary = _StubProvider("primary", {})
    fb = _StubProvider("fb", {})
    series_p = pd.Series([1.0])
    series_f = pd.Series([2.0])
    primary.get_daily_history_batch = lambda ts, d: {t: series_p for t in ts}  # type: ignore[assignment]
    fb.get_daily_history_batch = lambda ts, d: {t: series_f for t in ts}  # type: ignore[assignment]

    composite = FallbackMarketProvider(
        primary, fb, use_primary_for_daily_batch=True,
    )
    out = composite.get_daily_history_batch(["AAPL"], 30)
    assert out["AAPL"] is series_p


def test_daily_history_batch_fallback_when_flag_false():
    """Par défaut (use_primary_for_daily_batch=False), on route vers yfinance
    pour perf batch. Polygon 500 tickers = 100 min, yfinance = 60 sec."""
    primary = _StubProvider("primary", {})
    fb = _StubProvider("fb", {})
    series_p = pd.Series([1.0])
    series_f = pd.Series([2.0])
    primary.get_daily_history_batch = lambda ts, d: {t: series_p for t in ts}  # type: ignore[assignment]
    fb.get_daily_history_batch = lambda ts, d: {t: series_f for t in ts}  # type: ignore[assignment]

    composite = FallbackMarketProvider(
        primary, fb, use_primary_for_daily_batch=False,
    )
    out = composite.get_daily_history_batch(["AAPL"], 30)
    # YF répond → primary pas touché (series_f, pas series_p).
    assert out["AAPL"] is series_f


def test_daily_history_batch_yf_fails_polygon_backup():
    """Résilience batch : yfinance trip → Polygon prend le relais (lent mais OK)."""
    from data_providers.base import ProviderUnavailable
    primary = _StubProvider("primary", {})
    fb = _StubProvider("fb", {})
    series_p = pd.Series([100.0, 101.0])
    primary.get_daily_history_batch = lambda ts, d: {t: series_p for t in ts}  # type: ignore[assignment]
    def _fb_crash(ts, d):
        raise ProviderUnavailable("YF breaker tripped")
    fb.get_daily_history_batch = _fb_crash  # type: ignore[assignment]

    composite = FallbackMarketProvider(
        primary, fb, use_primary_for_daily_batch=False,
    )
    out = composite.get_daily_history_batch(["AAPL"], 30)
    # YF down → Polygon a répondu avec series_p
    assert out["AAPL"] is series_p


def test_daily_history_batch_yf_partial_polygon_fills():
    """YF répond pour certains tickers seulement → Polygon comble les holes."""
    primary = _StubProvider("primary", {})
    fb = _StubProvider("fb", {})
    series_p = pd.Series([100.0])
    series_f = pd.Series([200.0])
    fb.get_daily_history_batch = lambda ts, d: {  # type: ignore[assignment]
        "A": series_f, "B": None,  # hole sur B
    }
    primary.get_daily_history_batch = lambda ts, d: {t: series_p for t in ts}  # type: ignore[assignment]

    composite = FallbackMarketProvider(
        primary, fb, use_primary_for_daily_batch=False,
    )
    out = composite.get_daily_history_batch(["A", "B"], 30)
    assert out["A"] is series_f  # YF OK
    assert out["B"] is series_p  # Polygon backup
