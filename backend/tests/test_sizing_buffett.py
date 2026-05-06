"""Tests _sizing_buffett — tilt fundamentals-anchored sur les poids portfolio."""
from __future__ import annotations

from modules.portfolio._sizing_buffett import _tilt_factor, apply_buffett_tilt


# ─── Tilts catégoriels ────────────────────────────────────────
def test_compounder_factor():
    f, lab = _tilt_factor(quality=90, f_score=9)
    assert f == 1.5 and lab == "compounder"


def test_high_quality_factor():
    f, lab = _tilt_factor(quality=80, f_score=7)
    assert f == 1.2 and lab == "high_quality"


def test_baseline_factor():
    f, lab = _tilt_factor(quality=70, f_score=6)
    assert f == 1.0 and lab == "baseline"


def test_junior_factor():
    f, lab = _tilt_factor(quality=45, f_score=5)
    assert f == 0.7 and lab == "junior"


def test_junk_factor():
    f, lab = _tilt_factor(quality=30, f_score=2)
    assert f == 0.5 and lab == "junk"


# ─── Cas dégradés ─────────────────────────────────────────────
def test_no_data_baseline():
    f, lab = _tilt_factor(None, None)
    assert f == 1.0 and lab == "baseline_no_data"


def test_quality_only_compounder_partial():
    f, lab = _tilt_factor(quality=90, f_score=None)
    assert f == 1.2 and lab == "high_quality_partial"  # plus prudent qu'avec P confirmé


def test_f_score_only_partial():
    f, lab = _tilt_factor(quality=None, f_score=9)
    assert f == 1.2 and lab == "high_quality_partial"


# ─── apply_buffett_tilt ───────────────────────────────────────
def test_tilt_concentrates_on_compounders():
    """Compounder + junior à poids égal → compounder pèse plus après tilt.

    Phase 1 data hardening : data complète (data_quality=1.0 + age=10j) pour
    que le confidence_score n'atténue pas l'amplification compounder.
    """
    weights = {"NEM": 0.5, "JUNK": 0.5}
    scored = {
        "NEM":  {"quality_score": 95, "f_score": 9, "data_quality": 1.0,
                  "fundamentals_age_days": 10, "peg_ratio": 1.5},
        "JUNK": {"quality_score": 30, "f_score": 2, "data_quality": 1.0,
                  "fundamentals_age_days": 10},
    }
    new, diag = apply_buffett_tilt(weights, scored)
    assert abs(sum(new.values()) - 1.0) < 1e-9   # renormalisation préservée
    assert new["NEM"] > new["JUNK"]
    assert diag["n_compounders"] == 1
    assert diag["n_junk"] == 1


def test_tilt_renormalizes_to_total():
    """Tous les poids tiltés × 1.5 → renormalisation préserve la somme initiale."""
    weights = {"A": 0.4, "B": 0.3, "C": 0.3}
    scored = {t: {"quality_score": 95, "f_score": 9} for t in weights}
    new, _ = apply_buffett_tilt(weights, scored)
    assert abs(sum(new.values()) - 1.0) < 1e-9
    # Si tous compounders, la renormalisation efface le tilt → poids inchangés.
    for t in weights:
        assert abs(new[t] - weights[t]) < 1e-9


def test_tilt_diagnostics_traceable():
    weights = {"A": 0.5, "B": 0.5}
    scored = {
        "A": {"quality_score": 90, "f_score": 9, "data_quality": 1.0,
              "fundamentals_age_days": 10},
        "B": {"quality_score": 40, "f_score": 3, "data_quality": 1.0,
              "fundamentals_age_days": 10},
    }
    _, diag = apply_buffett_tilt(weights, scored)
    by = diag["by_ticker"]
    assert by["A"]["factor_raw"] == 1.5
    assert by["A"]["label"] == "compounder"
    assert by["B"]["factor_raw"] == 0.7
    assert by["B"]["label"] == "junior"
    # Phase 1 data hardening — confidence exposé aussi.
    assert by["A"]["confidence"] >= 80
    assert by["A"]["confidence_tier"] == "high"


def test_tilt_empty():
    new, diag = apply_buffett_tilt({}, {})
    assert new == {}
    assert diag["applied"] is False


def test_tilt_missing_fundamentals_neutral():
    """Aucune fondamentale → tilt = 1.0, poids inchangés."""
    weights = {"X": 0.6, "Y": 0.4}
    scored = {"X": {}, "Y": {}}
    new, _ = apply_buffett_tilt(weights, scored)
    for t in weights:
        assert abs(new[t] - weights[t]) < 1e-9
