"""Tests trailing fundamental SL — refonte 2026-04-29."""
from __future__ import annotations

from modules.fundamentals_levels import compute_trailing_fundamental_sl


def test_no_erosion_keeps_entry_sl():
    """Quality stable → SL inchangé."""
    r = compute_trailing_fundamental_sl(
        entry_price=100, entry_quality=80, entry_piotroski=8,
        current_quality=80, current_piotroski=8,
    )
    assert r["tightened"] is False
    assert r["tightening_pts"] == 0


def test_quality_improvement_no_loosening():
    """Quality s'améliore → on garde le SL d'entrée (pas d'élargissement)."""
    r = compute_trailing_fundamental_sl(
        entry_price=100, entry_quality=70, entry_piotroski=7,
        current_quality=85, current_piotroski=8,
    )
    assert r["tightened"] is False


def test_erosion_tightens_sl():
    """Quality 90 → 60, F-9 → 6 → SL se resserre."""
    r = compute_trailing_fundamental_sl(
        entry_price=100, entry_quality=90, entry_piotroski=9,
        current_quality=60, current_piotroski=6,
    )
    assert r["tightened"] is True
    assert r["tightening_pts"] > 0
    assert r["sl_trailing_price"] > 100 * (1 - r["sl_pct_trailing"] / 100) - 0.01


def test_extreme_erosion_floor():
    """Érosion massive → factor borné à 0.6."""
    r = compute_trailing_fundamental_sl(
        entry_price=100, entry_quality=95, entry_piotroski=9,
        current_quality=10, current_piotroski=1,
    )
    assert r["sl_pct_trailing"] is not None
    # Plancher : trailing doit être ≥ 60% du SL d'entrée.
    # Pour Q-95+P-9 le SL d'entrée est ~clamp 45 → trailing ≥ 27
    assert r["sl_pct_trailing"] >= 25.0


def test_missing_data_returns_none():
    r = compute_trailing_fundamental_sl(
        entry_price=100, entry_quality=None, entry_piotroski=None,
        current_quality=70, current_piotroski=7,
    )
    assert r["sl_pct_trailing"] is None
