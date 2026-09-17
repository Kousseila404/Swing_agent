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
    monkeypatch.setattr(market, "_ALPACA_CLOCK_CACHE", None)
    assert market.is_market_hours() is False


def test_is_market_hours_caches_alpaca_clock(monkeypatch):
    """Le clock Alpaca n'est interrogé qu'une fois par fenêtre TTL."""
    from modules import broker_gateway

    calls = {"n": 0}

    class _Clock:
        is_open = True

    class _Client:
        def get_clock(self):
            calls["n"] += 1
            return _Clock()

    class _FakeAlpaca(broker_gateway.AlpacaBroker):
        def __init__(self):  # pas d'exigence de clés API
            pass

        def _get_client(self):
            return _Client()

    monkeypatch.setattr(broker_gateway, "get_broker", lambda **_kw: _FakeAlpaca())
    monkeypatch.setattr(market, "_ALPACA_CLOCK_CACHE", None)

    assert market.is_market_hours() is True
    assert market.is_market_hours() is True
    assert calls["n"] == 1

    # TTL expiré → nouvel appel réseau.
    monkeypatch.setattr(market, "_ALPACA_CLOCK_CACHE", (True, 0.0))
    assert market.is_market_hours() is True
    assert calls["n"] == 2


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
# get_historical_price / get_historical_fx_rate — Upgrade 4 (journal
# d'exécution) : prix/FX de référence reconstruits a posteriori.
# ─────────────────────────────────────────────────────────────────

class _MockHistTicker:
    """Simule yf.Ticker().history(start=, end=, interval=) avec des barres
    Close à des timestamps contrôlés."""
    def __init__(self, bars: list[tuple[str, float]] | None = None, raise_error: bool = False):
        self._bars = bars or []
        self._raise = raise_error
        self.calls: list[dict] = []

    def history(self, **kw) -> pd.DataFrame:
        self.calls.append(kw)
        if self._raise:
            raise RuntimeError("simulated network error")
        if not self._bars:
            return pd.DataFrame(columns=["Close"])
        index = pd.DatetimeIndex([pd.Timestamp(ts) for ts, _ in self._bars])
        return pd.DataFrame({"Close": [c for _, c in self._bars]}, index=index)


def test_get_historical_price_rejects_naive_datetime():
    with pytest.raises(ValueError):
        market.get_historical_price("CNC", datetime(2026, 8, 19, 9, 50))


def test_get_historical_price_recent_uses_intraday(monkeypatch):
    at = datetime.now(UTC) - timedelta(days=2)
    mock = _MockHistTicker(bars=[(at.isoformat(), 64.02)])
    monkeypatch.setattr(market.yf, "Ticker", lambda _s: mock)
    price, resolution = market.get_historical_price("CNC", at)
    assert price == 64.02
    assert resolution == "intraday"
    assert mock.calls[0]["interval"] == "1m"


def test_get_historical_price_old_fill_skips_intraday_uses_daily_close(monkeypatch):
    """Fill de plus de ~30j : la fenêtre intraday yfinance n'existe plus,
    un seul fetch (daily) doit être tenté, pas deux."""
    at = datetime.now(UTC) - timedelta(days=90)
    calls = {"n": 0}
    def _factory(_s):
        calls["n"] += 1
        return _MockHistTicker(bars=[(at.isoformat(), 55.0)])
    monkeypatch.setattr(market.yf, "Ticker", _factory)
    price, resolution = market.get_historical_price("CNC", at)
    assert price == 55.0
    assert resolution == "daily_close"
    assert calls["n"] == 1


def test_get_historical_price_intraday_empty_falls_back_to_daily(monkeypatch):
    at = datetime.now(UTC) - timedelta(days=2)
    calls = {"n": 0}
    def _factory(_s):
        calls["n"] += 1
        if calls["n"] == 1:
            return _MockHistTicker(bars=[])  # intraday vide
        return _MockHistTicker(bars=[(at.isoformat(), 60.0)])  # daily
    monkeypatch.setattr(market.yf, "Ticker", _factory)
    price, resolution = market.get_historical_price("CNC", at)
    assert price == 60.0
    assert resolution == "daily_close"
    assert calls["n"] == 2


def test_get_historical_price_all_fail_returns_none(monkeypatch):
    at = datetime.now(UTC) - timedelta(days=2)
    monkeypatch.setattr(market.yf, "Ticker", lambda _s: _MockHistTicker(raise_error=True))
    price, resolution = market.get_historical_price("CNC", at)
    assert price is None
    assert resolution == "daily_close"


def test_get_historical_fx_rate_rejects_naive_datetime():
    with pytest.raises(ValueError):
        market.get_historical_fx_rate("EURUSD=X", datetime(2026, 8, 19, 9, 50))


def test_get_historical_fx_rate_same_day(monkeypatch):
    at = datetime.now(UTC) - timedelta(days=5)
    mock = _MockHistTicker(bars=[(at.isoformat(), 1.09)])
    monkeypatch.setattr(market.yf, "Ticker", lambda _s: mock)
    rate, is_approx = market.get_historical_fx_rate("EURUSD=X", at)
    assert rate == 1.09
    assert is_approx is False


def test_get_historical_fx_rate_holiday_falls_back_to_widened_window(monkeypatch):
    """Jour férié FX / trou Yahoo pile au jour du fill : repli sur une
    fenêtre élargie, taux marqué approximatif."""
    at = datetime.now(UTC) - timedelta(days=5)
    nearest = at - timedelta(days=3)
    calls = {"n": 0}
    def _factory(_s):
        calls["n"] += 1
        if calls["n"] == 1:
            return _MockHistTicker(bars=[])  # jour même : trou FX
        return _MockHistTicker(bars=[(nearest.isoformat(), 1.08)])
    monkeypatch.setattr(market.yf, "Ticker", _factory)
    rate, is_approx = market.get_historical_fx_rate("EURUSD=X", at)
    assert rate == 1.08
    assert is_approx is True
    assert calls["n"] == 2


def test_get_historical_fx_rate_all_fail_returns_none(monkeypatch):
    at = datetime.now(UTC) - timedelta(days=5)
    monkeypatch.setattr(market.yf, "Ticker", lambda _s: _MockHistTicker(raise_error=True))
    rate, is_approx = market.get_historical_fx_rate("EURUSD=X", at)
    assert rate is None
    assert is_approx is False
