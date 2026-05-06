"""SL/TP Buffett-anchored — niveaux dérivés des fondamentaux propres au name.

Refonte 2026-04-29 (étape 1/3 « Buffett-pure »). Remplace le SL/TP technique
σ-scaled par des multiplicateurs sur les piliers TITAN qu'on calcule déjà :

  • Quality (ROE, gross_margin, ROA)        — *« great business »*
  • Piotroski F-Score (0-9)                  — santé du bilan
  • Value (EV/EBITDA, FwdPE, FCF yield)      — marge de sécurité
  • PEG ratio                                — prix vs croissance

Logique :
  base_sl_pct = 30 %                       (catastrophe minimum, sera ajusté)
  base_tp_pct = 100 %                      (objectif minimum, sera ajusté)

  quality_factor   = 0.7 + 0.6 × (Q/100)   → 0.7 (mediocre) à 1.3 (wonderful)
  piotroski_factor = 0.85 + 0.03 × P       → 0.85 (P-0) à 1.12 (P-9)
  value_factor     = 0.6 + 0.8 × (V/100)   → 0.6 (cher) à 1.4 (cheap)
  peg_factor       = 1.0 si peg < 1.5      → 0.7 si peg > 3.0 (overpriced growth)

Asymétrie Buffett-pure :
  SL_pct = base × quality_factor × piotroski_factor
    → un Q90/P9 a SL 30 % × 1.24 × 1.12 ≈ 41 % (large corde, on tient le compounder)
    → un Q40/P3 a SL 30 % × 0.94 × 0.94 ≈ 26 % (corde courte, junior risqué)

  TP_pct = base × value_factor × peg_factor × quality_factor
    → un Q90/V70/PEG 1.0 a TP 100 % × 1.16 × 1.0 × 1.24 ≈ 144 %
    → un Q60/V30/PEG 3.5 a TP 100 % × 0.84 × 0.7 × 1.06 ≈ 62 %

Flag « let_it_ride » : si Quality ≥ 85 ET Piotroski ≥ 8 ET Value ≥ 60, le TP
n'est plus un nombre — c'est un compounder rare type Coke/Apple, on tient.
La UI affiche 🏔️ ride.

Bornes de sanité (broker — Alpaca refuse < 1 % bracket) :
  SL : [-15 %, -45 %]
  TP : [+25 %, +120 %]

Le SL/TP broker (Alpaca bracket) reste sur catastrophe_floor (cf. _trade_levels.py).
Ce module n'expose que des niveaux *informatifs* exposés dans /api/lt_decision et
la UI Portfolio. La vraie sortie passe par lt_exit_policy.decide().
"""
from __future__ import annotations

import math
from typing import Any

# ─── Bases (ajustables) ────────────────────────────────────────
_BASE_SL_PCT = 0.30
_BASE_TP_PCT = 1.00

# Bornes de sanité — calibrées pour rester lisibles à l'humain et compatibles
# Alpaca bracket. Au-delà, on bascule sur let_it_ride pour le TP.
_MIN_SL_PCT = 0.15
_MAX_SL_PCT = 0.45
_MIN_TP_PCT = 0.25
_MAX_TP_PCT = 1.20

# Seuils let_it_ride — un name avec ces 3 critères est un compounder rare,
# on n'attache pas de TP fixe, on tient (Buffett-style).
_LET_IT_RIDE_QUALITY    = 85.0
_LET_IT_RIDE_PIOTROSKI  = 8.0
_LET_IT_RIDE_VALUE      = 60.0


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


def _quality_factor(quality_score: float | None) -> tuple[float, str]:
    """Q-90 → 1.24 (wonderful) ; Q-50 → 1.0 ; Q-10 → 0.76 (mediocre)."""
    if quality_score is None:
        return 1.0, "Q-score N/A → ×1.00"
    f = 0.7 + 0.6 * (quality_score / 100.0)
    return f, f"Q-score {quality_score:.0f}/100 → ×{f:.2f}"


def _piotroski_factor(piotroski: float | None) -> tuple[float, str]:
    """F-9 → 1.12 ; F-5 → 1.0 ; F-0 → 0.85."""
    if piotroski is None:
        return 1.0, "Piotroski N/A → ×1.00"
    f = 0.85 + 0.03 * piotroski
    return f, f"Piotroski {int(piotroski)}/9 → ×{f:.2f}"


def _value_factor(value_score: float | None) -> tuple[float, str]:
    """V-70 → 1.16 (cheap, marge de sécu) ; V-50 → 1.0 ; V-20 → 0.76."""
    if value_score is None:
        return 1.0, "V-score N/A → ×1.00"
    f = 0.6 + 0.8 * (value_score / 100.0)
    return f, f"V-score {value_score:.0f}/100 → ×{f:.2f}"


def _peg_factor(peg_ratio: float | None) -> tuple[float, str]:
    """PEG sain (< 1.5) → 1.0 ; PEG cher (1.5-3) → 0.85 ; extrême (> 3) → 0.7."""
    if peg_ratio is None:
        return 1.0, "PEG N/A → ×1.00"
    if peg_ratio <= 0:
        # PEG négatif (earnings négatifs) — pas d'info exploitable.
        return 1.0, f"PEG {peg_ratio:.2f} (négatif) → ×1.00"
    if peg_ratio < 1.5:
        return 1.0, f"PEG {peg_ratio:.2f} (sain) → ×1.00"
    if peg_ratio < 3.0:
        return 0.85, f"PEG {peg_ratio:.2f} (cher) → ×0.85"
    return 0.7, f"PEG {peg_ratio:.2f} (extrême) → ×0.70"


def compute_trailing_fundamental_sl(
    *,
    entry_price: float,
    entry_quality: float | None,
    entry_piotroski: float | None,
    current_quality: float | None,
    current_piotroski: float | None,
) -> dict[str, Any]:
    """Trailing SL fondamental — refonte 2026-04-29 (étape suivante Buffett).

    Le SL fondamental se RESSERRE quand la qualité du business s'érode.

    Logique :
      Au moment de l'achat, le SL Buffett est calculé via les facteurs Q/P
      à l'entrée. Si Q passe de 90 → 75 (sans casser la thèse au point de
      déclencher EXIT_THESIS), le compounder devient un baseline — la corde
      qu'on lui donne ne devrait plus être aussi large. Le SL trailing
      monte donc.

    Formule :
      delta_quality = (current_q + current_p×10) - (entry_q + entry_p×10)
      Si delta < 0 (érosion), on resserre le SL proportionnellement.

      sl_pct_trailing = sl_pct_entry × max(0.6, 1 + delta/200)
      → érosion -20 pts → ×0.9 (SL passe de -40 % à -36 %)
      → érosion -50 pts → ×0.75 (SL passe de -40 % à -30 %)
      → plancher 0.6 (on ne peut pas resserrer indéfiniment ;
                      au-delà, c'est EXIT_THESIS).

    Le SL ne s'élargit JAMAIS — si la qualité s'améliore, on garde le SL
    initial. Cohérent avec l'idée Buffett : le compounder a obtenu sa
    corde large, on ne lui en donne pas plus parce qu'il s'améliore.

    Returns:
        {sl_pct_trailing, sl_trailing_price, tightened, tightening_pts}
        ou {sl_pct_trailing: None, ...} si data insuffisante.
    """
    if entry_price <= 0 or not math.isfinite(entry_price):
        return {"sl_pct_trailing": None, "sl_trailing_price": None,
                "tightened": False, "tightening_pts": 0}

    eq = _safe_float(entry_quality)
    ep = _safe_float(entry_piotroski)
    cq = _safe_float(current_quality)
    cp = _safe_float(current_piotroski)

    if eq is None or cq is None:
        # Sans baseline d'entrée ou sans état courant, on n'a pas de delta.
        return {"sl_pct_trailing": None, "sl_trailing_price": None,
                "tightened": False, "tightening_pts": 0}

    # On calcule le SL d'origine et celui que produirait le state actuel.
    entry_levels = compute_fundamental_levels(
        entry_price, quality_score=eq, piotroski_score=ep,
    )
    sl_pct_entry = entry_levels.get("sl_pct")
    if sl_pct_entry is None:
        return {"sl_pct_trailing": None, "sl_trailing_price": None,
                "tightened": False, "tightening_pts": 0}

    delta = (cq + (cp or 0) * 10) - (eq + (ep or 0) * 10)
    if delta >= 0:
        # Pas d'érosion → on garde le SL d'entrée tel quel.
        return {
            "sl_pct_trailing":   sl_pct_entry,
            "sl_trailing_price": round(entry_price * (1 - sl_pct_entry / 100), 4),
            "tightened":         False,
            "tightening_pts":    0,
        }

    # Érosion → resserrer.
    factor = max(0.6, 1 + delta / 200)
    sl_pct_trailing = sl_pct_entry * factor
    return {
        "sl_pct_trailing":   round(sl_pct_trailing, 2),
        "sl_trailing_price": round(entry_price * (1 - sl_pct_trailing / 100), 4),
        "tightened":         True,
        "tightening_pts":    round(sl_pct_entry - sl_pct_trailing, 2),
    }


def compute_fundamental_levels(
    price: float | None,
    *,
    quality_score: float | None = None,
    piotroski_score: float | None = None,
    value_score: float | None = None,
    peg_ratio: float | None = None,
    direction: str = "LONG",
) -> dict[str, Any]:
    """Niveaux SL/TP fundamentals-anchored Buffett-style.

    Tous les inputs fundamentals sont optionnels — chaque manquant utilise
    un facteur neutre 1.0. Si tous manquent, on retombe sur les bases
    (-30 %, +100 %) avec un flag method="fundamentals_unavailable".

    Returns:
        {
          sl, tp, sl_pct, tp_pct,
          method:        "fundamentals_anchored" | "fundamentals_unavailable" | "invalid_price",
          let_it_ride:   bool — si True, tp est juste indicatif (le compounder se tient),
          breakdown:     [str] — chaîne traçable des multiplicateurs appliqués,
        }
    """
    if price is None or not math.isfinite(price) or price <= 0:
        return {
            "sl": None, "tp": None, "sl_pct": None, "tp_pct": None,
            "method": "invalid_price", "let_it_ride": False, "breakdown": [],
        }

    direction = (direction or "LONG").upper().strip()
    if direction not in ("LONG", "SHORT"):
        return {
            "sl": None, "tp": None, "sl_pct": None, "tp_pct": None,
            "method": "unsupported_direction", "let_it_ride": False, "breakdown": [],
        }

    quality_score   = _safe_float(quality_score)
    piotroski_score = _safe_float(piotroski_score)
    value_score     = _safe_float(value_score)
    peg_ratio       = _safe_float(peg_ratio)

    # Si vraiment aucune fondamentale exploitable → on ne ment pas.
    has_any = any(x is not None for x in
                  (quality_score, piotroski_score, value_score, peg_ratio))
    if not has_any:
        sl_pct = _BASE_SL_PCT
        tp_pct = _BASE_TP_PCT
        method = "fundamentals_unavailable"
        breakdown = ["Aucune fondamentale exploitable → bases 30% / 100%"]
        let_it_ride = False
    else:
        q_f, q_s = _quality_factor(quality_score)
        p_f, p_s = _piotroski_factor(piotroski_score)
        v_f, v_s = _value_factor(value_score)
        peg_f, peg_s = _peg_factor(peg_ratio)

        # SL : asymétrie qualité (compounder = corde large, junior = corde courte)
        sl_pct = _BASE_SL_PCT * q_f * p_f
        # TP : value-driven, ajusté qualité (un wonderful business à fair price
        # mérite un TP plus haut qu'un cheap junk).
        tp_pct = _BASE_TP_PCT * v_f * peg_f * q_f

        breakdown = [
            f"SL base {_BASE_SL_PCT*100:.0f}% {q_s} {p_s} → {sl_pct*100:.1f}%",
            f"TP base {_BASE_TP_PCT*100:.0f}% {v_s} {peg_s} {q_s} → {tp_pct*100:.1f}%",
        ]
        method = "fundamentals_anchored"

        # Detection compounder rare (Buffett: Coke 30 ans, See's, Apple).
        let_it_ride = (
            quality_score   is not None and quality_score   >= _LET_IT_RIDE_QUALITY
            and piotroski_score is not None and piotroski_score >= _LET_IT_RIDE_PIOTROSKI
            and value_score     is not None and value_score     >= _LET_IT_RIDE_VALUE
        )
        if let_it_ride:
            breakdown.append(
                "🏔️ let_it_ride : Q≥85 + P≥8 + V≥60 → compounder rare, "
                "TP indicatif uniquement"
            )

    # Clamps de sanité (broker)
    sl_pct = max(_MIN_SL_PCT, min(_MAX_SL_PCT, sl_pct))
    tp_pct = max(_MIN_TP_PCT, min(_MAX_TP_PCT, tp_pct))

    if direction == "LONG":
        sl = round(price * (1.0 - sl_pct), 4)
        tp = round(price * (1.0 + tp_pct), 4)
    else:
        sl = round(price * (1.0 + sl_pct), 4)
        tp = round(price * (1.0 - tp_pct), 4)

    return {
        "sl":          sl,
        "tp":          tp,
        "sl_pct":      round(sl_pct * 100.0, 2),
        "tp_pct":      round(tp_pct * 100.0, 2),
        "method":      method,
        "let_it_ride": let_it_ride,
        "breakdown":   breakdown,
    }
