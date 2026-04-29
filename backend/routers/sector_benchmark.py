"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — SECTOR BENCHMARK (alpha vs ETF SPDR par position)      ║
║  GET /api/sector_benchmark/portfolio                             ║
║  GET /api/sector_benchmark/{ticker}?entry_date=YYYY-MM-DD&sector=…║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from modules import sector_benchmark
from modules.duckdb_journal import read_journal_df
from modules.log import logger

router = APIRouter(prefix="/api", tags=["sector_benchmark"])


@router.get("/sector_benchmark/portfolio")
def benchmark_portfolio_endpoint() -> dict[str, Any]:
    """Pour chaque position OPEN, calcule le return depuis l'achat
    + return de l'ETF sectoriel + alpha (delta).
    """
    try:
        df = read_journal_df()
    except Exception as exc:
        logger.error(f"[sector_benchmark] load_journal fail: {exc}")
        raise HTTPException(503, "Journal indisponible") from exc

    positions: list[dict[str, Any]] = []
    if df is not None and not df.empty and "Status" in df.columns:
        for _, row in df[df["Status"] == "OPEN"].iterrows():
            t = str(row.get("Ticker") or "").upper().strip()
            if not t:
                continue
            positions.append({
                "ticker":     t,
                "sector":     row.get("Sector") or "",
                "entry_date": str(row.get("Date") or "")[:10],
            })

    return sector_benchmark.benchmark_portfolio(positions)


@router.get("/sector_benchmark/{ticker}")
def benchmark_ticker_endpoint(
    ticker: str,
    entry_date: str = Query(..., description="YYYY-MM-DD"),
    sector: str | None = Query(None),
) -> dict[str, Any]:
    t = (ticker or "").upper().strip()
    if not t or not t.replace("-", "").replace(".", "").isalnum():
        raise HTTPException(400, "Ticker invalide")
    return sector_benchmark.benchmark_for_position(t, sector, entry_date)
