"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — HISTORY (snapshots + série temporelle par ticker)      ║
║  GET  /api/history/snapshots                                     ║
║  GET  /api/history/ticker/{ticker}                               ║
║                                                                  ║
║  Les endpoints POST /api/backtest* ont été retirés avec la purge ║
║  des pages Backtest / QuantBacktest (2026-04-24). Le module       ║
║  `modules.backtest` reste utilisable en CLI.                     ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import math
from datetime import date

from fastapi import APIRouter, HTTPException, Query

from modules import universe_history

router = APIRouter(prefix="/api", tags=["history"])


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise HTTPException(400, f"Format date invalide ({s}) — attendu YYYY-MM-DD") from e


# ─────────────────────────────────────────────────────────────────
# GET /api/history/snapshots — dates disponibles
# ─────────────────────────────────────────────────────────────────

@router.get("/history/snapshots")
def list_snapshots_endpoint():
    """Liste les dates de snapshots historiques disponibles (Lot 6 storage).

    Endpoint public — l'UI l'utilise pour borner le date-picker du ticker
    detail aux dates effectivement disponibles.
    """
    dates = universe_history.list_snapshots()
    return {
        "history_dates": [d.isoformat() for d in dates],
        "legacy_dates":  [],  # conservé pour compat frontend (TickerDetailPage)
        "n_history":     len(dates),
        "n_legacy":      0,
    }


# ─────────────────────────────────────────────────────────────────
# GET /api/history/ticker/{ticker} — historique scores d'un ticker
# ─────────────────────────────────────────────────────────────────

@router.get("/history/ticker/{ticker}")
def ticker_history_endpoint(
    ticker: str,
    start: str | None = Query(default=None, description="YYYY-MM-DD"),
    end:   str | None = Query(default=None),
    fields: str | None = Query(
        default=None,
        description="CSV des champs à extraire (default = scores TITAN principaux)",
    ),
):
    """Retourne l'historique d'un ticker. Champs par défaut = les scores TITAN
    + momentum + price (pratique pour grapher la trajectoire score/prix).
    """
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    field_list: list[str] | None = None
    if fields:
        field_list = [f.strip() for f in fields.split(",") if f.strip()]
    else:
        field_list = [
            "titan_composite_score", "titan_composite_raw",
            "quality_score", "value_score", "risk_score",
            "sentiment_score", "momentum_score", "piotroski_score",
            "f_score", "f_score_max",
            "data_quality", "current_price", "momentum_return_pct", "sector",
        ]

    rows = universe_history.ticker_history(
        ticker, start=start_d, end=end_d, fields=field_list,
    )
    # Sanitize NaN/inf pour JSON-safety (yfinance peut renvoyer des NaN).
    for r in rows:
        for k, v in list(r.items()):
            if isinstance(v, float) and not math.isfinite(v):
                r[k] = None
    return {
        "ticker":    ticker.upper().strip(),
        "n_points":  len(rows),
        "fields":    field_list,
        "history":   rows,
    }
