"""Helpers purs : arithmétique, normalisation, bucketing reco.

Aucun état mutable, aucune dépendance à la config/paths — safely importable
sans side-effects.
"""
from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime
from typing import Any

# Seuils de bucket recommandation analystes (1=Strong Buy, 5=Strong Sell).
_RECO_BUCKETS: list[tuple[str, float]] = [
    ("strong_buy",   1.5),
    ("buy",          2.5),
    ("hold",         3.5),
    ("underperform", 4.5),
    ("sell",         5.01),  # catch-all
]


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _median(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None and math.isfinite(v)]
    if not vals:
        return None
    return float(statistics.median(vals))


def _stdev(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None and math.isfinite(v)]
    if len(vals) < 2:
        return None
    return float(statistics.pstdev(vals))


def _mean(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None and math.isfinite(v)]
    if not vals:
        return None
    return float(statistics.fmean(vals))


def _upside_pct(ticker: dict[str, Any]) -> float | None:
    """Upside target / prix courant − 1, en %."""
    tgt = _safe_float(ticker.get("price_target_mean"))
    cur = _safe_float(ticker.get("current_price"))
    if tgt is None or cur is None or cur <= 0:
        return None
    return (tgt / cur - 1.0) * 100.0


def _reco_bucket_key(mean: float | None) -> str | None:
    if mean is None:
        return None
    for label, upper in _RECO_BUCKETS:
        if mean < upper:
            return label
    return "sell"


def _winsorize(
    values: dict[str, float | None],
    *,
    lower_pct: float = 1.0,
    upper_pct: float = 99.0,
) -> dict[str, float | None]:
    """Borne les valeurs extrêmes aux quantiles p1/p99 de la distribution.

    Pourquoi : un outlier (MAS ROE=71x, CLX D/E=9000) reçoit le percentile
    rank max et déforme l'interprétation. Winsoriser avant rank :
      • borne l'outlier à la valeur du p99 (rank reste max mais la valeur
        brute exposée dans le payload est crédible).
      • protège les calculs en aval (moyennes pondérées, distance à la médiane).

    Implémentation :
      • None / NaN / inf → préservés tel quel (pas pris en compte dans la borne).
      • < 4 valeurs valides → no-op (pas de calcul de quantile fiable).
      • Borne inclusive : v < p1 → p1, v > p99 → p99.
      • Symétrique : marche pour higher_is_better ET lower_is_better.

    Returns : nouveau dict, ne mute pas l'input.
    """
    return _winsorize_values(values, lower_pct=lower_pct, upper_pct=upper_pct)


def _winsorize_values(
    values: dict[str, float | None],
    *,
    lower_pct: float = 1.0,
    upper_pct: float = 99.0,
) -> dict[str, float | None]:
    """Implémentation cœur du winsorize — factorisée pour permettre la version
    sectorielle (`_winsorize_by_sector`)."""
    present = sorted([
        v for v in values.values()
        if v is not None and math.isfinite(v)
    ])
    n = len(present)
    if n < 4:
        return dict(values)

    def _quantile(p: float) -> float:
        # Linear interpolation entre les rangs (style numpy, sans la dépendance).
        idx = (p / 100.0) * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return present[lo] * (1 - frac) + present[hi] * frac

    p_lo = _quantile(lower_pct)
    p_hi = _quantile(upper_pct)
    # Garde-fou : si la distribution est dégénérée (toutes valeurs égales),
    # p_lo == p_hi → no-op effectif.
    if p_lo == p_hi:
        return dict(values)

    out: dict[str, float | None] = {}
    for k, v in values.items():
        if v is None or not math.isfinite(v):
            out[k] = v
        elif v < p_lo:
            out[k] = p_lo
        elif v > p_hi:
            out[k] = p_hi
        else:
            out[k] = v
    return out


def _winsorize_by_sector(
    values: dict[str, float | None],
    sectors: dict[str, str],
    *,
    lower_pct: float = 1.0,
    upper_pct: float = 99.0,
    min_sector_size: int = 8,
) -> dict[str, float | None]:
    """Winsorize INTRA-SECTEUR. Les bornes p1/p99 sont calculées groupe par
    groupe ; fallback global si un secteur a < `min_sector_size` valides.

    Pourquoi : la p99 globale est dominée par les secteurs majoritaires
    (Tech 88/480). Les outliers Utilities/REITs pour leurs métriques propres
    (D/E élevé par nature, ROE bas par nature) ne sortent donc quasi jamais
    des bornes globales → bruit conservé dans le ranking sectoriel.
    """
    by_sector: dict[str, dict[str, float | None]] = {}
    for ticker, v in values.items():
        sec = sectors.get(ticker) or "Unknown"
        by_sector.setdefault(sec, {})[ticker] = v

    # Calcule bornes global comme fallback pour secteurs trop petits.
    global_borders = _winsorize_values(values, lower_pct=lower_pct, upper_pct=upper_pct)

    out: dict[str, float | None] = {}
    for sec, sub in by_sector.items():
        n_valid = sum(
            1 for v in sub.values()
            if v is not None and math.isfinite(v)
        )
        if n_valid < min_sector_size:
            # Secteur dégénéré → prend le résultat global pour ces tickers.
            for t in sub.keys():
                out[t] = global_borders.get(t)
            continue
        out.update(_winsorize_values(sub, lower_pct=lower_pct, upper_pct=upper_pct))
    return out


def _minmax_norm(values: dict[str, float | None]) -> dict[str, float]:
    """Normalise min-max sur la série cross-sector. Valeurs None → 0.0.
    Si min == max (dégénéré), renvoie 0.5 pour chaque item non-null.
    """
    present = {k: v for k, v in values.items() if v is not None and math.isfinite(v)}
    if not present:
        return {k: 0.0 for k in values}
    lo, hi = min(present.values()), max(present.values())
    span = hi - lo
    out: dict[str, float] = {}
    for k in values:
        v = values.get(k)
        if v is None or not math.isfinite(v):
            out[k] = 0.0
        elif span <= 0:
            out[k] = 0.5
        else:
            out[k] = (v - lo) / span
    return out
