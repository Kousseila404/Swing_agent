"""Tests cache fundamentals — TTL, stale fallback, atomic write, edge cases."""
from __future__ import annotations

import time

import pytest

from data_providers.base import (
    FinancialRatios,
    FundamentalProviderBase,
    ProviderQuotaExceeded,
    ProviderUnavailable,
)
from modules import fundamentals_cache as fc


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Redirige le cache vers tmp_path + reset le store singleton."""
    p = tmp_path / "fc"
    monkeypatch.setattr(fc, "CACHE_DIR", p)
    monkeypatch.setattr(fc, "CACHE_PATH", p / "index.json.gz")
    monkeypatch.setattr(fc, "CACHE_LOCK_PATH", p / "index.json.gz.lock")
    fc._store.clear()  # reset RAM mirror
    yield p
    fc._store.clear()


class _FakeProvider(FundamentalProviderBase):
    """Provider scriptable pour tester le cache. Compteur appels + fail switch."""
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.next_exception: Exception | None = None
        self.next_ratios: FinancialRatios | None = None

    def get_financial_ratios(self, ticker: str) -> FinancialRatios:
        self.calls += 1
        if self.next_exception is not None:
            raise self.next_exception
        if self.next_ratios is not None:
            return self.next_ratios
        return FinancialRatios(
            ticker=ticker, name=f"Co {ticker}",
            return_on_equity=0.20, operating_margin=0.25,
            ev_to_ebitda=15.0, current_ratio=2.0,
            source_provider="fake",
            fetched_at="2026-04-22T17:00:00Z",
        )


# ─────────────────────────────────────────────────────────────────
# Hit / miss / TTL
# ─────────────────────────────────────────────────────────────────

def test_first_call_misses_cache_and_calls_upstream(isolated_cache):
    fake = _FakeProvider()
    cached = fc.CachedFundamentalProvider(fake)
    r = cached.get_financial_ratios("AAPL")
    assert r.ticker == "AAPL"
    assert fake.calls == 1


def test_second_call_within_ttl_serves_from_cache(isolated_cache):
    fake = _FakeProvider()
    cached = fc.CachedFundamentalProvider(fake, ttl_seconds=3600)
    cached.get_financial_ratios("AAPL")
    cached.get_financial_ratios("AAPL")
    cached.get_financial_ratios("AAPL")
    # 1 seul appel upstream, les 2 suivants sont des cache hits
    assert fake.calls == 1


def test_call_after_ttl_refetches(isolated_cache):
    fake = _FakeProvider()
    cached = fc.CachedFundamentalProvider(fake, ttl_seconds=1)
    cached.get_financial_ratios("AAPL")
    time.sleep(1.1)
    cached.get_financial_ratios("AAPL")
    assert fake.calls == 2


def test_different_tickers_independent(isolated_cache):
    fake = _FakeProvider()
    cached = fc.CachedFundamentalProvider(fake, ttl_seconds=3600)
    cached.get_financial_ratios("AAPL")
    cached.get_financial_ratios("MSFT")
    assert fake.calls == 2


def test_ticker_normalized_to_uppercase(isolated_cache):
    fake = _FakeProvider()
    cached = fc.CachedFundamentalProvider(fake, ttl_seconds=3600)
    cached.get_financial_ratios("aapl")
    cached.get_financial_ratios("AAPL")
    cached.get_financial_ratios("  Aapl  ")
    # Tous matchent la même clé
    assert fake.calls == 1


# ─────────────────────────────────────────────────────────────────
# Stale fallback
# ─────────────────────────────────────────────────────────────────

def test_quota_exceeded_returns_stale_cache(isolated_cache):
    fake = _FakeProvider()
    cached = fc.CachedFundamentalProvider(fake, ttl_seconds=1)
    # 1er fetch OK → cache rempli
    cached.get_financial_ratios("AAPL")
    time.sleep(1.1)
    # Quota épuisée → stale cache servi avec tag
    fake.next_exception = ProviderQuotaExceeded("FMP quota")
    r = cached.get_financial_ratios("AAPL")
    assert r.ticker == "AAPL"
    assert "stale_fallback_after_fail" in (r.error or "")
    assert "/stale" in (r.source_provider or "")


def test_provider_unavailable_returns_stale_cache(isolated_cache):
    fake = _FakeProvider()
    cached = fc.CachedFundamentalProvider(fake, ttl_seconds=1)
    cached.get_financial_ratios("AAPL")
    time.sleep(1.1)
    fake.next_exception = ProviderUnavailable("network down")
    r = cached.get_financial_ratios("AAPL")
    assert "stale_fallback_after_fail" in (r.error or "")


def test_no_cache_no_stale_propagates_exception(isolated_cache):
    fake = _FakeProvider()
    cached = fc.CachedFundamentalProvider(fake)
    fake.next_exception = ProviderQuotaExceeded("quota")
    with pytest.raises(ProviderQuotaExceeded):
        cached.get_financial_ratios("NEW_TICKER")


def test_stale_max_age_exceeded_propagates(isolated_cache):
    """Si le cache est plus vieux que stale_max, on raise quand même."""
    fake = _FakeProvider()
    cached = fc.CachedFundamentalProvider(
        fake, ttl_seconds=1, stale_max_seconds=2,
    )
    cached.get_financial_ratios("AAPL")
    time.sleep(2.1)
    fake.next_exception = ProviderQuotaExceeded("q")
    with pytest.raises(ProviderQuotaExceeded):
        cached.get_financial_ratios("AAPL")


# ─────────────────────────────────────────────────────────────────
# Empty payload handling
# ─────────────────────────────────────────────────────────────────

def test_empty_payload_not_cached(isolated_cache):
    """Un payload vide (error + tous None) ne doit PAS bloquer 24h le ticker."""
    fake = _FakeProvider()
    fake.next_ratios = FinancialRatios(
        ticker="BAD", error="provider crash", source_provider="fake",
    )
    cached = fc.CachedFundamentalProvider(fake)
    cached.get_financial_ratios("BAD")
    # Pas de cache → 2e appel re-call upstream
    cached.get_financial_ratios("BAD")
    assert fake.calls == 2


# ─────────────────────────────────────────────────────────────────
# Persistence + corruption
# ─────────────────────────────────────────────────────────────────

def test_cache_persists_across_instances(isolated_cache):
    """Une seconde instance lit le cache disque écrit par la première."""
    fake1 = _FakeProvider()
    cached1 = fc.CachedFundamentalProvider(fake1, ttl_seconds=3600)
    cached1.get_financial_ratios("AAPL")
    # Reset RAM mirror, recharge depuis disque
    fc._store._loaded = False
    fc._store._mem = {}
    fake2 = _FakeProvider()
    cached2 = fc.CachedFundamentalProvider(fake2, ttl_seconds=3600)
    cached2.get_financial_ratios("AAPL")
    # cached2 sert depuis disque → 0 call upstream
    assert fake2.calls == 0


def test_corrupted_index_recovers_silently(isolated_cache):
    """Si index.json.gz est corrompu, on repart d'un cache vide."""
    fc.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fc.CACHE_PATH.write_bytes(b"not gzip data")
    fc._store._loaded = False
    fc._store._mem = {}
    fake = _FakeProvider()
    cached = fc.CachedFundamentalProvider(fake)
    r = cached.get_financial_ratios("AAPL")  # ne crash pas
    assert r.ticker == "AAPL"


# ─────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────

def test_cache_stats_empty(isolated_cache):
    stats = fc.cache_stats()
    assert stats["n_cached"] == 0
    assert stats["oldest_age_sec"] is None


def test_cache_stats_populated(isolated_cache):
    fake = _FakeProvider()
    cached = fc.CachedFundamentalProvider(fake)
    cached.get_financial_ratios("AAPL")
    cached.get_financial_ratios("MSFT")
    stats = fc.cache_stats()
    assert stats["n_cached"] == 2
    assert stats["oldest_age_sec"] is not None
    assert stats["oldest_age_sec"] >= 0


# ─────────────────────────────────────────────────────────────────
# Sanitize listing + invalidation (audit 2026-04-23)
# ─────────────────────────────────────────────────────────────────

def _seed_cache_with_flagged(ticker: str, *, flag: str = "dq_sanitize=out_of_bounds:ev_to_ebitda"):
    r = FinancialRatios(
        ticker=ticker, source_provider="fake",
        fetched_at="2026-04-22T17:00:00Z", error=flag,
    )
    fc._store.put(ticker, r)


def test_list_flagged_tickers_empty(isolated_cache):
    assert fc.list_flagged_tickers() == []


def test_list_flagged_tickers_returns_only_sanitized(isolated_cache):
    # Seed : 3 flagged, 2 clean
    _seed_cache_with_flagged("FLAG1")
    _seed_cache_with_flagged("FLAG2", flag="dq_sanitize=mcap_mismatch")
    _seed_cache_with_flagged("FLAG3")
    clean = FinancialRatios(ticker="CLEAN1", source_provider="fake")
    fc._store.put("CLEAN1", clean)
    clean2 = FinancialRatios(ticker="CLEAN2", source_provider="fake", error="some_other_error")
    fc._store.put("CLEAN2", clean2)

    flagged = fc.list_flagged_tickers()
    assert flagged == ["FLAG1", "FLAG2", "FLAG3"]


def test_invalidate_tickers_removes_entries(isolated_cache):
    _seed_cache_with_flagged("FLAG1")
    _seed_cache_with_flagged("FLAG2")
    clean = FinancialRatios(ticker="CLEAN", source_provider="fake")
    fc._store.put("CLEAN", clean)

    removed = fc.invalidate_tickers(["FLAG1", "FLAG2"])
    assert removed == 2
    # Le cache contient plus que CLEAN
    stats = fc.cache_stats()
    assert stats["n_cached"] == 1
    assert fc.list_flagged_tickers() == []


def test_invalidate_tickers_empty_list_noop(isolated_cache):
    _seed_cache_with_flagged("FLAG1")
    assert fc.invalidate_tickers([]) == 0
    assert fc.cache_stats()["n_cached"] == 1


def test_invalidate_tickers_nonexistent_noop(isolated_cache):
    _seed_cache_with_flagged("FLAG1")
    removed = fc.invalidate_tickers(["NOT_IN_CACHE"])
    assert removed == 0
    assert fc.cache_stats()["n_cached"] == 1


def test_invalidate_tickers_case_insensitive(isolated_cache):
    _seed_cache_with_flagged("AAPL")
    removed = fc.invalidate_tickers(["aapl", " AAPL "])
    # Le store normalize en upper — on teste qu'on supprime bien (2 entrées
    # identiques mais case-trim-normalized).
    assert removed == 1
