"""Tests suggest_trade_levels — SL/TP catastrophe floor (refonte LT 2026-04-29).

Couvre :
  - vol valide → catastrophe_floor clampé sur les bornes (K_SL=3.5, K_TP=6)
  - vol absente/invalide → fixed_fallback -10/+25
  - prix invalide → retourne None
  - clamps min/max (5/35 pour SL, 10/500 pour TP)
  - sanity SL < price, TP > price
"""
from __future__ import annotations

import math

from modules.portfolio import suggest_trade_levels


def test_catastrophe_floor_typical():
    """Vol 25 % ann → horizon 30j ≈ 8.62 % → SL ≈ 30.2 %, TP ≈ 51.7 %.

    K_SL=3.5 / K_TP=6 → ratio σ pur 1.7:1. SL clampé à 30 % (sous le _MAX_SL_PCT
    de 35 %), TP libre.
    """
    r = suggest_trade_levels(100.0, 25.0)
    assert r["method"] == "catastrophe_floor"
    assert r["sl_pct"] is not None and 29.5 <= r["sl_pct"] <= 30.5
    assert r["tp_pct"] is not None and 51.0 <= r["tp_pct"] <= 52.0
    assert r["sl"] < 100.0 < r["tp"]


def test_fixed_fallback_when_vol_missing():
    """Fallback élargi : SL=−10 %, TP=+25 % (cohérent avec philosophie
    catastrophe-only)."""
    r = suggest_trade_levels(100.0, None)
    assert r["method"] == "fixed_fallback"
    assert r["sl"] == 90.0 and r["tp"] == 125.0


def test_fixed_fallback_when_vol_zero_or_nan():
    assert suggest_trade_levels(100.0, 0.0)["method"] == "fixed_fallback"
    assert suggest_trade_levels(100.0, float("nan"))["method"] == "fixed_fallback"


def test_invalid_price_returns_none():
    for bad in (None, 0.0, -5.0, float("nan"), float("inf")):
        r = suggest_trade_levels(bad, 20.0)
        assert r["sl"] is None and r["tp"] is None
        assert r["method"] == "invalid_price"


def test_clamp_high_volatility():
<<<<<<< Updated upstream
    """Penny σ=300 % → SL brut ≈ 362 % clampé à 35 %, TP brut ≈ 620 % clampé à 500 %."""
    r = suggest_trade_levels(10.0, 300.0)
    assert r["method"] == "catastrophe_floor"
    assert r["sl_pct"] == 35.0    # _MAX_SL_PCT (refonte 2026-04-29)
    assert r["tp_pct"] == 500.0   # _MAX_TP_PCT (refonte 2026-04-29)
=======
    """Penny stock σ=300 % → sl_pct brut ≈ 207 % doit être clampé à 30 %."""
    r = suggest_trade_levels(10.0, 300.0)
    assert r["method"] == "sigma_scaled"
    assert r["sl_pct"] == 30.0    # _MAX_SL_PCT (LT, recalibré 2026-04-27)
    assert r["tp_pct"] == 100.0   # _MAX_TP_PCT (LT, recalibré 2026-04-27)
>>>>>>> Stashed changes


def test_clamp_low_volatility():
    """Ticker ultra-stable σ=2 % → SL brut ≈ 2.4 % relevé à 5 %."""
    r = suggest_trade_levels(100.0, 2.0)
    assert r["sl_pct"] == 5.0    # _MIN_SL_PCT (broker minimum)
    assert r["tp_pct"] == 10.0   # _MIN_TP_PCT (broker minimum)


def test_sl_always_below_price_and_tp_above():
    """Invariant : quelle que soit la vol, SL < price < TP."""
    for vol in (None, 0, 5, 25, 100, 500):
        r = suggest_trade_levels(50.0, vol)
        if r["sl"] is not None:
            assert r["sl"] < 50.0 < r["tp"]


def test_short_direction_mirrors_long():
    """SHORT support : SL > price, TP < price (miroir LONG)."""
    r = suggest_trade_levels(100.0, 20.0, direction="SHORT")
    assert r["method"] in ("catastrophe_floor", "fixed_fallback")
    assert r["sl"] is not None and r["tp"] is not None
    assert r["sl"] > 100.0, f"SHORT SL must be above entry (got {r['sl']})"
    assert r["tp"] < 100.0, f"SHORT TP must be below entry (got {r['tp']})"
    assert r["tp"] > 0


def test_unknown_direction_still_rejected():
    r = suggest_trade_levels(100.0, 20.0, direction="SIDEWAYS")
    assert r["method"] == "unsupported_direction"
    assert r["sl"] is None and r["tp"] is None
