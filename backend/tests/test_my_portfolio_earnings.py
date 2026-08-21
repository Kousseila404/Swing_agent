"""Tests — modules/my_portfolio_earnings.py (Upgrade 2, calendrier earnings).

Toutes les sources réseau (Finnhub, yfinance) sont mockées — jamais de vrai
appel dans ces tests (pattern `_MockTicker`/monkeypatch déjà utilisé dans
test_tracker_market.py et test_finnhub_provider.py). Le cache disque est
redirigé vers `tmp_path` pour ne jamais toucher `data/.my_portfolio_earnings_cache.json`
ni polluer les autres tests.
"""
from __future__ import annotations

import time

import pytest

from modules import my_portfolio_earnings as earnings


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(earnings, "_CACHE_PATH", tmp_path / ".my_portfolio_earnings_cache.json")


class _FakeFinnhubData:
    def __init__(self, next_earnings_date=None):
        self.next_earnings_date = next_earnings_date


class _FakeFinnhubProvider:
    """Stub de FinnhubProvider — pas d'instanciation réseau, pas de clé requise."""
    configured = True
    next_date: str | None = None
    raises = False

    @classmethod
    def is_configured(cls) -> bool:
        return cls.configured

    def get_revisions_and_earnings(self, ticker, use_cache=True):
        if _FakeFinnhubProvider.raises:
            raise RuntimeError("simulated finnhub failure")
        return _FakeFinnhubData(next_earnings_date=_FakeFinnhubProvider.next_date)


@pytest.fixture(autouse=True)
def _reset_fake_finnhub():
    _FakeFinnhubProvider.configured = True
    _FakeFinnhubProvider.next_date = None
    _FakeFinnhubProvider.raises = False
    yield
    _FakeFinnhubProvider.configured = True
    _FakeFinnhubProvider.next_date = None
    _FakeFinnhubProvider.raises = False


# ─────────────────────────────────────────────────────────────────
# Cache disque bas niveau
# ─────────────────────────────────────────────────────────────────

def test_load_cache_missing_file_returns_empty_dict():
    assert earnings._load_cache() == {}


def test_save_then_load_cache_roundtrip():
    earnings._save_cache({"AAPL": {"next_earnings_date": "2026-11-01"}})
    assert earnings._load_cache() == {"AAPL": {"next_earnings_date": "2026-11-01"}}


def test_load_cache_corrupted_file_fails_open(monkeypatch):
    earnings._CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    earnings._CACHE_PATH.write_text("{not valid json", encoding="utf-8")
    assert earnings._load_cache() == {}


# ─────────────────────────────────────────────────────────────────
# _fetch_finnhub / _fetch_yfinance — fail-open par source
# ─────────────────────────────────────────────────────────────────

def test_fetch_finnhub_returns_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(earnings, "FinnhubProvider", _FakeFinnhubProvider)
    _FakeFinnhubProvider.configured = False
    assert earnings._fetch_finnhub("AAPL") is None


def test_fetch_finnhub_returns_date_when_available(monkeypatch):
    monkeypatch.setattr(earnings, "FinnhubProvider", _FakeFinnhubProvider)
    _FakeFinnhubProvider.next_date = "2026-11-05"
    assert earnings._fetch_finnhub("AAPL") == "2026-11-05"


def test_fetch_finnhub_fails_open_on_exception(monkeypatch):
    monkeypatch.setattr(earnings, "FinnhubProvider", _FakeFinnhubProvider)
    _FakeFinnhubProvider.raises = True
    assert earnings._fetch_finnhub("AAPL") is None


def test_fetch_yfinance_extracts_next_earnings_date(monkeypatch):
    monkeypatch.setattr(
        earnings, "_scrape_revisions_and_earnings",
        lambda tk: {"next_earnings_date": "2026-12-01"},
    )
    assert earnings._fetch_yfinance("0992.HK") == "2026-12-01"


def test_fetch_yfinance_fails_open_on_exception(monkeypatch):
    def _raise(tk):
        raise RuntimeError("simulated yfinance failure")
    monkeypatch.setattr(earnings, "_scrape_revisions_and_earnings", _raise)
    assert earnings._fetch_yfinance("0992.HK") is None


def test_fetch_yfinance_returns_none_when_calendar_absent(monkeypatch):
    monkeypatch.setattr(earnings, "_scrape_revisions_and_earnings", lambda tk: {"next_earnings_date": None})
    assert earnings._fetch_yfinance("0992.HK") is None


# ─────────────────────────────────────────────────────────────────
# refresh_earnings — orchestration fail-open + cache TTL
# ─────────────────────────────────────────────────────────────────

def test_refresh_earnings_uses_finnhub_when_available(monkeypatch):
    monkeypatch.setattr(earnings, "FinnhubProvider", _FakeFinnhubProvider)
    _FakeFinnhubProvider.next_date = "2026-11-05"
    monkeypatch.setattr(earnings, "_fetch_yfinance", lambda t: pytest.fail("yfinance ne doit pas être appelé"))

    result = earnings.refresh_earnings(["AAPL"])
    assert result["AAPL"]["next_earnings_date"] == "2026-11-05"
    assert result["AAPL"]["source"] == "finnhub"


def test_refresh_earnings_falls_back_to_yfinance_when_finnhub_has_no_date(monkeypatch):
    monkeypatch.setattr(earnings, "FinnhubProvider", _FakeFinnhubProvider)
    _FakeFinnhubProvider.configured = False  # simule HK non couvert (0992.HK)
    monkeypatch.setattr(earnings, "_fetch_yfinance", lambda t: "2026-12-20")

    result = earnings.refresh_earnings(["0992.HK"])
    assert result["0992.HK"]["next_earnings_date"] == "2026-12-20"
    assert result["0992.HK"]["source"] == "yfinance"


def test_refresh_earnings_no_source_available_writes_null_not_hardcoded(monkeypatch):
    monkeypatch.setattr(earnings, "FinnhubProvider", _FakeFinnhubProvider)
    _FakeFinnhubProvider.configured = False
    monkeypatch.setattr(earnings, "_fetch_yfinance", lambda t: None)

    result = earnings.refresh_earnings(["XXX"])
    assert result["XXX"]["next_earnings_date"] is None
    assert result["XXX"]["source"] is None


def test_refresh_earnings_keeps_last_known_value_when_refetch_fails(monkeypatch):
    """Si un ticker a déjà une entrée en cache et que le refetch échoue des
    deux côtés, on garde la dernière valeur connue plutôt que de la
    remplacer par None — évite qu'une panne transitoire fasse disparaître un
    badge valide (voir docstring module)."""
    earnings._save_cache({"AAPL": {
        "next_earnings_date": "2026-09-01", "source": "finnhub", "fetched_at": 0.0,
    }})
    monkeypatch.setattr(earnings, "FinnhubProvider", _FakeFinnhubProvider)
    _FakeFinnhubProvider.configured = False
    monkeypatch.setattr(earnings, "_fetch_yfinance", lambda t: None)

    result = earnings.refresh_earnings(["AAPL"], use_cache=False)
    assert result["AAPL"]["next_earnings_date"] == "2026-09-01"
    assert result["AAPL"]["source"] == "finnhub"


def test_refresh_earnings_skips_refetch_within_ttl(monkeypatch):
    earnings._save_cache({"AAPL": {
        "next_earnings_date": "2026-09-01", "source": "finnhub", "fetched_at": time.time(),
    }})
    monkeypatch.setattr(earnings, "_fetch_finnhub", lambda t: pytest.fail("ne doit pas refetch dans le TTL"))
    monkeypatch.setattr(earnings, "_fetch_yfinance", lambda t: pytest.fail("ne doit pas refetch dans le TTL"))

    result = earnings.refresh_earnings(["AAPL"])
    assert result["AAPL"]["next_earnings_date"] == "2026-09-01"


def test_refresh_earnings_refetches_when_ttl_expired(monkeypatch):
    stale_fetched_at = time.time() - earnings.CACHE_TTL_SECONDS - 3600
    earnings._save_cache({"AAPL": {
        "next_earnings_date": "2026-09-01", "source": "finnhub", "fetched_at": stale_fetched_at,
    }})
    monkeypatch.setattr(earnings, "FinnhubProvider", _FakeFinnhubProvider)
    _FakeFinnhubProvider.next_date = "2026-10-15"

    result = earnings.refresh_earnings(["AAPL"])
    assert result["AAPL"]["next_earnings_date"] == "2026-10-15"


def test_refresh_earnings_persists_to_disk(monkeypatch):
    monkeypatch.setattr(earnings, "FinnhubProvider", _FakeFinnhubProvider)
    _FakeFinnhubProvider.next_date = "2026-11-05"

    earnings.refresh_earnings(["AAPL"])
    assert earnings._load_cache()["AAPL"]["next_earnings_date"] == "2026-11-05"


def test_refresh_earnings_uppercases_symbols(monkeypatch):
    monkeypatch.setattr(earnings, "FinnhubProvider", _FakeFinnhubProvider)
    _FakeFinnhubProvider.next_date = "2026-11-05"

    result = earnings.refresh_earnings(["aapl"])
    assert "AAPL" in result
    assert "aapl" not in result


# ─────────────────────────────────────────────────────────────────
# get_earnings_snapshot — lecture pure, jamais de fetch
# ─────────────────────────────────────────────────────────────────

def test_get_earnings_snapshot_never_fetches(monkeypatch):
    monkeypatch.setattr(earnings, "_fetch_finnhub", lambda t: pytest.fail("get_earnings_snapshot ne doit jamais fetch"))
    monkeypatch.setattr(earnings, "_fetch_yfinance", lambda t: pytest.fail("get_earnings_snapshot ne doit jamais fetch"))
    result = earnings.get_earnings_snapshot(["AAPL"])
    assert result["AAPL"]["next_earnings_date"] is None


def test_get_earnings_snapshot_absent_symbol_is_null_not_stale():
    result = earnings.get_earnings_snapshot(["NEVER_FETCHED"])
    assert result["NEVER_FETCHED"] == {"next_earnings_date": None, "source": None, "stale": False}


def test_get_earnings_snapshot_fresh_entry_not_stale():
    earnings._save_cache({"AAPL": {
        "next_earnings_date": "2026-11-05", "source": "finnhub", "fetched_at": time.time(),
    }})
    result = earnings.get_earnings_snapshot(["AAPL"])
    assert result["AAPL"]["stale"] is False
    assert result["AAPL"]["next_earnings_date"] == "2026-11-05"


def test_get_earnings_snapshot_marks_stale_beyond_48h():
    old_fetched_at = time.time() - earnings.STALE_MAX_AGE_SECONDS - 3600
    earnings._save_cache({"AAPL": {
        "next_earnings_date": "2026-11-05", "source": "finnhub", "fetched_at": old_fetched_at,
    }})
    result = earnings.get_earnings_snapshot(["AAPL"])
    assert result["AAPL"]["stale"] is True
    # Valeur servie malgré le staleness (fail-open, pas de disparition brutale).
    assert result["AAPL"]["next_earnings_date"] == "2026-11-05"


def test_get_earnings_snapshot_between_ttl_and_stale_max_not_yet_stale():
    # Au-delà du TTL nominal (24h) mais sous le seuil stale (48h) : servi
    # tel quel, pas encore signalé stale (seul un refresh_earnings déclenche
    # le refetch ; get_earnings_snapshot est une lecture pure).
    age = earnings.CACHE_TTL_SECONDS + 3600
    earnings._save_cache({"AAPL": {
        "next_earnings_date": "2026-11-05", "source": "finnhub", "fetched_at": time.time() - age,
    }})
    result = earnings.get_earnings_snapshot(["AAPL"])
    assert result["AAPL"]["stale"] is False
