"""
Tests du scheduler staggered : tri par ancienneté, budget 50/j, skip < 14j,
forced tickers qui outrepassent min_age, never-fetched priorisés en tête.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from modules.universe_scheduler import (
    DEFAULT_DAILY_BUDGET,
    _age_days,
    _parse_iso,
    select_refresh_candidates,
)

NOW = datetime(2026, 4, 20, 12, 0, 0)


def _iso(days_ago: float) -> str:
    """Produit une chaîne ISO correspondant à `days_ago` jours avant NOW."""
    dt = NOW - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ═══════════════════════════════════════════════════════════════════════════
# Parsing + age
# ═══════════════════════════════════════════════════════════════════════════

def test_parse_iso_round_trip():
    s = "2026-04-01T12:00:00Z"
    dt = _parse_iso(s)
    assert dt is not None
    assert dt.year == 2026 and dt.month == 4 and dt.day == 1


def test_parse_iso_none_on_garbage():
    assert _parse_iso(None) is None
    assert _parse_iso("") is None
    assert _parse_iso("pas une date") is None


def test_age_days_inf_on_missing():
    # Never-fetched → age infini → priorité max.
    assert _age_days(None, NOW) == float("inf")
    assert _age_days("garbage", NOW) == float("inf")


def test_age_days_positive_for_past():
    assert _age_days(_iso(10), NOW) == pytest.approx(10.0, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════
# Sélection de candidats
# ═══════════════════════════════════════════════════════════════════════════

def _make_universe(age_days_by_ticker: dict[str, float | None]) -> dict:
    """Construit un dict existing_tickers avec fetched_at fictifs."""
    return {
        t: {"fetched_at": _iso(age) if age is not None else None}
        for t, age in age_days_by_ticker.items()
    }


def test_select_oldest_first_order():
    existing = _make_universe({"A": 5.0, "B": 30.0, "C": 20.0, "D": 100.0})
    picked = select_refresh_candidates(
        existing_tickers=existing,
        all_known_tickers=["A", "B", "C", "D"],
        budget=10, min_age_days=14, now=NOW,
    )
    # A (5j) en dessous du seuil → skipé. Ordre : D > B > C.
    assert picked == ["D", "B", "C"]


def test_select_respects_budget_cap():
    existing = _make_universe({t: 30.0 for t in ["A", "B", "C", "D", "E"]})
    picked = select_refresh_candidates(
        existing_tickers=existing,
        all_known_tickers=["A", "B", "C", "D", "E"],
        budget=2, min_age_days=14, now=NOW,
    )
    assert len(picked) == 2


def test_select_skips_fresh_tickers():
    existing = _make_universe({"FRESH": 3.0, "STALE": 40.0})
    picked = select_refresh_candidates(
        existing_tickers=existing,
        all_known_tickers=["FRESH", "STALE"],
        budget=10, min_age_days=14, now=NOW,
    )
    assert picked == ["STALE"]
    assert "FRESH" not in picked


def test_select_forced_bypass_min_age():
    """Un ticker forcé passe MÊME SI son age < min_age_days."""
    existing = _make_universe({"FRESH": 2.0, "STALE": 30.0})
    picked = select_refresh_candidates(
        existing_tickers=existing,
        all_known_tickers=["FRESH", "STALE"],
        budget=10, min_age_days=14,
        force_tickers=["FRESH"], now=NOW,
    )
    # FRESH forcé passe en tête, STALE suit.
    assert picked == ["FRESH", "STALE"]


def test_select_forced_consume_budget_first():
    existing = _make_universe({
        "STALE1": 50.0, "STALE2": 40.0,
        "FORCED1": 1.0, "FORCED2": 2.0,
    })
    picked = select_refresh_candidates(
        existing_tickers=existing,
        all_known_tickers=["STALE1", "STALE2", "FORCED1", "FORCED2"],
        budget=2, min_age_days=14,
        force_tickers=["FORCED1", "FORCED2"], now=NOW,
    )
    # Budget 2 entièrement consommé par les forcés.
    assert picked == ["FORCED1", "FORCED2"]


def test_select_never_fetched_prioritized():
    """Ticker absent de l'univers existant → never fetched → passe en tête."""
    existing = _make_universe({"OLD": 30.0})
    picked = select_refresh_candidates(
        existing_tickers=existing,
        all_known_tickers=["OLD", "NEW"],
        budget=10, min_age_days=14, now=NOW,
    )
    # NEW jamais fetched → age inf → devant OLD.
    assert picked[0] == "NEW"
    assert "OLD" in picked


def test_select_default_budget_matches_constant():
    """Sanity : DEFAULT_DAILY_BUDGET utilisé quand budget non précisé."""
    existing = _make_universe({f"T{i}": 30.0 for i in range(100)})
    picked = select_refresh_candidates(
        existing_tickers=existing,
        all_known_tickers=list(existing.keys()),
        min_age_days=14, now=NOW,
    )
    assert len(picked) == DEFAULT_DAILY_BUDGET
