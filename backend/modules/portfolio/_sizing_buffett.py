"""Sizing asymétrique Buffett-style — refonte 2026-04-29 (étape 3).

Modifie les poids du portefeuille APRÈS risk parity (1/σ) et AVANT sector_cap
en appliquant un tilt fonction des fondamentaux propres au name :

  • Compounder rare (Q≥85 ET P≥8)        → ×1.5  (Buffett concentre sur Coke/Apple)
  • High quality   (Q≥75 ET P≥7)          → ×1.2
  • Baseline       (sinon)                 → ×1.0
  • Junior risqué  (Q<50 OU P<4)           → ×0.7
  • Junk           (Q<35 ET P<3)           → ×0.5

Logique : un name « wonderful » mérite une concentration au-delà du risk-parity
σ-only. Risk parity équilibre la **volatilité** ; ce tilt équilibre la **qualité
du business**. Les deux sont complémentaires, pas redondants.

Renormalisation : après le tilt, on remet sum(weights) = 1.0. Les caps per-name
et sector qui suivent dans le pipeline absorbent les excès — ce qui est sain
(le tilt propose, les caps disposent).

Le module est purement déterministe et borne le multiplicateur dans [0.5, 1.5]
pour éviter une explosion sur les valeurs aberrantes des scores.
"""
from __future__ import annotations

import math
from typing import Any

# ─── Bornes (calibrées à partir des seuils fundamentals_levels) ──
_TILT_COMPOUNDER  = 1.5   # Q≥85 + P≥8       — concentrate
_TILT_HIGH_QUAL   = 1.2   # Q≥75 + P≥7
_TILT_BASELINE    = 1.0
_TILT_JUNIOR      = 0.7   # Q<50 OU P<4
_TILT_JUNK        = 0.5   # Q<35 ET P<3      — déconcentrer

_Q_COMPOUNDER     = 85.0
_Q_HIGH_QUAL      = 75.0
_Q_JUNIOR         = 50.0
_Q_JUNK           = 35.0

_P_COMPOUNDER     = 8
_P_HIGH_QUAL      = 7
_P_JUNIOR         = 4
_P_JUNK           = 3


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


def _tilt_factor(quality: float | None, f_score: float | None) -> tuple[float, str]:
    """Multiplicateur appliqué au poids risk-parity du ticker.

    Returns:
        (factor, label) — label expose la catégorie pour les diags portfolio.
    """
    quality = _safe_float(quality)
    f_score = _safe_float(f_score)

    # Si on n'a aucune fondamentale, on ne se prononce pas → baseline.
    if quality is None and f_score is None:
        return _TILT_BASELINE, "baseline_no_data"

    q = quality if quality is not None else 0.0
    p = f_score if f_score is not None else 0.0

    # Hiérarchie descendante — on test du plus exigeant au moins exigeant.
    if quality is not None and f_score is not None:
        if q >= _Q_COMPOUNDER and p >= _P_COMPOUNDER:
            return _TILT_COMPOUNDER, "compounder"
        if q >= _Q_HIGH_QUAL and p >= _P_HIGH_QUAL:
            return _TILT_HIGH_QUAL, "high_quality"
        if q < _Q_JUNK and p < _P_JUNK:
            return _TILT_JUNK, "junk"
        if q < _Q_JUNIOR or p < _P_JUNIOR:
            return _TILT_JUNIOR, "junior"

    # Cas dégradés : un seul score connu.
    if quality is not None and f_score is None:
        if q >= _Q_COMPOUNDER:
            return _TILT_HIGH_QUAL, "high_quality_partial"
        if q < _Q_JUNIOR:
            return _TILT_JUNIOR, "junior_partial"
    if f_score is not None and quality is None:
        if p >= _P_COMPOUNDER:
            return _TILT_HIGH_QUAL, "high_quality_partial"
        if p < _P_JUNIOR:
            return _TILT_JUNIOR, "junior_partial"

    return _TILT_BASELINE, "baseline"


def apply_buffett_tilt(
    weights: dict[str, float],
    scored: dict[str, dict[str, Any]],
) -> tuple[dict[str, float], dict[str, Any]]:
    """Applique le tilt Buffett aux poids et renormalise.

    Args:
        weights: dict ticker → weight (somme ≈ 1.0 attendue, mais on
            renormalise quoi qu'il en soit en sortie).
        scored: dict ticker → row scoré avec au minimum quality_score
            et f_score.

    Returns:
        (new_weights, diagnostics) :
          new_weights: poids tiltés et renormalisés.
          diagnostics: {applied, n_compounders, n_high_quality, n_junior,
                        n_junk, by_ticker}

    Refonte 2026-04-29 (Phase 1 data hardening) : le tilt est ATTÉNUÉ par
    le confidence_score du ticker. Un compounder ×1.5 sur des data peu
    fiables (confidence=45) ne vaut que ×1.5×0.7=1.05 — on ne concentre
    pas sur de la donnée pourrie. Buffett-pure : margin of safety dans
    les inputs, pas seulement dans le prix.
    """
    if not weights:
        return weights, {"applied": False, "reason": "empty"}

    # Import tardif pour éviter cycle (data_confidence ne doit pas dépendre
    # de portfolio).
    from modules.data_confidence import compute_confidence, confidence_modifier

    by_ticker: dict[str, dict[str, Any]] = {}
    counts = {
        "compounder": 0, "high_quality": 0, "baseline": 0,
        "junior": 0, "junk": 0, "high_quality_partial": 0,
        "junior_partial": 0, "baseline_no_data": 0,
    }
    tilted: dict[str, float] = {}
    total_weight_before = sum(weights.values()) or 1.0
    n_low_confidence = 0

    for t, w in weights.items():
        row = scored.get(t) or {}
        q = row.get("quality_score")
        p = row.get("f_score")
        raw_factor, label = _tilt_factor(q, p)

        # Atténuation par confidence — un tilt 1.5 sur un junior à confidence
        # 45 = 1.5 × 0.7 = 1.05 (presque baseline). Inversement, un tilt 0.7
        # sur un junior bien sourcé reste 0.7 (on déconcentre quand même).
        # Règle : on n'atténue QUE l'amplification (factor > 1), pas la
        # déconcentration (factor < 1) — un junk reste junk même si data
        # propre.
        conf = compute_confidence(row)
        conf_mod = confidence_modifier(conf["score"])
        if raw_factor > 1.0:
            # Compounder/high_quality avec data douteuse : on rapproche de 1.0
            adjusted_factor = 1.0 + (raw_factor - 1.0) * conf_mod
        else:
            adjusted_factor = raw_factor
        if conf["score"] < 60:
            n_low_confidence += 1

        # Étape suivante (2026-04-29 — phase 1 plus) — insider signal Buffett-pure.
        # `insider_score` (0-100) provient de SEC EDGAR Form 4 : net buys 30/90j,
        # cluster buying, distinct insiders buying. Buffett : *« Insider buys
        # are the cleanest signal. »* On bonifie le tilt si insider_score ≥ 70
        # (cluster buys), on pénalise si ≤ 25 (sell pressure persistente).
        ins = row.get("insider_score")
        try:
            ins = float(ins) if ins is not None else None
        except (TypeError, ValueError):
            ins = None
        insider_kicker = 0.0
        if ins is not None:
            if ins >= 70:
                insider_kicker = +0.10
            elif ins <= 25:
                insider_kicker = -0.15
        adjusted_factor = max(0.4, adjusted_factor + insider_kicker)

        tilted[t] = w * adjusted_factor
        counts[label] = counts.get(label, 0) + 1
        by_ticker[t] = {
            "weight_before":     round(w, 6),
            "factor_raw":        round(raw_factor, 3),
            "factor_applied":    round(adjusted_factor, 3),
            "confidence":        conf["score"],
            "confidence_tier":   conf["tier"],
            "insider_score":     ins,
            "insider_kicker":    round(insider_kicker, 3),
            "label":             label,
            "quality":           q,
            "f_score":           p,
        }

    # Renormalisation à la même somme que les poids d'entrée (généralement 1.0).
    total_after = sum(tilted.values())
    if total_after > 0:
        scale = total_weight_before / total_after
        tilted = {t: v * scale for t, v in tilted.items()}

    # Mettre à jour weight_after dans diagnostics.
    for t, v in tilted.items():
        by_ticker[t]["weight_after"] = round(v, 6)

    return tilted, {
        "applied":            True,
        "n_compounders":      counts.get("compounder", 0),
        "n_high_quality":     counts.get("high_quality", 0)
                              + counts.get("high_quality_partial", 0),
        "n_baseline":         counts.get("baseline", 0)
                              + counts.get("baseline_no_data", 0),
        "n_junior":           counts.get("junior", 0)
                              + counts.get("junior_partial", 0),
        "n_junk":             counts.get("junk", 0),
        "n_low_confidence":   n_low_confidence,
        "by_ticker":          by_ticker,
    }
