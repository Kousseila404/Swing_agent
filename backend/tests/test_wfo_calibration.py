"""Tests unitaires — modules/wfo_calibration.py

Couvre :
  • _spearman : signe attendu, ties, edge cases (variance nulle, < 3 points).
  • _build_pairs : assemblage scoring × prix t × prix t+1, gestion des None.
  • _ic_per_pillar : un pilier prédictif retourne IC > 0 ; tout-None → nan.
  • _enum_simplex : combinaisons sommant à 1 sur grille.
  • _optimal_weights : maximisation IC sur le simplex, fallback < 2 piliers.
  • run_walk_forward : valid path + erreurs claires si historique trop court.
"""
from __future__ import annotations

import math
from datetime import date

import pytest

from modules import wfo_calibration as wfo


# ─────────────────────────────────────────────────────────────────
# _spearman
# ─────────────────────────────────────────────────────────────────

def test_spearman_perfect_positive_returns_one():
    """Strictement croissant → ρ = +1."""
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert wfo._spearman(xs, ys) == pytest.approx(1.0)


def test_spearman_perfect_negative_returns_minus_one():
    """Strictement décroissant → ρ = −1."""
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [50.0, 40.0, 30.0, 20.0, 10.0]
    assert wfo._spearman(xs, ys) == pytest.approx(-1.0)


def test_spearman_returns_none_below_three_points():
    """Stats triviales avec < 3 points → None."""
    assert wfo._spearman([1.0], [2.0]) is None
    assert wfo._spearman([1.0, 2.0], [3.0, 4.0]) is None


def test_spearman_returns_none_zero_variance():
    """Si l'une des séries est constante, σ=0 → corrélation indéfinie → None."""
    assert wfo._spearman([1.0, 1.0, 1.0, 1.0], [1.0, 2.0, 3.0, 4.0]) is None


def test_spearman_handles_ties_with_mid_rank():
    """Ties → mid-rank. Test que 2 ex-aequo dans xs donnent un IC fini cohérent."""
    xs = [1.0, 2.0, 2.0, 3.0]
    ys = [1.0, 2.0, 2.0, 3.0]
    rho = wfo._spearman(xs, ys)
    assert rho is not None
    assert rho == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────
# _build_pairs — assemblage 3 snapshots
# ─────────────────────────────────────────────────────────────────

def _mk_snap(rows: dict) -> dict:
    """Helper pour construire un snapshot minimal."""
    return {"tickers": rows}


def test_build_pairs_computes_forward_return():
    """Un ticker présent partout avec prix valides → row avec fwd_return correct."""
    snap = _mk_snap({
        "AAPL": {"current_price": 100.0, "quality_score": 80.0,
                  "value_score": 70.0, "momentum_score": 60.0,
                  "risk_score": 75.0, "sentiment_score": 50.0,
                  "piotroski_score": 65.0, "growth_score": 55.0},
    })
    snap_t1 = _mk_snap({
        "AAPL": {"current_price": 110.0},
    })
    rows = wfo._build_pairs(snap, snap, snap_t1)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["fwd_return"] == pytest.approx(0.10)
    # Tous les piliers doivent être conservés.
    assert rows[0]["quality_score"] == 80.0
    assert rows[0]["momentum_score"] == 60.0


def test_build_pairs_skips_tickers_missing_price():
    """Tickers sans prix t ou t+1 → exclus (pas de NaN dans le résultat)."""
    snap = _mk_snap({
        "AAPL": {"current_price": 100.0, "quality_score": 80.0},
        "NOPRICE": {"current_price": None, "quality_score": 50.0},
    })
    snap_t1 = _mk_snap({
        "AAPL": {"current_price": 105.0},
        "NOPRICE": {"current_price": 50.0},
    })
    rows = wfo._build_pairs(snap, snap, snap_t1)
    assert {r["ticker"] for r in rows} == {"AAPL"}


def test_build_pairs_uses_score_snap_separately():
    """`snap_score ≠ snap_price_t` doit être respecté (cas anti-lookahead)."""
    snap_score = _mk_snap({
        "AAPL": {"current_price": 999.0, "quality_score": 99.0},
    })
    snap_t = _mk_snap({"AAPL": {"current_price": 100.0}})
    snap_t1 = _mk_snap({"AAPL": {"current_price": 110.0}})

    rows = wfo._build_pairs(snap_score, snap_t, snap_t1)
    assert len(rows) == 1
    # Le score vient de snap_score, le return de (snap_t, snap_t1).
    assert rows[0]["quality_score"] == 99.0
    assert rows[0]["fwd_return"] == pytest.approx(0.10)


# ─────────────────────────────────────────────────────────────────
# _ic_per_pillar
# ─────────────────────────────────────────────────────────────────

def test_ic_per_pillar_predictive_signal_is_positive():
    """Construire un dataset où quality_score prédit fwd_return → IC > 0."""
    rows = [
        {"ticker": f"T{i}", "fwd_return": i * 0.01,
         "quality_score": float(i * 10)}
        for i in range(10)
    ]
    ic = wfo._ic_per_pillar(rows)
    assert ic["quality_score"] > 0.5, \
        f"IC attendue > 0.5 sur signal parfait, got {ic['quality_score']}"


def test_ic_per_pillar_pillar_all_none_returns_nan():
    """Pilier jamais renseigné → nan (pour exclure de l'optim)."""
    rows = [
        {"ticker": f"T{i}", "fwd_return": i * 0.01, "quality_score": 50.0}
        for i in range(10)
    ]
    ic = wfo._ic_per_pillar(rows)
    # value_score absent partout → nan.
    assert math.isnan(ic["value_score"])


def test_ic_per_pillar_below_min_pairs_returns_nan():
    """< 5 paires valides → nan (pas assez de signal)."""
    rows = [
        {"ticker": f"T{i}", "fwd_return": i * 0.01, "quality_score": 50.0}
        for i in range(3)
    ]
    ic = wfo._ic_per_pillar(rows)
    assert math.isnan(ic["quality_score"])


# ─────────────────────────────────────────────────────────────────
# _enum_simplex
# ─────────────────────────────────────────────────────────────────

def test_enum_simplex_2d_step_0_25():
    """n=2, step=0.25 → 5 points : (0,1)(0.25,0.75)(0.5,0.5)(0.75,0.25)(1,0)."""
    pts = wfo._enum_simplex(2, 0.25)
    assert len(pts) == 5
    for w in pts:
        assert sum(w) == pytest.approx(1.0)
        assert all(x >= 0 for x in w)


def test_enum_simplex_3d_step_0_5_count():
    """n=3, step=0.5 ⇒ multinomial(2; 3) = C(2+2,2) = 6 points."""
    pts = wfo._enum_simplex(3, 0.5)
    assert len(pts) == 6
    for w in pts:
        assert sum(w) == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────
# _optimal_weights
# ─────────────────────────────────────────────────────────────────

def test_optimal_weights_picks_predictive_pillar():
    """Avec un seul signal prédictif (quality), l'optimiseur doit lui donner
    quasi tout le poids."""
    # Dataset : quality prédit fwd_return parfaitement, autres piliers = bruit.
    import random
    random.seed(0)
    rows = []
    for i in range(20):
        rows.append({
            "ticker": f"T{i}",
            "fwd_return": i * 0.01,  # corrélé à quality
            "quality_score": float(i * 5),
            "value_score": random.random() * 100,
            "momentum_score": random.random() * 100,
            "risk_score": random.random() * 100,
            "sentiment_score": random.random() * 100,
            "piotroski_score": random.random() * 100,
            "growth_score": random.random() * 100,
        })

    weights, ic = wfo._optimal_weights(rows)
    assert weights["quality_score"] >= 0.5, \
        f"quality_score doit dominer (signal parfait), got {weights}"
    assert ic > 0.5, f"IC attendue > 0.5, got {ic}"


def test_optimal_weights_fallback_when_lt_two_valid_pillars():
    """Avec 1 seul pilier valide → fallback equal-weights."""
    rows = [
        {"ticker": f"T{i}", "fwd_return": i * 0.01, "quality_score": 50.0}
        for i in range(10)
    ]
    weights, _ic = wfo._optimal_weights(rows)
    # Tous égaux → 1/7 par pilier.
    expected = 1.0 / len(wfo.PILLARS)
    for p in wfo.PILLARS:
        assert weights[p] == pytest.approx(expected)


# ─────────────────────────────────────────────────────────────────
# run_walk_forward — erreurs claires
# ─────────────────────────────────────────────────────────────────

def test_run_walk_forward_raises_when_history_too_short(monkeypatch):
    """< 5 snapshots → ValueError explicite (pas de fold possible)."""
    monkeypatch.setattr(
        wfo.universe_history, "list_snapshots",
        lambda: [date(2026, 4, 22), date(2026, 4, 23)],
    )
    with pytest.raises(ValueError, match="Au moins 5 snapshots requis"):
        wfo.run_walk_forward(train_days=60, test_days=20)


def test_run_walk_forward_returns_empty_when_no_pairs(monkeypatch):
    """Snapshots existent mais sans prix valides → ValueError sur paires
    insuffisantes (chemin de defense distinct du précédent)."""
    fake_dates = [date(2026, 4, 20 + i) for i in range(6)]
    monkeypatch.setattr(wfo.universe_history, "list_snapshots", lambda: fake_dates)

    # Snapshots sans current_price → 0 paires utilisables.
    monkeypatch.setattr(
        wfo.universe_history, "read_snapshot",
        lambda d: {"tickers": {"AAPL": {"quality_score": 50.0}}},
    )
    with pytest.raises(ValueError, match="Moins de 3 paires utilisables"):
        wfo.run_walk_forward(train_days=2, test_days=1)
