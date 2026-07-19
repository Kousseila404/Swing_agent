"""Endpoint /api/peers/{ticker} — top-N tickers comparables.

Sectoriel + market_cap proche. Pure data, pas d'IA, pas d'API externe.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from modules.peer_comparison import (
    MAX_COMPARE_TICKERS,
    build_compare_table,
    build_peer_table,
)
from modules.sector_metrics import get_scored_universe

router = APIRouter(prefix="/api", tags=["peers"])


@router.get("/peers/{ticker}")
def peers(
    ticker: str,
    n: int = Query(5, ge=1, le=15),
) -> dict[str, Any]:
    ticker = ticker.upper().strip()
    if not ticker or not ticker.isalnum():
        raise HTTPException(status_code=400, detail="Ticker invalide")
    universe = get_scored_universe()
    if not universe:
        raise HTTPException(status_code=503, detail="Universe scoré indisponible")
    if ticker not in universe:
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' absent")
    return build_peer_table(ticker, universe, n=n)


@router.get("/compare")
def compare(
    tickers: str = Query(..., description="Tickers séparés par des virgules, ex: AAPL,MSFT,GOOG"),
) -> dict[str, Any]:
    raw = [t.strip() for t in tickers.split(",") if t.strip()]
    if len(raw) < 2:
        raise HTTPException(status_code=400, detail="Au moins 2 tickers requis")
    if len(raw) > MAX_COMPARE_TICKERS:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_COMPARE_TICKERS} tickers")
    for t in raw:
        if not t.isalnum():
            raise HTTPException(status_code=400, detail=f"Ticker invalide : {t}")
    universe = get_scored_universe()
    if not universe:
        raise HTTPException(status_code=503, detail="Universe scoré indisponible")
    return build_compare_table(raw, universe)
