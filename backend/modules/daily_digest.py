"""Module — digest Telegram quotidien actionnable.

Un seul message le matin plutôt que devoir ouvrir l'appli pour voir l'état
de la file de propositions : combien en attente, depuis combien de temps,
lesquelles regarder en premier — plus un rappel de l'état du portefeuille.

Étape 3 roadmap (proactivité réelle) : le digest ne liste plus le top-N par
`titan_score` (quasi figé d'un jour à l'autre vu le cycle de rescoring
budgété ~5j, cf. diagnostic roadmap) mais uniquement les propositions dont
`context.qualification.conviction == "new_signal"` (Étape 1 —
verdict actionnable ET qui vient de changer). Ajoute aussi un lien direct
vers l'onglet Propositions si `config.FRONTEND_URL` est configuré.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from html import escape as _esc
from typing import Any

import config
from modules import api_core, proposals
from modules.log import logger

_TOP_N = 5


def _fmt_age(created_at: str) -> str:
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return "?"
    days = (datetime.now(UTC) - dt).total_seconds() / 86400
    if days < 1:
        return f"{days * 24:.0f}h"
    return f"{days:.0f}j"


def _equity_snapshot() -> dict[str, Any]:
    path = api_core.BASE / "data" / "equity_state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[DailyDigest] Lecture equity_state.json échouée : {exc}")
        return {}


def _new_signals(pending: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Propositions en `conviction == "new_signal"` (Étape 1), triées par
    score TITAN décroissant. `qualification` est `None` en fail-open —
    ces propositions ne sont jamais comptées "nouvelles" sans preuve.
    """
    fresh = [
        p for p in pending
        if ((p.get("context") or {}).get("qualification") or {}).get("conviction")
        == "new_signal"
    ]
    return sorted(
        fresh,
        key=lambda p: (p.get("context") or {}).get("titan_score") or 0,
        reverse=True,
    )


def _proposals_link() -> str:
    if not config.FRONTEND_URL:
        return ""
    return f"{config.FRONTEND_URL}/#/proposals"


def build_digest_text() -> str:
    all_items = proposals.list_all()
    pending = [p for p in all_items if p.get("status") == "pending"]
    fresh = _new_signals(pending)[:_TOP_N]
    oldest = min(pending, key=lambda p: p.get("created_at") or "", default=None)
    recurring = proposals.recurring_undecided(all_items, min_streak=3)

    eq = _equity_snapshot()
    open_positions = eq.get("open_positions") or []

    lines = ["\U0001f4ca <b>Digest quotidien SwingQuant</b>", ""]

    if fresh:
        lines.append(f"\U0001f525 <b>{len(fresh)} nouveau(x) signal(aux)</b> :")
        for p in fresh:
            ctx = p.get("context") or {}
            verdict = (ctx.get("buy_signal") or {}).get("verdict", "?")
            narrative = ((ctx.get("qualification") or {}).get("narrative")
                         or p.get("ticker"))
            lines.append(
                f"  • <b>{_esc(str(p.get('ticker')))}</b> ({_esc(str(verdict))}) — {_esc(str(narrative))}"
            )
    else:
        lines.append("\U0001f441 Aucun nouveau signal depuis le dernier cycle.")

    if pending:
        oldest_age = _fmt_age(oldest["created_at"]) if oldest else "?"
        lines.append(
            f"\U0001f4cb {len(pending)} proposition(s) en attente au total "
            f"(la plus ancienne : {oldest_age})"
        )
    else:
        lines.append("\U0001f4cb Aucune proposition en attente.")

    if recurring:
        lines.append("")
        lines.append(
            f"⚠️ <b>{len(recurring)} proposition(s) récurrente(s) "
            f"jamais décidée(s)</b> :"
        )
        for r in recurring[:5]:
            score = r.get("titan_score")
            score_str = f" · TITAN {score:.0f}" if isinstance(score, (int, float)) else ""
            lines.append(f"  • <b>{r['ticker']}</b> — recyclée {r['streak']}×{score_str}")

    link = _proposals_link()
    if link:
        lines.append(f'\U0001f449 <a href="{link}">Voir les propositions</a>')

    lines.append("")
    if eq:
        lines.append(
            f"\U0001f4b0 Capital : ${eq.get('current_equity', 0):,.0f} | "
            f"Réalisé : ${eq.get('realized_pnl', 0):+,.0f} | "
            f"Latent : ${eq.get('unrealized_pnl', 0):+,.0f}"
        )
        lines.append(f"\U0001f4c8 {len(open_positions)} position(s) ouverte(s)")
    else:
        lines.append("\U0001f4b0 État du portefeuille indisponible.")

    return "\n".join(lines)


def send_daily_digest() -> None:
    text = build_digest_text()
    try:
        from modules.alerter import _send_telegram_message
        _send_telegram_message(text)
        logger.info("[DailyDigest] Envoyé avec succès")
    except Exception as exc:
        logger.error(f"[DailyDigest] Envoi échoué : {exc}", exc_info=True)
