"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — CALENDAR (catalysts agrégés)                           ║
║  GET /api/calendar?days=30                                       ║
║                                                                  ║
║  Agrège :                                                        ║
║   - earnings dates des positions OPEN + watchlist                ║
║   - événements macro (FOMC/CPI/NFP) du macro_calendar.json       ║
║                                                                  ║
║  Pure data, pas d'IA. Réutilise universe_engine.get_scored_     ║
║  universe(), load_journal(), watchlist.list_watchlist().         ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Query

from modules import api_core
from modules import watchlist as wl_mod
from modules.duckdb_journal import read_journal_df
from modules.log import logger
from modules.sector_metrics import get_scored_universe

router = APIRouter(prefix="/api", tags=["calendar"])

_MACRO_CALENDAR_PATH = api_core.BASE / "data" / "macro_calendar.json"


def _safe_parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def _open_position_tickers() -> list[dict[str, Any]]:
    """Retourne [{ticker, sector}] pour positions OPEN."""
    try:
        df = read_journal_df()
        if df is None or df.empty:
            return []
        if "Status" in df.columns:
            df = df[df["Status"] == "OPEN"]
        return [
            {
                "ticker": str(row.get("Ticker") or "").upper().strip(),
                "sector": str(row.get("Sector") or ""),
            }
            for _, row in df.iterrows()
            if row.get("Ticker")
        ]
    except Exception as exc:
        logger.warning(f"[calendar] load_journal fail: {exc}")
        return []


@router.get("/calendar")
def get_calendar(days: int = Query(30, ge=1, le=120)) -> dict[str, Any]:
    """Catalyseurs agrégés sur la fenêtre [today, today + days].

    Output :
      {
        "today": "2026-04-28",
        "horizon_days": 30,
        "events": [
            {date, days_delta, type, ticker?, label, source, scope},
            ...
        ],
        "scope": {n_open: int, n_watchlist: int, n_macro: int},
      }
    """
    today = date.today()
    end = today + timedelta(days=days)

    open_pos = _open_position_tickers()
    open_set = {p["ticker"] for p in open_pos}

    try:
        watch_items = wl_mod.list_watchlist()
    except Exception as exc:
        logger.warning(f"[calendar] watchlist fail: {exc}")
        watch_items = []
    watch_set = {it["ticker"] for it in watch_items}

    # Universe scoré pour récupérer next_earnings_date.
    universe = get_scored_universe() or {}

    events: list[dict[str, Any]] = []

    # ─── Earnings ──────────────────────────────────────────────
    candidate_tickers = open_set | watch_set
    for t in sorted(candidate_tickers):
        info = universe.get(t) or {}
        ed = _safe_parse_date(info.get("next_earnings_date"))
        if ed is None or ed < today or ed > end:
            continue
        scope = []
        if t in open_set:
            scope.append("position")
        if t in watch_set:
            scope.append("watchlist")
        events.append({
            "date":        ed.isoformat(),
            "days_delta":  (ed - today).days,
            "type":        "EARNINGS",
            "ticker":      t,
            "label":       f"Earnings {t}",
            "source":      "universe.json",
            "scope":       scope,
            "sector":      info.get("sector"),
        })

    # ─── Macro events ──────────────────────────────────────────
    if _MACRO_CALENDAR_PATH.exists():
        try:
            with open(_MACRO_CALENDAR_PATH, encoding="utf-8") as f:
                macro = json.load(f)
            for e in macro.get("events", []):
                ed = _safe_parse_date(e.get("date"))
                if ed is None or ed < today or ed > end:
                    continue
                events.append({
                    "date":        ed.isoformat(),
                    "days_delta":  (ed - today).days,
                    "type":        e.get("type") or "MACRO",
                    "ticker":      None,
                    "label":       e.get("label") or e.get("name") or "Macro event",
                    "source":      "macro_calendar.json",
                    "scope":       ["macro"],
                    "blackout":    bool(e.get("blackout")),
                })
        except Exception as exc:
            logger.warning(f"[calendar] macro_calendar parse fail: {exc}")

    # Tri par date asc puis ticker.
    events.sort(key=lambda e: (e["date"], e.get("ticker") or "", e["type"]))

    return {
        "today":        today.isoformat(),
        "horizon_days": days,
        "events":       events,
        "scope": {
            "n_open":       len(open_set),
            "n_watchlist":  len(watch_set),
            "n_macro":      sum(1 for e in events if e["type"] in {"FOMC", "CPI", "NFP", "MACRO"}),
            "n_earnings":   sum(1 for e in events if e["type"] == "EARNINGS"),
        },
    }
