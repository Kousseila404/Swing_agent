"""Factor Grades — mapping percentile/score 0-100 → lettre A+/A/B+/B/C+/C/D/F.

Inspiré du système Seeking Alpha "Factor Grades" qui exprime la position
sectorielle d'un titre sur chaque pilier sous forme de note. Ici on travaille
sur les scores TITAN existants (quality_score, value_score…) qui sont DÉJÀ
des percentiles cross-universe ou sector-relative — donc directement mappables.

Mapping (calibré pour distinguer le top 10/30/50/70/90 % d'un univers SP500) :
  ≥ 90 → "A+"
  80-89 → "A"
  70-79 → "B+"
  60-69 → "B"
  50-59 → "C+"
  40-49 → "C"
  20-39 → "D"
  < 20  → "F"
  None  → "N/A"

Usage typique :
  letter = factor_grade(scored_row.get("quality_score"))
  grades = compute_factor_grades(scored_row)
"""
from __future__ import annotations

import math
from typing import Any

# Champs scoring TITAN (sortie de sector_metrics._score_universe) pour
# lesquels une note lettrée a du sens.
PILLAR_FIELDS: tuple[tuple[str, str], ...] = (
    ("quality_score",   "Quality"),
    ("value_score",     "Value"),
    ("risk_score",      "Risk"),
    ("momentum_score",  "Momentum"),
    ("piotroski_score", "Piotroski"),
    ("growth_score",    "Growth"),
    ("revisions_score", "Revisions"),
    ("insider_score",   "Insider"),
    ("sentiment_score", "Sentiment"),
)


def factor_grade(score: float | None) -> str:
    """0-100 → lettre. None / NaN / hors bornes → 'N/A'."""
    if score is None:
        return "N/A"
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "N/A"
    if math.isnan(s) or math.isinf(s):
        return "N/A"
    if s >= 90:
        return "A+"
    if s >= 80:
        return "A"
    if s >= 70:
        return "B+"
    if s >= 60:
        return "B"
    if s >= 50:
        return "C+"
    if s >= 40:
        return "C"
    if s >= 20:
        return "D"
    return "F"


def compute_factor_grades(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Retourne {pillar_label: {score, grade}} pour les 8 piliers TITAN.

    Args:
      row: dict ticker enrichi (output de sector_metrics._score_universe ou
           get_scored_universe).
    """
    out: dict[str, dict[str, Any]] = {}
    for field_name, label in PILLAR_FIELDS:
        v = row.get(field_name)
        try:
            s = float(v) if v is not None else None
            if s is not None and (math.isnan(s) or math.isinf(s)):
                s = None
        except (TypeError, ValueError):
            s = None
        out[label] = {
            "score": round(s, 2) if s is not None else None,
            "grade": factor_grade(s),
        }
    return out


def quant_rating_letter(composite_score: float | None) -> str:
    """Mapping TITAN composite → label "Strong Buy / Buy / Hold / Sell /
    Strong Sell" (vocabulaire SA). Calibration plus stricte que les piliers
    car c'est un signal d'action.
    """
    if composite_score is None:
        return "N/A"
    try:
        s = float(composite_score)
    except (TypeError, ValueError):
        return "N/A"
    if math.isnan(s) or math.isinf(s):
        return "N/A"
    if s >= 80:
        return "STRONG_BUY"
    if s >= 65:
        return "BUY"
    if s >= 45:
        return "HOLD"
    if s >= 30:
        return "SELL"
    return "STRONG_SELL"
