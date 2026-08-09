"""Prix cible fondamental 12 mois — moteur fair-value TITAN.

Voir `docs/price_target_design.md` (§3) pour la méthodologie complète et
`price_target_calibration.py` pour la boucle qui apprend `weights`/`band_pct`.

Blend de 3 composantes fondamentales (aucune nouvelle collecte de donnée,
tout est déjà présent sur le record ticker scoré) :
  A. Multiple reversion sector-relative (ev_to_ebitda préféré, fallback
     forward_pe — cf. `_fair_price_multiple`)
  B. PEG-reversion sector-relative
  C. Ancrage `fundamentals_levels.compute_fundamental_levels()["tp"]`
     (Buffett-TP déjà en prod pour `lt_exit_policy`) — appelé, jamais dupliqué.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

from modules import data_confidence, fundamentals_levels

# Même seuil que sector_metrics._scoring._percentile_rank_by_sector — sous ce
# nombre de tickers valides, un secteur est trop petit pour une médiane fiable
# (cf. commentaire `_MIN_SECTOR_SIZE_FOR_RELATIVE` dans ce module-là).
_MIN_SECTOR_SIZE_FOR_RELATIVE = 12

_MULTIPLE_FIELDS = ("forward_pe", "ev_to_ebitda", "peg_ratio")

DEFAULT_WEIGHTS: dict[str, float] = {"w_multiple": 1 / 3, "w_peg": 1 / 3, "w_buffett": 1 / 3}
DEFAULT_BAND_PCT = 0.15

# §3.1 D — tilt flags déjà calculés par sector_metrics._scoring, pas recalculés ici.
_NEGATIVE_TILT_FLAGS = {"cheap_junk", "falling_knife"}
_POSITIVE_TILT_FLAGS = {"qarp", "garp"}

_UNAVAILABLE: dict[str, Any] = {
    "price_target": None,
    "price_target_low": None,
    "price_target_high": None,
    "upside_pct": None,
    "price_target_confidence": None,
    "method": "unavailable",
    "components": {"multiple": None, "peg": None, "buffett": None},
    "let_it_ride": False,
    "tilt_flags": [],
}


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def compute_sector_medians(
    universe: dict[str, dict[str, Any]],
) -> dict[str, dict[str, float | None]]:
    """Médiane sectorielle (+ fallback global) de forward_pe/ev_to_ebitda/peg_ratio.

    Un secteur avec moins de `_MIN_SECTOR_SIZE_FOR_RELATIVE` valeurs valides
    pour un champ donné bascule sur la médiane globale de ce champ, comme
    `sector_metrics._scoring._percentile_rank_by_sector`.
    """
    by_sector: dict[str, dict[str, list[float]]] = {}
    global_vals: dict[str, list[float]] = {f: [] for f in _MULTIPLE_FIELDS}

    for row in universe.values():
        if not row:
            continue
        sector = row.get("sector") or "Unknown"
        bucket = by_sector.setdefault(sector, {f: [] for f in _MULTIPLE_FIELDS})
        for field in _MULTIPLE_FIELDS:
            v = _safe_float(row.get(field))
            if v is not None and v > 0:
                bucket[field].append(v)
                global_vals[field].append(v)

    global_medians = {
        field: (statistics.median(vals) if vals else None)
        for field, vals in global_vals.items()
    }

    out: dict[str, dict[str, float | None]] = {}
    for sector, bucket in by_sector.items():
        medians: dict[str, float | None] = {}
        for field, vals in bucket.items():
            if len(vals) >= _MIN_SECTOR_SIZE_FOR_RELATIVE:
                medians[field] = statistics.median(vals)
            else:
                medians[field] = global_medians[field]
        out[sector] = medians
    return out


def _fair_price_multiple(
    row: dict[str, Any], sector_medians: dict[str, dict[str, float | None]]
) -> float | None:
    current_price = _safe_float(row.get("current_price"))
    if current_price is None or current_price <= 0:
        return None
    # ev_to_ebitda préféré (neutre à la structure capital), fallback forward_pe
    # si absent — cf. §3.1 A du design doc.
    ev = _safe_float(row.get("ev_to_ebitda"))
    fpe = _safe_float(row.get("forward_pe"))
    if ev is not None and ev > 0:
        metric_val, metric_field = ev, "ev_to_ebitda"
    elif fpe is not None and fpe > 0:
        metric_val, metric_field = fpe, "forward_pe"
    else:
        return None

    sector = row.get("sector") or "Unknown"
    fair_multiple = (sector_medians.get(sector) or {}).get(metric_field)
    if fair_multiple is None or fair_multiple <= 0:
        return None
    return current_price * (fair_multiple / metric_val)


def _fair_price_peg(
    row: dict[str, Any], sector_medians: dict[str, dict[str, float | None]]
) -> float | None:
    current_price = _safe_float(row.get("current_price"))
    peg = _safe_float(row.get("peg_ratio"))
    if current_price is None or current_price <= 0 or peg is None or peg <= 0:
        return None
    sector = row.get("sector") or "Unknown"
    peg_median = (sector_medians.get(sector) or {}).get("peg_ratio")
    if peg_median is None or peg_median <= 0:
        return None
    return current_price * (peg_median / peg)


def _fair_price_buffett(row: dict[str, Any]) -> tuple[float | None, bool]:
    current_price = _safe_float(row.get("current_price"))
    if current_price is None or current_price <= 0:
        return None, False
    levels = fundamentals_levels.compute_fundamental_levels(
        price=current_price,
        quality_score=row.get("quality_score"),
        piotroski_score=row.get("f_score"),
        value_score=row.get("value_score"),
        peg_ratio=row.get("peg_ratio"),
    )
    return _safe_float(levels.get("tp")), bool(levels.get("let_it_ride"))


def compute_price_target(
    row: dict[str, Any],
    *,
    sector_medians: dict[str, dict[str, float | None]] | None = None,
    weights: dict[str, float] | None = None,
    band_pct: float = DEFAULT_BAND_PCT,
) -> dict[str, Any]:
    """Prix cible fondamental 12 mois pour un ticker (record scoré unique).

    `sector_medians` doit venir de `compute_sector_medians()` sur l'univers
    complet (pas recalculable depuis un seul ticker). Si omis, les composantes
    A/B (sector-relative) sont indisponibles et seul C (Buffett-TP) contribue.
    """
    weights = weights or DEFAULT_WEIGHTS
    sector_medians = sector_medians or {}

    current_price = _safe_float(row.get("current_price"))
    if current_price is None or current_price <= 0:
        return dict(_UNAVAILABLE)

    fair_multiple = _fair_price_multiple(row, sector_medians)
    fair_peg = _fair_price_peg(row, sector_medians)
    fair_buffett, let_it_ride = _fair_price_buffett(row)

    parts: list[tuple[float, float]] = []
    if fair_multiple is not None:
        parts.append((weights.get("w_multiple", 0.0), fair_multiple))
    if fair_peg is not None:
        parts.append((weights.get("w_peg", 0.0), fair_peg))
    if fair_buffett is not None:
        parts.append((weights.get("w_buffett", 0.0), fair_buffett))

    weight_sum = sum(w for w, _ in parts)
    if weight_sum <= 0:
        return dict(_UNAVAILABLE)

    price_target = sum(w * p for w, p in parts) / weight_sum

    conf = data_confidence.compute_confidence(row)
    conf_score = conf.get("score")
    conf_frac = (conf_score / 100.0) if conf_score is not None else 0.5
    # §3.2 — "band_pct réduit par confidence" : haute confidence resserre la
    # bande (×0.7), basse confidence l'élargit (×1.3).
    conf_factor = 1.3 - 0.6 * conf_frac
    band_low = band_pct * conf_factor
    band_high = band_pct * conf_factor

    # §3.1 D — tilt flags déjà calculés par sector_metrics (pas recalculés ici).
    # Le doc contient deux formulations légèrement différentes pour le tilt
    # négatif (§3.1D: "compresse la fourchette basse" / §3.2: "élargit par
    # tilt négatif") — on suit ici la formulation la plus précise (§3.1D, qui
    # décrit explicitement l'ajustement composante par composante) ; à
    # clarifier avec le propriétaire si le comportement observé en calibration
    # ne correspond pas à l'intention.
    flags = set(row.get("titan_tilt_flags") or [])
    if flags & _NEGATIVE_TILT_FLAGS:
        band_low *= 0.7
    if (flags & _POSITIVE_TILT_FLAGS) or let_it_ride:
        band_high *= 1.3

    price_target_low = price_target * (1.0 - band_low)
    price_target_high = price_target * (1.0 + band_high)
    upside_pct = (price_target / current_price - 1.0) * 100.0

    return {
        "price_target": round(price_target, 4),
        "price_target_low": round(price_target_low, 4),
        "price_target_high": round(price_target_high, 4),
        "upside_pct": round(upside_pct, 2),
        "price_target_confidence": conf_score,
        "method": "fundamental_blend",
        "components": {
            "multiple": fair_multiple,
            "peg": fair_peg,
            "buffett": fair_buffett,
        },
        "let_it_ride": let_it_ride,
        "tilt_flags": sorted(flags),
    }


def compute_for_universe(
    universe: dict[str, dict[str, Any]],
    *,
    weights: dict[str, float] | None = None,
    band_pct: float = DEFAULT_BAND_PCT,
) -> dict[str, dict[str, Any]]:
    """Prix cible pour tout un univers scoré — calcule les médianes une fois."""
    sector_medians = compute_sector_medians(universe)
    return {
        ticker: compute_price_target(
            row, sector_medians=sector_medians, weights=weights, band_pct=band_pct
        )
        for ticker, row in universe.items()
    }
