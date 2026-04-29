"""Buy Signal — verdict BUY/STRONG_BUY/WATCH/SKIP par ticker.

Lot 18 — règle de décision déterministe au-dessus du composite TITAN, calibrée
pour un investisseur LT débutant (3 premiers mois conservateur).

Grille de décision :
  STRONG_BUY  : TITAN ≥ 80 + Quality ≥ 60 + Momentum ≥ 50 + earnings ≥ 14j +
                ≥ 2 boosters validés
  BUY         : TITAN ≥ 75 + Quality ≥ 60 + Momentum ≥ 50 + earnings ≥ 14j +
                ≥ 1 booster validé
  WATCH       : TITAN 70-74 + earnings ≥ 14j (intéressant mais conviction insuffisante)
  EARNINGS_BLACKOUT : earnings dans < 7j (skippé d'office)
  EARNINGS_NEAR : earnings 7-14j (watch only, size réduit)
  CHEAP_JUNK  : Value ≥ 80 ET Quality < 35 → piège fondamental
  FALLING_KNIFE : Value ≥ 80 ET Momentum < 30 → cassure de tendance malgré cheapness
  SKIP        : composite < 70 ou autre filtre négatif

Boosters reconnus :
  • Revisions ≥ 65        (analystes positifs récents)
  • Insider cluster=True   (3+ insiders distincts/7j)
  • Piotroski ≥ 7/9       (bilan + cash flow excellents)
  • Support ≥ 70 (ON_SUPPORT) (entry timing optimal)
  • Earnings beat rate ≥ 0.75 sur 8Q (PEAD signal)

Sizing recommandé selon le verdict :
  STRONG_BUY  : 20% du budget TITAN (600€ sur 3000€)
  BUY         : 13% (400€)
  WATCH       : 0% (paper-trading mental, pas d'achat)
  reste       : 0%

Garde-fou "ATH extended" (depuis 2026-04-29) :
  Si support OFF + drawdown_from_high > -3% + momentum_6m > 50% (entrée à
  l'ATH absolu d'un name parabolique), un STRONG_BUY est rétrogradé en BUY
  et le sizing est encore divisé. Le R/R est défavorable dans cette config
  même si les fondamentaux sont parfaits.

Modulation sizing par qualité d'entrée (depuis 2026-04-29) :
  ON_SUPPORT     × 1.0   (entrée optimale)
  NEAR_SUPPORT   × 0.7
  OFF_SUPPORT    × 0.4   (réduit, attendre pullback préférable)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# Seuils principaux — ajustables selon la phase d'utilisation.
_T_STRONG_BUY = 80.0
_T_BUY        = 75.0
_T_WATCH      = 70.0

_T_QUALITY_MIN = 60.0
_T_MOMENTUM_MIN = 50.0

# Boosters
_T_REVISIONS_BOOSTER = 65.0
_T_PIOTROSKI_BOOSTER = 7  # F-Score sur 9
_T_SUPPORT_BOOSTER   = 70.0
_T_BEAT_RATE_BOOSTER = 0.75

# Earnings blackout / near
_EARNINGS_BLACKOUT_DAYS = 7
_EARNINGS_NEAR_DAYS = 14

# Tilt négatifs déjà présents dans titan_tilt_flags du scoring.
_TILT_CHEAP_JUNK = "cheap_junk"
_TILT_FALLING_KNIFE = "falling_knife"

# Sizing recommandé (ratio du budget TITAN total)
_SIZE_STRONG_BUY = 0.20
_SIZE_BUY = 0.13
_SIZE_BUY_NEAR_EARNINGS = 0.09  # réduit 30% si earnings 7-14j

# Modulation sizing par qualité du support (multiplicateur appliqué sur sizing_pct).
# Permet de garder le verdict (BUY/STRONG_BUY) tout en réduisant l'exposition
# quand l'entry timing est défavorable.
_SUP_MULT_ON = 1.00
_SUP_MULT_NEAR = 0.70
_SUP_MULT_OFF = 0.40

# Garde-fou "Entrée à l'ATH" : si name OFF_SUPPORT + drawdown > -3% (à <3% du
# 52w high) + momentum 6M > 50% (parabolique), on rétrograde STRONG_BUY → BUY
# et on encore divise le sizing.
_ATH_DRAWDOWN_THRESHOLD = -3.0   # > -3% = à moins de 3% du 52w high
_ATH_MOMENTUM_6M_THRESHOLD = 50.0
_ATH_SIZING_PENALTY = 0.5        # multiplicateur additionnel


@dataclass
class BuySignalResult:
    """Verdict + détails pour la UI."""
    verdict: str           # STRONG_BUY / BUY / WATCH / EARNINGS_BLACKOUT / SKIP / etc.
    score: int             # 0-100, score interne d'éligibilité (différent de TITAN)
    reasons_pos: list[str] = field(default_factory=list)
    reasons_neg: list[str] = field(default_factory=list)
    boosters_active: list[str] = field(default_factory=list)
    sizing_pct: float = 0.0   # % budget TITAN recommandé
    days_until_earnings: int | None = None
    label: str = ""        # libellé court UI ("STRONG BUY", "Skip — earnings 5j", ...)
    color: str = "muted"   # success / primary / warning / danger / muted

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "score": self.score,
            "reasons_pos": self.reasons_pos,
            "reasons_neg": self.reasons_neg,
            "boosters_active": self.boosters_active,
            "sizing_pct": round(self.sizing_pct, 3),
            "days_until_earnings": self.days_until_earnings,
            "label": self.label,
            "color": self.color,
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


def _days_until_iso(target: Any) -> int | None:
    if not target:
        return None
    s = str(target)[:10]
    try:
        d = date.fromisoformat(s)
    except ValueError:
        return None
    return (d - date.today()).days


def compute_buy_signal(
    row: dict[str, Any],
    *,
    support_score: float | None = None,
) -> BuySignalResult:
    """Calcule le verdict BUY pour un ticker.

    Args:
      row: dict scoré (output get_scored_universe) — doit contenir au minimum
        titan_composite_score et les sous-scores piliers. Lit aussi
        row['support'] (level/score) et row['price_action'] (drawdown_from_high_pct,
        momentum_6m_pct) si présents pour le garde-fou ATH.
      support_score: optionnel, peut être passé séparément depuis support_score.py.
        Si None, on essaie de le lire dans row.support.score.

    Returns:
      BuySignalResult — toujours retourné, jamais None.
    """
    composite = _safe(row.get("titan_composite_score"))
    quality = _safe(row.get("quality_score"))
    value = _safe(row.get("value_score"))
    momentum = _safe(row.get("momentum_score"))
    revisions = _safe(row.get("revisions_score"))
    insider_cluster = bool(row.get("insider_cluster_buying"))
    insider_score = _safe(row.get("insider_score"))
    piotroski_f = row.get("f_score")
    try:
        piotroski_f = int(piotroski_f) if piotroski_f is not None else None
    except (TypeError, ValueError):
        piotroski_f = None
    beat_rate = _safe(row.get("earnings_beat_rate_8q"))
    tilt_flags = row.get("titan_tilt_flags") or []
    sup_block = row.get("support") or {}
    if support_score is None:
        support_score = _safe(sup_block.get("score"))
    support_level = sup_block.get("level")  # ON / NEAR / OFF / INSUFFICIENT_DATA

    pa_block = row.get("price_action") or {}
    drawdown_from_high = _safe(pa_block.get("drawdown_from_high_pct"))
    momentum_6m = _safe(pa_block.get("momentum_6m_pct"))

    next_earnings = row.get("next_earnings_date")
    days_to_earnings = _days_until_iso(next_earnings)

    res = BuySignalResult(
        verdict="SKIP",
        score=0,
        days_until_earnings=days_to_earnings,
    )

    # ── Filtres rouges (game-over) ─────────────────────────────────
    if composite is None:
        res.verdict = "NO_DATA"
        res.label = "Données insuffisantes"
        res.color = "muted"
        res.reasons_neg.append("Composite TITAN absent")
        return res

    if _TILT_CHEAP_JUNK in tilt_flags:
        res.verdict = "CHEAP_JUNK"
        res.label = "Value trap — Quality faible"
        res.color = "danger"
        res.reasons_neg.append(
            f"Cheap-junk pattern : Value {value:.0f} + Quality {quality:.0f} < 35"
        )
        return res

    if _TILT_FALLING_KNIFE in tilt_flags:
        res.verdict = "FALLING_KNIFE"
        res.label = "Falling knife — momentum négatif"
        res.color = "danger"
        res.reasons_neg.append(
            f"Falling knife : Value {value:.0f} + Momentum {momentum:.0f} < 30"
        )
        return res

    # Earnings blackout (≤ 7 jours)
    if days_to_earnings is not None and 0 <= days_to_earnings < _EARNINGS_BLACKOUT_DAYS:
        res.verdict = "EARNINGS_BLACKOUT"
        res.label = f"Earnings dans {days_to_earnings}j — skip"
        res.color = "danger"
        res.reasons_neg.append(
            f"Earnings catalyst à J+{days_to_earnings} (blackout < {_EARNINGS_BLACKOUT_DAYS}j)"
        )
        return res

    # ── Calcul boosters ────────────────────────────────────────────
    boosters: list[str] = []
    if revisions is not None and revisions >= _T_REVISIONS_BOOSTER:
        boosters.append("Revisions ≥ 65")
    if insider_cluster:
        boosters.append("Insider cluster (3+ insiders/7j)")
    elif insider_score is not None and insider_score >= 75:
        boosters.append(f"Insider score {insider_score:.0f}")
    if piotroski_f is not None and piotroski_f >= _T_PIOTROSKI_BOOSTER:
        boosters.append(f"Piotroski {piotroski_f}/9")
    if support_score is not None and support_score >= _T_SUPPORT_BOOSTER:
        boosters.append("Support ≥ 70 (ON_SUPPORT)")
    if beat_rate is not None and beat_rate >= _T_BEAT_RATE_BOOSTER:
        boosters.append(f"Beat rate {beat_rate*100:.0f}% / 8Q")
    res.boosters_active = boosters
    n_boosters = len(boosters)

    # ── Filtres conditions de base (Quality + Momentum) ───────────
    quality_ok = quality is None or quality >= _T_QUALITY_MIN
    momentum_ok = momentum is None or momentum >= _T_MOMENTUM_MIN

    if quality is not None and quality < _T_QUALITY_MIN:
        res.reasons_neg.append(f"Quality {quality:.0f} < {_T_QUALITY_MIN:.0f}")
    if momentum is not None and momentum < _T_MOMENTUM_MIN:
        res.reasons_neg.append(f"Momentum {momentum:.0f} < {_T_MOMENTUM_MIN:.0f}")

    # Earnings near (7-14j) — pas blackout, mais size réduit
    earnings_near = (
        days_to_earnings is not None
        and _EARNINGS_BLACKOUT_DAYS <= days_to_earnings < _EARNINGS_NEAR_DAYS
    )
    if earnings_near:
        res.reasons_neg.append(f"Earnings dans {days_to_earnings}j (size −30%)")

    # Reasons positifs structurés
    res.reasons_pos.append(f"TITAN composite {composite:.1f}/100")
    if quality is not None and quality >= _T_QUALITY_MIN:
        res.reasons_pos.append(f"Quality {quality:.0f} (≥ {_T_QUALITY_MIN:.0f})")
    if momentum is not None and momentum >= _T_MOMENTUM_MIN:
        res.reasons_pos.append(f"Momentum {momentum:.0f} (≥ {_T_MOMENTUM_MIN:.0f})")
    for b in boosters:
        res.reasons_pos.append(b)

    # ── Garde-fou ATH extended ────────────────────────────────────
    # Si OFF_SUPPORT + price à <3% de l'ATH 52w + momentum 6M parabolique,
    # le R/R est défavorable même avec des fondamentaux parfaits.
    is_ath_extended = (
        support_level == "OFF_SUPPORT"
        and drawdown_from_high is not None
        and drawdown_from_high > _ATH_DRAWDOWN_THRESHOLD
        and momentum_6m is not None
        and momentum_6m > _ATH_MOMENTUM_6M_THRESHOLD
    )

    # Multiplicateur sizing par qualité du support.
    if support_level == "ON_SUPPORT":
        support_mult = _SUP_MULT_ON
    elif support_level == "NEAR_SUPPORT":
        support_mult = _SUP_MULT_NEAR
    elif support_level == "OFF_SUPPORT":
        support_mult = _SUP_MULT_OFF
    else:
        support_mult = 1.0  # INSUFFICIENT_DATA → ne pas pénaliser

    # ── Verdict principal ─────────────────────────────────────────
    # STRONG_BUY : TITAN ≥ 80 + Q ≥ 60 + M ≥ 50 + ≥ 2 boosters + earnings safe
    if (
        composite >= _T_STRONG_BUY
        and quality_ok and momentum_ok
        and n_boosters >= 2
    ):
        if is_ath_extended:
            # Rétrograde STRONG_BUY → BUY et applique pénalité ATH.
            res.verdict = "BUY"
            res.label = "BUY (rétrogradé — entrée à l'ATH)"
            res.color = "primary"
            res.reasons_neg.append(
                f"Entrée à l'ATH ({drawdown_from_high:.1f}% du 52w high) "
                f"+ momentum 6M {momentum_6m:.0f}% — wait pullback"
            )
            base_size = _SIZE_BUY_NEAR_EARNINGS if earnings_near else _SIZE_BUY
            res.sizing_pct = base_size * support_mult * _ATH_SIZING_PENALTY
            if earnings_near:
                res.label += f" earnings J+{days_to_earnings}"
            res.score = 70
            return res
        if earnings_near:
            res.verdict = "STRONG_BUY"
            res.label = f"STRONG BUY (size −30% earnings J+{days_to_earnings})"
            res.color = "success"
            res.sizing_pct = _SIZE_STRONG_BUY * 0.7 * support_mult
        else:
            res.verdict = "STRONG_BUY"
            res.label = "STRONG BUY"
            res.color = "success"
            res.sizing_pct = _SIZE_STRONG_BUY * support_mult
        if support_mult < 1.0:
            res.reasons_neg.append(
                f"Sizing réduit ×{support_mult:.1f} — support {support_level or 'inconnu'}"
            )
        res.score = 95
        return res

    # BUY : TITAN ≥ 75 + Q ≥ 60 + M ≥ 50 + ≥ 1 booster
    if (
        composite >= _T_BUY
        and quality_ok and momentum_ok
        and n_boosters >= 1
    ):
        if earnings_near:
            res.verdict = "BUY"
            res.label = f"BUY (size −30% earnings J+{days_to_earnings})"
            res.color = "primary"
            res.sizing_pct = _SIZE_BUY_NEAR_EARNINGS * support_mult
        else:
            res.verdict = "BUY"
            res.label = "BUY"
            res.color = "primary"
            res.sizing_pct = _SIZE_BUY * support_mult
        if is_ath_extended:
            res.reasons_neg.append(
                f"Entrée à l'ATH ({drawdown_from_high:.1f}% du 52w high) "
                f"+ momentum 6M {momentum_6m:.0f}% — wait pullback"
            )
            res.sizing_pct *= _ATH_SIZING_PENALTY
        elif support_mult < 1.0:
            res.reasons_neg.append(
                f"Sizing réduit ×{support_mult:.1f} — support {support_level or 'inconnu'}"
            )
        res.score = 80
        return res

    # WATCH : TITAN 70-74, ou TITAN ≥ 75 mais 0 booster, ou Q/M faible mais TITAN haut
    if composite >= _T_WATCH:
        if composite >= _T_BUY and n_boosters == 0:
            res.label = "WATCH — aucun booster"
            res.reasons_neg.append("0 booster validé (Revisions/Insider/Piotroski/Support/Beat)")
        elif not quality_ok:
            res.label = "WATCH — Quality faible"
        elif not momentum_ok:
            res.label = "WATCH — Momentum dégradé"
        else:
            res.label = f"WATCH — TITAN {composite:.0f} (< {_T_BUY:.0f})"
        res.verdict = "WATCH"
        res.color = "warning"
        res.score = 60
        return res

    # SKIP — composite < 70
    res.verdict = "SKIP"
    res.label = f"SKIP — TITAN {composite:.0f} < {_T_WATCH:.0f}"
    res.color = "muted"
    res.score = 30
    res.reasons_neg.append(f"Composite {composite:.0f} sous le seuil watch ({_T_WATCH:.0f})")
    return res
