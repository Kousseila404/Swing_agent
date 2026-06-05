"""Tests lt_exit_policy — refonte SL/TP LT 2026-04-29 (Buffett-style).

Couvre les 4 couches dans l'ordre de priorité :
  1. EXIT_CATASTROPHE (SL touché ou drawdown ≤ −35 %)
  2. EXIT_THESIS (thèse fondamentale BROKEN)
  3. EXIT_VALUATION (Value pillar drift / PEG / FwdPE en gain notable)
  4. TRIM (thèse WARN)
  5. ADD_ON (thèse INTACT + drawdown −12 % à −25 % + Support)
  6. HOLD (default)

+ aggregate_portfolio + edge cases (NO_DATA, SHORT, prix invalide,
priorité catastrophe > thesis > valuation).
"""
from __future__ import annotations

from modules import lt_exit_policy
from modules.lt_exit_policy import LTDecision, aggregate_portfolio, decide


# ─────────────────────────────────────────────────────────────────
# 1. EXIT_CATASTROPHE
# ─────────────────────────────────────────────────────────────────
def test_catastrophe_when_sl_hit():
    """Prix ≤ SL → EXIT_CATASTROPHE même si thèse INTACT."""
    d = decide(
        ticker="MU", entry_price=100.0, current_price=64.0, stop_loss=65.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
    )
    assert d.action == "EXIT_CATASTROPHE"
    assert d.severity == 4
    assert d.catastrophe_hit is True
    assert any("Catastrophe" in r or "SL" in r for r in d.reasons)


def test_catastrophe_when_drawdown_extreme_without_sl():
    """SL absent + drawdown ≤ −35 % → EXIT_CATASTROPHE (digue ultime)."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=60.0, stop_loss=None,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
    )
    assert d.action == "EXIT_CATASTROPHE"
    assert d.catastrophe_hit is False  # pas de SL physique
    assert d.drawdown_from_entry_pct == -40.0


def test_catastrophe_priority_over_thesis_broken():
    """Si SL touché ET thèse BROKEN → on garde EXIT_CATASTROPHE (priorité 1)."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=60.0, stop_loss=65.0,
        thesis={"status": "BROKEN", "reasons_break": ["TITAN -25"], "reasons_warn": []},
    )
    assert d.action == "EXIT_CATASTROPHE"


# ─────────────────────────────────────────────────────────────────
# 2. EXIT_THESIS
# ─────────────────────────────────────────────────────────────────
def test_exit_thesis_when_broken():
    """Thèse BROKEN, prix au-dessus du SL → EXIT_THESIS."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=98.0, stop_loss=70.0,
        thesis={
            "status": "BROKEN", "severity": 2,
            "reasons_break": ["TITAN 82 → 58 (-24 pts)", "F-Score 8/9 → 4/9"],
            "reasons_warn": [],
        },
    )
    assert d.action == "EXIT_THESIS"
    assert d.severity == 3
    assert "TITAN" in d.reasons[0]


def test_exit_thesis_priority_over_valuation():
    """Thèse BROKEN + survalorisation simultanée → on garde EXIT_THESIS."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=140.0, stop_loss=70.0,
        thesis={"status": "BROKEN", "reasons_break": ["TITAN -25"], "reasons_warn": []},
        entry_value_pillar=80.0, current_value_pillar=20.0,  # drift −60
        current_peg_ratio=5.0,
    )
    assert d.action == "EXIT_THESIS"


# ─────────────────────────────────────────────────────────────────
# 3. EXIT_VALUATION
# ─────────────────────────────────────────────────────────────────
def test_exit_valuation_on_value_drift():
    """Gain ≥ 30 % + Value pillar chute ≥ 35 pts → EXIT_VALUATION."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=140.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        entry_value_pillar=70.0, current_value_pillar=30.0,  # drift -40
    )
    assert d.action == "EXIT_VALUATION"
    assert d.severity == 3


def test_exit_valuation_on_peg_extreme():
    """Gain ≥ 30 % + PEG ≥ 3.5 → EXIT_VALUATION."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=135.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        current_peg_ratio=4.2,
    )
    assert d.action == "EXIT_VALUATION"


def test_exit_valuation_fallback_forward_pe():
    """Gain ≥ 30 %, PEG indispo, FwdPE ≥ 60 → EXIT_VALUATION."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=135.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        current_peg_ratio=None, current_forward_pe=75.0,
    )
    assert d.action == "EXIT_VALUATION"


def test_no_valuation_exit_without_gain():
    """Survalorisation forte mais position en perte → pas d'EXIT_VALUATION
    (rotation sectorielle Value, pas une vraie cassure de thèse)."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=95.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        entry_value_pillar=80.0, current_value_pillar=20.0,  # drift -60
        current_peg_ratio=5.0,
    )
    assert d.action == "HOLD"


# ─────────────────────────────────────────────────────────────────
# 4. TRIM (thèse WARN)
# ─────────────────────────────────────────────────────────────────
def test_trim_on_warn():
    """Thèse WARN, pas d'autre signal → TRIM."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=105.0, stop_loss=70.0,
        thesis={
            "status": "WARN", "severity": 1,
            "reasons_break": [],
            "reasons_warn": ["Quality 80 → 55", "Momentum 70 → 40"],
        },
    )
    assert d.action == "TRIM"
    assert d.severity == 2
    assert "Quality" in d.reasons[0]


# ─────────────────────────────────────────────────────────────────
# 5. ADD_ON (Buffett averaging down)
# ─────────────────────────────────────────────────────────────────
def test_add_on_intact_thesis_correction_with_support():
    """Thèse INTACT + drawdown −15 % + Support ON → ADD_ON."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=85.0, stop_loss=65.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        support_level="ON_SUPPORT",
    )
    assert d.action == "ADD_ON"
    assert d.severity == 1
    assert any("INTACT" in r for r in d.reasons)


def test_add_on_unknown_support_still_suggested():
    """Sans support data, on suggère ADD_ON avec note prudente."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=85.0, stop_loss=65.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        support_level=None,
    )
    assert d.action == "ADD_ON"
    assert any("inconnu" in r.lower() for r in d.reasons)


def test_no_add_on_when_too_close_to_stop():
    """Audit 2026-06-05 (cas NEM réel) — zone de renfort MAIS prix à <5 % du
    stop catastrophe → HOLD, pas ADD_ON (renforcer juste avant un stop-out
    probable empilerait la perte sur l'add)."""
    # entry 118.07, current 97.695, SL 94.456 → drawdown −17.3 %, 3.3 % du stop.
    d = decide(
        ticker="NEM", entry_price=118.07, current_price=97.695,
        stop_loss=94.456,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        support_level=None,
    )
    assert d.action == "HOLD"
    assert any("stop" in r.lower() for r in d.reasons)


def test_add_on_fires_when_stop_is_far():
    """Même drawdown que NEM mais stop lointain (29 % en dessous) → la marge
    est suffisante, ADD_ON doit bien se déclencher."""
    d = decide(
        ticker="X", entry_price=118.07, current_price=97.695, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        support_level="ON_SUPPORT",
    )
    assert d.action == "ADD_ON"


def test_no_add_on_when_drawdown_floor_exceeded():
    """Drawdown < −25 % → on n'ajoute plus, on attend."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=72.0, stop_loss=60.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        support_level="ON_SUPPORT",
    )
    assert d.action == "HOLD"


def test_no_add_on_when_thesis_warn():
    """Drawdown −15 % MAIS thèse WARN → TRIM, pas ADD_ON (priorité 4)."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=85.0, stop_loss=60.0,
        thesis={
            "status": "WARN", "reasons_break": [],
            "reasons_warn": ["Quality drop"],
        },
        support_level="ON_SUPPORT",
    )
    assert d.action == "TRIM"


# ─────────────────────────────────────────────────────────────────
# 6. HOLD (default)
# ─────────────────────────────────────────────────────────────────
def test_hold_intact_thesis_small_pl():
    """Thèse INTACT + drawdown < −12 % → HOLD."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=98.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
    )
    assert d.action == "HOLD"
    assert d.severity == 0


def test_hold_intact_thesis_with_gain():
    """Thèse INTACT + gain modéré → HOLD (let it ride)."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=120.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
    )
    assert d.action == "HOLD"


def test_hold_no_thesis_data():
    """Pas de thesis input → HOLD avec note 'données partielles'."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=102.0, stop_loss=70.0,
        thesis=None,
    )
    assert d.action == "HOLD"


# ─────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────
def test_no_data_when_invalid_price():
    """current_price None → NO_DATA."""
    d = decide(ticker="X", entry_price=100.0, current_price=None, stop_loss=70.0)
    assert d.action == "NO_DATA"


def test_short_returns_hold():
    """SHORT non géré par v1 → HOLD (pas de bruit côté SL standard)."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=80.0, stop_loss=120.0,
        direction="SHORT",
        thesis={"status": "BROKEN", "reasons_break": ["x"], "reasons_warn": []},
    )
    assert d.action == "HOLD"


def test_to_dict_serialization():
    """to_dict expose tous les champs nécessaires à l'API."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=110.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
    )
    o = d.to_dict()
    assert set(o.keys()) >= {
        "ticker", "action", "severity", "reasons", "pct_gain",
        "drawdown_from_entry_pct", "thesis_status", "valuation_drift",
        "peg_ratio", "catastrophe_hit",
    }


# ─────────────────────────────────────────────────────────────────
# aggregate_portfolio
# ─────────────────────────────────────────────────────────────────
def test_aggregate_portfolio_counts_and_severity():
    decisions = [
        LTDecision(ticker="A", action="HOLD",            severity=0),
        LTDecision(ticker="B", action="HOLD",            severity=0),
        LTDecision(ticker="C", action="ADD_ON",          severity=1),
        LTDecision(ticker="D", action="TRIM",            severity=2),
        LTDecision(ticker="E", action="EXIT_THESIS",     severity=3),
        LTDecision(ticker="F", action="EXIT_CATASTROPHE", severity=4),
    ]
    s = aggregate_portfolio(decisions)
    assert s["counts"] == {
        "HOLD": 2, "ADD_ON": 1, "TRIM": 1,
        "EXIT_THESIS": 1, "EXIT_CATASTROPHE": 1,
    }
    assert s["actionable_count"] == 3   # severity ≥ 2
    assert s["highest_severity"] == 4
    assert s["tickers_by_action"]["EXIT_CATASTROPHE"] == ["F"]
    assert s["tickers_by_action"]["HOLD"] == ["A", "B"]


def test_aggregate_portfolio_empty():
    s = aggregate_portfolio([])
    assert s["counts"] == {}
    assert s["actionable_count"] == 0
    assert s["highest_severity"] == 0
    assert s["tickers_by_action"] == {}


# ─────────────────────────────────────────────────────────────────
# Étape 2 Buffett — overshoot vs fair value
# ─────────────────────────────────────────────────────────────────
def test_exit_valuation_on_fair_value_overshoot():
    """Prix > buffett_tp × 1.20 + gain notable → EXIT_VALUATION."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=180.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        buffett_tp=140.0,  # ceiling fundamentals à 140 ; current 180 = +28 % au-dessus
        let_it_ride=False,
    )
    assert d.action == "EXIT_VALUATION"
    assert any("fair value" in r.lower() or "ceiling" in r.lower() for r in d.reasons)


def test_let_it_ride_suppresses_valuation_exit_when_intact():
    """Compounder rare (let_it_ride) + thèse INTACT → on tient même si overshoot."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=180.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        buffett_tp=140.0,
        let_it_ride=True,  # Coke/Apple type — tenir
        current_peg_ratio=4.0,  # même PEG extrême ne déclenche pas
    )
    assert d.action == "HOLD"


def test_let_it_ride_does_not_protect_broken_thesis():
    """let_it_ride OK + thèse BROKEN → EXIT_THESIS quand même (priorité 2 > 3)."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=180.0, stop_loss=70.0,
        thesis={"status": "BROKEN", "reasons_break": ["Quality crash"], "reasons_warn": []},
        buffett_tp=140.0,
        let_it_ride=True,
    )
    assert d.action == "EXIT_THESIS"


def test_no_overshoot_when_price_below_ceiling():
    """Prix sous buffett_tp × 1.2 → pas de signal overshoot."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=140.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        buffett_tp=140.0,  # current = exactement ceiling, pas overshoot ×1.2
        let_it_ride=False,
    )
    assert d.action == "HOLD"


def test_overshoot_requires_min_gain_gate():
    """Overshoot mais position en perte → pas d'EXIT (gate VALUATION_EXIT_MIN_GAIN_PCT)."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=95.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        buffett_tp=70.0,  # ceiling très bas, current >> ×1.2 mais position négative
        let_it_ride=False,
    )
    # current=95, gain=-5% < 30% → pas d'EXIT
    assert d.action == "HOLD"


# ─────────────────────────────────────────────────────────────────
# Étape 4 Buffett — dérive de catégorie
# ─────────────────────────────────────────────────────────────────
def test_category_break_compounder_to_junk_triggers_exit():
    """Un name acheté comme compounder devenu junk → EXIT_THESIS."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=98.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        entry_category="compounder",
        current_category="junk",
    )
    assert d.action == "EXIT_THESIS"
    assert any("Catégorie Buffett dégradée" in r for r in d.reasons)


def test_category_drop_2_levels_triggers_trim():
    """Compounder → baseline (drop 3 paliers) → TRIM."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=98.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        entry_category="compounder",
        current_category="baseline",
    )
    assert d.action == "TRIM"
    assert any("Dérive catégorie" in r for r in d.reasons)


def test_category_drop_1_level_no_signal():
    """compounder → high_quality (drop 1) → HOLD (pas significatif)."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=98.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        entry_category="compounder",
        current_category="high_quality",
    )
    assert d.action == "HOLD"


def test_category_upgrade_no_signal():
    """junior → compounder (amélioration) → HOLD."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=98.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        entry_category="junior",
        current_category="compounder",
    )
    assert d.action == "HOLD"


def test_category_missing_data_no_signal():
    """Si entry_category None → on ne signale rien."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=98.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        entry_category=None,
        current_category="junk",
    )
    assert d.action == "HOLD"


# ─────────────────────────────────────────────────────────────────
# Phase 1 plus — confidence drop, insider signal
# ─────────────────────────────────────────────────────────────────
def test_confidence_drop_triggers_trim():
    """Entry confidence 90 → current 50 (-40 pts) → TRIM."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=105.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        entry_confidence=90,
        confidence_score=50,
    )
    assert d.action == "TRIM"
    assert any("confiance" in r.lower() for r in d.reasons)


def test_confidence_stable_no_signal():
    """90→80 (drop 10 pts) → pas de signal (sous seuil 30)."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=105.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        entry_confidence=90,
        confidence_score=80,
    )
    assert d.action == "HOLD"


def test_insider_sells_triggers_trim():
    """insider_score ≤ 20 → TRIM."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=105.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        insider_score=15,
    )
    assert d.action == "TRIM"
    assert any("insider" in r.lower() for r in d.reasons)


def test_insider_neutral_no_signal():
    d = decide(
        ticker="X", entry_price=100.0, current_price=105.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        insider_score=55,
    )
    assert d.action == "HOLD"


def test_low_confidence_inhibits_valuation_exit():
    """Confidence < 50 → EXIT_VALUATION inhibé même si gain et survalorisation."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=140.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        current_peg_ratio=4.5,  # normalement triggers EXIT_VALUATION
        confidence_score=40,    # mais data peu fiable → on ne coupe pas
    )
    assert d.action == "HOLD"


def test_high_confidence_allows_valuation_exit():
    """Confidence ≥ 50 → EXIT_VALUATION normal."""
    d = decide(
        ticker="X", entry_price=100.0, current_price=140.0, stop_loss=70.0,
        thesis={"status": "INTACT", "reasons_break": [], "reasons_warn": []},
        current_peg_ratio=4.5,
        confidence_score=85,
    )
    assert d.action == "EXIT_VALUATION"
