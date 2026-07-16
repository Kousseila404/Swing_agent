"""Niveaux SL/TP — refonte LT 2026-04-29 (philosophie Buffett).

Ce module ne pilote PLUS la décision d'exit. Il fournit deux niveaux purement
techniques destinés au broker (Alpaca bracket order exige un SL et un TP) :

  - SL = catastrophe_floor : filet large (~3.5σ) anti black-swan (fraude,
    bankruptcy, guerre). N'est PAS censé être touché en LT sain — la vraie
    sortie est `lt_exit_policy.decide` (couche 2/3 : thèse cassée ou
    survalorisation extrême). Buffett : « Risk comes from not knowing what
    you're doing » → la qualité d'entrée (TITAN ≥ 80 + Support + F-Score)
    EST le stop ; le floor est un dernier recours.

  - TP = ceiling_cap : plafond de sanité contre outliers de vol (penny
    σ 300 % → tp brut +1000 % absurde). Ne plafonne PAS les vrais winners
    power-law. Le « vrai » TP est désormais une décision fondamentale prise
    par lt_exit_policy quand le pilier Value chute fortement ou que la
    valuation devient extrême — pas un % arbitraire.

Formules :
  horizon_vol_pct = volatility_pct × √(holding_days / 252)
  SL_pct = K_SL × horizon_vol_pct   (K_SL = 3.5 → ~99.95 % CI normale)
  TP_pct = K_TP × horizon_vol_pct   (K_TP = 6   → ratio 1.7:1 en σ pur)

Fallback quand vol absente ou price ≤ 0 : −10 % / +25 % (fixed % large).
"""
from __future__ import annotations

import math

# Long-Term horizon ~30 jours ouvrés = ~6 semaines ouvrées (1.5 mois civils).
# Aligné avec MAX_HOLDING_DAYS=60 dans config.py : le TP est sizé sur le point
# milieu de la fenêtre de détention, pas sur sa fin (qui verrait trop de bruit).
_HOLDING_HORIZON_DAYS = 30
_TRADING_DAYS_PER_YEAR = 252

# Refonte 2026-04-29 — Multiplicateurs σ Buffett-LT.
# K_SL = 3.5 (~99.95 % CI normale) — on accepte un drawdown profond plutôt
# que de couper sur du bruit ; la vraie sortie est lt_exit_policy.
# K_TP = 6 — laisse courir les winners ; le plafond est un anti-outlier, pas
# un objectif. Le RR σ pur 1.7:1 est volontairement asymétrique : on n'a plus
# besoin d'un RR 2:1 quand l'exit fondamental remplace le TP statique.
_K_SL = 3.5
_K_TP = 6.0

# Fallback % fixe si vol indisponible. Élargi (5→10 / 15→25 %) pour cohérence
# avec la philosophie « catastrophe floor, pas anti-bruit ».
_FALLBACK_SL_PCT = 0.10   # −10 %
_FALLBACK_TP_PCT = 0.25   # +25 %

# Bornes de sanité — refonte 2026-04-29 (Buffett-LT).
# SL : floor catastrophe → 35 % (était 30). Wide-net anti black-swan, pas
# anti-correction ordinaire. La vraie sortie passe par lt_exit_policy.
# TP : plafond élargi à 500 % pour ne plus jamais plafonner un winner LT
# (Buffett : « Our favorite holding period is forever »). Le plafond n'existe
# que pour empêcher l'envoi d'un bracket order absurde côté Alpaca sur un
# ticker à σ 300 % annualisée. Le minimum SL/TP reste pour Alpaca (refus
# d'un TP < entry × 1.001).
_MIN_SL_PCT = 0.05   # −5 % (broker minimum — distance ≥ 0.5 % requise)
_MAX_SL_PCT = 0.35   # −35 % (catastrophe floor élargi)
_MIN_TP_PCT = 0.10   # +10 % (sanity broker)
_MAX_TP_PCT = 5.00   # +500 % (plafond Alpaca uniquement, pas un objectif)


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
    `method` trace l'origine ("catastrophe_floor" | "fixed_fallback") pour audit.
    """
    if price is None or not math.isfinite(price) or price <= 0:
        return {"sl": None, "tp": None, "sl_pct": None, "tp_pct": None, "method": "invalid_price"}
    direction = (direction or "LONG").upper().strip()
    if direction not in ("LONG", "SHORT"):
        return {"sl": None, "tp": None, "sl_pct": None, "tp_pct": None, "method": "unsupported_direction"}

    # Condition inline (pas de variable intermédiaire) pour que mypy narrow
    # `volatility_pct` à float dans la branche — sinon il reste float | None.
    if (
        volatility_pct is not None
        and math.isfinite(volatility_pct)
        and volatility_pct > 0
    ):
        # Vol sur horizon LT = vol_annuelle × √(horizon / 252)
        horizon_vol_frac = (volatility_pct / 100.0) * math.sqrt(
            holding_days / _TRADING_DAYS_PER_YEAR
        )
        sl_pct = _K_SL * horizon_vol_frac
        tp_pct = _K_TP * horizon_vol_frac
        # Clamp aux bornes de sanité (catastrophe floor + ceiling)
        sl_pct = max(_MIN_SL_PCT, min(_MAX_SL_PCT, sl_pct))
        tp_pct = max(_MIN_TP_PCT, min(_MAX_TP_PCT, tp_pct))
        method = "catastrophe_floor"  # ex sigma_scaled — exit primaire = lt_exit_policy
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
