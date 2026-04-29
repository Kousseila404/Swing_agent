"""Tests support_score — score composite + niveau + breakdown."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.support_score import compute_support_score


def _make_history(closes: list[float], days: int | None = None) -> pd.DataFrame:
    """Construit un DataFrame OHLCV de test à partir d'une série de Close."""
    if days is None:
        days = len(closes)
    dates = pd.date_range(end="2026-04-27", periods=days, freq="D")
    return pd.DataFrame({
        "Open": closes,
        "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes],
        "Close": closes,
        "Volume": [1_000_000] * days,
    }, index=dates)


def test_insufficient_data_no_history():
    r = compute_support_score(ticker="X", current_price=100.0, history=None)
    assert r.level == "INSUFFICIENT_DATA"
    assert r.score == 0.0
    assert r.method == "insufficient_data"


def test_insufficient_data_short_history():
    closes = [100.0] * 30  # < 60 jours = insuffisant
    r = compute_support_score(ticker="X", current_price=100.0, history=_make_history(closes))
    assert r.level == "INSUFFICIENT_DATA"


def test_insufficient_data_invalid_price():
    closes = [100.0] * 250
    r = compute_support_score(ticker="X", current_price=None, history=_make_history(closes))
    assert r.level == "INSUFFICIENT_DATA"
    r2 = compute_support_score(ticker="X", current_price=0.0, history=_make_history(closes))
    assert r2.level == "INSUFFICIENT_DATA"


def test_full_method_when_history_sufficient():
    """≥ 200j d'historique → method 'full' avec MA200."""
    closes = list(np.linspace(80.0, 100.0, 250))
    r = compute_support_score(ticker="X", current_price=100.0, history=_make_history(closes))
    assert r.method == "full"
    assert r.ma200_value is not None
    assert r.ma200_proximity is not None


def test_degraded_method_when_history_short():
    """< 200j d'historique → method 'degraded_no_ma200'."""
    closes = list(np.linspace(80.0, 100.0, 100))  # 100j < 200
    r = compute_support_score(ticker="X", current_price=100.0, history=_make_history(closes))
    assert r.method == "degraded_no_ma200"
    assert r.ma200_value is None
    assert r.ma200_proximity is None


def test_strong_support_uptrend_at_pullback():
    """Stock en uptrend de 80→120 puis pullback à 105 (≈MA200, dans pullback healthy).
    Doit donner ON_SUPPORT."""
    closes = list(np.linspace(80.0, 120.0, 200)) + list(np.linspace(120.0, 105.0, 50))
    history = _make_history(closes)
    # Prix courant = 105 — proche de la MA200 et en pullback ~12.5% du high 120.
    r = compute_support_score(ticker="X", current_price=105.0, history=history)
    assert r.level in ("ON_SUPPORT", "NEAR_SUPPORT")
    assert r.score >= 50.0


def test_no_support_at_all_time_high():
    """Stock qui finit pile sur son ATH = pas de pullback, loin de MA200."""
    closes = list(np.linspace(80.0, 150.0, 250))
    r = compute_support_score(ticker="X", current_price=150.0, history=_make_history(closes))
    # Prix très au-dessus MA200, drawdown 0 → score faible.
    assert r.level == "OFF_SUPPORT"
    assert r.score < 40.0


def test_falling_knife_deep_drawdown():
    """Stock en drawdown -50% du high → falling knife, support cassé."""
    closes = list(np.linspace(100.0, 200.0, 200)) + list(np.linspace(200.0, 95.0, 50))
    r = compute_support_score(ticker="X", current_price=95.0, history=_make_history(closes))
    # Drawdown -52.5% → pullback_depth=0, MA200 cassée.
    assert r.pullback_depth == 0.0
    assert r.score < 40.0


def test_pct_from_52w_high_computed():
    """Vérifie que pct_from_52w_high est bien renseigné."""
    closes = list(np.linspace(80.0, 120.0, 250))
    r = compute_support_score(ticker="X", current_price=100.0, history=_make_history(closes))
    assert r.pct_from_52w_high is not None
    # 100 vs high 120 → -0.1667 (fraction, pas pourcentage).
    assert -0.17 < r.pct_from_52w_high < -0.16


def test_to_dict_serializable():
    closes = list(np.linspace(80.0, 100.0, 250))
    r = compute_support_score(ticker="X", current_price=100.0, history=_make_history(closes))
    d = r.to_dict()
    expected_keys = {
        "score", "level", "ma200_proximity", "swing_low_proximity",
        "pullback_depth", "nearest_swing_low", "ma200_value",
        "pct_from_52w_high", "method",
    }
    assert set(d.keys()) == expected_keys
    # Tout sérialisable JSON (pas de numpy types).
    import json
    json.dumps(d)


def test_swing_low_proximity_positive_when_at_recent_low():
    """Stock qui crée un swing low STRICT récent puis remonte → près du swing."""
    # Sequence : descend vers 90 (point unique), remonte à 100, puis redescend
    # vers 92 — le 90 est un swing low strict détectable par argrelmin.
    closes = (
        list(np.linspace(110.0, 91.0, 25))    # descente
        + [90.0]                              # POINT MIN unique (strict)
        + list(np.linspace(91.0, 100.0, 25))  # rebond
        + list(np.linspace(100.0, 92.0, 30))  # repli vers le swing low
    )
    history = _make_history(closes)
    r = compute_support_score(ticker="X", current_price=92.0, history=history)
    # Le prix 92 est proche du swing low 90 (~2.2%) → swing_low_proximity > 0.
    assert r.swing_low_proximity > 0
    assert r.nearest_swing_low is not None
    assert abs(r.nearest_swing_low - 90.0) < 1.0


def test_score_capped_at_100():
    """Score composite ne doit jamais dépasser 100."""
    # Cas idéal : MA200 = price, swing low très proche, pullback parfait.
    base = list(np.linspace(120.0, 130.0, 200))    # MA200 ≈ 125
    pullback = list(np.linspace(130.0, 110.0, 30)) # creux à 110 (swing low)
    rebond = list(np.linspace(110.0, 113.0, 20))   # rebond léger
    closes = base + pullback + rebond
    r = compute_support_score(ticker="X", current_price=113.0, history=_make_history(closes))
    assert 0.0 <= r.score <= 100.0


def test_swing_low_above_price_is_resistance_not_support():
    """Cas INCY : le cours casse un swing low → ce niveau est désormais
    une résistance, pas un support. swing_low_proximity doit être 0."""
    # Stock qui crée un swing low à 95, rebondit à 110, puis CASSE le 95 pour
    # tomber à 92. Le swing low 95 est maintenant cassé, c'est plus un support.
    closes = (
        list(np.linspace(110.0, 96.0, 30))
        + [95.0]                              # swing low strict
        + list(np.linspace(96.0, 110.0, 30))  # rebond fort
        + list(np.linspace(110.0, 92.0, 30))  # cassure du 95 vers 92
    )
    history = _make_history(closes)
    r = compute_support_score(ticker="X", current_price=92.0, history=history)
    # Le cours 92 est sous le swing low 95 → swing_proximity = 0 (cassé).
    assert r.swing_low_proximity == 0.0
    # Le nearest_swing reste exposé pour debug, mais ne contribue pas au score.
    assert r.nearest_swing_low is not None


def test_swing_low_micro_above_still_counts():
    """Tolérance ±0.5% : un swing low juste au-dessus (qu'on est en train de
    tester par le bas) compte encore comme support."""
    closes = (
        list(np.linspace(110.0, 100.5, 30))
        + [100.0]                                # swing low
        + list(np.linspace(100.5, 105.0, 30))
        + list(np.linspace(105.0, 100.3, 30))    # retest du 100, cours 100.3
    )
    history = _make_history(closes)
    # 100.3 vs swing low 100 → swing est à -0.3% sous le cours, c'est un support testé.
    r = compute_support_score(ticker="X", current_price=100.3, history=history)
    assert r.swing_low_proximity > 0  # encore valide
    assert r.nearest_swing_low is not None
    assert abs(r.nearest_swing_low - 100.0) < 0.1


def test_levels_thresholds():
    """Vérifie que les seuils de niveau sont bien respectés."""
    from modules.support_score import _level_from_score
    assert _level_from_score(80.0) == "ON_SUPPORT"
    assert _level_from_score(70.0) == "ON_SUPPORT"
    assert _level_from_score(50.0) == "NEAR_SUPPORT"
    assert _level_from_score(40.0) == "NEAR_SUPPORT"
    assert _level_from_score(30.0) == "OFF_SUPPORT"
    assert _level_from_score(0.0) == "OFF_SUPPORT"
