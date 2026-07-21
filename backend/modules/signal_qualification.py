"""Module — qualification du signal (Étape 1 roadmap produit).

Root cause traitée : `auto_proposer.plan_proposals` classe les propositions par
simple tri `titan_score` décroissant, sans notion de fraîcheur ni de
changement d'état — les mêmes tickers ressortent en tête d'un refresh à
l'autre, ce qui donne l'impression d'un signal figé. Ce module ajoute une
couche de LECTURE/SYNTHÈSE pure au-dessus de l'existant :

  • Delta de score TITAN + flip de verdict `buy_signal` vs N jours en arrière,
    en réutilisant `universe_history.ticker_history()` (zéro nouvelle infra).
  • Segmentation en 3 catégories de conviction (🔥 nouveau / ⭐ confirmé /
    👁 surveillance) à la place du tri plat.
  • Narratif une-phrase par ticker, synthèse pure des ingrédients déjà
    présents dans `context` (Piotroski, momentum, support, earnings, tilt).
  • Fraîcheur causale : quand le verdict a changé, croise earnings_surprise /
    insider (cluster buying + 8-K événement matériel) / revisions entre
    l'instantané de référence et aujourd'hui pour esquisser un "pourquoi".

Ne touche JAMAIS à la logique de scoring/sizing/exit — lecture seule sur
`universe_history` + `scored_universe`, aucune écriture, aucun impact sur
`compute_buy_signal`/`_sizing_buffett`/`lt_exit_policy`.

Limite connue (documentée, pas une supposition) : `buy_signal` n'est pas
historisé dans les snapshots `universe_history` (calculé live uniquement
dans `auto_proposer`/`ticker_analysis`). Le verdict "N jours en arrière" est
donc une RECONSTITUTION approximative — `compute_buy_signal` rejoué sur le
snapshot historique, sans le bloc `support` de l'époque (non historisé non
plus) ni `price_action` (jamais peuplé nulle part dans le pipeline actuel,
y compris en live — donc pas une régression introduite ici).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from modules.buy_signal import compute_buy_signal
from modules.log import logger

# "N jours en arrière" par défaut — aligné sur le cycle de rescoring budgété
# de l'univers (~100 tickers/jour, cycle complet ~5 jours ouvrés, cf.
# ROADMAP.md "Diagnostic"). En dessous de ce nombre de jours, un même ticker
# n'a souvent pas eu le temps d'être rescoré → comparer une fenêtre plus
# courte reviendrait à comparer un snapshot à lui-même la plupart du temps.
DEFAULT_LOOKBACK_DAYS = 5

# Fenêtre de tolérance pour trouver un snapshot de référence : les scans ne
# sont pas garantis quotidiens par ticker (rotation budgétée), on prend donc
# le snapshot le plus récent À OU AVANT `today - lookback_days`, dans cette
# fenêtre. Au-delà : pas d'historique exploitable, `None`.
_REFERENCE_WINDOW_DAYS = 15

CONVICTION_NEW = "new_signal"
CONVICTION_CONFIRMED = "confirmed"
CONVICTION_WATCH = "watch"
CONVICTION_OTHER = "other"

# Badge affiché — même mapping que `ProposalsPage.jsx` (CONVICTION_META),
# réutilisé côté notifications Telegram (digest + `_notify_new_proposals`,
# Étape 10) pour rester cohérent avec ce que l'utilisateur voit dans l'UI.
CONVICTION_BADGES = {
    CONVICTION_NEW: "\U0001f525 Nouveau",
    CONVICTION_CONFIRMED: "⭐ Confirmé",
    CONVICTION_WATCH: "\U0001f441 Surveillance",
}

_ACTIONABLE_VERDICTS = {"STRONG_BUY", "BUY"}

_ES_LEVEL_RANK = {
    "STRONG_MISS": 0, "MISS": 1, "INLINE": 2, "BEAT": 3, "STRONG_BEAT": 4,
}

_TILT_NARRATIVE_LABELS = {
    "qarp": "profil QARP (qualité à prix raisonnable)",
    "garp": "profil GARP (croissance à prix raisonnable)",
    "consistent": "régularité historique des fondamentaux",
}


def _find_reference_snapshot(
    ticker: str, lookback_days: int,
) -> tuple[date, dict[str, Any]] | None:
    """Snapshot le plus récent À OU AVANT `today - lookback_days`, dans la
    fenêtre `_REFERENCE_WINDOW_DAYS`. None si l'historique est insuffisant
    (ticker nouvellement ajouté à l'univers, gap de rotation, etc.).
    """
    from modules import universe_history

    today = date.today()
    target = today - timedelta(days=lookback_days)
    window_start = target - timedelta(days=_REFERENCE_WINDOW_DAYS)
    rows = universe_history.ticker_history(
        ticker, start=window_start, end=target,
    )
    if not rows:
        return None
    row = rows[-1]  # dernière ligne <= target (ticker_history trie croissant)
    try:
        d = date.fromisoformat(row["date"])
    except (KeyError, ValueError):
        return None
    return d, row


def _historical_verdict(row: dict[str, Any]) -> str | None:
    """Rejoue `compute_buy_signal` sur un snapshot historique (best-effort —
    cf. limites documentées en tête de module). Fail-open : None si le calcul
    échoue, jamais d'exception propagée vers l'appelant (chemin proposals).
    """
    try:
        return compute_buy_signal(row).verdict
    except Exception as e:
        logger.warning(f"[SignalQualification] historical verdict replay failed: {e}")
        return None


def compute_verdict_trend(
    ticker: str,
    *,
    current_verdict: str | None,
    current_score: float | None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Delta de score + flip de verdict vs `lookback_days` jours en arrière.

    Returns un dict toujours présent (jamais None) — `verdict_changed=None`
    signifie "historique insuffisant pour juger", pas "pas changé".
    """
    ref = _find_reference_snapshot(ticker, lookback_days)
    if ref is None:
        return {
            "lookback_days": lookback_days,
            "reference_date": None,
            "prior_verdict": None,
            "prior_score": None,
            "score_delta": None,
            "verdict_changed": None,
        }

    ref_date, ref_row = ref
    prior_verdict = _historical_verdict(ref_row)
    prior_score = ref_row.get("titan_composite_score")

    score_delta = None
    if isinstance(current_score, (int, float)) and isinstance(prior_score, (int, float)):
        score_delta = round(current_score - prior_score, 2)

    verdict_changed = None
    if prior_verdict is not None and current_verdict is not None:
        verdict_changed = prior_verdict != current_verdict

    return {
        "lookback_days": lookback_days,
        "reference_date": ref_date.isoformat(),
        "prior_verdict": prior_verdict,
        "prior_score": prior_score,
        "score_delta": score_delta,
        "verdict_changed": verdict_changed,
    }


def classify_conviction(verdict: str | None, verdict_changed: bool | None) -> str:
    """3 catégories de conviction (roadmap Étape 1) :
      🔥 new_signal : verdict actionnable ET vient de changer.
      ⭐ confirmed  : verdict actionnable, stable (ou historique insuffisant
                      pour prouver un changement — on ne prétend pas "nouveau"
                      sans preuve).
      👁 watch      : WATCH — proche du seuil, pas encore actionnable.
      other         : le reste (SKIP/CHEAP_JUNK/FALLING_KNIFE/EARNINGS_BLACKOUT/
                      NO_DATA) — pas de notion de conviction pour ces verdicts.
    """
    if verdict == "WATCH":
        return CONVICTION_WATCH
    if verdict in _ACTIONABLE_VERDICTS:
        return CONVICTION_NEW if verdict_changed else CONVICTION_CONFIRMED
    return CONVICTION_OTHER


def build_narrative(context: dict[str, Any]) -> str:
    """Synthèse en une phrase du "pourquoi" — pure formatage des ingrédients
    déjà présents dans `context` (Piotroski, momentum, support, earnings,
    tilt), aucun nouveau calcul de score.
    """
    buy = context.get("buy_signal") or {}
    label = buy.get("label") or buy.get("verdict") or "Signal"

    clauses: list[str] = []

    f_score = context.get("f_score")
    f_score_max = context.get("f_score_max") or 9
    if isinstance(f_score, (int, float)) and f_score >= 7:
        clauses.append(f"bilan Piotroski {int(f_score)}/{int(f_score_max)}")

    momentum = context.get("momentum_score")
    if isinstance(momentum, (int, float)):
        if momentum >= 70:
            clauses.append(f"momentum fort ({momentum:.0f}/100)")
        elif momentum < 40:
            clauses.append(f"momentum faible ({momentum:.0f}/100)")

    support = context.get("support") or {}
    support_level = support.get("level")
    if support_level == "ON_SUPPORT":
        clauses.append("entrée sur support technique")
    elif support_level == "NEAR_SUPPORT":
        clauses.append("proche d'un support technique")

    revisions = context.get("revisions_score")
    if isinstance(revisions, (int, float)) and revisions >= 65:
        clauses.append(f"révisions analystes positives ({revisions:.0f}/100)")

    days_until_earnings = context.get("days_until_earnings")
    if isinstance(days_until_earnings, (int, float)) and 0 <= days_until_earnings < 14:
        clauses.append(f"earnings dans {int(days_until_earnings)}j")

    for flag in context.get("titan_tilt_flags") or []:
        if flag in _TILT_NARRATIVE_LABELS:
            clauses.append(_TILT_NARRATIVE_LABELS[flag])

    if not clauses:
        return label

    clauses = clauses[:3]  # garde "une phrase", pas une liste exhaustive
    detail = clauses[0] if len(clauses) == 1 else (
        ", ".join(clauses[:-1]) + " et " + clauses[-1]
    )
    return f"{label} — {detail}."


def causal_reasons(
    prior_row: dict[str, Any] | None, current_row: dict[str, Any],
) -> list[str]:
    """Quand le verdict a changé, tente d'expliquer *pourquoi* en croisant
    earnings_surprise / insider (cluster buying + 8-K événement matériel +
    10-K/10-Q rapports périodiques) / revisions entre l'instantané de
    référence et aujourd'hui. [] si `prior_row` est absent ou si aucune
    cause précise n'est identifiable (le narratif générique reste alors la
    seule info).
    """
    if prior_row is None:
        return []
    reasons: list[str] = []

    try:
        from modules.earnings_surprise import compute_earnings_surprise_score
        prior_es = compute_earnings_surprise_score(prior_row)
        current_es = compute_earnings_surprise_score(current_row)
        p_rank = _ES_LEVEL_RANK.get(prior_es.level)
        c_rank = _ES_LEVEL_RANK.get(current_es.level)
        if p_rank is not None and c_rank is not None and c_rank > p_rank:
            reasons.append(f"Earnings récents meilleurs que prévu ({current_es.level})")
    except Exception as e:
        logger.warning(f"[SignalQualification] earnings_surprise cross-ref failed: {e}")

    prior_cluster = bool(prior_row.get("insider_cluster_buying"))
    current_cluster = bool(current_row.get("insider_cluster_buying"))
    if current_cluster and not prior_cluster:
        reasons.append("Nouveau cluster d'achats insiders détecté")

    prior_rev = prior_row.get("revisions_score")
    current_rev = current_row.get("revisions_score")
    if isinstance(prior_rev, (int, float)) and isinstance(current_rev, (int, float)):
        delta = current_rev - prior_rev
        if delta >= 10:
            reasons.append(f"Révisions analystes en hausse (+{delta:.0f} pts)")

    prior_8k = prior_row.get("insider_most_recent_8k")
    current_8k = current_row.get("insider_most_recent_8k")
    if current_8k and current_8k != prior_8k and (prior_8k is None or current_8k > prior_8k):
        reasons.append(f"Événement matériel déposé (8-K, {current_8k})")

    prior_10k = prior_row.get("insider_most_recent_10k")
    current_10k = current_row.get("insider_most_recent_10k")
    if current_10k and current_10k != prior_10k and (prior_10k is None or current_10k > prior_10k):
        reasons.append(f"Nouveau rapport annuel déposé (10-K, {current_10k})")

    prior_10q = prior_row.get("insider_most_recent_10q")
    current_10q = current_row.get("insider_most_recent_10q")
    if current_10q and current_10q != prior_10q and (prior_10q is None or current_10q > prior_10q):
        reasons.append(f"Nouveau rapport trimestriel déposé (10-Q, {current_10q})")

    return reasons


def qualify_proposal(
    ticker: str,
    *,
    scored_row: dict[str, Any],
    context_ingredients: dict[str, Any],
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Point d'entrée unique appelé par `auto_proposer` — assemble trend,
    segmentation, narratif et causal reasons pour un ticker.

    Args:
      scored_row: ligne scored_universe brute du ticker (pour le composite
        actuel + la comparaison historique côté earnings/insider/revisions).
      context_ingredients: sous-ensemble de `context` déjà construit par
        `auto_proposer` (buy_signal, f_score, momentum_score, support,
        revisions_score, days_until_earnings, titan_tilt_flags) — évite de
        recalculer quoi que ce soit, pure synthèse.
    """
    buy_signal = context_ingredients.get("buy_signal") or {}
    current_verdict = buy_signal.get("verdict")
    current_score = scored_row.get("titan_composite_score")

    trend = compute_verdict_trend(
        ticker,
        current_verdict=current_verdict,
        current_score=current_score,
        lookback_days=lookback_days,
    )
    conviction = classify_conviction(current_verdict, trend["verdict_changed"])
    narrative = build_narrative(context_ingredients)

    reasons: list[str] = []
    if trend["verdict_changed"]:
        ref = _find_reference_snapshot(ticker, lookback_days)
        prior_row = ref[1] if ref is not None else None
        reasons = causal_reasons(prior_row, scored_row)

    return {
        "conviction": conviction,
        "trend": trend,
        "narrative": narrative,
        "causal_reasons": reasons,
    }
