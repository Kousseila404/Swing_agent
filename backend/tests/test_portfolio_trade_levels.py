"""Tests suggest_trade_levels — SL/TP σ-scaled pour /recommendations.

Couvre :
  - vol valide → sigma_scaled clampé sur les bornes
  - vol absente/invalide → fixed_fallback -5/+15
  - prix invalide → retourne None
  - clamps min/max
  - sanity SL < price, TP > price
"""
from __future__ import annotations

import math

from modules.portfolio import suggest_trade_levels


def test_sigma_scaled_typical():
    """Vol 25 % ann → horizon 30j ≈ 8.62 % → SL ≈ 17.25 %, TP ≈ 34.5 %. RR = 2:1."""
    r = suggest_trade_levels(100.0, 25.0)
    assert r["method"] == "sigma_scaled"
    assert r["sl_pct"] is not None and 17.0 <= r["sl_pct"] <= 17.5
    assert r["tp_pct"] is not None and 34.0 <= r["tp_pct"] <= 35.0
    assert r["sl"] < 100.0 < r["tp"]
    # Ratio TP:SL ≈ 2 (k_tp=4 / k_sl=2).
    assert math.isclose(r["tp_pct"] / r["sl_pct"], 2.0, rel_tol=0.01)


def test_fixed_fallback_when_vol_missing():
    r = suggest_trade_levels(100.0, None)
    assert r["method"] == "fixed_fallback"
    assert r["sl"] == 95.0 and r["tp"] == 115.0


def test_fixed_fallback_when_vol_zero_or_nan():
    assert suggest_trade_levels(100.0, 0.0)["method"] == "fixed_fallback"
    assert suggest_trade_levels(100.0, float("nan"))["method"] == "fixed_fallback"


def test_invalid_price_returns_none():
    for bad in (None, 0.0, -5.0, float("nan"), float("inf")):
        r = suggest_trade_levels(bad, 20.0)
        assert r["sl"] is None and r["tp"] is None
        assert r["method"] == "invalid_price"


def test_clamp_high_volatility():
    """Penny stock σ=300 % → sl_pct brut ≈ 207 % doit être clampé à 30 %."""
    r = suggest_trade_levels(10.0, 300.0)
    assert r["method"] == "sigma_scaled"
    assert r["sl_pct"] == 30.0    # _MAX_SL_PCT (LT, recalibré 2026-04-27)
    assert r["tp_pct"] == 200.0   # _MAX_TP_PCT (LT P3, recalibré 2026-04-29)


def test_clamp_low_volatility():
    """Ticker ultra-stable σ=2 % → SL brut ≈ 1.38 % doit être relevé à 4 %."""
    r = suggest_trade_levels(100.0, 2.0)
    assert r["sl_pct"] == 4.0    # _MIN_SL_PCT (LT)
    assert r["tp_pct"] == 10.0   # _MIN_TP_PCT (LT)


def test_sl_always_below_price_and_tp_above():
    """Invariant : quelle que soit la vol, SL < price < TP."""
    for vol in (None, 0, 5, 25, 100, 500):
        r = suggest_trade_levels(50.0, vol)
        if r["sl"] is not None:
            assert r["sl"] < 50.0 < r["tp"]


def test_short_direction_mirrors_long():
    """Lot 12 : SHORT support activé. SL > price, TP < price (miroir LONG)."""
    r = suggest_trade_levels(100.0, 20.0, direction="SHORT")
    assert r["method"] in ("sigma_scaled", "fixed_fallback")
    assert r["sl"] is not None and r["tp"] is not None
    assert r["sl"] > 100.0, f"SHORT SL must be above entry (got {r['sl']})"
    assert r["tp"] < 100.0, f"SHORT TP must be below entry (got {r['tp']})"
    assert r["tp"] > 0


def test_unknown_direction_still_rejected():
    r = suggest_trade_levels(100.0, 20.0, direction="SIDEWAYS")
    assert r["method"] == "unsupported_direction"
    assert r["sl"] is None and r["tp"] is None
