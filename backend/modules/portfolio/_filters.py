"""Filtres purs : bullish momentum + top-N par titan_composite_score."""
from __future__ import annotations

from typing import Any

from ._utils import _safe_float

# Seuil "tendance haussière" : momentum 6M > -10% (au lieu de strict > 0).
# Lot 5 a intégré le momentum dans le composite TITAN — un ticker à mom -2%
# avec excellents fundamentaux et top score TITAN ne devrait PAS être éjecté
# par un seuil binaire arbitraire. Le momentum est désormais sanctionné de
# manière continue via le pilier Momentum.
# Le seuil -10% reste un garde-fou contre les "falling knives" extrêmes
# (ex: ticker en perte 30%+ sur 6 mois → probablement story broken).
# Tickers None (data manquante) : kept (fail-open) — les agréggats peuvent
# manquer en data sans être un signal négatif.
BULLISH_MOMENTUM_THRESHOLD = -10.0


def filter_bullish(
    tickers: list[str],
    scored: dict[str, dict[str, Any]],
    *,
    threshold: float | None = None,
) -> list[str]:
    """Garde les tickers avec momentum 6M > threshold.

    Args:
        threshold : override du seuil global (testing / régime BEAR plus strict).
                    Default = `BULLISH_MOMENTUM_THRESHOLD` (= -10%).

    Comportement : un mom None est gardé (data manquante ≠ signal bear).
    Lot 5 a intégré le momentum dans le composite donc le filtre n'est plus
    qu'un garde-fou contre les pertes extrêmes.
    """
    threshold = BULLISH_MOMENTUM_THRESHOLD if threshold is None else threshold
    kept = []
    for t in tickers:
        row = scored.get(t)
        if not row:
            continue
        mom = _safe_float(row.get("momentum_return_pct"))
        if mom is None or mom > threshold:
            kept.append(t)
    return kept


def filter_top_by_score(
    tickers: list[str],
    n: int,
    scored: dict[str, dict[str, Any]],
) -> list[str]:
    """Top N par titan_composite_score décroissant. Ties brisés par ticker alpha."""
    ranked = []
    for t in tickers:
        row = scored.get(t) or {}
        score = _safe_float(row.get("titan_composite_score"))
        if score is None:
            continue
        ranked.append((t, score))
    ranked.sort(key=lambda x: (-x[1], x[0]))
    return [t for t, _ in ranked[:n]]
