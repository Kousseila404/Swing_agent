"""Tests unitaires — modules/price_target.py

Couvre (cf. docs/price_target_design.md §7) :
  - cas nominal (toutes composantes disponibles)
  - fallback (aucune donnée exploitable)
  - peg_ratio négatif/nul
  - secteur trop petit (< 12 tickers) → fallback global
  - cohérence avec let_it_ride / tilt flags existants
"""
from __future__ import annotations

from modules import price_target as pt


def _row(**overrides):
    base = {
        "current_price": 100.0,
        "forward_pe": 20.0,
        "ev_to_ebitda": 12.0,
        "peg_ratio": 1.5,
        "sector": "Technology",
        "quality_score": 60.0,
        "value_score": 55.0,
        "f_score": 6,
        "data_quality": 1.0,
        "fundamentals_age_days": 5,
        "titan_tilt_flags": [],
    }
    base.update(overrides)
    return base


def _sector_universe(n=12, sector="Technology", **overrides):
    """n tickers dans le même secteur, valeurs de multiple/peg variées."""
    universe = {}
    for i in range(n):
        fields = {
            "forward_pe": 15.0 + i,
            "ev_to_ebitda": 8.0 + i,
            "peg_ratio": 1.0 + 0.1 * i,
            "sector": sector,
        }
        fields.update(overrides)
        universe[f"{sector[:2].upper()}{i}"] = _row(**fields)
    return universe


# ─────────────────────────────────────────────────────────────────
# Cas nominal
# ─────────────────────────────────────────────────────────────────

def test_nominal_all_components_available():
    universe = _sector_universe(n=12)
    medians = pt.compute_sector_medians(universe)
    row = universe["TE0"]

    out = pt.compute_price_target(row, sector_medians=medians)

    assert out["method"] == "fundamental_blend"
    assert out["price_target"] is not None
    assert out["price_target_low"] < out["price_target"] < out["price_target_high"]
    assert out["components"]["multiple"] is not None
    assert out["components"]["peg"] is not None
    assert out["components"]["buffett"] is not None
    assert out["price_target_confidence"] is not None
    assert out["components"]["analyst"] is None  # _row() ne fixe pas price_target_mean
    assert isinstance(out["upside_pct"], float)


def test_compute_for_universe_covers_all_tickers():
    universe = _sector_universe(n=12)
    out = pt.compute_for_universe(universe)
    assert set(out.keys()) == set(universe.keys())
    for r in out.values():
        assert r["method"] == "fundamental_blend"


# ─────────────────────────────────────────────────────────────────
# Fallback — aucune donnée exploitable
# ─────────────────────────────────────────────────────────────────

def test_fallback_no_price_returns_unavailable():
    out = pt.compute_price_target({"current_price": None})
    assert out["method"] == "unavailable"
    assert out["price_target"] is None
    assert out["price_target_low"] is None
    assert out["price_target_high"] is None
    assert out["upside_pct"] is None
    assert out["components"] == {"multiple": None, "peg": None, "buffett": None, "analyst": None}


def test_fallback_invalid_price_returns_unavailable():
    for bad_price in (0, -5.0, float("nan")):
        out = pt.compute_price_target({"current_price": bad_price})
        assert out["method"] == "unavailable"


def test_missing_sector_medians_disables_multiple_and_peg():
    """Sans médianes sectorielles, seule la composante Buffett contribue."""
    row = _row()
    out = pt.compute_price_target(row, sector_medians=None)
    assert out["components"]["multiple"] is None
    assert out["components"]["peg"] is None
    assert out["components"]["buffett"] is not None
    assert out["method"] == "fundamental_blend"


# ─────────────────────────────────────────────────────────────────
# PEG négatif / nul
# ─────────────────────────────────────────────────────────────────

def test_peg_negative_component_unavailable():
    universe = _sector_universe(n=12)
    medians = pt.compute_sector_medians(universe)
    row = _row(peg_ratio=-1.2, sector="Technology")

    out = pt.compute_price_target(row, sector_medians=medians)

    assert out["components"]["peg"] is None
    # Le reste du blend continue de fonctionner (multiple + buffett).
    assert out["price_target"] is not None


def test_peg_zero_component_unavailable():
    universe = _sector_universe(n=12)
    medians = pt.compute_sector_medians(universe)
    row = _row(peg_ratio=0.0, sector="Technology")

    out = pt.compute_price_target(row, sector_medians=medians)

    assert out["components"]["peg"] is None


# ─────────────────────────────────────────────────────────────────
# Secteur trop petit (< 12 tickers) → fallback médiane globale
# ─────────────────────────────────────────────────────────────────

def test_small_sector_falls_back_to_global_median():
    import statistics

    small = _sector_universe(n=5, sector="Real Estate", forward_pe=100.0, ev_to_ebitda=100.0)
    big = _sector_universe(n=15, sector="Technology")
    universe = {**small, **big}

    medians = pt.compute_sector_medians(universe)

    all_fpe = [r["forward_pe"] for r in universe.values()]
    expected_global_fpe = statistics.median(all_fpe)

    # Real Estate a < 12 valeurs valides → doit reprendre la médiane globale
    # (calculée sur TOUT l'univers), pas la médiane locale du petit secteur
    # (qui serait 100.0 — cf. _sector_universe(forward_pe=100.0) ci-dessus).
    assert medians["Real Estate"]["forward_pe"] == expected_global_fpe
    assert medians["Real Estate"]["forward_pe"] != 100.0

    # Le secteur assez grand (Technology, n=15 ≥ 12) garde sa propre médiane locale.
    tech_only_medians = pt.compute_sector_medians(big)
    assert medians["Technology"]["forward_pe"] == tech_only_medians["Technology"]["forward_pe"]


def test_large_sector_keeps_local_median():
    universe = _sector_universe(n=12, sector="Technology")
    medians = pt.compute_sector_medians(universe)
    valid_fpe = sorted(r["forward_pe"] for r in universe.values())
    import statistics
    assert medians["Technology"]["forward_pe"] == statistics.median(valid_fpe)


# ─────────────────────────────────────────────────────────────────
# Tilt flags / let_it_ride
# ─────────────────────────────────────────────────────────────────

def test_negative_tilt_compresses_low_band():
    universe = _sector_universe(n=12)
    medians = pt.compute_sector_medians(universe)

    neutral = pt.compute_price_target(_row(sector="Technology"), sector_medians=medians)
    tilted = pt.compute_price_target(
        _row(sector="Technology", titan_tilt_flags=["cheap_junk"]), sector_medians=medians
    )

    neutral_low_band = 1.0 - neutral["price_target_low"] / neutral["price_target"]
    tilted_low_band = 1.0 - tilted["price_target_low"] / tilted["price_target"]
    assert tilted_low_band < neutral_low_band
    assert "cheap_junk" in tilted["tilt_flags"]


def test_positive_tilt_widens_high_band():
    universe = _sector_universe(n=12)
    medians = pt.compute_sector_medians(universe)

    neutral = pt.compute_price_target(_row(sector="Technology"), sector_medians=medians)
    tilted = pt.compute_price_target(
        _row(sector="Technology", titan_tilt_flags=["qarp"]), sector_medians=medians
    )

    neutral_high_band = neutral["price_target_high"] / neutral["price_target"] - 1.0
    tilted_high_band = tilted["price_target_high"] / tilted["price_target"] - 1.0
    assert tilted_high_band > neutral_high_band
    assert "qarp" in tilted["tilt_flags"]


def test_let_it_ride_widens_high_band():
    """Q≥85 + P≥8 + V≥60 → let_it_ride côté fundamentals_levels, band_high élargie."""
    universe = _sector_universe(n=12)
    medians = pt.compute_sector_medians(universe)

    neutral = pt.compute_price_target(_row(sector="Technology"), sector_medians=medians)
    compounder = pt.compute_price_target(
        _row(sector="Technology", quality_score=90.0, value_score=70.0, f_score=9),
        sector_medians=medians,
    )

    assert compounder["let_it_ride"] is True
    neutral_high_band = neutral["price_target_high"] / neutral["price_target"] - 1.0
    compounder_high_band = compounder["price_target_high"] / compounder["price_target"] - 1.0
    assert compounder_high_band > neutral_high_band


def test_low_confidence_widens_band():
    universe = _sector_universe(n=12)
    medians = pt.compute_sector_medians(universe)

    confident = pt.compute_price_target(
        _row(sector="Technology", data_quality=1.0, fundamentals_age_days=5),
        sector_medians=medians,
    )
    unsure = pt.compute_price_target(
        _row(sector="Technology", data_quality=0.2, fundamentals_age_days=400),
        sector_medians=medians,
    )

    confident_band = confident["price_target_high"] / confident["price_target"] - 1.0
    unsure_band = unsure["price_target_high"] / unsure["price_target"] - 1.0
    assert unsure_band > confident_band
