"""Earnings Surprise score — quantifie le PEAD signal pour un ticker.

Indépendant du pilier Revisions (qui rank cross-universe). Ce module produit
un score 0-100 ABSOLU pour la factsheet (Seeking Alpha-style).

Score combine :
  • beat_rate_8q ∈ [0, 1] (poids 0.5)
  • surprise_avg_4q clampée à ±15 % puis remappée 0-100 (poids 0.3)
  • surprise_pct_last clampée ±20 % remappée 0-100 (poids 0.2)

Mapping :
  ≥ 80 → "STRONG_BEAT"
  60-79 → "BEAT"
  40-59 → "INLINE"
  20-39 → "MISS"
  < 20 → "STRONG_MISS"
  None → "INSUFFICIENT_DATA"
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

_W_BEAT_RATE = 0.50
_W_SURPRISE_AVG = 0.30
_W_SURPRISE_LAST = 0.20

# Bornes de remapping (% surprise → score).
_SURPRISE_AVG_BAND = 15.0   # ±15 % couvre la majorité des cas (queue extrême écrasée)
_SURPRISE_LAST_BAND = 20.0


@dataclass
class EarningsSurpriseScore:
    score: float | None
    level: str
    beat_rate_8q: float | None
    surprise_avg_4q: float | None
    surprise_pct_last: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1) if self.score is not None else None,
            "level": self.level,
            "beat_rate_8q": round(self.beat_rate_8q, 3) if self.beat_rate_8q is not None else None,
            "surprise_avg_4q": round(self.surprise_avg_4q, 2) if self.surprise_avg_4q is not None else None,
            "surprise_pct_last": round(self.surprise_pct_last, 2) if self.surprise_pct_last is not None else None,
        }


def _safe(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _remap_surprise_to_score(pct: float | None, band: float) -> float | None:
    """Surprise pct → score 0-100, clampé à ±band. 0 % → 50, +band → 100, -band → 0."""
    if pct is None:
        return None
    clamped = max(-band, min(band, pct))
    return 50.0 + (clamped / band) * 50.0


def _level_from_score(score: float | None) -> str:
    if score is None:
        return "INSUFFICIENT_DATA"
    if score >= 80:
        return "STRONG_BEAT"
    if score >= 60:
        return "BEAT"
    if score >= 40:
        return "INLINE"
    if score >= 20:
        return "MISS"
    return "STRONG_MISS"


def compute_earnings_surprise_score(row: dict[str, Any]) -> EarningsSurpriseScore:
    """Calcule le score earnings surprise pour un ticker.

    Args:
      row: dict ticker depuis universe.json. Champs lus :
        earnings_beat_rate_8q, earnings_surprise_avg_4q, earnings_surprise_pct_last.

    Returns:
      EarningsSurpriseScore — score=None si AUCUN champ disponible.
    """
    beat = _safe(row.get("earnings_beat_rate_8q"))
    avg4 = _safe(row.get("earnings_surprise_avg_4q"))
    last = _safe(row.get("earnings_surprise_pct_last"))

    components: list[tuple[float, float]] = []  # (weight, sub_score)
    if beat is not None:
        components.append((_W_BEAT_RATE, beat * 100.0))
    avg4_score = _remap_surprise_to_score(avg4, _SURPRISE_AVG_BAND)
    if avg4_score is not None:
        components.append((_W_SURPRISE_AVG, avg4_score))
    last_score = _remap_surprise_to_score(last, _SURPRISE_LAST_BAND)
    if last_score is not None:
        components.append((_W_SURPRISE_LAST, last_score))

    if not components:
        return EarningsSurpriseScore(
            score=None, level="INSUFFICIENT_DATA",
            beat_rate_8q=beat, surprise_avg_4q=avg4, surprise_pct_last=last,
        )

    num = sum(w * s for w, s in components)
    den = sum(w for w, _ in components)
    score = num / den if den > 0 else 50.0
    return EarningsSurpriseScore(
        score=score,
        level=_level_from_score(score),
        beat_rate_8q=beat,
        surprise_avg_4q=avg4,
        surprise_pct_last=last,
    )
