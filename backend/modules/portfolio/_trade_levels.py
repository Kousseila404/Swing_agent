"""Suggestion SL/TP σ-adaptive pour l'endpoint /recommendations.

On dérive les niveaux à partir de la volatilité annualisée déjà calculée par
le pipeline momentum (`volatility_pct`) — aucun I/O supplémentaire. La vol
quotidienne = vol_annual / √252, rescalée sur un horizon Long-Term (~30 jours
ouvrés) via √30 — convention sizing pour une position Quantamental tenue
1-3 mois (audit 2026-04-23 : ancien horizon 5j incohérent avec MAX_HOLDING_DAYS
et avec la thèse "convergence fondamentale").

Formules :
  horizon_vol_pct = volatility_pct × √(holding_days / 252)
  SL_pct = k_sl × horizon_vol_pct   (par défaut k_sl = 2 → ~95 % CI)
  TP_pct = k_tp × horizon_vol_pct   (par défaut k_tp = 4 → RR 2:1)

Fallback quand vol absente ou price ≤ 0 : −5 % / +15 % (fixed % legacy).
"""
from __future__ import annotations

import math

# Long-Term horizon ~30 jours ouvrés = ~6 semaines ouvrées (1.5 mois civils).
# Aligné avec MAX_HOLDING_DAYS=60 dans config.py : le TP est sizé sur le point
# milieu de la fenêtre de détention, pas sur sa fin (qui verrait trop de bruit).
_HOLDING_HORIZON_DAYS = 30
_TRADING_DAYS_PER_YEAR = 252

# Multiplicateurs σ — k_sl = 2 (~95 % CI normale), k_tp = 4 → RR cible 2:1.
_K_SL = 2.0
_K_TP = 4.0

# Fallback % fixe si vol indisponible (FMP stable sans history, ou série < 20 j).
_FALLBACK_SL_PCT = 0.05   # −5 %
_FALLBACK_TP_PCT = 0.15   # +15 %

# Bornes de sanité — calibrées pour horizon LT (30 j ouvrés). Évite des SL/TP
# aberrants sur un outlier de vol (ex: penny stock σ annualisée 300 % → SL
# à −100 % = absurde) tout en laissant respirer un hold 1-3 mois.
# Recalibrage 2026-04-27 : SL max élargi à −30 %, TP max +100 %.
# Recalibrage 2026-04-29 (P3 transition) : TP max élargi à +200 % pour ne plus
# plafonner les vrais winners power-law (un name TITAN 90 sur 1-3 mois peut
# délivrer +150 %). Filet conservé contre les outliers de vol (penny σ 300 %
# → tp brut +412 % serait absurde). Pas de retrait complet du plafond tant que
# thesis_stop n'a pas 20+ trades clos pour valider la sortie fondamentale.
# Le ratio R/R cible reste 2:1 sur la zone non-clampée (k_tp/k_sl = 4/2).
_MIN_SL_PCT = 0.04   # −4 % (stop minimum — un hold 2 mois mérite +3 % de corde)
_MAX_SL_PCT = 0.30   # −30 % (garde-fou catastrophe LT — pas anti-bruit)
_MIN_TP_PCT = 0.10   # +10 % (pas de TP ridicule sur un LT)
_MAX_TP_PCT = 2.00   # +200 % (filet anti-outlier vol, mais ne plafonne plus les vrais winners)


def suggest_trade_levels(
    price: float | None,
    volatility_pct: float | None,
    *,
    direction: str = "LONG",
    holding_days: int = _HOLDING_HORIZON_DAYS,
) -> dict[str, float | str | None]:
    """Retourne {sl, tp, sl_pct, tp_pct, method} pour un LONG.

    - `price` : prix d'entrée (cours live ou scoré).
    - `volatility_pct` : σ annualisée en % (issu du pipeline momentum).
    - `direction` : "LONG" uniquement pour l'instant (SHORT = miroir,
      non déployé tant que l'UI ne le propose pas).

    Retourne None pour sl/tp si `price` invalide ; sinon 4 décimales.
    `method` trace l'origine ("sigma_scaled" | "fixed_fallback") pour audit.
    """
    if price is None or not math.isfinite(price) or price <= 0:
        return {"sl": None, "tp": None, "sl_pct": None, "tp_pct": None, "method": "invalid_price"}
    direction = (direction or "LONG").upper().strip()
    if direction not in ("LONG", "SHORT"):
        return {"sl": None, "tp": None, "sl_pct": None, "tp_pct": None, "method": "unsupported_direction"}

    vol_usable = (
        volatility_pct is not None
        and math.isfinite(volatility_pct)
        and volatility_pct > 0
    )

    if vol_usable:
        # Vol sur horizon swing = vol_annuelle × √(horizon / 252)
        horizon_vol_frac = (volatility_pct / 100.0) * math.sqrt(
            holding_days / _TRADING_DAYS_PER_YEAR
        )
        sl_pct = _K_SL * horizon_vol_frac
        tp_pct = _K_TP * horizon_vol_frac
        # Clamp aux bornes de sanité
        sl_pct = max(_MIN_SL_PCT, min(_MAX_SL_PCT, sl_pct))
        tp_pct = max(_MIN_TP_PCT, min(_MAX_TP_PCT, tp_pct))
        method = "sigma_scaled"
    else:
        sl_pct = _FALLBACK_SL_PCT
        tp_pct = _FALLBACK_TP_PCT
        method = "fixed_fallback"

    if direction == "LONG":
        sl = round(price * (1.0 - sl_pct), 4)
        tp = round(price * (1.0 + tp_pct), 4)
        # Sanity stricte : SL < price, TP > price.
        if sl >= price or sl <= 0:
            sl = round(price * (1.0 - _FALLBACK_SL_PCT), 4)
        if tp <= price:
            tp = round(price * (1.0 + _FALLBACK_TP_PCT), 4)
    else:
        # SHORT : SL > price (vers le haut, pertes), TP < price (vers le bas, gains).
        sl = round(price * (1.0 + sl_pct), 4)
        tp = round(price * (1.0 - tp_pct), 4)
        if sl <= price:
            sl = round(price * (1.0 + _FALLBACK_SL_PCT), 4)
        if tp >= price or tp <= 0:
            tp = round(price * (1.0 - _FALLBACK_TP_PCT), 4)

    return {
        "sl":      sl,
        "tp":      tp,
        "sl_pct":  round(sl_pct * 100.0, 2),
        "tp_pct":  round(tp_pct * 100.0, 2),
        "method":  method,
    }
