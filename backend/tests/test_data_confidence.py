"""Tests data_confidence — Phase 1 Buffett-data hardening (2026-04-29)."""
from __future__ import annotations

import pytest

from modules.data_confidence import compute_confidence, confidence_modifier


# ─── Score parfait ────────────────────────────────────────────
def test_perfect_data_high_confidence():
    """Data complète, fraîche, cohérente → confidence ≥ 95."""
    info = {
        "data_quality":  1.0,
        "quality_score": 80,
        "f_score":       8,
        "peg_ratio":     1.5,
        "forward_pe":    20.0,
        "fundamentals_age_days": 10,
    }
    r = compute_confidence(info, sector_quality_median=70)
    assert r["score"] >= 95
    assert r["tier"] == "high"


def test_no_info_returns_zero():
    r = compute_confidence(None)
    assert r["score"] == 0
    assert r["tier"] == "very_low"


# ─── Coverage ────────────────────────────────────────────────
def test_partial_coverage_lowers_score():
    """data_quality 0.5 → score à peu près moitié de la base."""
    full = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7,
    })
    half = compute_confidence({
        "data_quality": 0.5, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7,
    })
    assert half["score"] < full["score"]
    assert half["score"] <= 55


# ─── Freshness ───────────────────────────────────────────────
def test_freshness_decreases_with_age():
    base_info = {"data_quality": 1.0, "quality_score": 70, "f_score": 7}
    fresh = compute_confidence({**base_info, "fundamentals_age_days": 5})
    medium = compute_confidence({**base_info, "fundamentals_age_days": 200})
    stale = compute_confidence({**base_info, "fundamentals_age_days": 400})
    assert fresh["score"] > medium["score"] > stale["score"]
    assert fresh["freshness"] == 1.0
    assert stale["freshness"] == 0.40   # plancher


def test_freshness_unknown_is_prudent():
    """Âge None → facteur 0.7 (prudent)."""
    r = compute_confidence({"data_quality": 1.0, "quality_score": 70, "f_score": 7})
    assert r["freshness"] == 0.7


# ─── Sanity ──────────────────────────────────────────────────
def test_peg_aberrant_penalty():
    """PEG > 5 = aberrant → sanity penalty."""
    sane = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7, "peg_ratio": 1.5,
    })
    crazy = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7, "peg_ratio": 50.0,
    })
    assert crazy["score"] < sane["score"]
    assert crazy["sanity"] < sane["sanity"]


def test_fwd_pe_extreme_penalty():
    crazy = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7, "forward_pe": 350,
    })
    assert crazy["sanity"] < 1.0


def test_quality_outlier_vs_sector():
    """Q-95 alors que médiane secteur = 50 → flag."""
    outlier = compute_confidence(
        {"data_quality": 1.0, "fundamentals_age_days": 10,
         "quality_score": 95, "f_score": 7},
        sector_quality_median=50,
    )
    normal = compute_confidence(
        {"data_quality": 1.0, "fundamentals_age_days": 10,
         "quality_score": 70, "f_score": 7},
        sector_quality_median=65,
    )
    assert outlier["score"] < normal["score"]


def test_internal_contradiction_quality_vs_fscore():
    """Q-90 + F-2 = data probable broken → forte pénalité."""
    contradictory = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 90, "f_score": 2,
    })
    coherent = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 90, "f_score": 8,
    })
    assert contradictory["score"] < coherent["score"]


# ─── Tiers ────────────────────────────────────────────────────
def test_tier_thresholds():
    """Couvre les 4 tiers via différentes coverage."""
    def info(dq):
        return {"data_quality": dq, "fundamentals_age_days": 10,
                "quality_score": 70, "f_score": 7}
    assert compute_confidence(info(1.0))["tier"] in ("high",)
    assert compute_confidence(info(0.7))["tier"] in ("medium", "high")
    assert compute_confidence(info(0.5))["tier"] in ("low", "medium")
    assert compute_confidence(info(0.3))["tier"] in ("very_low", "low")


# ─── confidence_modifier ──────────────────────────────────────
def test_modifier_thresholds():
    assert confidence_modifier(95) == 1.00
    assert confidence_modifier(80) == 1.00
    assert confidence_modifier(65) == 0.85
    assert confidence_modifier(45) == 0.70
    assert confidence_modifier(30) == 0.50


# ─── MultiSource (Étape 0 généralisation, 2026-07-20) ─────────
def test_multi_source_absent_is_neutral():
    """Ticker jamais enrichi par insider_enrich (champ absent) → ×1.00,
    aucune régression sur le score existant."""
    with_field = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7, "insider_error": None,
    })
    without_field = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7,
    })
    assert with_field["multi_source"] == 1.0
    assert without_field["multi_source"] == 1.0
    assert with_field["score"] == without_field["score"]


def test_multi_source_quiet_ticker_not_penalized():
    """insider_data_quality=0 SANS insider_error (insiders juste calmes)
    ne doit pas faire chuter multi_source — pas une panne."""
    r = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7,
        "insider_data_quality": 0.0, "insider_error": None,
    })
    assert r["multi_source"] == 1.0


def test_multi_source_insider_error_penalizes():
    ok = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7, "insider_error": None,
    })
    failed = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7, "insider_error": "sec_fetch_failed",
    })
    assert failed["multi_source"] == 0.85
    assert failed["score"] < ok["score"]
    assert any("Insider SEC EDGAR en échec" in line for line in failed["breakdown"])


# ─── MultiSource — finnhub_error (Étape 5, 2026-07-20) ────────
def test_multi_source_finnhub_error_absent_is_neutral():
    """Ticker jamais enrichi par finnhub_enrich (champ absent) → ×1.00."""
    r = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7,
    })
    assert r["multi_source"] == 1.0


def test_multi_source_finnhub_no_coverage_not_penalized():
    """finnhub_error=None malgré un ticker sans couverture analyste
    (upgrades/downgrades restés None) n'est pas une panne."""
    r = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7, "finnhub_error": None,
    })
    assert r["multi_source"] == 1.0


def test_multi_source_finnhub_error_penalizes():
    ok = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7, "finnhub_error": None,
    })
    failed = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7, "finnhub_error": "recommendation:rate_limited",
    })
    assert failed["multi_source"] == 0.85
    assert failed["score"] < ok["score"]
    assert any("Finnhub revisions/earnings en échec" in line for line in failed["breakdown"])


def test_multi_source_both_errors_are_cumulative():
    """insider_error + finnhub_error simultanés → pénalités cumulées (×0.85²),
    pas juste la pire des deux."""
    both = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7,
        "insider_error": "cik_unknown", "finnhub_error": "fetch_failed",
    })
    one = compute_confidence({
        "data_quality": 1.0, "fundamentals_age_days": 10,
        "quality_score": 70, "f_score": 7, "insider_error": "cik_unknown",
    })
    assert both["multi_source"] == pytest.approx(0.85 * 0.85, abs=1e-3)
    assert both["score"] < one["score"]


# ─── Breakdown traceable ─────────────────────────────────────
def test_breakdown_has_explanations():
    r = compute_confidence({
        "data_quality": 0.7, "fundamentals_age_days": 200,
        "quality_score": 70, "f_score": 7, "peg_ratio": 6.0,
    })
    bd = r["breakdown"]
    assert any("Coverage" in line for line in bd)
    assert any("Données vieillissantes" in line for line in bd)
    assert any("PEG aberrant" in line for line in bd)
    assert any("Score final" in line for line in bd)
