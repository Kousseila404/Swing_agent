"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — SYSTEM (santé + calendrier macro)                      ║
║  GET  /api/status            /api/market_status                  ║
║  GET  /api/macro_calendar                                        ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import os
from datetime import date as _date
from datetime import datetime

from fastapi import APIRouter

from modules import api_core
from modules.api_schemas import (
    MacroCalendarResponse,
    StatusResponse,
)
from modules.log import logger

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/status", response_model=StatusResponse)
def get_status():
    """Santé du système."""
    equity  = api_core.load_equity()
    trading = api_core.read_json(api_core.TRADING_PATH, {})
    macro   = api_core.load_macro()
    blocked = trading.get("blocked", False)

    open_count = len(equity.get("open_positions", []))
    start   = float(equity.get("starting_equity", api_core.INITIAL_CAPITAL))
    current = float(equity.get("current_equity",  api_core.INITIAL_CAPITAL))
    daily_dd = max(0, (start - current) / start * 100) if start > 0 else 0.0

    return {
        "ok":                  True,
        "timestamp":           datetime.now().isoformat(),
        "trading_blocked":     blocked,
        "nuclear_stop":        blocked,
        "open_positions":      open_count,
        "max_positions":       5,
        "daily_drawdown_pct":  round(daily_dd, 2),
        "account_equity":      round(current, 2),
        "regime":              macro.get("confirmed_regime") or macro.get("regime", "UNKNOWN"),
        "vix":                 macro.get("vix"),
        "broker_mode":         os.getenv("BROKER_MODE", "paper"),
    }


@router.get("/macro_calendar", response_model=MacroCalendarResponse)
def get_macro_calendar(horizon_days: int = 60):
    """Calendrier macro avec zones de blackout. Lit data/macro_calendar.json."""
    raw = api_core.read_json(api_core.MACRO_CALENDAR_PATH, {}) or {}
    events_raw = raw.get("events") if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    today = _date.today()

    events: list[dict] = []
    for ev in events_raw or []:
        try:
            evt_date = _date.fromisoformat(ev["date"])
            delta = (evt_date - today).days
            if delta < -30 or delta > horizon_days:
                continue
            events.append({
                "date":       ev["date"],
                "type":       ev.get("type", "?"),
                "label":      ev.get("label", ""),
                "days_delta": delta,
                "blackout":   -1 <= delta <= 0,
                "upcoming":   0 < delta <= 7,
            })
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(f"[macro_calendar] event ignoré (parse): {ev!r} — {exc}")
            continue
    events.sort(key=lambda x: x["date"])
    in_blackout = any(e["blackout"] for e in events)
    upcoming = next((e for e in events if e["days_delta"] >= 0), None)
    return {
        "today":       today.isoformat(),
        "in_blackout": in_blackout,
        "next_event":  upcoming,
        "events":      events,
    }


@router.get("/market_status")
def get_market_status():
    """État de l'horloge NYSE côté Alpaca (is_open, next_open, next_close).

    Utilisé par le frontend pour désactiver Execute hors heures et par le broker
    comme fail-closed gate. Si Alpaca injoignable, retourne is_open=None (le
    frontend affiche un warning neutre plutôt que bloquer).
    """
    try:
        from modules.broker_gateway import get_broker
        broker = get_broker()
        if not hasattr(broker, "market_status"):
            return {"is_open": None, "error": "Broker non-Alpaca — market hours non vérifiables"}
        return broker.market_status()
    except Exception as e:
        logger.warning(f"[API /market_status] {e}")
        return {"is_open": None, "error": str(e)[:200]}
