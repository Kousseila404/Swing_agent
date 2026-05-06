"""Tests fundamentals_levels — SL/TP Buffett-anchored (refonte 2026-04-29 étape 1)."""
from __future__ import annotations

from modules.fundamentals_levels import compute_fundamental_levels


# ─── Compounder rare (Buffett dream) ──────────────────────────
def test_compounder_let_it_ride():
    """Q90/P9/V70/PEG 1.0 → SL large, TP haut, flag let_it_ride."""
    r = compute_fundamental_levels(
        100.0, quality_score=90, piotroski_score=9, value_score=70, peg_ratio=1.0,
    )
    assert r["method"] == "fundamentals_anchored"
    assert r["let_it_ride"] is True
    # SL large : 30 × 1.24 × 1.12 ≈ 41.7 %
    assert 38.0 <= r["sl_pct"] <= 45.0
    # TP haut : 100 × 1.16 × 1.0 × 1.24 ≈ 144 % → clampé à 120 %
    assert r["tp_pct"] == 120.0
    assert r["sl"] < 100 < r["tp"]


def test_compounder_breakdown_traceable():
    r = compute_fundamental_levels(
        100.0, quality_score=88, piotroski_score=9, value_score=65, peg_ratio=1.2,
    )
    bd = r["breakdown"]
    assert any("SL base" in line for line in bd)
    assert any("TP base" in line for line in bd)
    assert any("let_it_ride" in line for line in bd)


# ─── Junk / mediocre ──────────────────────────────────────────
def test_junk_tight_sl_low_tp():
    """Q40/P3/V25/PEG 4.0 → SL serré, TP bas."""
    r = compute_fundamental_levels(
        100.0, quality_score=40, piotroski_score=3, value_score=25, peg_ratio=4.0,
    )
    assert r["method"] == "fundamentals_anchored"
    assert r["let_it_ride"] is False
    # SL : 30 × 0.94 × 0.94 ≈ 26.5 % → entre min 15 et 30
    assert 25.0 <= r["sl_pct"] <= 30.0
    # TP : 100 × 0.8 × 0.7 × 0.94 ≈ 52.6 %
    assert 45.0 <= r["tp_pct"] <= 60.0


def test_extreme_junk_clamped_at_min():
    """Q5/P0/V5/PEG 5 → tous les facteurs tirent vers le bas, clamp min."""
    r = compute_fundamental_levels(
        100.0, quality_score=5, piotroski_score=0, value_score=5, peg_ratio=5.0,
    )
    # Le clamp min SL = 15 %, min TP = 25 %
    assert r["sl_pct"] >= 15.0
    assert r["tp_pct"] >= 25.0


# ─── Asymétrie qualité (cas Buffett-pure) ─────────────────────
def test_compounder_has_wider_sl_than_junior():
    """Même prix, deux profils opposés → corde plus large pour le compounder."""
    compounder = compute_fundamental_levels(
        100.0, quality_score=90, piotroski_score=9, value_score=60, peg_ratio=1.2,
    )
    junior = compute_fundamental_levels(
        100.0, quality_score=45, piotroski_score=3, value_score=60, peg_ratio=1.2,
    )
    # Compounder : SL plus large (Buffett tient les wonderful businesses)
    assert compounder["sl_pct"] > junior["sl_pct"]
    # Compounder : TP plus haut aussi (qualité × value)
    assert compounder["tp_pct"] > junior["tp_pct"]


# ─── Fondamentales partielles ─────────────────────────────────
def test_quality_only():
    """Seule Q connue → autres facteurs neutres ×1.0."""
    r = compute_fundamental_levels(100.0, quality_score=80)
    assert r["method"] == "fundamentals_anchored"
    # SL = 30 × 1.18 × 1.0 ≈ 35.4 %
    assert 33.0 <= r["sl_pct"] <= 37.0


def test_no_fundamentals_falls_back_to_base():
    """Aucune fondamentale → bases 30/100, method=fundamentals_unavailable."""
    r = compute_fundamental_levels(100.0)
    assert r["method"] == "fundamentals_unavailable"
    assert r["sl_pct"] == 30.0
    assert r["tp_pct"] == 100.0
    assert r["let_it_ride"] is False
    assert any("Aucune fondamentale" in line for line in r["breakdown"])


# ─── PEG edge cases ───────────────────────────────────────────
def test_peg_negative_ignored():
    """PEG négatif (earnings négatifs) → facteur neutre, pas de pénalité."""
    r1 = compute_fundamental_levels(100.0, quality_score=70, peg_ratio=-2.0)
    r2 = compute_fundamental_levels(100.0, quality_score=70, peg_ratio=None)
    assert r1["tp_pct"] == r2["tp_pct"]


def test_peg_extreme_caps_tp():
    """PEG 4 → ×0.7 sur TP (overpriced growth)."""
    r_sain   = compute_fundamental_levels(100.0, quality_score=70, value_score=50, peg_ratio=1.0)
    r_extreme = compute_fundamental_levels(100.0, quality_score=70, value_score=50, peg_ratio=4.0)
    assert r_extreme["tp_pct"] < r_sain["tp_pct"]


# ─── Bornes et invariants ─────────────────────────────────────
def test_sl_below_price_tp_above():
    """Quel que soit le profil LONG, SL < price < TP."""
    for q in (10, 50, 90):
        for p in (0, 5, 9):
            for v in (10, 50, 80):
                r = compute_fundamental_levels(
                    50.0, quality_score=q, piotroski_score=p, value_score=v,
                )
                if r["sl"] is not None:
                    assert r["sl"] < 50.0 < r["tp"], (
                        f"Q{q} P{p} V{v} : SL={r['sl']} TP={r['tp']}"
                    )


def test_short_mirrors_long():
    r = compute_fundamental_levels(
        100.0, quality_score=70, piotroski_score=7, value_score=50, peg_ratio=1.5,
        direction="SHORT",
    )
    assert r["sl"] > 100 > r["tp"] > 0


def test_invalid_price_returns_none():
    for bad in (None, 0, -5, float("nan"), float("inf")):
        r = compute_fundamental_levels(bad, quality_score=80)
        assert r["sl"] is None and r["tp"] is None
        assert r["method"] == "invalid_price"


def test_unknown_direction_rejected():
    r = compute_fundamental_levels(100.0, direction="SIDE")
    assert r["method"] == "unsupported_direction"


# ─── Let_it_ride threshold ────────────────────────────────────
def test_ride_requires_all_three_thresholds():
    """Ride flag uniquement si Q≥85 ET P≥8 ET V≥60."""
    # Manque V
    r = compute_fundamental_levels(100.0, quality_score=85, piotroski_score=8, value_score=55)
    assert r["let_it_ride"] is False
    # Manque P
    r = compute_fundamental_levels(100.0, quality_score=85, piotroski_score=7, value_score=60)
    assert r["let_it_ride"] is False
    # Tous OK
    r = compute_fundamental_levels(100.0, quality_score=85, piotroski_score=8, value_score=60)
    assert r["let_it_ride"] is True
