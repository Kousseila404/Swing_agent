"""Support Score composite — détecte si un ticker est sur une zone de support technique.

Combine 3 signaux indépendants pour produire un score 0-100 et un niveau qualitatif :

  Support_Score = 0.4 × MA200_proximity     (proximité moyenne mobile 200j)
                + 0.4 × swing_low_proximity (proximité dernier swing low validé)
                + 0.2 × pullback_depth      (profondeur du pullback depuis 52w high)

Niveau dérivé du score :
  - score >= 70 → "ON_SUPPORT"     (zone d'achat — le tilt TA s'aligne)
  - score 40-69 → "NEAR_SUPPORT"   (proche, surveiller)
  - score < 40  → "OFF_SUPPORT"    (pas de signal TA favorable)

Le module est PUREMENT informatif — il NE filtre PAS les propositions, il les
annote. L'utilisateur décide manuellement (cf. règle "TITAN > 80 + support").

Pas de boucle de feedback avec le pilier Momentum TITAN : le score est exposé
séparément, pas additionné au composite.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import argrelmin

# Fenêtres techniques. MA200 = standard institutional pour LT trend.
# Swing low scan sur 90j = couvre 1 trimestre, capture les vrais swings sans
# bruit court-terme. Pullback measure sur 252j = 1 année trading (52w high).
_MA_LONG = 200
_SWING_WINDOW_DAYS = 90
_PULLBACK_WINDOW_DAYS = 252
_MIN_HISTORY_DAYS = 60  # en dessous, le score est dégradé / non fiable

# Bandes de proximité — calibrées pour LT (60j hold typique).
# Au-delà de _MA_BAND_PCT au-dessus de la MA200, on n'est plus "sur support".
# En dessous, le prix peut être *sous* la MA200 (cassure récente) → encore considéré
# comme zone de support contestée pendant 5%, puis OFF.
_MA_BAND_ABOVE_PCT = 0.08   # +8% au-dessus de la MA200 = encore proche
_MA_BAND_BELOW_PCT = 0.05   # -5% en dessous = MA200 cassée mais pas effondrée

# Swing low : la zone de support s'étend ±_SWING_BAND_PCT autour du minimum local.
_SWING_BAND_PCT = 0.04      # ±4% du swing low

# Pullback "sain" : le prix idéal pour entrer après pullback est entre -10% et -25%
# du 52w high. Trop peu = pas de pullback (FOMO entry), trop = falling knife.
_PULLBACK_MIN_PCT = 0.08    # -8% du 52w high : pas encore un vrai pullback
_PULLBACK_IDEAL_LOW = 0.10  # -10% : début zone idéale
_PULLBACK_IDEAL_HIGH = 0.25 # -25% : fin zone idéale
_PULLBACK_MAX_PCT = 0.45    # -45% du 52w high : falling knife, support cassé

# Pondération composite — MA200 et swing low équipondérés (40% chacun) car ce
# sont les signaux les plus sémantiquement "support". Pullback (20%) confirme
# que ce n'est pas une cassure brutale.
_W_MA = 0.40
_W_SWING = 0.40
_W_PULLBACK = 0.20

# Seuils de niveau qualitatif.
_LEVEL_ON = 70.0
_LEVEL_NEAR = 40.0


@dataclass
class SupportScoreResult:
    """Sortie structurée — facile à JSON-serializer pour le contexte de proposition."""
    score: float                     # 0-100 composite
    level: str                       # "ON_SUPPORT" | "NEAR_SUPPORT" | "OFF_SUPPORT" | "INSUFFICIENT_DATA"
    ma200_proximity: float | None    # 0-100 (None si historique < 200j)
    swing_low_proximity: float       # 0-100
    pullback_depth: float            # 0-100
    nearest_swing_low: float | None  # niveau de prix du swing low le plus proche
    ma200_value: float | None        # niveau de la MA200 actuelle
    pct_from_52w_high: float | None  # ex: -0.18 = -18% du high
    method: str                      # "full" | "degraded_no_ma200" | "insufficient_data"

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "level": self.level,
            "ma200_proximity": round(self.ma200_proximity, 1) if self.ma200_proximity is not None else None,
            "swing_low_proximity": round(self.swing_low_proximity, 1),
            "pullback_depth": round(self.pullback_depth, 1),
            "nearest_swing_low": round(self.nearest_swing_low, 4) if self.nearest_swing_low is not None else None,
            "ma200_value": round(self.ma200_value, 4) if self.ma200_value is not None else None,
            "pct_from_52w_high": round(self.pct_from_52w_high * 100.0, 2) if self.pct_from_52w_high is not None else None,
            "method": self.method,
        }


def _level_from_score(score: float) -> str:
    if score >= _LEVEL_ON:
        return "ON_SUPPORT"
    if score >= _LEVEL_NEAR:
        return "NEAR_SUPPORT"
    return "OFF_SUPPORT"


def _ma200_proximity(price: float, ma200: float | None) -> float | None:
    """100 si prix ∈ [MA200, MA200×(1+band_above)], déclin linéaire sinon.
    Si prix < MA200 mais dans la bande below, score dégradé. Sinon 0."""
    if ma200 is None or ma200 <= 0:
        return None
    if price <= 0:
        return 0.0
    deviation = (price - ma200) / ma200
    if 0.0 <= deviation <= _MA_BAND_ABOVE_PCT:
        # Zone idéale : juste au-dessus de la MA200, support en train de tenir.
        return 100.0
    if deviation > _MA_BAND_ABOVE_PCT:
        # Au-dessus de la zone idéale : déclin linéaire jusqu'à 0 à +20%.
        excess = deviation - _MA_BAND_ABOVE_PCT
        return max(0.0, 100.0 * (1.0 - excess / 0.12))
    # deviation < 0 → prix sous la MA200, support contesté.
    # Décline de 100 (pile sur MA200) à 30 à la limite basse de la bande,
    # puis rapidement à 0 au-delà. On garde un score résiduel à -5% car la
    # MA200 reste un repère psychologique même légèrement cassée.
    if deviation >= -_MA_BAND_BELOW_PCT:
        ratio = -deviation / _MA_BAND_BELOW_PCT  # 0 (pile MA) → 1 (-5%)
        return 100.0 - 70.0 * ratio  # 100 → 30
    return 0.0


def _detect_swing_lows(closes: np.ndarray, order: int = 5) -> list[int]:
    """Détecte les indices des swing lows locaux. order=5 = ±5 jours = swing
    significatif (filtre le bruit). Retourne les indices triés croissants."""
    if len(closes) < order * 2 + 1:
        return []
    indices = argrelmin(closes, order=order)[0]
    return list(indices)


def _swing_low_proximity(price: float, recent_closes: np.ndarray) -> tuple[float, float | None]:
    """Cherche le swing low pertinent dans les recent_closes pour évaluer la
    proximité d'un VRAI support.

    Distinction critique :
    - Un swing low EN DESSOUS du cours = support potentiel (le titre a rebondi
      sur ce niveau → pourrait rebondir encore).
    - Un swing low AU-DESSUS du cours = niveau RÉCEMMENT CASSÉ → maintenant
      résistance, pas support. Faux positif si on les compte comme supports.

    Stratégie :
    1. On filtre les swing lows pour ne garder que ceux ≤ price (ou très
       légèrement au-dessus, dans une marge de tolérance étroite ±0.5% pour
       absorber le bruit micro).
    2. Score basé sur la distance au plus proche swing low VALIDE.
    3. Si le cours est sous tous les swing lows (price < min(swing_prices)),
       on est en cassure → score 0 + signal nearest_swing pour debug.

    Retourne (score 0-100, niveau du swing low retenu ou None).
    """
    if len(recent_closes) < 15:
        return 0.0, None
    swing_indices = _detect_swing_lows(recent_closes, order=5)
    if not swing_indices:
        return 0.0, None
    swing_prices = [float(recent_closes[i]) for i in swing_indices if recent_closes[i] > 0]
    if not swing_prices:
        return 0.0, None

    # Tolérance micro : un swing low jusqu'à +0.5% au-dessus du cours peut être
    # considéré comme un "vrai" support (le cours est en train de le tester).
    # Au-delà, c'est un niveau cassé.
    MICRO_ABOVE = 0.005
    valid_swings = [s for s in swing_prices if s <= price * (1.0 + MICRO_ABOVE)]

    if not valid_swings:
        # Tous les swing lows sont franchement au-dessus du cours → cassure
        # totale, le titre a perdu tous ses supports récents. Signal bearish.
        # On retourne 0 mais on expose le swing le plus proche pour debug.
        nearest = min(swing_prices, key=lambda s: abs(price - s) / s)
        return 0.0, nearest

    # Plus proche swing low VALIDE (en dessous ou à la limite micro).
    distances = [abs(price - s) / s for s in valid_swings]
    nearest_idx = int(np.argmin(distances))
    nearest_swing = valid_swings[nearest_idx]
    nearest_dist = distances[nearest_idx]

    if nearest_dist <= _SWING_BAND_PCT:
        # Dans la bande : 100 au centre, déclin linéaire vers 50 aux bords.
        return 100.0 - 50.0 * (nearest_dist / _SWING_BAND_PCT), nearest_swing
    if nearest_dist <= 2 * _SWING_BAND_PCT:
        return max(0.0, 50.0 * (1.0 - (nearest_dist - _SWING_BAND_PCT) / _SWING_BAND_PCT)), nearest_swing
    return 0.0, nearest_swing


def _pullback_depth_score(price: float, high_52w: float) -> tuple[float, float]:
    """Score 0-100 selon profondeur du pullback. Zone idéale -10% à -25%.
    Retourne (score, pct_from_high)."""
    if high_52w <= 0 or price <= 0:
        return 0.0, 0.0
    pct_from_high = (price - high_52w) / high_52w  # négatif = sous le high
    drawdown = -pct_from_high  # 0.18 = -18% du high

    if drawdown < _PULLBACK_MIN_PCT:
        # Pas vraiment de pullback — ramping vers ATH, FOMO entry zone.
        return 100.0 * (drawdown / _PULLBACK_MIN_PCT) * 0.5, pct_from_high
    if _PULLBACK_IDEAL_LOW <= drawdown <= _PULLBACK_IDEAL_HIGH:
        # Zone idéale.
        return 100.0, pct_from_high
    if drawdown < _PULLBACK_IDEAL_LOW:
        # Entre min et ideal_low : interpolation linéaire 50→100.
        ratio = (drawdown - _PULLBACK_MIN_PCT) / (_PULLBACK_IDEAL_LOW - _PULLBACK_MIN_PCT)
        return 50.0 + 50.0 * ratio, pct_from_high
    if drawdown <= _PULLBACK_MAX_PCT:
        # Au-delà de l'ideal : déclin linéaire vers 0 au max (falling knife).
        ratio = (drawdown - _PULLBACK_IDEAL_HIGH) / (_PULLBACK_MAX_PCT - _PULLBACK_IDEAL_HIGH)
        return max(0.0, 100.0 * (1.0 - ratio)), pct_from_high
    return 0.0, pct_from_high


def compute_support_score(
    *,
    ticker: str,
    current_price: float | None,
    history: pd.DataFrame | None,
) -> SupportScoreResult:
    """Calcule le Support Score composite pour un ticker.

    Args:
        ticker: symbole (utilisé pour log seulement)
        current_price: prix de référence (live ou close du jour)
        history: DataFrame OHLCV avec au minimum colonne 'Close' et index date,
                 typiquement 252j (1 an). Si None ou < _MIN_HISTORY_DAYS, retourne
                 INSUFFICIENT_DATA.

    Returns:
        SupportScoreResult — toujours retourné, jamais None. Le caller peut
        gérer le niveau "INSUFFICIENT_DATA" pour skip l'affichage.
    """
    if current_price is None or not math.isfinite(current_price) or current_price <= 0:
        return SupportScoreResult(
            score=0.0, level="INSUFFICIENT_DATA",
            ma200_proximity=None, swing_low_proximity=0.0, pullback_depth=0.0,
            nearest_swing_low=None, ma200_value=None, pct_from_52w_high=None,
            method="insufficient_data",
        )
    if history is None or "Close" not in history.columns or len(history) < _MIN_HISTORY_DAYS:
        return SupportScoreResult(
            score=0.0, level="INSUFFICIENT_DATA",
            ma200_proximity=None, swing_low_proximity=0.0, pullback_depth=0.0,
            nearest_swing_low=None, ma200_value=None, pct_from_52w_high=None,
            method="insufficient_data",
        )

    closes = history["Close"].dropna().astype(float).to_numpy()
    if len(closes) < _MIN_HISTORY_DAYS:
        return SupportScoreResult(
            score=0.0, level="INSUFFICIENT_DATA",
            ma200_proximity=None, swing_low_proximity=0.0, pullback_depth=0.0,
            nearest_swing_low=None, ma200_value=None, pct_from_52w_high=None,
            method="insufficient_data",
        )

    # ── Composante 1 : MA200 proximity ───────────────────────────
    ma200_value = None
    ma_score: float | None = None
    if len(closes) >= _MA_LONG:
        ma200_value = float(np.mean(closes[-_MA_LONG:]))
        ma_score = _ma200_proximity(current_price, ma200_value)

    # ── Composante 2 : Swing low proximity (90j scan) ────────────
    recent_closes = closes[-_SWING_WINDOW_DAYS:]
    swing_score, nearest_swing = _swing_low_proximity(current_price, recent_closes)

    # ── Composante 3 : Pullback depth (52w) ──────────────────────
    pullback_window = closes[-_PULLBACK_WINDOW_DAYS:]
    high_52w = float(np.max(pullback_window))
    pullback_score, pct_from_high = _pullback_depth_score(current_price, high_52w)

    # ── Composite ────────────────────────────────────────────────
    if ma_score is None:
        # Mode dégradé : sans MA200, on rebalance sur swing+pullback (60/40 dans
        # l'esprit de l'original 0.4/0.2). Le score est plafonné à 80 pour
        # signaler que c'est moins fiable.
        composite = (0.6 * swing_score + 0.4 * pullback_score) * 0.8
        method = "degraded_no_ma200"
    else:
        composite = _W_MA * ma_score + _W_SWING * swing_score + _W_PULLBACK * pullback_score
        method = "full"

    composite = max(0.0, min(100.0, composite))

    return SupportScoreResult(
        score=composite,
        level=_level_from_score(composite),
        ma200_proximity=ma_score,
        swing_low_proximity=swing_score,
        pullback_depth=pullback_score,
        nearest_swing_low=nearest_swing,
        ma200_value=ma200_value,
        pct_from_52w_high=pct_from_high,
        method=method,
    )
