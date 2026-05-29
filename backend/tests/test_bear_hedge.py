"""Tests unitaires bear_hedge — décision OPEN/CLOSE/HOLD selon régime macro."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from modules import bear_hedge


@pytest.fixture
def _enable_hedge(monkeypatch):
    """Force BEAR_HEDGE_ENABLED=True pour les tests (default = False en prod)."""
    monkeypatch.setattr(bear_hedge, "BEAR_HEDGE_ENABLED", True, raising=False)


@pytest.fixture
def _today():
    return date(2026, 5, 12)


def _macro(regime: str, since_days_ago: int, today: date) -> dict:
    return {
        "confirmed_regime": regime,
        "candidate_since": (today - timedelta(days=since_days_ago)).isoformat(),
    }


class TestDisabled:
    def test_skip_when_disabled(self, monkeypatch):
        # Depuis 2026-05-12 le défaut prod est True (config.BEAR_HEDGE_ENABLED).
        # On force False explicitement pour tester le court-circuit "désactivé",
        # sans dépendre du défaut de config (qui a changé et cassait ce test).
        monkeypatch.setattr(bear_hedge, "BEAR_HEDGE_ENABLED", False, raising=False)
        d = bear_hedge.decide(macro={"confirmed_regime": "BEAR_MARKET"})
        assert d.action == "SKIP"
        assert "BEAR_HEDGE_ENABLED=False" in d.reason


class TestBearRegime:
    def test_open_hedge_after_min_days(self, _enable_hedge, _today):
        d = bear_hedge.decide(
            macro=_macro("BEAR_MARKET", since_days_ago=5, today=_today),
            state={}, today=_today,
        )
        assert d.action == "OPEN"
        assert d.ticker == "SH"
        assert d.regime == "BEAR_MARKET"
        assert d.days_in_regime == 5

    def test_hold_no_hedge_below_min_days(self, _enable_hedge, _today):
        d = bear_hedge.decide(
            macro=_macro("BEAR_MARKET", since_days_ago=1, today=_today),
            state={}, today=_today,
        )
        assert d.action == "HOLD_NO_HEDGE"
        assert "anti-whipsaw" in d.reason

    def test_hold_hedge_if_already_open(self, _enable_hedge, _today):
        d = bear_hedge.decide(
            macro=_macro("BEAR_MARKET", since_days_ago=10, today=_today),
            state={"hedge_open": True, "hedge_ticker": "SH"},
            today=_today,
        )
        assert d.action == "HOLD_HEDGE"

    def test_crash_panic_triggers_hedge(self, _enable_hedge, _today):
        d = bear_hedge.decide(
            macro=_macro("CRASH_PANIC", since_days_ago=5, today=_today),
            state={}, today=_today,
        )
        assert d.action == "OPEN"


class TestBullRegime:
    def test_close_hedge_when_bull_persistent(self, _enable_hedge, _today):
        d = bear_hedge.decide(
            macro=_macro("BULL_MARKET", since_days_ago=5, today=_today),
            state={"hedge_open": True, "hedge_ticker": "SH"},
            today=_today,
        )
        assert d.action == "CLOSE"

    def test_hold_hedge_when_bull_too_recent(self, _enable_hedge, _today):
        # BULL_MARKET depuis 1j seulement → on attend la confirmation 3j
        d = bear_hedge.decide(
            macro=_macro("BULL_MARKET", since_days_ago=1, today=_today),
            state={"hedge_open": True, "hedge_ticker": "SH"},
            today=_today,
        )
        assert d.action == "HOLD_HEDGE"
        assert "attendre confirmation" in d.reason

    def test_hold_no_hedge_when_bull_and_no_hedge(self, _enable_hedge, _today):
        d = bear_hedge.decide(
            macro=_macro("BULL_MARKET", since_days_ago=10, today=_today),
            state={}, today=_today,
        )
        assert d.action == "HOLD_NO_HEDGE"


class TestEdgeCases:
    def test_skip_on_missing_macro(self, _enable_hedge, _today):
        d = bear_hedge.decide(macro={}, state={}, today=_today)
        assert d.action == "SKIP"
        assert "incomplete" in d.reason

    def test_skip_on_unparseable_date(self, _enable_hedge, _today):
        d = bear_hedge.decide(
            macro={"confirmed_regime": "BEAR_MARKET", "candidate_since": "not-a-date"},
            state={}, today=_today,
        )
        assert d.action == "SKIP"
        assert "unparseable" in d.reason

    def test_unknown_regime_skips(self, _enable_hedge, _today):
        d = bear_hedge.decide(
            macro=_macro("CONSOLIDATION", since_days_ago=5, today=_today),
            state={}, today=_today,
        )
        assert d.action == "SKIP"
