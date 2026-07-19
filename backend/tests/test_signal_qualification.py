"""Tests modules/signal_qualification.py — Étape 1 roadmap (qualification du
signal : delta/flip de verdict vs N jours, segmentation 3 catégories,
narratif, fraîcheur causale).

Snapshots isolés via tmp_path (même pattern que test_universe_history.py)
pour ne pas dépendre du filesystem prod.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from modules import signal_qualification as sq
from modules import universe_history as uh


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setattr(uh, "HISTORY_DIR", tmp_path / ".universe_history")
    return tmp_path / ".universe_history"


def _row(**overrides):
    base = {
        "titan_composite_score": 75.0,
        "quality_score": 70.0,
        "value_score": 60.0,
        "risk_score": 60.0,
        "momentum_score": 65.0,
        "revisions_score": 55.0,
        "growth_score": 50.0,
        "piotroski_score": 60.0,
        "f_score": 5,
        "earnings_beat_rate_8q": 0.5,
        "insider_cluster_buying": False,
        "insider_score": 50.0,
        "titan_tilt_flags": [],
        "next_earnings_date": (date.today() + timedelta(days=60)).isoformat(),
    }
    base.update(overrides)
    return base


def _write(ticker, snapshot_date, **row_overrides):
    uh.write_snapshot(
        {ticker: _row(**row_overrides)},
        snapshot_date=snapshot_date,
    )


# ─────────────────────────────────────────────────────────────────
# _find_reference_snapshot
# ─────────────────────────────────────────────────────────────────

def test_find_reference_snapshot_picks_closest_at_or_before_target(isolated_history):
    today = date.today()
    _write("AAA", today - timedelta(days=10))
    _write("AAA", today - timedelta(days=6))
    _write("AAA", today - timedelta(days=3))

    ref = sq._find_reference_snapshot("AAA", lookback_days=5)
    assert ref is not None
    ref_date, row = ref
    assert ref_date == today - timedelta(days=6)


def test_find_reference_snapshot_none_when_no_history(isolated_history):
    assert sq._find_reference_snapshot("ZZZ", lookback_days=5) is None


def test_find_reference_snapshot_none_outside_window(isolated_history):
    today = date.today()
    # Snapshot bien trop ancien pour rentrer dans la fenêtre de tolérance.
    _write("AAA", today - timedelta(days=90))
    assert sq._find_reference_snapshot("AAA", lookback_days=5) is None


# ─────────────────────────────────────────────────────────────────
# compute_verdict_trend
# ─────────────────────────────────────────────────────────────────

def test_verdict_trend_no_history_is_unknown(isolated_history):
    trend = sq.compute_verdict_trend(
        "ZZZ", current_verdict="STRONG_BUY", current_score=85.0,
    )
    assert trend["reference_date"] is None
    assert trend["prior_verdict"] is None
    assert trend["score_delta"] is None
    assert trend["verdict_changed"] is None


def test_verdict_trend_detects_flip_and_score_delta(isolated_history):
    today = date.today()
    # Il y a 6j : composite faible → verdict historique WATCH.
    _write("AAA", today - timedelta(days=6), titan_composite_score=72.0)

    trend = sq.compute_verdict_trend(
        "AAA", current_verdict="STRONG_BUY", current_score=85.0, lookback_days=5,
    )
    assert trend["prior_verdict"] == "WATCH"
    assert trend["prior_score"] == 72.0
    assert trend["score_delta"] == pytest.approx(13.0)
    assert trend["verdict_changed"] is True


def test_verdict_trend_stable_verdict_not_flagged_as_changed(isolated_history):
    today = date.today()
    _write(
        "AAA", today - timedelta(days=6),
        titan_composite_score=85.0, revisions_score=70.0,
        insider_cluster_buying=True, f_score=8,
    )
    trend = sq.compute_verdict_trend(
        "AAA", current_verdict="STRONG_BUY", current_score=86.0, lookback_days=5,
    )
    assert trend["prior_verdict"] == "STRONG_BUY"
    assert trend["verdict_changed"] is False


# ─────────────────────────────────────────────────────────────────
# classify_conviction
# ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("verdict,changed,expected", [
    ("WATCH", True, sq.CONVICTION_WATCH),
    ("WATCH", False, sq.CONVICTION_WATCH),
    ("WATCH", None, sq.CONVICTION_WATCH),
    ("STRONG_BUY", True, sq.CONVICTION_NEW),
    ("BUY", True, sq.CONVICTION_NEW),
    ("STRONG_BUY", False, sq.CONVICTION_CONFIRMED),
    # Historique insuffisant (`changed=None`) → pas de "nouveau" sans preuve.
    ("BUY", None, sq.CONVICTION_CONFIRMED),
    ("SKIP", True, sq.CONVICTION_OTHER),
    ("CHEAP_JUNK", None, sq.CONVICTION_OTHER),
    (None, None, sq.CONVICTION_OTHER),
])
def test_classify_conviction(verdict, changed, expected):
    assert sq.classify_conviction(verdict, changed) == expected


# ─────────────────────────────────────────────────────────────────
# build_narrative
# ─────────────────────────────────────────────────────────────────

def test_build_narrative_combines_ingredients():
    context = {
        "buy_signal": {"verdict": "STRONG_BUY", "label": "STRONG BUY"},
        "f_score": 8, "f_score_max": 9,
        "momentum_score": 75.0,
        "support": {"level": "ON_SUPPORT"},
        "revisions_score": 70.0,
        "days_until_earnings": 20,
        "titan_tilt_flags": [],
    }
    narrative = sq.build_narrative(context)
    assert narrative.startswith("STRONG BUY —")
    assert "Piotroski 8/9" in narrative
    assert "momentum fort" in narrative
    assert "support technique" in narrative


def test_build_narrative_falls_back_to_label_when_no_ingredients():
    context = {"buy_signal": {"verdict": "WATCH", "label": "WATCH — TITAN 72"}}
    assert sq.build_narrative(context) == "WATCH — TITAN 72"


def test_build_narrative_caps_at_three_clauses():
    context = {
        "buy_signal": {"verdict": "STRONG_BUY", "label": "STRONG BUY"},
        "f_score": 8, "f_score_max": 9,
        "momentum_score": 80.0,
        "support": {"level": "ON_SUPPORT"},
        "revisions_score": 80.0,
        "days_until_earnings": 5,
        "titan_tilt_flags": ["qarp"],
    }
    narrative = sq.build_narrative(context)
    # 6 ingrédients disponibles, seulement 3 retenus (garde "une phrase").
    assert narrative.count(",") + narrative.count(" et ") <= 3


# ─────────────────────────────────────────────────────────────────
# causal_reasons
# ─────────────────────────────────────────────────────────────────

def test_causal_reasons_empty_without_prior_row():
    assert sq.causal_reasons(None, _row()) == []


def test_causal_reasons_detects_earnings_beat_improvement():
    prior = _row(earnings_beat_rate_8q=0.3, earnings_surprise_avg_4q=None,
                 earnings_surprise_pct_last=None)
    current = _row(earnings_beat_rate_8q=0.9, earnings_surprise_avg_4q=10.0,
                    earnings_surprise_pct_last=12.0)
    reasons = sq.causal_reasons(prior, current)
    assert any("Earnings" in r for r in reasons)


def test_causal_reasons_detects_new_insider_cluster():
    prior = _row(insider_cluster_buying=False)
    current = _row(insider_cluster_buying=True)
    reasons = sq.causal_reasons(prior, current)
    assert any("insider" in r.lower() for r in reasons)


def test_causal_reasons_detects_revisions_jump():
    prior = _row(revisions_score=50.0)
    current = _row(revisions_score=65.0)
    reasons = sq.causal_reasons(prior, current)
    assert any("Révisions" in r for r in reasons)


def test_causal_reasons_no_change_no_reasons():
    row = _row()
    assert sq.causal_reasons(row, row) == []


# ─────────────────────────────────────────────────────────────────
# qualify_proposal (intégration)
# ─────────────────────────────────────────────────────────────────

def test_qualify_proposal_new_signal_end_to_end(isolated_history):
    today = date.today()
    _write("AAA", today - timedelta(days=6), titan_composite_score=72.0)

    scored_row = _row(
        titan_composite_score=85.0, revisions_score=70.0,
        insider_cluster_buying=True, f_score=8,
    )
    context_ingredients = {
        "buy_signal": {"verdict": "STRONG_BUY", "label": "STRONG BUY"},
        "f_score": 8, "f_score_max": 9,
        "momentum_score": 75.0,
        "support": {"level": "ON_SUPPORT"},
        "revisions_score": 70.0,
        "days_until_earnings": 40,
        "titan_tilt_flags": [],
    }

    out = sq.qualify_proposal(
        "AAA", scored_row=scored_row,
        context_ingredients=context_ingredients, lookback_days=5,
    )
    assert out["conviction"] == sq.CONVICTION_NEW
    assert out["trend"]["verdict_changed"] is True
    assert out["narrative"].startswith("STRONG BUY —")
    assert isinstance(out["causal_reasons"], list)


def test_qualify_proposal_no_history_defaults_to_confirmed(isolated_history):
    scored_row = _row(titan_composite_score=85.0)
    context_ingredients = {
        "buy_signal": {"verdict": "STRONG_BUY", "label": "STRONG BUY"},
    }
    out = sq.qualify_proposal(
        "ZZZ", scored_row=scored_row,
        context_ingredients=context_ingredients, lookback_days=5,
    )
    assert out["conviction"] == sq.CONVICTION_CONFIRMED
    assert out["trend"]["verdict_changed"] is None
    assert out["causal_reasons"] == []
