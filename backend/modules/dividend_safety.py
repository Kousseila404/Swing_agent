"""Dividend Safety scorecard — inspiré Seeking Alpha "Dividend Safety Grade".

Score 0-100 dérivé de 4 axes indépendants (chacun 0-100, moyenne pondérée) :

  axis 1 — payout_ratio        (poids 0.30) — le dividende est-il soutenable
                                              par le résultat net ? ≤ 60 % = safe.
  axis 2 — fcf_cover            (poids 0.30) — FCF / dividends_paid ≥ 1.5 = safe.
                                              Plus robuste que earnings cover
                                              (FCF est cash réel, earnings sont
                                              ajustables comptablement).
  axis 3 — yield_vs_5y_avg      (poids 0.20) — yield courant proche de la
                                              moyenne 5Y = stable. Spike yield
                                              = price-fall (red flag).
  axis 4 — dividend_history     (poids 0.20) — flag binaire "le ticker paie
                                              un dividende" + bonus "5Y avg
                                              dispo" (proxy consistency).

Mapping niveau :
  ≥ 80 → "VERY_SAFE"
  60-79 → "SAFE"
  40-59 → "MODERATE"
  20-39 → "RISKY"
  < 20 → "UNSAFE"
  None → "NO_DIVIDEND" si dividend_yield ∈ {None, 0}, sinon "INSUFFICIENT_DATA"
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

_W_PAYOUT = 0.30
_W_FCF_COVER = 0.30
_W_YIELD_STABILITY = 0.20
_W_HISTORY = 0.20


@dataclass
class DividendSafetyResult:
    score: float | None
    level: str
    components: dict[str, float | None] = field(default_factory=dict)
    raw: dict[str, float | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1) if self.score is not None else None,
            "level": self.level,
            "components": {
                k: (round(v, 1) if v is not None else None)
                for k, v in self.components.items()
            },
            "raw": self.raw,
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


def _payout_score(payout_ratio: float | None) -> float | None:
    """payout_ratio ≤ 0.40 → 100 ; ≥ 1.0 → 0 ; déclin linéaire entre."""
    if payout_ratio is None:
        return None
    if payout_ratio < 0:
        # payout négatif = earnings négatifs → on note 20 (red flag mais pas 0
        # car le dividende peut être couvert par FCF).
        return 20.0
    if payout_ratio <= 0.40:
        return 100.0
    if payout_ratio >= 1.0:
        return 0.0
    return 100.0 * (1.0 - (payout_ratio - 0.40) / 0.60)


def _fcf_cover_score(fcf: float | None, dividends_paid: float | None) -> float | None:
    """FCF / dividends_paid. ≥ 2.0 → 100 ; ≤ 0 → 0."""
    if fcf is None or dividends_paid is None or dividends_paid <= 0:
        return None
    if fcf <= 0:
        return 0.0
    cover = fcf / dividends_paid
    if cover >= 2.0:
        return 100.0
    if cover <= 0.5:
        return 0.0
    return 100.0 * (cover - 0.5) / 1.5


def _yield_stability_score(
    yield_now: float | None, yield_5y_avg: float | None,
) -> float | None:
    """Yield stable proche de la moyenne 5Y = 100. Si yield_now > 1.5× avg →
    spike (red flag), score décline. Si yield_now < 0.5× avg → compression
    (price ramping, OK pour total return mais signal de mean-reversion).
    """
    if yield_now is None or yield_5y_avg is None or yield_5y_avg <= 0:
        return None
    ratio = yield_now / yield_5y_avg
    if ratio > 2.0:
        return 0.0   # yield doublé → price effondré, suspicion cut
    if ratio > 1.5:
        return 30.0
    if ratio > 1.2:
        return 70.0
    if ratio >= 0.8:
        return 100.0
    if ratio >= 0.5:
        return 80.0
    return 60.0


def _history_score(
    dividend_yield: float | None, five_year_avg: float | None,
) -> float | None:
    """Bonus binaire si paie + bonus si historique 5Y dispo."""
    if dividend_yield is None or dividend_yield <= 0:
        return None  # pas de dividende → score = None pour exclure de la moyenne
    if five_year_avg is not None and five_year_avg > 0:
        return 100.0
    return 60.0  # paie un dividende mais pas d'historique 5Y → modéré


def compute_dividend_safety(row: dict[str, Any]) -> DividendSafetyResult:
    """Calcule la dividend safety score pour un ticker.

    Champs lus dans `row` (universe.json) :
      • dividend_yield (décimal, 0.04 = 4 %)
      • payout_ratio (décimal)
      • free_cash_flow (USD absolute, TTM)
      • dividends_paid (USD absolute, TTM)
      • five_year_avg_dividend_yield (décimal)
    """
    div_yield = _safe(row.get("dividend_yield"))
    payout = _safe(row.get("payout_ratio"))
    fcf = _safe(row.get("free_cash_flow"))
    dpaid = _safe(row.get("dividends_paid"))
    avg5 = _safe(row.get("five_year_avg_dividend_yield"))

    raw = {
        "dividend_yield": div_yield,
        "payout_ratio": payout,
        "free_cash_flow": fcf,
        "dividends_paid": dpaid,
        "five_year_avg_dividend_yield": avg5,
    }

    # Cas dégénéré : le ticker ne paie pas de dividende.
    if div_yield is None or div_yield == 0.0:
        return DividendSafetyResult(
            score=None, level="NO_DIVIDEND", components={}, raw=raw,
        )

    components = {
        "payout":          _payout_score(payout),
        "fcf_cover":       _fcf_cover_score(fcf, dpaid),
        "yield_stability": _yield_stability_score(div_yield, avg5),
        "history":         _history_score(div_yield, avg5),
    }
    weights = {
        "payout":          _W_PAYOUT,
        "fcf_cover":       _W_FCF_COVER,
        "yield_stability": _W_YIELD_STABILITY,
        "history":         _W_HISTORY,
    }

    num = den = 0.0
    for name, val in components.items():
        if val is None:
            continue
        w = weights[name]
        num += w * val
        den += w

    if den <= 0:
        return DividendSafetyResult(
            score=None, level="INSUFFICIENT_DATA",
            components=components, raw=raw,
        )

    score = num / den
    if score >= 80:
        level = "VERY_SAFE"
    elif score >= 60:
        level = "SAFE"
    elif score >= 40:
        level = "MODERATE"
    elif score >= 20:
        level = "RISKY"
    else:
        level = "UNSAFE"

    return DividendSafetyResult(
        score=score, level=level, components=components, raw=raw,
    )
