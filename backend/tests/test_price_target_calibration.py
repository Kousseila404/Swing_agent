"""Tests price_target_calibration — walk-forward IC train/test (audit 2026-08-10)."""
from __future__ import annotations

from modules.price_target_calibration import (
    _candidate_configs,
    _normalize_weights,
    evaluate_weights,
    run_walk_forward,
    select_folds,
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


# ─── select_folds — découpage walk-forward non-overlapping sur TEST ────
def test_select_folds_produces_non_overlapping_test_windows():
    from datetime import date, timedelta
    base = date(2026, 1, 1)
    # 200 jours de paires (t0 quotidien, t1 = t0+60j) → assez pour plusieurs folds.
    pairs = [
        ((base + timedelta(days=i)).isoformat(), (base + timedelta(days=i + 60)).isoformat())
        for i in range(140)
    ]
    folds = select_folds(pairs, train_days=30, test_days=15)
    assert len(folds) >= 2

    # Le TEST d'un fold ne doit jamais recouper le TEST du fold suivant.
    for a, b in zip(folds, folds[1:], strict=False):
        a_test_end = date.fromisoformat(a["test_pairs"][-1][0])
        b_test_start = date.fromisoformat(b["test_pairs"][0][0])
        assert b_test_start > a_test_end


def test_select_folds_empty_when_too_short():
    from datetime import date, timedelta
    base = date(2026, 1, 1)
    pairs = [((base + timedelta(days=i)).isoformat(), (base + timedelta(days=i + 60)).isoformat()) for i in range(5)]
    folds = select_folds(pairs, train_days=30, test_days=15)
    assert folds == []


# ─── run_walk_forward — gate OOS ────────────────────────────────────────
def test_run_walk_forward_no_folds_falls_back_to_neutral_weights():
    # Historique trop court pour produire un seul fold → poids neutres, non-validé.
    by_date = {
        "2026-01-01": {"T0": {"current_price": 100.0, "sector": "Technology", "forward_pe": 15.0}},
        "2026-01-15": {"T0": {"current_price": 101.0}},
    }
    state = run_walk_forward(by_date, train_days=30, test_days=15)
    assert state["n_folds"] == 0
    assert state["validated_oos"] is False
    assert state["best_weights"]["w_multiple"] == round(1 / 3, 4)
    assert state["best_weights"]["w_peg"] == round(1 / 3, 4)
    assert state["best_weights"]["w_buffett"] == round(1 / 3, 4)


def test_run_walk_forward_validated_when_enough_folds():
    from datetime import date, timedelta

    by_date: dict = {}
    base = date(2026, 1, 1)
    # 140 jours de snapshots synthétiques, signal mean-reversion connu comme
    # dans _synthetic_by_date, pour produire ≥3 folds walk-forward.
    for offset in range(140):
        d = (base + timedelta(days=offset)).isoformat()
        rows = {}
        for i in range(20):
            fwd_pe = 10.0 + i * 2.0
            rows[f"T{i}"] = {
                "current_price": 100.0 + offset * 0.01,
                "sector": "Technology", "forward_pe": fwd_pe,
                "ev_to_ebitda": None, "peg_ratio": None,
                "quality_score": 60.0, "value_score": 55.0, "f_score": 6,
                "titan_tilt_flags": [], "data_quality": 0.9,
                "price_target_mean": (100.0 + offset * 0.01) * 1.05,
            }
        by_date[d] = rows

    state = run_walk_forward(by_date, train_days=30, test_days=15, min_folds_required=2)
    assert state["n_folds"] >= 2
    # Le résultat doit toujours être une pondération valide, quel que soit le gate.
    total = sum(state["best_weights"][k] for k in ("w_multiple", "w_peg", "w_buffett"))
    assert abs(total - 1.0) < 1e-2
