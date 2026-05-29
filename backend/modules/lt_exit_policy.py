"""Politique d'exit Long-Term — refonte 2026-04-29 (philosophie Buffett).

Source de vérité unique pour la décision « que fait-on de cette position
ouverte aujourd'hui ? ». Remplace la logique implicite SL/TP statique +
trailing stop par une décision fondamentale + filet catastrophe.

Quatre couches dans l'ordre de priorité :

  1. EXIT_CATASTROPHE (Stop Loss touché)
     « Risk comes from not knowing what you're doing. » Le SL est à −35 %
     environ — il n'est pas censé être touché en LT sain. S'il l'est, on
     suppose un événement black-swan (fraude révélée, bankruptcy, guerre)
     et on coupe sans débat.

  2. EXIT_THESIS (thèse fondamentale cassée)
     « Sell when the thesis is wrong. » Le module thesis_stop évalue la
     dérive TITAN, F-Score, Quality, Momentum, revisions, tilts négatifs
     vs l'entrée — relatif au secteur quand possible. BROKEN = la raison
     d'être de la position a disparu, on coupe (manuel après lecture).

  3. EXIT_VALUATION (survalorisation extrême)
     « Be fearful when others are greedy. » Le pilier Value chute
     fortement vs l'entrée (le marché a re-pricé fortement à la hausse),
     ou le PEG explose. On coupe pour réallouer vers un name plus en
     marge de sécurité — ce n'est pas un TP arbitraire à +50 %, c'est
     un TP fondamental quand le ratio prix/valeur intrinsèque devient
     déraisonnable.

  4. ADD_ON (« Be greedy when others are fearful »)
     Si la thèse est INTACT et que le prix a corrigé sans raison
     fondamentale (drawdown depuis entrée > −12 % avec Support ON),
     suggérer un renforcement plutôt qu'un exit. Buffett achète quand
     les prix baissent, à condition que la thèse tienne.

  TRIM (entre WARN et BROKEN)
     Si la thèse est WARN (érosion partielle, 1-2 axes), suggérer un
     allègement plutôt qu'un exit complet — la conviction est entamée
     mais pas annulée.

  HOLD (default)
     Rien à faire. Buffett : « Our favorite holding period is forever ».

Le module est purement informatif côté tracker — il NE force PAS l'exit
(aucune fermeture automatique de position). Les décisions Telegram et UI
exposent la recommandation + raisons, l'utilisateur tranche.

Le SL technique (catastrophe_floor) reste actif côté broker (Alpaca exige
un bracket order avec SL/TP) et déclenche la branche EXIT_CATASTROPHE
si touché — c'est la dernière ligne de défense quand la thèse n'a pas
encore eu le temps de signaler.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# ─────────────────────────────────────────────────────────────────
# CONSTANTES — calibration LT (à reviser après 20+ trades clos)
# ─────────────────────────────────────────────────────────────────

# Seuil drawdown (depuis entrée) pour suggérer un ADD_ON quand thèse INTACT.
# Buffett-style averaging down : on renforce sur correction sans cassure.
# −12 % est une correction normale (1 σ sur 30j à vol annuelle ~50 %),
# pas une panique — pertinent pour une vraie opportunité de renfort.
ADD_ON_DRAWDOWN_THRESHOLD = -12.0

# Drawdown maximum pour ADD_ON. Au-delà de −25 % depuis entrée, même si
# thèse INTACT, on ne renforce plus (signal du marché trop hostile, on
# attend la stabilisation plutôt que de doubler le losing trade).
ADD_ON_DRAWDOWN_FLOOR = -25.0

# Drawdown depuis entrée déclenchant EXIT_CATASTROPHE même si SL pas
# touché (clamp gap overnight + conviction perdue). Aligné sur _MAX_SL_PCT.
CATASTROPHE_DRAWDOWN_THRESHOLD = -35.0

# Survalorisation : drift Value pillar (entry → now) déclenchant l'exit.
# Le pilier Value est sector-relative, 0-100. Une chute de 35+ pts veut
# dire que le name est passé de cheap à expensive vs ses pairs en quelques
# semaines — typiquement parce que le prix s'est apprécié plus vite que
# les fondamentaux n'ont suivi. Buffett vendrait.
VALUATION_DRIFT_EXIT = -35.0

# PEG ratio absolu. Au-delà de 3.5, le prix anticipe une croissance
# extrême : Buffett refuse ce profil.
PEG_RATIO_EXIT = 3.5

# Forward P/E absolu. Sanity check pour les names sans peg fiable
# (peg = NaN si croissance attendue ≈ 0). Au-delà de 60, pricing
# extrême même pour un growth name.
FORWARD_PE_EXIT = 60.0

# Gain depuis entrée minimum pour qu'EXIT_VALUATION soit activable.
# Sans ce gate, on pourrait sortir sur une « survalorisation » alors
# que le ticker est en perte (typiquement si le marché chute partout
# et que le pilier Value se contracte parce que tous les peers sont
# devenus cheap). On veut couper sur un gain réalisé, pas une mauvaise
# rotation sectorielle.
VALUATION_EXIT_MIN_GAIN_PCT = 30.0

# Refonte 2026-04-29 (étape 2 Buffett) — overshoot vs fair value.
# Si le prix courant dépasse le TP Buffett-anchored × 1.20, c'est un
# overshoot net : le marché paie 20 % au-dessus de notre estimation
# de fair value haute, gain ou pas. Buffett vendait Washington Post
# en 1999 à 4× son fair value estimé.
# NB : `let_it_ride` (Coke/Apple type) supprime ce check tant que la
# thèse reste INTACT — Buffett a tenu Coke pendant des décennies de
# « valuation expensive ».
FAIR_VALUE_OVERSHOOT_RATIO = 1.20


# ─────────────────────────────────────────────────────────────────
# DATACLASS — résultat de la décision
# ─────────────────────────────────────────────────────────────────
@dataclass
class LTDecision:
    """Recommandation d'action sur une position LT ouverte.

    Le code décisionnel est dans l'attribut `action` ; les raisons et
    métriques sont exposées pour traçabilité et UI.
    """

    ticker:           str
    action:           str                        # HOLD | ADD_ON | TRIM | EXIT_THESIS | EXIT_VALUATION | EXIT_CATASTROPHE | NO_DATA
    severity:         int                        # 0 (HOLD) ↑ 4 (CATASTROPHE)
    reasons:          list[str]                  = field(default_factory=list)
    pct_gain:         float | None               = None
    drawdown_from_entry_pct: float | None        = None
    thesis_status:    str | None                 = None  # INTACT | WARN | BROKEN | NO_DATA
    valuation_drift:  float | None               = None  # Value_now - Value_entry
    peg_ratio:        float | None               = None
    catastrophe_hit:  bool                       = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker":          self.ticker,
            "action":          self.action,
            "severity":        self.severity,
            "reasons":         list(self.reasons),
            "pct_gain":        self.pct_gain,
            "drawdown_from_entry_pct": self.drawdown_from_entry_pct,
            "thesis_status":   self.thesis_status,
            "valuation_drift": self.valuation_drift,
            "peg_ratio":       self.peg_ratio,
            "catastrophe_hit": self.catastrophe_hit,
        }


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────
# Sévérité ordinale des catégories Buffett pour détecter un downgrade.
# Plus le score est haut, plus le name est « wonderful ».
_CATEGORY_SEVERITY = {
    "compounder":          5,
    "high_quality":        4,
    "high_quality_partial": 3,
    "baseline":            2,
    "baseline_no_data":    2,
    "junior":              1,
    "junior_partial":      1,
    "junk":                0,
}


def _category_severity_drop(entry_cat: str | None, current_cat: str | None) -> int:
    """Différence ordinale entry → current. Positif = downgrade."""
    if entry_cat is None or current_cat is None:
        return 0
    e = _CATEGORY_SEVERITY.get(entry_cat, 2)
    c = _CATEGORY_SEVERITY.get(current_cat, 2)
    return e - c


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


# ─────────────────────────────────────────────────────────────────
# DÉCISION
# ─────────────────────────────────────────────────────────────────
def decide(
    *,
    ticker: str,
    entry_price: float,
    current_price: float | None,
    stop_loss: float | None,
    direction: str = "LONG",
    thesis: dict[str, Any] | None = None,
    entry_value_pillar: float | None = None,
    current_value_pillar: float | None = None,
    current_peg_ratio: float | None = None,
    current_forward_pe: float | None = None,
    support_level: str | None = None,        # "ON_SUPPORT" | "NEAR_SUPPORT" | "OFF_SUPPORT"
    buffett_tp: float | None = None,          # fair-value ceiling (fundamentals_levels)
    let_it_ride: bool = False,                # compounder rare → pas d'EXIT_VALUATION
    entry_category: str | None = None,        # buffett category au moment de l'achat
    current_category: str | None = None,      # buffett category courante
    confidence_score: int | None = None,      # 0-100 (data_confidence courant)
    entry_confidence: int | None = None,      # 0-100 capturé à l'entrée
    insider_score: float | None = None,       # 0-100 (SEC EDGAR Form 4)
) -> LTDecision:
    """Agrège les 4 couches en une recommandation LT unique.

    Args:
        ticker:              Symbol (informational, propagated to result).
        entry_price:         Prix d'entrée.
        current_price:       Prix courant (None → NO_DATA).
        stop_loss:           SL technique du journal (catastrophe floor).
        direction:           "LONG" (par défaut). SHORT non supporté côté policy.
        thesis:              Output de `thesis_stop.compute_thesis_status` ou None.
        entry_value_pillar:  Value_Entry du journal (capturé à l'achat).
        current_value_pillar: value_score actuel du scored_universe.
        current_peg_ratio:   peg_ratio actuel (peut être None).
        current_forward_pe:  forward_pe actuel (peut être None).
        support_level:       Output de support_score (gate pour ADD_ON).

    Returns:
        LTDecision avec action et reasons triés par priorité.
    """
    # ── Sanity inputs ─────────────────────────────────────────────
    if current_price is None or entry_price <= 0 or not math.isfinite(entry_price):
        return LTDecision(
            ticker=ticker, action="NO_DATA", severity=0,
            reasons=["Prix d'entrée ou courant indisponible"],
        )

    direction = (direction or "LONG").upper().strip()
    if direction != "LONG":
        # SHORT : non géré par lt_exit_policy v1. Renvoie HOLD pour
        # ne rien interférer ; le tracker continue son chemin SL/TP.
        return LTDecision(
            ticker=ticker, action="HOLD", severity=0,
            reasons=["SHORT non supporté par lt_exit_policy"],
        )

    pct_gain = (current_price - entry_price) / entry_price * 100.0
    drawdown = pct_gain  # pour LONG : pct_gain négatif = drawdown depuis entrée

    thesis_status = (thesis or {}).get("status") if thesis else None

    # Cast défensif — entry_*_pillar peut arriver en str depuis le CSV journal,
    # current_*_pillar en float depuis scored_universe. On normalise pour
    # éviter une TypeError sur la soustraction.
    entry_value_pillar = _safe_float(entry_value_pillar)
    current_value_pillar = _safe_float(current_value_pillar)
    current_peg_ratio = _safe_float(current_peg_ratio)
    current_forward_pe = _safe_float(current_forward_pe)

    valuation_drift = None
    if entry_value_pillar is not None and current_value_pillar is not None:
        valuation_drift = round(current_value_pillar - entry_value_pillar, 2)

    catastrophe_hit = False
    if stop_loss is not None and math.isfinite(stop_loss) and stop_loss > 0:
        # Floor touché si current ≤ SL (LONG)
        if current_price <= stop_loss:
            catastrophe_hit = True

    # ── 1. EXIT_CATASTROPHE (priorité 1) ──────────────────────────
    # Soit le SL est physiquement touché, soit le drawdown dépasse −35 %
    # (cas d'un SL absent / mal renseigné — on garde une dernière digue).
    if catastrophe_hit or drawdown <= CATASTROPHE_DRAWDOWN_THRESHOLD:
        reasons: list[str] = []
        if catastrophe_hit and stop_loss is not None:
            reasons.append(
                f"Catastrophe floor touché : prix {current_price:.2f} ≤ SL {stop_loss:.2f}"
                f" ({drawdown:+.1f}% depuis entrée)"
            )
        else:
            reasons.append(
                f"Drawdown extrême sans SL physique : {drawdown:+.1f}%"
                f" (seuil {CATASTROPHE_DRAWDOWN_THRESHOLD:.0f}%)"
            )
        return LTDecision(
            ticker=ticker, action="EXIT_CATASTROPHE", severity=4,
            reasons=reasons, pct_gain=round(pct_gain, 2),
            drawdown_from_entry_pct=round(drawdown, 2),
            thesis_status=thesis_status, valuation_drift=valuation_drift,
            peg_ratio=current_peg_ratio, catastrophe_hit=catastrophe_hit,
        )

    # ── 2. EXIT_THESIS (priorité 2) ───────────────────────────────
    # La thèse fondamentale est cassée → on coupe quel que soit le P&L.
    # Buffett vend Tesco quand le bilan se détériore, pas parce que le
    # prix bouge.
    #
    # Refonte 2026-04-29 (étape 4) : on capture aussi la dérive de
    # catégorie Buffett (compounder → junior/junk). Une chute de 2 paliers
    # ou plus = dégradation fondamentale majeure même si thesis_stop ne l'a
    # pas (encore) flaggé en BROKEN — on ajoute le signal aux reasons mais
    # on ne déclenche EXIT_THESIS QUE si thesis_stop est lui aussi BROKEN
    # ou si la catégorie passe à junk.
    cat_severity_drop = _category_severity_drop(entry_category, current_category)
    category_break = (
        current_category in ("junk",)
        and entry_category in ("compounder", "high_quality", "high_quality_partial")
    )

    if thesis_status == "BROKEN" or category_break:
        reasons = list((thesis or {}).get("reasons_break") or [])
        if category_break:
            reasons.append(
                f"Catégorie Buffett dégradée : {entry_category} → {current_category}"
            )
        if not reasons:
            reasons.append("Thèse fondamentale cassée (détails indisponibles)")
        return LTDecision(
            ticker=ticker, action="EXIT_THESIS", severity=3,
            reasons=reasons, pct_gain=round(pct_gain, 2),
            drawdown_from_entry_pct=round(drawdown, 2),
            thesis_status=thesis_status, valuation_drift=valuation_drift,
            peg_ratio=current_peg_ratio, catastrophe_hit=False,
        )

    # ── 3. EXIT_VALUATION (priorité 3) ────────────────────────────
    # Survalorisation extrême : on ne coupe que si on est en gain notable
    # (sinon on serait en train de matérialiser une perte sur une
    # « rotation Value » qui n'a rien à voir avec notre thèse propre).
    #
    # Refonte 2026-04-29 étape 2 : on ajoute un check **prix vs fair value
    # fundamentals-anchored** (buffett_tp). Si le prix courant dépasse
    # buffett_tp × 1.20, c'est un overshoot net face à notre estimation de
    # valeur intrinsèque haute — Buffett vendait dans ces zones. Le flag
    # `let_it_ride` (compounder rare type Coke/Apple) supprime ce check tant
    # que la thèse reste INTACT.
    # Phase 1 data hardening : EXIT_VALUATION inhibé si confidence < 50.
    # Le signal de survalorisation pourrait venir d'une donnée cassée
    # (PEG aberrant, value_score outlier sectoriel, fundamentals stales).
    # On préfère HOLD qu'une sortie sur fausse alerte. Buffett : « When in
    # doubt about the data, do nothing. »
    val_reasons: list[str] = []
    confidence_blocks_valuation = (
        confidence_score is not None and confidence_score < 50
    )
    if (pct_gain >= VALUATION_EXIT_MIN_GAIN_PCT
            and not (let_it_ride and thesis_status == "INTACT")
            and not confidence_blocks_valuation):
        if valuation_drift is not None and valuation_drift <= VALUATION_DRIFT_EXIT:
            val_reasons.append(
                f"Pilier Value {entry_value_pillar:.0f} → {current_value_pillar:.0f}"
                f" ({valuation_drift:+.0f} pts) — re-pricing extrême"
            )
        if current_peg_ratio is not None and current_peg_ratio >= PEG_RATIO_EXIT:
            val_reasons.append(
                f"PEG {current_peg_ratio:.2f} ≥ {PEG_RATIO_EXIT:.1f}"
                f" — croissance future déjà dans le prix"
            )
        if (current_peg_ratio is None
                and current_forward_pe is not None
                and current_forward_pe >= FORWARD_PE_EXIT):
            # Fallback PEG indisponible : Forward P/E absolu
            val_reasons.append(
                f"Forward P/E {current_forward_pe:.1f} ≥ {FORWARD_PE_EXIT:.0f}"
                f" — pricing extrême"
            )
        # Overshoot vs fair value Buffett-anchored.
        if buffett_tp is not None and buffett_tp > 0:
            overshoot_threshold = buffett_tp * FAIR_VALUE_OVERSHOOT_RATIO
            if current_price >= overshoot_threshold:
                pct_above = (current_price / buffett_tp - 1.0) * 100
                val_reasons.append(
                    f"Prix ${current_price:.2f} ≥ fair value ceiling ×{FAIR_VALUE_OVERSHOOT_RATIO:.2f}"
                    f" (Buffett-TP ${buffett_tp:.2f}, +{pct_above:.0f}% au-dessus)"
                )
    if val_reasons:
        # On ajoute le contexte gain pour rendre lisible la recommandation.
        val_reasons.insert(0, f"Position en gain {pct_gain:+.1f}% — réallouer vers une marge de sécurité plus large")
        return LTDecision(
            ticker=ticker, action="EXIT_VALUATION", severity=3,
            reasons=val_reasons, pct_gain=round(pct_gain, 2),
            drawdown_from_entry_pct=round(drawdown, 2),
            thesis_status=thesis_status, valuation_drift=valuation_drift,
            peg_ratio=current_peg_ratio, catastrophe_hit=False,
        )

    # ── 4. TRIM (érosion partielle de thèse) ──────────────────────
    # Inclut le signal de dérive catégorie (≥ 2 paliers descendants sans
    # atteindre junk — sinon EXIT_THESIS plus haut). Ex : compounder →
    # baseline = drop 3, on alerte sans couper.
    cat_warn = cat_severity_drop >= 2 and not category_break

    # Phase 1 data hardening — chute de confiance ≥ 30 pts entre entrée et
    # état courant = dégradation des sources data sur ce name (delisting,
    # fournisseur en panne, fraud disclosure). Signal Buffett : *« If the
    # data goes dark, the thesis can't be trusted anymore. »*
    # Delta précalculé (None si une borne manque) pour que mypy narrow le calcul
    # ici plutôt que dans le message plus bas (où le garde booléen ne narrow pas).
    confidence_delta = (
        entry_confidence - confidence_score
        if entry_confidence is not None and confidence_score is not None
        else None
    )
    confidence_drop = confidence_delta is not None and confidence_delta >= 30
    # Insider net sells persistent — signal Buffett-pure : les dirigeants ont
    # de l'information avant les chiffres. insider_score ≤ 20 = pression
    # vendeuse soutenue → TRIM.
    insider_warn = insider_score is not None and insider_score <= 20

    if thesis_status == "WARN" or cat_warn or confidence_drop or insider_warn:
        reasons = list((thesis or {}).get("reasons_warn") or [])
        if cat_warn:
            reasons.append(
                f"Dérive catégorie Buffett : {entry_category} → {current_category}"
                f" (-{cat_severity_drop} paliers)"
            )
        if confidence_drop:
            reasons.append(
                f"Chute de confiance des données : {entry_confidence}→{confidence_score}"
                f" (-{confidence_delta} pts) — sources peut-être dégradées"
            )
        if insider_warn:
            reasons.append(
                f"Insider sells soutenus : score {insider_score:.0f}/100"
                f" — les dirigeants se désengagent"
            )
        if not reasons:
            reasons.append("Érosion partielle de thèse (signaux WARN)")
        return LTDecision(
            ticker=ticker, action="TRIM", severity=2,
            reasons=reasons, pct_gain=round(pct_gain, 2),
            drawdown_from_entry_pct=round(drawdown, 2),
            thesis_status=thesis_status, valuation_drift=valuation_drift,
            peg_ratio=current_peg_ratio, catastrophe_hit=False,
        )

    # ── 5. ADD_ON (« Be greedy when others are fearful ») ─────────
    # Thèse INTACT + correction modérée + Support ON/NEAR → renfort.
    if (thesis_status == "INTACT"
            and ADD_ON_DRAWDOWN_FLOOR <= drawdown <= ADD_ON_DRAWDOWN_THRESHOLD
            and (support_level in ("ON_SUPPORT", "NEAR_SUPPORT") or support_level is None)):
        reasons = [
            f"Thèse INTACT, drawdown {drawdown:+.1f}% sans cassure fondamentale",
        ]
        if support_level == "ON_SUPPORT":
            reasons.append("Support technique ON — point d'entrée propre")
        elif support_level == "NEAR_SUPPORT":
            reasons.append("Support technique NEAR — entrée graduée raisonnable")
        elif support_level is None:
            reasons.append("Support inconnu — renfort à pondérer prudemment")
        return LTDecision(
            ticker=ticker, action="ADD_ON", severity=1,
            reasons=reasons, pct_gain=round(pct_gain, 2),
            drawdown_from_entry_pct=round(drawdown, 2),
            thesis_status=thesis_status, valuation_drift=valuation_drift,
            peg_ratio=current_peg_ratio, catastrophe_hit=False,
        )

    # ── 6. HOLD (default Buffett) ─────────────────────────────────
    reasons = []
    if thesis_status == "INTACT":
        reasons.append("Thèse INTACT — let it ride")
    elif thesis_status == "NO_DATA":
        reasons.append("Données fondamentales partielles — hold par défaut")
    else:
        reasons.append("Aucun signal d'action — hold")
    return LTDecision(
        ticker=ticker, action="HOLD", severity=0,
        reasons=reasons, pct_gain=round(pct_gain, 2),
        drawdown_from_entry_pct=round(drawdown, 2),
        thesis_status=thesis_status, valuation_drift=valuation_drift,
        peg_ratio=current_peg_ratio, catastrophe_hit=False,
    )


def aggregate_portfolio(decisions: list[LTDecision]) -> dict[str, Any]:
    """Agrège les décisions individuelles en stats portfolio.

    Returns:
        {
          counts: {HOLD: n, ADD_ON: n, ...},
          actionable_count: int (severity ≥ 2),
          highest_severity: int,
          tickers_by_action: {action: [ticker, ...]},
        }
    """
    counts: dict[str, int] = {}
    by_action: dict[str, list[str]] = {}
    highest_severity = 0
    actionable = 0
    for d in decisions:
        counts[d.action] = counts.get(d.action, 0) + 1
        by_action.setdefault(d.action, []).append(d.ticker)
        if d.severity > highest_severity:
            highest_severity = d.severity
        if d.severity >= 2:
            actionable += 1
    return {
        "counts":            counts,
        "actionable_count":  actionable,
        "highest_severity":  highest_severity,
        "tickers_by_action": by_action,
    }
