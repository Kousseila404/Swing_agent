"""Vol-targeting post-sector-cap.

Raison d'être : le risk parity (`_risk_parity.compute_weights`) égalise les
contributions marginales au risque mais ne borne PAS la vol totale du portefeuille.
En régime HIGH-VOL (VIX 30+), toutes les σ individuelles grimpent ⇒ la vol portefeuille
grimpe proportionnellement ⇒ drawdowns qui écrasent le DrawdownCircuitBreaker.

Formule (approximation ex-ante, matrice de corrélation = identité) :
    σ_p² ≈ Σ_i (w_i × σ_i)²
    leverage = target_vol / σ_p, clippé dans [leverage_min, leverage_max]
    w_i *= leverage

On n'utilise PAS la vraie matrice de covariance :
  • pas d'historique daily dispo pour tous les tickers en temps réel
  • l'hypothèse conservatrice σ_p = Σ w_i σ_i (all-corr-1) serait sous-optimale
  • l'identité (corr=0) sous-estime donc on ajoute un facteur de stress empirique

Default target = 14 % annualisé — fourchette long-term passive (S&P 500 ≈ 15-18 %).
Clippage : leverage ∈ [0.5, 1.0]. Jamais > 1.0 par default (pas d'emprunt
sur marge pour de l'allocation quant retail).
"""
from __future__ import annotations

import math
from typing import Any

from modules.log import logger

from ._utils import _safe_float

# Cible ~14 % σ annualisée. Abaisser vers 10 % pour profil plus conservateur,
# monter à 18 % si broker autorise un peu plus de volatilité.
DEFAULT_TARGET_VOL_PCT = 14.0

# Facteur de sécurité face à l'approximation corr=0. On suppose corr moyenne ≈ 0.4
# sur un portefeuille large-cap US ⇒ σ_p réelle ≈ 0.4 × Σw×σ (majorant) + 0.6 × √Σ(wσ)².
# On prend le majorant le plus prudent — évite de leverage-up aveuglément.
_CORR_SAFETY_FACTOR = 0.7

# Bornes de leverage — par default, jamais > 1.0 (long-only, pas de margin).
LEVERAGE_MIN = 0.5
LEVERAGE_MAX = 1.0


def estimate_portfolio_vol(
    weights: dict[str, float],
    scored: dict[str, dict[str, Any]],
) -> float | None:
    """Approxime σ_p (annualisée %) via σ_p² ≈ Σ (w_i × σ_i)² avec facteur de sécurité.

    Retourne None si aucune vol individuelle n'est disponible.
    """
    contributions: list[float] = []
    sum_wsigma = 0.0
    for t, w in weights.items():
        if w <= 0:
            continue
        row = scored.get(t) or {}
        sigma = _safe_float(row.get("momentum_volatility_pct"))
        if sigma is None or sigma <= 0:
            continue
        contributions.append((w * sigma) ** 2)
        sum_wsigma += w * sigma

    if not contributions:
        return None

    # σ_p (zero-correlation) = √Σ(w×σ)² ; σ_p (full-correlation) = Σ w×σ.
    # On pondère les deux via _CORR_SAFETY_FACTOR :
    #   factor=1.0 → majorant total (corr=1 partout, très prudent)
    #   factor=0.0 → minorant (indépendance totale, optimiste)
    # Default 0.7 = 70 % du chemin vers le majorant = corr moyenne ≈ 0.5.
    sigma_zero_corr = math.sqrt(sum(contributions))
    return _CORR_SAFETY_FACTOR * sum_wsigma + (1 - _CORR_SAFETY_FACTOR) * sigma_zero_corr


def apply_vol_target(
    weights: dict[str, float],
    scored: dict[str, dict[str, Any]],
    *,
    target_vol_pct: float = DEFAULT_TARGET_VOL_PCT,
    leverage_min: float = LEVERAGE_MIN,
    leverage_max: float = LEVERAGE_MAX,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Scale les weights par un leverage global = target / σ_p, clippé.

    Retourne (nouveaux_weights, diagnostics). Si σ_p indisponible, no-op.

    Side effect attendu : en régime calme (VIX 15), leverage peut monter à 1.0
    pour atteindre target. En régime stressé (σ_p > target), leverage descend
    vers leverage_min — le résidu part en cash_remaining.
    """
    sigma_p = estimate_portfolio_vol(weights, scored)
    if sigma_p is None or sigma_p <= 0:
        return weights, {
            "applied":       False,
            "reason":        "no_vol_data",
            "target_vol_pct": target_vol_pct,
        }

    raw_leverage = target_vol_pct / sigma_p
    leverage = max(leverage_min, min(leverage_max, raw_leverage))
    if abs(leverage - 1.0) < 0.01:
        # No-op effectif
        return weights, {
            "applied":          False,
            "reason":           "portfolio_already_on_target",
            "portfolio_vol_pct": round(sigma_p, 2),
            "raw_leverage":     round(raw_leverage, 3),
            "leverage":         1.0,
            "target_vol_pct":   target_vol_pct,
        }

    scaled = {t: w * leverage for t, w in weights.items()}
    if leverage < 1.0:
        logger.info(
            f"[VolTarget] σ_p={sigma_p:.1f}% > target={target_vol_pct:.1f}% → "
            f"leverage={leverage:.2f} (cash_residual={1-leverage:.0%})"
        )
    else:
        logger.info(
            f"[VolTarget] σ_p={sigma_p:.1f}% < target={target_vol_pct:.1f}% → "
            f"leverage={leverage:.2f} (plafonné à {leverage_max:.2f})"
        )
    return scaled, {
        "applied":          True,
        "portfolio_vol_pct": round(sigma_p, 2),
        "raw_leverage":     round(raw_leverage, 3),
        "leverage":         round(leverage, 3),
        "target_vol_pct":   target_vol_pct,
        "cash_residual_pct": round(max(0.0, 1.0 - leverage) * 100.0, 2),
    }
