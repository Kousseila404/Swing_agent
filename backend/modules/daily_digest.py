"""Module — digest Telegram quotidien actionnable.

Un seul message le matin plutôt que devoir ouvrir l'appli pour voir l'état
de la file de propositions : combien en attente, depuis combien de temps,
lesquelles regarder en premier — plus un rappel de l'état du portefeuille.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from modules import api_core, proposals
from modules.log import logger

_TOP_N = 3


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


def build_digest_text() -> str:
    pending = proposals.list_all(status="pending")
    pending_sorted = sorted(
        pending,
        key=lambda p: (p.get("context") or {}).get("titan_score") or 0,
        reverse=True,
    )
    top = pending_sorted[:_TOP_N]
    oldest = min(pending, key=lambda p: p.get("created_at") or "", default=None)

    eq = _equity_snapshot()
    open_positions = eq.get("open_positions") or []

    lines = ["\U0001f4ca <b>Digest quotidien SwingQuant</b>", ""]

    if pending:
        oldest_age = _fmt_age(oldest["created_at"]) if oldest else "?"
        lines.append(
            f"\U0001f4cb <b>{len(pending)} proposition(s) en attente</b> "
            f"(la plus ancienne : {oldest_age})"
        )
        for p in top:
            ctx = p.get("context") or {}
            titan = ctx.get("titan_score")
            support = (ctx.get("support") or {}).get("level", "?")
            verdict = (ctx.get("buy_signal") or {}).get("verdict", "?")
            titan_str = f"{titan:.0f}" if titan is not None else "?"
            lines.append(
                f"  • {p.get('ticker')} — TITAN {titan_str} | {support} | {verdict}"
            )
        if len(pending) > _TOP_N:
            lines.append(f"  … et {len(pending) - _TOP_N} autre(s)")
    else:
        lines.append("\U0001f4cb Aucune proposition en attente.")

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
