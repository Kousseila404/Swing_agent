"""Commandes Telegram entrantes (2026-09-17) — alertes actionnables.

Lit les messages du chat autorisé (`TELEGRAM_CHAT_ID`) via `getUpdates`
(offset persisté dans `data/.telegram_offset.json`) et exécute :

    /status              equity, positions, protection, killswitch
    /approve TICKER      approuve la proposition pending du ticker (mêmes gates que l'UI)
    /reject TICKER       rejette la proposition pending du ticker
    /pause               gèle les nouvelles entrées (killswitch freeze)
    /resume              lève le gel
    /help

Cron (deploy/crontab.txt) : toutes les 2 min, 12–22 h UTC. Fail-open :
une erreur ne bloque jamais le reste du système. Aucune commande de vente
directe : la sortie reste gérée par les stops, la rotation et l'UI.
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

import httpx

import config
from modules import api_core, proposals
from modules.log import logger

_BACKEND = Path(__file__).resolve().parents[1]
OFFSET_PATH = _BACKEND / "data" / ".telegram_offset.json"
_API_BASE = "http://127.0.0.1:8000"


def _load_offset() -> int:
    try:
        return int(json.loads(OFFSET_PATH.read_text()).get("offset", 0))
    except Exception:
        return 0


def _save_offset(offset: int) -> None:
    try:
        OFFSET_PATH.write_text(json.dumps({"offset": offset}))
    except Exception:
        pass


def _send(text: str) -> None:
    try:
        from modules.alerter import _send_telegram_message
        _send_telegram_message(text)
    except Exception as exc:
        logger.warning(f"[TelegramInbox] envoi : {exc}")


def _api(method: str, path: str, **kw) -> dict[str, Any]:
    with httpx.Client(base_url=_API_BASE, timeout=30.0) as c:
        r = c.request(method, path, headers={"Authorization": f"Bearer {api_core.API_TOKEN}"}, **kw)
        r.raise_for_status()
        return r.json() if r.content else {}


def _find_pending(ticker: str) -> dict[str, Any] | None:
    for p in proposals.list_all(status="pending"):
        if str(p.get("ticker") or "").upper() == ticker.upper():
            return p
    return None


def handle_command(text: str) -> str:
    """Retourne la réponse (HTML Telegram). Pure sauf pour les actions."""
    parts = text.strip().split()
    if not parts:
        return ""
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1].upper() if len(parts) > 1 else ""

    if cmd == "/help":
        return ("<b>Commandes</b>\n/status\n/approve TICKER\n/reject TICKER\n/pause · /resume\n"
                "Les ventes restent gérées par les stops, la rotation et l'interface.")

    if cmd == "/status":
        eq = api_core.load_equity() or {}
        pos = eq.get("open_positions") or []
        try:
            prot = _api("GET", "/api/portfolio/protection")
            prot_txt = f"{(prot.get('n_positions') or 0) - (prot.get('n_unprotected') or 0)}/{prot.get('n_positions')} protégées"
        except Exception:
            prot_txt = "protection : n/a"
        trading = api_core.read_json(api_core.TRADING_PATH, {}) or {}
        lines = [f"<b>Equity</b> {float(eq.get('current_equity') or 0):,.0f} $ · latent {float(eq.get('unrealized_pnl') or 0):+,.0f} $",
                 f"{len(pos)} positions · {prot_txt} · killswitch {'ACTIF' if trading.get('blocked') else 'inactif'}"]
        for p in sorted(pos, key=lambda x: -(x.get('unrealized_pnl') or 0)):
            lines.append(f"• {escape(str(p.get('ticker')))} {float(p.get('pct_from_entry') or 0):+.1f} %")
        n_pending = len(proposals.list_all(status="pending"))
        lines.append(f"{n_pending} proposition(s) en attente")
        return "\n".join(lines)

    if cmd in ("/approve", "/reject"):
        if not arg:
            return f"Usage : {cmd} TICKER"
        p = _find_pending(arg)
        if p is None:
            return f"Aucune proposition pending pour {escape(arg)}"
        try:
            if cmd == "/approve":
                res = _api("POST", "/api/proposals/approve_batch", json={"items": [{"id": p["id"]}], "decided_by": "telegram"})
            else:
                res = _api("POST", "/api/proposals/reject_batch", json={"items": [{"id": p["id"]}], "decided_by": "telegram", "reason": "telegram"})
            r0 = (res.get("results") or [{}])[0]
            ok = r0.get("ok", res.get("ok", True))
            return f"{'✅' if ok else '❌'} {escape(arg)} : {escape(str(r0.get('message') or res.get('message') or ('fait' if ok else 'refusé')))}"
        except Exception as exc:
            return f"❌ {escape(arg)} : {escape(str(exc))}"

    if cmd == "/pause":
        from modules.tracker.killswitch import _set_trading_blocked
        _set_trading_blocked(True)
        return "🧊 Nouvelles entrées gelées (/resume pour lever)."

    if cmd == "/resume":
        from modules.tracker.killswitch import _set_trading_blocked
        _set_trading_blocked(False)
        return "▶️ Entrées réactivées."

    return "Commande inconnue — /help"


def poll_once() -> dict[str, Any]:
    token = getattr(config, "TELEGRAM_BOT_TOKEN", None)
    chat_id = str(getattr(config, "TELEGRAM_CHAT_ID", "") or "")
    if not token or not chat_id:
        return {"skipped": "telegram non configuré"}
    offset = _load_offset()
    try:
        r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates",
                      params={"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["message"])}, timeout=20)
        r.raise_for_status()
        updates = r.json().get("result") or []
    except Exception as exc:
        logger.warning(f"[TelegramInbox] getUpdates : {exc}")
        return {"error": str(exc)}
    handled = 0
    for u in updates:
        offset = max(offset, int(u.get("update_id", 0)) + 1)
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != chat_id:
            continue
        text = str(msg.get("text") or "")
        if not text.startswith("/"):
            continue
        try:
            reply = handle_command(text)
        except Exception as exc:
            reply = f"❌ erreur : {escape(str(exc))}"
        if reply:
            _send(reply)
        handled += 1
        logger.info(f"[TelegramInbox] {text.split()[0]} → traité")
    _save_offset(offset)
    return {"updates": len(updates), "handled": handled, "offset": offset}


if __name__ == "__main__":
    print(json.dumps(poll_once(), ensure_ascii=False))
