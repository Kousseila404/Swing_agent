"""Module — auto-approbation des propositions les plus sûres.

Applique la règle d'achat manuelle déjà documentée et validée par
l'utilisateur (memory `user_buy_rule_manual` — précédent INCY validé) :

    TITAN >= 80 (nominal)
    OU
    TITAN in [70, 80) ET Support ON très net (score >= 90) ET Piotroski F-score >= 7

Les propositions qui qualifient sont approuvées via le même endpoint que
l'UI (POST /api/proposals/approve_batch) — donc soumises exactement aux
mêmes gates (over-cap secteur, idempotence, réalignement prix live) que
si l'utilisateur cliquait "Approuver" à la main. Aucun override de secteur
n'est jamais forcé automatiquement, et un ticker déjà détenu (top-up)
n'est jamais auto-approuvé — ces deux cas restent un choix humain.

Budget : capé par run ET par semaine glissante (compté via l'audit log
existant, événement "executed" + decided_by="auto_approve_bot") pour
éviter un déploiement de capital trop rapide.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from modules import api_core, proposals
from modules.log import logger

TITAN_MIN_NOMINAL      = 80.0
TITAN_MIN_OVERRIDE     = 70.0
SUPPORT_MIN_OVERRIDE   = 90.0
PIOTROSKI_MIN_OVERRIDE = 7

MAX_AUTO_APPROVE_PER_RUN  = 2
MAX_AUTO_APPROVE_PER_WEEK = 5

DECIDED_BY = "auto_approve_bot"

_API_BASE = "http://127.0.0.1:8000"


def _qualifies(ctx: dict[str, Any]) -> tuple[bool, str]:
    """Applique la règle d'achat manuelle. Retourne (qualifie, raison)."""
    titan = ctx.get("titan_score")
    if titan is None:
        return False, "titan_score manquant"

    if titan >= TITAN_MIN_NOMINAL:
        return True, f"TITAN {titan:.1f} ≥ {TITAN_MIN_NOMINAL:.0f} (nominal)"

    support = ctx.get("support") or {}
    f_score = ctx.get("f_score")
    if (
        TITAN_MIN_OVERRIDE <= titan < TITAN_MIN_NOMINAL
        and support.get("level") == "ON_SUPPORT"
        and (support.get("score") or 0) >= SUPPORT_MIN_OVERRIDE
        and f_score is not None
        and f_score >= PIOTROSKI_MIN_OVERRIDE
    ):
        return True, (
            f"TITAN {titan:.1f} ∈ [70,80) + Support ON "
            f"({support.get('score')}) + Piotroski {f_score}/9 (override)"
        )

    return False, "hors règle (ni TITAN≥80, ni override 70-80+support+piotroski)"


def _recent_auto_approve_count(days: float = 7.0) -> int:
    """Compte les auto-approvals des N derniers jours via l'audit log existant."""
    path = proposals.PROPOSALS_AUDIT_PATH
    if not path.exists():
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=days)
    count = 0
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("event") != "executed" or rec.get("decided_by") != DECIDED_BY:
                    continue
                ts_raw = str(rec.get("ts") or "")
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts >= cutoff:
                    count += 1
    except Exception as exc:
        logger.warning(f"[AutoApprove] Lecture audit log échouée : {exc}")
        return 0
    return count


def run_auto_approve() -> dict[str, Any]:
    """Point d'entrée cron. Retourne un résumé (pour logs/tests)."""
    pending = proposals.list_all(status="pending")

    budget_left = MAX_AUTO_APPROVE_PER_WEEK - _recent_auto_approve_count()
    if budget_left <= 0:
        logger.info(
            f"[AutoApprove] Budget hebdomadaire épuisé "
            f"({MAX_AUTO_APPROVE_PER_WEEK}/semaine) — rien à faire"
        )
        return {"qualified": 0, "approved": 0, "skipped_budget": True}

    candidates: list[tuple[dict[str, Any], str]] = []
    for p in pending:
        ctx = p.get("context") or {}
        if bool(ctx.get("already_held")):
            continue
        if bool((ctx.get("sector_exposure") or {}).get("over_cap")):
            continue
        ok, reason = _qualifies(ctx)
        if ok:
            candidates.append((p, reason))

    n_take = min(len(candidates), MAX_AUTO_APPROVE_PER_RUN, budget_left)
    to_approve = candidates[:n_take]

    if not to_approve:
        logger.info(
            f"[AutoApprove] {len(pending)} pending, {len(candidates)} qualifient "
            "la règle — aucune capacité/budget disponible ce cycle" if candidates
            else f"[AutoApprove] {len(pending)} pending, aucune ne qualifie la règle"
        )
        return {"qualified": len(candidates), "approved": 0}

    token = api_core.API_TOKEN
    approved: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    try:
        with httpx.Client(base_url=_API_BASE, timeout=30.0) as client:
            for prop, reason in to_approve:
                resp = client.post(
                    "/api/proposals/approve_batch",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "items": [{"id": prop["id"]}],
                        "decided_by": DECIDED_BY,
                    },
                )
                resp.raise_for_status()
                result = (resp.json() or {}).get("results", [{}])[0]
                ticker = str(prop.get("ticker") or "")
                if result.get("ok"):
                    approved.append({"ticker": ticker, "id": prop["id"], "reason": reason})
                    logger.info(f"[AutoApprove] {ticker} approuvé automatiquement — {reason}")
                    _notify_telegram(ticker, reason, prop)
                else:
                    failed.append({"ticker": ticker, "id": prop["id"], "message": result.get("message")})
                    logger.warning(
                        f"[AutoApprove] {ticker} qualifiait mais approve_batch a refusé : "
                        f"{result.get('message')}"
                    )
    except Exception as exc:
        logger.error(f"[AutoApprove] Appel approve_batch échoué : {exc}", exc_info=True)

    return {
        "qualified": len(candidates),
        "approved": len(approved),
        "failed": len(failed),
        "details": approved,
    }


def _notify_telegram(ticker: str, reason: str, prop: dict[str, Any]) -> None:
    try:
        from modules.alerter import _send_telegram_message
        ctx = prop.get("context") or {}
        msg = (
            f"\U0001f916 <b>Auto-approuvé</b> — {ticker}\n"
            f"{reason}\n"
            f"Entry {prop.get('entry')} | SL {prop.get('stop_loss')} | "
            f"TP {prop.get('take_profit')} | Taille {prop.get('size')}\n"
            f"Poids portefeuille visé : {ctx.get('weight_pct', 0):.1f}%"
        )
        _send_telegram_message(msg)
    except Exception as exc:
        logger.warning(f"[AutoApprove] Notification Telegram échouée pour {ticker} : {exc}")
