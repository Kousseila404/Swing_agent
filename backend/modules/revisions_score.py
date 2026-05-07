"""Revisions pillar — score 0-100 capturant la dynamique des révisions analyste
et la qualité des earnings (PEAD signal).

Inspiration Seeking Alpha "Revisions Grade" — l'IC empirique de ce signal est
le plus élevé des 7 facteurs SA (~0.07 à horizon 1-3M). Sources :
  • upgrades_30d / downgrades_30d / upgrades_90d / downgrades_90d
  • revisions_net_score   ∈ [-1, 1] (= (up-down)/(up+down) sur 90j)
  • earnings_surprise_pct_last
  • earnings_surprise_avg_4q
  • earnings_beat_rate_8q ∈ [0, 1]

Composition du score (0-100) :
  0.40 × revision_intensity    (rank cross-universe sur revisions_net_score)
  0.20 × revision_volume       (rank cross-universe sur up_90 + down_90)
  0.25 × beat_rate             (rank cross-universe sur earnings_beat_rate_8q)
  0.15 × surprise_avg          (rank cross-universe sur earnings_surprise_avg_4q)

Composantes None → ignorées dans la moyenne ; toutes None → score neutre 50.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from typing import Any

_NEUTRAL = 50.0

# Sous-poids des composantes du pilier — somme = 1.
_W_INTENSITY = 0.40
_W_VOLUME    = 0.20
_W_BEAT      = 0.25
_W_SURPRISE  = 0.15


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


def _percentile_rank(values: dict[str, float | None], higher_is_better: bool = True) -> dict[str, float | None]:
    """Percentile-rank cross-universe (fractional ranking, ties = mid-rank).

    Implémentation : `bisect` sur la liste triée — O(n log n) total au lieu
    du O(n²) naïf (sum-comprehension sur tout le tableau pour chaque ticker).
    Aligné avec `sector_metrics._scoring._percentile_rank` (Phase 5 audit) :
    `bisect_left` compte les < strict, `bisect_right - bisect_left` compte
    les == — formule "fractional ranking" inchangée numériquement.
    """
    present = sorted(v for v in values.values() if v is not None and math.isfinite(v))
    n = len(present)
    if n < 2:
        return {k: (_NEUTRAL if (v is not None and math.isfinite(v)) else None)
                for k, v in values.items()}
    out: dict[str, float | None] = {}
    for k, v in values.items():
        if v is None or not math.isfinite(v):
            out[k] = None
            continue
        less = bisect_left(present, v)
        equal = bisect_right(present, v) - less
        pct = (less + 0.5 * equal) / n * 100.0
        out[k] = pct if higher_is_better else (100.0 - pct)
    return out


def compute_revisions_pillar(tickers_map: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Calcule le score Revisions 0-100 par ticker. Retourne {ticker: {revisions_score,
    revisions_components, revisions_data_quality}}.

    `tickers_map` doit contenir au minimum les fields :
      upgrades_90d, downgrades_90d, revisions_net_score,
      earnings_beat_rate_8q, earnings_surprise_avg_4q.
    """
    if not tickers_map:
        return {}

    keys = list(tickers_map.keys())

    intensity = {k: _safe(tickers_map[k].get("revisions_net_score")) for k in keys}
    volume_raw: dict[str, float | None] = {}
    for k in keys:
        up90 = _safe(tickers_map[k].get("upgrades_90d"))
        dn90 = _safe(tickers_map[k].get("downgrades_90d"))
        if up90 is None and dn90 is None:
            volume_raw[k] = None
        else:
            volume_raw[k] = (up90 or 0.0) + (dn90 or 0.0)
    beat = {k: _safe(tickers_map[k].get("earnings_beat_rate_8q")) for k in keys}
    surprise = {k: _safe(tickers_map[k].get("earnings_surprise_avg_4q")) for k in keys}

    intensity_r = _percentile_rank(intensity, higher_is_better=True)
    volume_r    = _percentile_rank(volume_raw, higher_is_better=True)
    beat_r      = _percentile_rank(beat, higher_is_better=True)
    surprise_r  = _percentile_rank(surprise, higher_is_better=True)

    out: dict[str, dict[str, Any]] = {}
    for k in keys:
        components = {
            "intensity": intensity_r[k],
            "volume":    volume_r[k],
            "beat_rate": beat_r[k],
            "surprise":  surprise_r[k],
        }
        weights = {
            "intensity": _W_INTENSITY,
            "volume":    _W_VOLUME,
            "beat_rate": _W_BEAT,
            "surprise":  _W_SURPRISE,
        }
        num = den = 0.0
        for name, val in components.items():
            if val is None:
                continue
            w = weights[name]
            num += w * val
            den += w
        if den > 0:
            score = num / den
        else:
            score = _NEUTRAL  # toutes composantes absentes → neutre

        # Data quality du pilier : fraction de composantes renseignées.
        n_present = sum(1 for v in components.values() if v is not None)
        dq = n_present / len(components)

        out[k] = {
            "revisions_score":          round(score, 2),
            "revisions_components":     {n: (round(v, 2) if v is not None else None)
                                          for n, v in components.items()},
            "revisions_data_quality":   round(dq, 3),
        }
    return out
