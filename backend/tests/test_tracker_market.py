"""Tests unitaires — modules/tracker/market.py

Couvre :
  - is_market_hours : basé sur pytz NY (lun-ven 09:30-16:00)
  - get_current_price : Alpaca success / Alpaca fail → yf intraday
                        / yf fail → yf EOD fallback / tout échoue → None
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from modules.tracker import market

# ─────────────────────────────────────────────────────────────────
# is_market_hours — basé sur l'heure réelle NY
# ─────────────────────────────────────────────────────────────────

def test_is_market_hours_returns_bool():
    """is_market_hours retourne toujours un bool (même en cas d'exception)."""
    result = market.is_market_hours()
    assert isinstance(result, bool)


def test_is_market_hours_fallbacks_to_false_on_error(monkeypatch):
    """Si zoneinfo + pytz indisponibles + Alpaca KO → False (conservateur).
    Phase 7 audit : utilise zoneinfo (3.9+) avec pytz fallback ; le test
    doit casser les deux + Alpaca."""
    import builtins
    real_import = builtins.__import__
    def _mock_import(name, *a, **k):
        if name in ("pytz", "zoneinfo"):
            raise ImportError(f"{name} missing")
        if "broker_gateway" in name or "AlpacaBroker" in name:
            raise ImportError(f"{name} missing")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", _mock_import)
    assert market.is_market_hours() is False


# ─────────────────────────────────────────────────────────────────
# get_current_price — chaîne de fallbacks
# ─────────────────────────────────────────────────────────────────

class _MockTicker:
    """Simule yf.Ticker avec un historique contrôlé."""
    def __init__(self, close_price: float | None = None, raise_error: bool = False):
        self._price = close_price
        self._raise = raise_error

    def history(self, **_kw) -> pd.DataFrame:
        if self._raise:
            raise RuntimeError("simulated network error")
        if self._price is None:
            return pd.DataFrame(columns=["Close"])
        # DatetimeIndex tz-aware — reflète le format réel de yfinance,
        # nécessaire depuis que get_current_price_detailed lit hist.index[-1]
        # comme timestamp `as_of` de la source.
        return pd.DataFrame(
            {"Close": [self._price]}, index=[pd.Timestamp.now(tz="UTC")]
        )


def test_get_current_price_alpaca_success(monkeypatch):
    """BROKER_MODE=alpaca + Alpaca renvoie un prix valide → pas de yf."""
    import config
    monkeypatch.setattr(config, "BROKER_MODE", "alpaca", raising=False)
    import modules.alpaca_data as ad
    monkeypatch.setattr(ad, "get_latest_price", lambda _t: 123.45)
    assert market.get_current_price("AAPL") == 123.45


def test_get_current_price_alpaca_fails_fallbacks_to_yf(monkeypatch):
    """Alpaca retourne None → fallback sur yfinance."""
    import config
    monkeypatch.setattr(config, "BROKER_MODE", "alpaca", raising=False)
    import modules.alpaca_data as ad
    monkeypatch.setattr(ad, "get_latest_price", lambda _t: None)
    monkeypatch.setattr(market.yf, "Ticker", lambda _t: _MockTicker(close_price=99.0))
    assert market.get_current_price("AAPL") == 99.0


def test_get_current_price_yf_paper_mode(monkeypatch):
    """BROKER_MODE=paper → direct yfinance, pas d'appel Alpaca."""
    import config
    monkeypatch.setattr(config, "BROKER_MODE", "paper", raising=False)
    monkeypatch.setattr(market.yf, "Ticker", lambda _t: _MockTicker(close_price=150.0))
    assert market.get_current_price("AAPL") == 150.0


def test_get_current_price_yf_empty_returns_none(monkeypatch):
    """yfinance retourne un historique vide → None."""
    import config
    monkeypatch.setattr(config, "BROKER_MODE", "paper", raising=False)
    monkeypatch.setattr(market.yf, "Ticker", lambda _t: _MockTicker(close_price=None))
    assert market.get_current_price("AAPL") is None


def test_get_current_price_intraday_fails_eod_fallback(monkeypatch):
    """Marché ouvert + intraday échoue → fallback sur EOD."""
    import config
    monkeypatch.setattr(config, "BROKER_MODE", "paper", raising=False)
    monkeypatch.setattr(market, "is_market_hours", lambda: True)

    call_count = {"n": 0}
    def _ticker_factory(_t):
        call_count["n"] += 1
        # 1er appel (intraday) raise ; 2ème (EOD) renvoie un prix
        if call_count["n"] == 1:
            return _MockTicker(raise_error=True)
        return _MockTicker(close_price=98.5)

    monkeypatch.setattr(market.yf, "Ticker", _ticker_factory)
    assert market.get_current_price("AAPL") == 98.5


def test_get_current_price_all_fail_returns_none(monkeypatch):
    """Alpaca + intraday + EOD tous échouent → None."""
    import config
    monkeypatch.setattr(config, "BROKER_MODE", "paper", raising=False)
    monkeypatch.setattr(market, "is_market_hours", lambda: True)
    monkeypatch.setattr(market.yf, "Ticker", lambda _t: _MockTicker(raise_error=True))
    assert market.get_current_price("AAPL") is None


# ─────────────────────────────────────────────────────────────────
# get_current_price_detailed — expose le timestamp `as_of` de la source
# (diagnostic staleness FMX/HRTG/PSX, sans couche de cache).
# ─────────────────────────────────────────────────────────────────

def test_get_current_price_detailed_returns_bar_timestamp_as_of(monkeypatch):
    import config
    monkeypatch.setattr(config, "BROKER_MODE", "paper", raising=False)
    monkeypatch.setattr(market.yf, "Ticker", lambda _t: _MockTicker(close_price=150.0))
    price, as_of, fetched_at = market.get_current_price_detailed("AAPL")
    assert price == 150.0
    assert as_of is not None
    assert fetched_at is not None


def test_get_current_price_detailed_use_alpaca_false_skips_alpaca(monkeypatch):
    """Audit 2026-08-21 : Alpaca gratuit = IEX seul, dérive vs NBBO sur les
    tickers peu liquides. use_alpaca=False doit forcer yfinance même en
    BROKER_MODE=alpaca (cas my_portfolio, book non exécuté via Alpaca)."""
    import config
    monkeypatch.setattr(config, "BROKER_MODE", "alpaca", raising=False)
    import modules.alpaca_data as ad
    called = {"alpaca": False}
    def _alpaca_price(_t):
        called["alpaca"] = True
        return 999.0
    monkeypatch.setattr(ad, "get_latest_price", _alpaca_price)
    monkeypatch.setattr(market.yf, "Ticker", lambda _t: _MockTicker(close_price=150.0))

    price, _as_of, _fetched_at = market.get_current_price_detailed("FMX", use_alpaca=False)

    assert called["alpaca"] is False
    assert price == 150.0


def test_get_current_price_detailed_none_when_empty(monkeypatch):
    import config
    monkeypatch.setattr(config, "BROKER_MODE", "paper", raising=False)
    monkeypatch.setattr(market.yf, "Ticker", lambda _t: _MockTicker(close_price=None))
    price, as_of, fetched_at = market.get_current_price_detailed("AAPL")
    assert price is None
    assert as_of is None
    assert fetched_at is not None  # on sait toujours QUAND on a tenté le fetch


# ─────────────────────────────────────────────────────────────────
# get_fx_rate — taux de change live, caché 5 min
# ─────────────────────────────────────────────────────────────────

def test_get_fx_rate_returns_live_rate(monkeypatch):
    market._FX_CACHE.clear()
    monkeypatch.setattr(market.yf, "Ticker", lambda _t: _MockTicker(close_price=1.1677))
    assert market.get_fx_rate("EURUSD=X") == 1.1677


def test_get_fx_rate_caches_within_ttl(monkeypatch):
    market._FX_CACHE.clear()
    calls = {"n": 0}
    def _ticker_factory(_t):
        calls["n"] += 1
        return _MockTicker(close_price=1.1677 + calls["n"])
    monkeypatch.setattr(market.yf, "Ticker", _ticker_factory)
    first = market.get_fx_rate("EURUSD=X")
    second = market.get_fx_rate("EURUSD=X")
    assert first == second  # 2e appel sert le cache, pas un nouveau fetch
    assert calls["n"] == 1


def test_get_fx_rate_falls_back_to_stale_cache_on_error(monkeypatch):
    market._FX_CACHE.clear()
    monkeypatch.setattr(market.yf, "Ticker", lambda _t: _MockTicker(close_price=1.1677))
    first = market.get_fx_rate("EURUSD=X")
    assert first == 1.1677

    # Force l'expiration du cache puis simule un échec réseau -> doit
    # retourner le dernier taux connu plutôt que None.
    market._FX_CACHE["EURUSD=X"] = (first, 0.0)
    monkeypatch.setattr(market.yf, "Ticker", lambda _t: _MockTicker(raise_error=True))
    assert market.get_fx_rate("EURUSD=X") == 1.1677


def test_get_fx_rate_returns_none_when_never_fetched_and_fails(monkeypatch):
    market._FX_CACHE.clear()
    monkeypatch.setattr(market.yf, "Ticker", lambda _t: _MockTicker(raise_error=True))
    assert market.get_fx_rate("EURUSD=X") is None


# ─────────────────────────────────────────────────────────────────
# get_historical_price / get_historical_fx_rate — Upgrade 4
# (journal d'exécution my_portfolio, reconstruction de prix/FX passés)
# ─────────────────────────────────────────────────────────────────

class _MockHistTicker:
    """Simule yf.Ticker(...).history(start=, end=, interval=).

    `intraday_rows`/`daily_rows` : listes de (pd.Timestamp tz-aware, close)
    — le choix de la liste servie dépend de l'`interval` demandé par
    l'appelant, ce qui permet de distinguer la branche intraday de la
    branche daily_close dans un même test sans deux mocks séparés.
    """
    def __init__(self, intraday_rows=None, daily_rows=None, raise_error=False):
        self._intraday = intraday_rows or []
        self._daily = daily_rows or []
        self._raise = raise_error

    def history(self, interval="1d", **_kw):
        if self._raise:
            raise RuntimeError("simulated network error")
        rows = self._intraday if interval == "1m" else self._daily
        if not rows:
            return pd.DataFrame(columns=["Close"])
        return pd.DataFrame(
            {"Close": [c for _, c in rows]},
            index=pd.DatetimeIndex([ts for ts, _ in rows]),
        )


def test_get_historical_price_rejects_naive_datetime():
    with pytest.raises(ValueError):
        market.get_historical_price("AAPL", datetime(2026, 8, 19, 9, 50))


def test_get_historical_fx_rate_rejects_naive_datetime():
    with pytest.raises(ValueError):
        market.get_historical_fx_rate("EURUSD=X", datetime(2026, 8, 19, 9, 50))


def test_get_historical_price_intraday_within_30_days_picks_closest_bar(monkeypatch):
    at = datetime.now(UTC) - timedelta(days=1)
    rows = [
        (pd.Timestamp(at - timedelta(minutes=10)), 100.0),
        (pd.Timestamp(at + timedelta(minutes=1)), 101.5),  # la + proche
        (pd.Timestamp(at + timedelta(minutes=12)), 103.0),
    ]
    monkeypatch.setattr(
        market.yf, "Ticker", lambda _t: _MockHistTicker(intraday_rows=rows)
    )
    price, resolution = market.get_historical_price("AAPL", at)
    assert price == 101.5
    assert resolution == "intraday"


def test_get_historical_price_older_than_30_days_skips_intraday(monkeypatch):
    at = datetime.now(UTC) - timedelta(days=60)
    daily_rows = [(pd.Timestamp(at - timedelta(days=1)), 64.0)]
    calls = {"intervals": []}

    def _ticker_factory(_t):
        return _MockHistTicker(
            intraday_rows=[(pd.Timestamp(at), 999.0)],  # ne doit JAMAIS être lu
            daily_rows=daily_rows,
        )

    monkeypatch.setattr(market.yf, "Ticker", _ticker_factory)
    price, resolution = market.get_historical_price("AAPL", at)
    assert price == 64.0
    assert resolution == "daily_close"


def test_get_historical_price_intraday_empty_falls_back_to_daily_close(monkeypatch):
    at = datetime.now(UTC) - timedelta(days=2)
    daily_rows = [(pd.Timestamp(at - timedelta(days=1)), 88.0)]
    monkeypatch.setattr(
        market.yf, "Ticker", lambda _t: _MockHistTicker(daily_rows=daily_rows)
    )
    price, resolution = market.get_historical_price("AAPL", at)
    assert price == 88.0
    assert resolution == "daily_close"


def test_get_historical_price_daily_close_never_uses_future_bar(monkeypatch):
    """Pas de lookahead : une clôture postérieure à `at` doit être ignorée
    même si c'est la seule barre renvoyée par le mock."""
    at = datetime.now(UTC) - timedelta(days=2)
    daily_rows = [(pd.Timestamp(at + timedelta(days=1)), 999.0)]
    monkeypatch.setattr(
        market.yf, "Ticker", lambda _t: _MockHistTicker(daily_rows=daily_rows)
    )
    price, resolution = market.get_historical_price("AAPL", at)
    assert price is None
    assert resolution == "daily_close"


def test_get_historical_price_no_data_anywhere_returns_none(monkeypatch):
    at = datetime.now(UTC) - timedelta(days=2)
    monkeypatch.setattr(market.yf, "Ticker", lambda _t: _MockHistTicker())
    price, resolution = market.get_historical_price("AAPL", at)
    assert price is None
    assert resolution == "daily_close"


def test_get_historical_price_fail_open_on_exception(monkeypatch):
    at = datetime.now(UTC) - timedelta(days=2)
    monkeypatch.setattr(
        market.yf, "Ticker", lambda _t: _MockHistTicker(raise_error=True)
    )
    price, resolution = market.get_historical_price("AAPL", at)
    assert price is None
    assert resolution == "daily_close"


def test_get_historical_fx_rate_exact_date(monkeypatch):
    at = datetime.now(UTC) - timedelta(days=2)
    daily_rows = [(pd.Timestamp(at), 1.09)]
    monkeypatch.setattr(
        market.yf, "Ticker", lambda _t: _MockHistTicker(daily_rows=daily_rows)
    )
    rate, date_iso = market.get_historical_fx_rate("EURUSD=X", at)
    assert rate == 1.09
    assert date_iso == at.date().isoformat()


def test_get_historical_fx_rate_falls_back_to_nearest_past_trading_day(monkeypatch):
    """Jour férié FX : pas de barre exactement à `at`, mais une barre plus
    ancienne dans la fenêtre de 7 jours -> repli, `date_iso` != date de `at`
    permet à l'appelant de détecter l'approximation."""
    at = datetime.now(UTC) - timedelta(days=2)
    stale_day = at - timedelta(days=3)
    daily_rows = [(pd.Timestamp(stale_day), 1.07)]
    monkeypatch.setattr(
        market.yf, "Ticker", lambda _t: _MockHistTicker(daily_rows=daily_rows)
    )
    rate, date_iso = market.get_historical_fx_rate("EURUSD=X", at)
    assert rate == 1.07
    assert date_iso == stale_day.date().isoformat()
    assert date_iso != at.date().isoformat()


def test_get_historical_fx_rate_no_data_returns_none_none(monkeypatch):
    at = datetime.now(UTC) - timedelta(days=2)
    monkeypatch.setattr(market.yf, "Ticker", lambda _t: _MockHistTicker())
    assert market.get_historical_fx_rate("EURUSD=X", at) == (None, None)


def test_get_historical_fx_rate_fail_open_on_exception(monkeypatch):
    at = datetime.now(UTC) - timedelta(days=2)
    monkeypatch.setattr(
        market.yf, "Ticker", lambda _t: _MockHistTicker(raise_error=True)
    )
    assert market.get_historical_fx_rate("EURUSD=X", at) == (None, None)
