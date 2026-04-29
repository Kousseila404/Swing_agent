"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — SEC EDGAR filings (10-K / 10-Q / 8-K / Form 4 / 13F)   ║
║  GET /api/sec_filings/{ticker}?limit=30                          ║
║                                                                  ║
║  Public read-only. Données SEC = officielles, gratuites,         ║
║  illimitées (rate limit politesse 10 req/sec côté backend).      ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from modules import sec_edgar

router = APIRouter(prefix="/api", tags=["sec_filings"])


@router.get("/sec_filings/{ticker}")
def get_sec_filings(
    ticker: str,
    limit: int = Query(30, ge=1, le=100),
) -> dict[str, Any]:
    t = (ticker or "").upper().strip()
    if not t or not t.replace("-", "").replace(".", "").isalnum():
        raise HTTPException(status_code=400, detail="Ticker invalide")
    return sec_edgar.fetch_recent_filings(t, limit=limit)
