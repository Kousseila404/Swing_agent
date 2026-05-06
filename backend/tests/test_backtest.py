"""Tests unitaires — modules/backtest.py

Couvre les fonctions internes pures (sans IO snapshots) :
  • _rank_top_n : tri desc, active_filter, score_field alternatif.
  • _extract_prices : skip None, NaN, prix ≤ 0.
  • _compute_weights : 3 modes (equal / score / risk_parity), fallback,
    renormalisation, ValueError si mode inconnu.
  • _compute_period : returns pondérés, turnover one-way, costs (slippage,
    commission, impact ADTV), point-in-time filter, override dates avec lag.
  • _compute_stats : equity, max_dd, Sharpe, hit_rate, edge case empty.
  • run_titan_top_n : ValueError si < 2 snapshots, intégration des flags.
"""
from __future__ import annotations

from datetime import date

import pytest

from modules import backtest as bt

# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _mk_snap(rows: dict, snapshot_date: str = "2026-04-22") -> dict:
    return {"snapshot_date": snapshot_date, "tickers": rows}


# ─────────────────────────────────────────────────────────────────
# _rank_top_n
# ─────────────────────────────────────────────────────────────────

def test_rank_top_n_sorts_desc_by_score():
    """Les tickers doivent être triés par titan_composite_score décroissant."""
    snap = _mk_snap({
        "AAPL": {"titan_composite_score": 70.0},
        "MSFT": {"titan_composite_score": 90.0},
        "TSLA": {"titan_composite_score": 50.0},
    })
    top = bt._rank_top_n(snap, top_n=10)
    assert [t for t, _ in top] == ["MSFT", "AAPL", "TSLA"]


def test_rank_top_n_skips_invalid_scores():
    """Les scores None / NaN / non-numériques sont exclus du ranking."""
    snap = _mk_snap({
        "OK": {"titan_composite_score": 80.0},
        "NAN": {"titan_composite_score": float("nan")},
        "NULL": {"titan_composite_score": None},
        "STR": {"titan_composite_score": "high"},
    })
    top = bt._rank_top_n(snap, top_n=10)
    assert [t for t, _ in top] == ["OK"]


def test_rank_top_n_active_filter_excludes_delisted():
    """active_filter=set → seuls les tickers du set sont rankés."""
    snap = _mk_snap({
        "AAPL": {"titan_composite_score": 90.0},
        "DELISTED": {"titan_composite_score": 95.0},
        "MSFT": {"titan_composite_score": 80.0},
    })
    top = bt._rank_top_n(snap, top_n=10, active_filter={"AAPL", "MSFT"})
    assert "DELISTED" not in [t for t, _ in top]
    assert [t for t, _ in top] == ["AAPL", "MSFT"]


def test_rank_top_n_caps_to_top_n():
    """top_n=2 sur 5 candidats → seulement les 2 meilleurs."""
    snap = _mk_snap({
        f"T{i}": {"titan_composite_score": float(i)}
        for i in range(5)
    })
    top = bt._rank_top_n(snap, top_n=2)
    assert len(top) == 2
    assert [t for t, _ in top] == ["T4", "T3"]


# ─────────────────────────────────────────────────────────────────
# _extract_prices
# ─────────────────────────────────────────────────────────────────

def test_extract_prices_skips_invalid():
    """None, NaN, ≤ 0 → exclus. Numérique > 0 → conservé."""
    snap = _mk_snap({
        "OK": {"current_price": 100.0},
        "NEG": {"current_price": -5.0},
        "ZERO": {"current_price": 0.0},
        "NAN": {"current_price": float("nan")},
        "NULL": {"current_price": None},
    })
    out = bt._extract_prices(snap, ["OK", "NEG", "ZERO", "NAN", "NULL", "ABSENT"])
    assert out == {"OK": 100.0}


# ─────────────────────────────────────────────────────────────────
# _compute_weights
# ─────────────────────────────────────────────────────────────────

def test_weights_equal_mode():
    """Mode equal → 1/N partout."""
    top = [("A", 10.0), ("B", 20.0), ("C", 30.0), ("D", 40.0)]
    w = bt._compute_weights(_mk_snap({}), top, "equal")
    assert all(v == pytest.approx(0.25) for v in w.values())
    assert sum(w.values()) == pytest.approx(1.0)


def test_weights_score_mode_proportional():
    """Mode score → poids ∝ (score + floor). Plus haut score = plus gros poids."""
    top = [("A", 10.0), ("B", 30.0), ("C", 50.0)]
    w = bt._compute_weights(_mk_snap({}), top, "score")
    assert sum(w.values()) == pytest.approx(1.0)
    # Plus le score est haut, plus le poids est haut.
    assert w["A"] < w["B"] < w["C"]


def test_weights_score_mode_handles_negative_scores():
    """Scores négatifs : rebase positif (floor) → poids tous > 0, somme = 1."""
    top = [("A", -5.0), ("B", 0.0), ("C", 10.0)]
    w = bt._compute_weights(_mk_snap({}), top, "score")
    assert all(v > 0 for v in w.values())
    assert sum(w.values()) == pytest.approx(1.0)


def test_weights_risk_parity_inverse_vol():
    """Mode risk_parity : poids ∝ 1/vol. Faible vol → gros poids."""
    snap = _mk_snap({
        "LOW_VOL": {"realized_vol_30d": 0.10},
        "MID_VOL": {"realized_vol_30d": 0.20},
        "HIGH_VOL": {"realized_vol_30d": 0.40},
    })
    top = [("LOW_VOL", 80.0), ("MID_VOL", 70.0), ("HIGH_VOL", 60.0)]
    w = bt._compute_weights(snap, top, "risk_parity")
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["LOW_VOL"] > w["MID_VOL"] > w["HIGH_VOL"]


def test_weights_risk_parity_falls_back_to_equal_when_vol_missing():
    """Si majorité des vols absents → fallback equal."""
    snap = _mk_snap({
        "ONLY_ONE": {"realized_vol_30d": 0.20},
        "NO_VOL_A": {},
        "NO_VOL_B": {},
        "NO_VOL_C": {},
    })
    top = [("ONLY_ONE", 90.0), ("NO_VOL_A", 80.0),
           ("NO_VOL_B", 70.0), ("NO_VOL_C", 60.0)]
    w = bt._compute_weights(snap, top, "risk_parity")
    # 1 vol valide sur 4 → < (4+1)//2=2 → fallback equal-weight
    assert all(v == pytest.approx(0.25) for v in w.values())


def test_weights_invalid_mode_raises():
    """Mode inconnu → ValueError explicite."""
    with pytest.raises(ValueError, match="weighting inconnu"):
        bt._compute_weights(_mk_snap({}), [("A", 10.0)], "wat")


def test_weights_empty_top_returns_empty_dict():
    """Top vide → dict vide (pas de division par 0)."""
    assert bt._compute_weights(_mk_snap({}), [], "equal") == {}


# ─────────────────────────────────────────────────────────────────
# _compute_period — returns + turnover
# ─────────────────────────────────────────────────────────────────

def _two_snaps_simple_return():
    """Setup minimal : 1 ticker, 100 → 110 = +10 %."""
    snap_t = _mk_snap({
        "AAPL": {"titan_composite_score": 80.0, "current_price": 100.0,
                  "market_cap": 1e12},
    })
    snap_t1 = _mk_snap({
        "AAPL": {"current_price": 110.0},
    })
    return snap_t, snap_t1


def test_compute_period_basic_equal_weight_return():
    """1 ticker, +10 % → portfolio_return_gross == +10 %, no costs."""
    snap_t, snap_t1 = _two_snaps_simple_return()
    p = bt._compute_period(snap_t, snap_t1, top_n=10)
    assert p.portfolio_return_gross == pytest.approx(0.10)
    assert p.portfolio_return == pytest.approx(0.10)
    assert p.cost_pct == 0.0
    assert p.n_valid == 1
    assert p.n_skipped == 0


def test_compute_period_turnover_first_period_full_rotation():
    """Sans prev_weights → turnover = 1.0 (tout le book entre)."""
    snap_t, snap_t1 = _two_snaps_simple_return()
    p = bt._compute_period(snap_t, snap_t1, top_n=10, prev_weights=None)
    # 1 ticker, weights[AAPL]=1.0, prev=0.0 → |Δw|=1.0, /2 = 0.5 (one-way).
    assert p.turnover == pytest.approx(0.5)


def test_compute_period_turnover_zero_when_unchanged():
    """Si prev_weights == weights → turnover = 0."""
    snap_t, snap_t1 = _two_snaps_simple_return()
    p = bt._compute_period(snap_t, snap_t1, top_n=10,
                           prev_weights={"AAPL": 1.0})
    assert p.turnover == pytest.approx(0.0)


def test_compute_period_slippage_proportional_to_turnover():
    """Slippage en bps × turnover one-way → cost_pct cohérent.

    1 ticker, weights AAPL=1.0, prev=0 → turnover 0.5, slippage 100bps
    → coût = 0.01 × 0.5 = 0.005.
    """
    snap_t, snap_t1 = _two_snaps_simple_return()
    p = bt._compute_period(snap_t, snap_t1, top_n=10,
                           slippage_bps=100.0, prev_weights=None)
    assert p.cost_pct == pytest.approx(0.005)
    # Net = gross − cost.
    assert p.portfolio_return == pytest.approx(p.portfolio_return_gross - 0.005)


def test_compute_period_commission_uses_avg_price():
    """commission $/sh → translation en bps via prix moyen.

    1 ticker à 100 $, commission 0.05 $/sh → 0.05/100 = 5 bps,
    × turnover 0.5 → 2.5 bps = 0.00025.
    """
    snap_t, snap_t1 = _two_snaps_simple_return()
    p = bt._compute_period(snap_t, snap_t1, top_n=10,
                           commission_per_share=0.05, prev_weights=None)
    assert p.cost_pct == pytest.approx(0.00025)


def test_compute_period_skipped_when_no_price_t():
    """Tickers sans prix t exclus du book → n_skipped + return réduit."""
    snap_t = _mk_snap({
        "OK": {"titan_composite_score": 80.0, "current_price": 100.0},
        "NOPRICE": {"titan_composite_score": 90.0, "current_price": None},
    })
    snap_t1 = _mk_snap({
        "OK": {"current_price": 110.0},
        "NOPRICE": {"current_price": 50.0},
    })
    p = bt._compute_period(snap_t, snap_t1, top_n=10)
    assert "NOPRICE" in p.top_tickers
    assert p.n_skipped == 1
    assert p.n_valid == 1
    # weights renormalisés : OK doit avoir 100 % du book effective
    assert p.weights == {"OK": pytest.approx(1.0)}


def test_compute_period_active_filter_excludes_delisted():
    """active_filter ne ranke que les tickers du set."""
    snap_t = _mk_snap({
        "AAPL": {"titan_composite_score": 80.0, "current_price": 100.0},
        "DEAD": {"titan_composite_score": 99.0, "current_price": 50.0},
    })
    snap_t1 = _mk_snap({
        "AAPL": {"current_price": 110.0},
        "DEAD": {"current_price": 60.0},
    })
    p = bt._compute_period(snap_t, snap_t1, top_n=10,
                           active_filter={"AAPL"})
    assert "DEAD" not in p.top_tickers
    assert p.weights == {"AAPL": pytest.approx(1.0)}


def test_compute_period_impact_negligible_on_large_cap():
    """Book 100k$ sur S&P-size large-cap (1T$) → impact quasi-nul."""
    snap_t = _mk_snap({
        "MEGA": {"titan_composite_score": 80.0, "current_price": 200.0,
                  "market_cap": 1e12, "avg_volume_3m": 5e7},
    })
    snap_t1 = _mk_snap({"MEGA": {"current_price": 200.0}})
    p = bt._compute_period(snap_t, snap_t1, top_n=10,
                           book_size_usd=100_000.0, impact_coef=10.0)
    # Order size 50k$ (turnover 0.5 sur 100k book) vs ADTV 10G$ → ratio ~5e-6
    # → impact² ~3e-11 → ~0.
    assert p.cost_pct < 1e-5


def test_compute_period_impact_kicks_in_on_oversized_book():
    """Book gigantesque sur small market_cap → impact significatif."""
    snap_t = _mk_snap({
        "SMALL": {"titan_composite_score": 80.0, "current_price": 50.0,
                  "market_cap": 1e9},  # 1B$ market cap, no avg_volume_3m
    })
    snap_t1 = _mk_snap({"SMALL": {"current_price": 51.0}})
    p = bt._compute_period(snap_t, snap_t1, top_n=10,
                           book_size_usd=1e8, impact_coef=10000.0,
                           fallback_turnover_ratio=0.005)
    # Order = 0.5×100M = 50M$ ; ADTV proxy = 1B × 0.005 = 5M$ ; ratio = 10
    # → impact = 1e4 × 1e-4 × 100 × 0.5 = 50 (50 fois le book...)
    # On vérifie juste qu'il est notable (> 1 % => modèle réagit).
    assert p.cost_pct > 0.01


def test_compute_period_top_n_caps_basket_size():
    """Avec 5 tickers et top_n=2 → portfolio limité aux 2 meilleurs."""
    snap_t = _mk_snap({
        f"T{i}": {"titan_composite_score": float(i),
                  "current_price": 100.0, "market_cap": 1e10}
        for i in range(5)
    })
    snap_t1 = _mk_snap({
        f"T{i}": {"current_price": 100.0 * (1 + i * 0.01)}
        for i in range(5)
    })
    p = bt._compute_period(snap_t, snap_t1, top_n=2)
    assert len(p.top_tickers) == 2
    assert set(p.top_tickers) == {"T4", "T3"}
    # Equal-weight 2 tickers → 0.5 chacun
    assert all(v == pytest.approx(0.5) for v in p.weights.values())


# ─────────────────────────────────────────────────────────────────
# _compute_stats
# ─────────────────────────────────────────────────────────────────

def _mk_period(ret: float) -> bt.PeriodResult:
    """PeriodResult minimal pour les tests stats."""
    return bt.PeriodResult(
        signal_date="d0", next_date="d1", top_tickers=[], weights={},
        returns={}, portfolio_return_gross=ret, portfolio_return=ret,
        cost_pct=0.0, turnover=0.0, n_valid=0, n_skipped=0,
    )


def test_stats_empty_returns_zeros():
    """Pas de période → struct safe avec 0/None."""
    s = bt._compute_stats([])
    assert s["total_return"] == 0.0
    assert s["sharpe_daily"] is None
    assert s["max_drawdown"] == 0.0


def test_stats_max_drawdown_from_peak():
    """Suite +10 %, +5 %, -8 %, +2 % → peak 1.155, low 1.063 → DD ~7.97 %."""
    rets = [0.10, 0.05, -0.08, 0.02]
    periods = [_mk_period(r) for r in rets]
    s = bt._compute_stats(periods)
    assert s["max_drawdown"] > 0.07
    assert s["max_drawdown"] < 0.09


def test_stats_hit_rate():
    """3 positifs sur 5 → hit_rate 0.6."""
    periods = [_mk_period(r) for r in [0.01, 0.02, -0.01, 0.005, -0.02]]
    s = bt._compute_stats(periods)
    assert s["hit_rate"] == pytest.approx(0.6)


def test_stats_sharpe_none_with_single_period():
    """1 seule période → σ indéfinie → Sharpe None."""
    s = bt._compute_stats([_mk_period(0.05)])
    assert s["sharpe_daily"] is None


def test_stats_sharpe_none_when_zero_variance():
    """Tous les rets identiques → σ=0 → Sharpe None."""
    s = bt._compute_stats([_mk_period(0.01) for _ in range(5)])
    assert s["sharpe_daily"] is None


# ─────────────────────────────────────────────────────────────────
# run_titan_top_n — intégration
# ─────────────────────────────────────────────────────────────────

def test_run_titan_top_n_raises_when_no_snapshots(monkeypatch):
    """0 snapshot → ValueError clair."""
    monkeypatch.setattr(bt.universe_history, "list_snapshots", lambda: [])
    with pytest.raises(ValueError, match="2 snapshots requis"):
        bt.run_titan_top_n(top_n=10, benchmark=None)


def test_run_titan_top_n_smoke_with_two_snapshots(monkeypatch):
    """Path heureux end-to-end : 2 snapshots → 1 période, return cohérent."""
    fake_dates = [date(2026, 4, 22), date(2026, 4, 23)]
    snap_a = _mk_snap({
        "AAPL": {"titan_composite_score": 80.0, "current_price": 100.0,
                  "market_cap": 1e12},
    }, snapshot_date="2026-04-22")
    snap_b = _mk_snap({"AAPL": {"current_price": 105.0}}, snapshot_date="2026-04-23")

    monkeypatch.setattr(bt.universe_history, "list_snapshots", lambda: fake_dates)
    monkeypatch.setattr(bt.universe_history, "read_snapshot",
                        lambda d: snap_a if d == fake_dates[0] else snap_b)

    result = bt.run_titan_top_n(top_n=10, benchmark=None,
                                 weighting="equal", point_in_time=False,
                                 publication_lag_days=0)
    assert result.diagnostics["n_periods"] == 1
    assert result.total_return == pytest.approx(0.05, rel=1e-6)


def test_run_titan_top_n_publication_lag_skips_periods_when_history_short(monkeypatch):
    """Lag > historique → toutes les périodes skippées (0 n_periods)."""
    fake_dates = [date(2026, 4, 22), date(2026, 4, 23), date(2026, 4, 24)]
    snap = _mk_snap({"AAPL": {"titan_composite_score": 80.0,
                              "current_price": 100.0, "market_cap": 1e12}})
    monkeypatch.setattr(bt.universe_history, "list_snapshots", lambda: fake_dates)
    monkeypatch.setattr(bt.universe_history, "read_snapshot", lambda d: snap)

    result = bt.run_titan_top_n(
        top_n=10, benchmark=None, point_in_time=False,
        publication_lag_days=90,
    )
    assert result.diagnostics["n_skipped_lag"] >= 2
    assert result.diagnostics["n_periods"] == 0
