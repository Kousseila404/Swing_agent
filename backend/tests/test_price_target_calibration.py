"""Tests price_target_calibration — boucle IC (docs/price_target_design.md §6/§7)."""
from __future__ import annotations

from modules.price_target_calibration import (
    _candidate_configs,
    _check_stop_conditions,
    _normalize_weights,
    evaluate_weights,
    select_pairs,
)


# ─── select_pairs ────────────────────────────────────────────
def test_select_pairs_matches_target_window():
    # Génère 100 jours consécutifs à partir du 2026-04-01.
    from datetime import date, timedelta
    base = date(2026, 4, 1)
    dates = [(base + timedelta(days=i)).isoformat() for i in range(100)]

    pairs = select_pairs(dates, window_days=60, tolerance_days=5)
    assert len(pairs) > 0
    for t0, t1 in pairs:
        d0 = date.fromisoformat(t0)
        d1 = date.fromisoformat(t1)
        assert 55 <= (d1 - d0).days <= 65


def test_select_pairs_empty_when_no_window_fits():
    dates = ["2026-04-01", "2026-04-02", "2026-04-03"]
    pairs = select_pairs(dates, window_days=60, tolerance_days=2)
    assert pairs == []


def test_select_pairs_tolerates_a_gap():
    from datetime import date, timedelta
    base = date(2026, 4, 1)
    # 90 jours consécutifs sauf un trou au jour 40 (comme le gap réel 2026-04-23).
    dates = [(base + timedelta(days=i)).isoformat() for i in range(90) if i != 40]
    pairs = select_pairs(dates, window_days=60, tolerance_days=5)
    assert len(pairs) > 0


# ─── evaluate_weights — IC sur données synthétiques à signal connu ─────
def _synthetic_by_date(n_tickers: int = 20) -> dict:
    """Univers synthétique : le rendement réalisé à t+N est corrélé
    positivement au signal (price_target/current_price - 1) — sanity check
    que l'IC calculé a le bon signe et une magnitude raisonnable."""
    by_date: dict = {}
    t0_rows = {}
    t1_rows = {}
    for i in range(n_tickers):
        # Ticker i a un forward_pe d'autant plus bas (donc plus "sous-évalué"
        # vs médiane secteur) que i est petit → price_target relatif plus haut.
        fwd_pe = 10.0 + i * 2.0  # 10..48
        price0 = 100.0
        # Le rendement réalisé est inversement lié au multiple de départ
        # (mean-reversion) : ticker cher (fwd_pe haut) sous-performe.
        realized_return = 0.30 - i * 0.02  # de +0.30 à -0.08
        price1 = price0 * (1 + realized_return)

        t0_rows[f"T{i}"] = {
            "current_price": price0, "sector": "Technology", "forward_pe": fwd_pe,
            "ev_to_ebitda": None, "peg_ratio": None,
            "quality_score": 60.0, "value_score": 55.0, "f_score": 6,
            "titan_tilt_flags": [], "data_quality": 0.9,
            # Baseline analystes légèrement bruitée mais SANS lien avec le
            # rendement réalisé (contrairement au signal modèle) — variance
            # non-nulle nécessaire pour que Spearman soit défini (sinon None,
            # cf. wfo_calibration._spearman), et IC baseline doit être faible.
            "price_target_mean": price0 * (1.05 + 0.01 * ((i * 7) % 5)),
        }
        t1_rows[f"T{i}"] = {"current_price": price1}

    by_date["2026-01-01"] = t0_rows
    by_date["2026-03-02"] = t1_rows  # ~60j plus tard
    return by_date


def test_evaluate_weights_ic_model_positive_on_known_signal():
    by_date = _synthetic_by_date()
    pairs = [("2026-01-01", "2026-03-02")]
    weights = {"w_multiple": 1.0, "w_peg": 0.0, "w_buffett": 0.0}
    result = evaluate_weights(by_date, pairs, weights=weights, band_pct=0.15)

    assert result["n_pairs_valid"] == 1
    assert result["ic_model"] is not None
    # Bas fwd_pe (cher relatif secteur inversé) → price_target haut → et le
    # rendement réalisé est aussi haut pour les mêmes tickers (i petit) :
    # corrélation positive attendue.
    assert result["ic_model"] > 0.5


def test_evaluate_weights_no_valid_pairs_returns_none():
    result = evaluate_weights({}, [("2026-01-01", "2026-03-02")], weights={"w_multiple": 1.0, "w_peg": 0.0, "w_buffett": 0.0}, band_pct=0.15)
    assert result["ic_model"] is None
    assert result["ic_baseline"] is None
    assert result["n_pairs_valid"] == 0


# ─── _normalize_weights / _candidate_configs ───────────────────
def test_normalize_weights_sums_to_one():
    w = _normalize_weights({"w_multiple": 2.0, "w_peg": 1.0, "w_buffett": 1.0})
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_normalize_weights_handles_all_zero():
    w = _normalize_weights({"w_multiple": 0.0, "w_peg": 0.0, "w_buffett": 0.0})
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_candidate_configs_all_normalized_and_include_base():
    base = {"w_multiple": 0.34, "w_peg": 0.33, "w_buffett": 0.33, "band_pct": 0.15}
    configs = _candidate_configs(base)
    assert len(configs) >= 8
    for cfg in configs:
        total = sum(cfg["weights"].values())
        assert abs(total - 1.0) < 1e-3
        assert 0.05 <= cfg["band_pct"] <= 0.35


# ─── Critères d'arrêt §6 ─────────────────────────────────────
def test_stop_condition_hard_cap():
    state = {
        "round": 20, "best_ic_model": 0.02, "best_ic_baseline": 0.01,
        "rounds_since_improvement": 1, "ic_history": [],
    }
    assert _check_stop_conditions(state) == "hard_cap"


def test_stop_condition_plateau():
    state = {
        "round": 8, "best_ic_model": 0.05, "best_ic_baseline": 0.02,
        "rounds_since_improvement": 5, "ic_history": [],
    }
    assert _check_stop_conditions(state) == "plateau"


def test_stop_condition_convergence():
    history = [
        {"ic_model": 0.11}, {"ic_model": 0.115}, {"ic_model": 0.105},
    ]
    state = {
        "round": 6, "best_ic_model": 0.105, "best_ic_baseline": 0.06,
        "rounds_since_improvement": 0, "ic_history": history,
    }
    assert _check_stop_conditions(state) == "convergence"


def test_stop_condition_convergence_requires_beating_baseline():
    history = [{"ic_model": 0.11}, {"ic_model": 0.115}, {"ic_model": 0.105}]
    state = {
        "round": 6, "best_ic_model": 0.105, "best_ic_baseline": 0.20,  # baseline gagne
        "rounds_since_improvement": 0, "ic_history": history,
    }
    assert _check_stop_conditions(state) is None


def test_stop_condition_convergence_requires_stability():
    # IC oscille trop (> 0.02 de spread) sur les 3 derniers rounds → pas convergé.
    history = [{"ic_model": 0.11}, {"ic_model": 0.20}, {"ic_model": 0.105}]
    state = {
        "round": 6, "best_ic_model": 0.20, "best_ic_baseline": 0.05,
        "rounds_since_improvement": 0, "ic_history": history,
    }
    assert _check_stop_conditions(state) is None


def test_stop_condition_none_when_nothing_triggered():
    state = {
        "round": 3, "best_ic_model": 0.04, "best_ic_baseline": 0.02,
        "rounds_since_improvement": 1, "ic_history": [{"ic_model": 0.04}],
    }
    assert _check_stop_conditions(state) is None
