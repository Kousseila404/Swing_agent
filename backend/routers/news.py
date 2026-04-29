"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — NEWS (Finnhub company-news par ticker)                 ║
║  GET /api/news/{ticker}?days=14                                  ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from modules import finnhub_news
from modules import watchlist as wl_mod
from modules.duckdb_journal import read_journal_df
from modules.log import logger

router = APIRouter(prefix="/api", tags=["news"])


@router.get("/news/{ticker}")
def get_news(
    ticker: str,
    days: int = Query(14, ge=1, le=60),
) -> dict[str, Any]:
    t = (ticker or "").upper().strip()
    if not t or not t.replace("-", "").replace(".", "").isalnum():
        raise HTTPException(status_code=400, detail="Ticker invalide")
    return finnhub_news.fetch_news(t, days=days)


@router.get("/news/portfolio/firehose")
def portfolio_news_firehose(
    days: int = Query(7, ge=1, le=30),
    max_per_ticker: int = Query(5, ge=1, le=20),
) -> dict[str, Any]:
    """News agrégées sur positions OPEN + watchlist, triées par date desc.

    Rate-limited (cache disque 1h par ticker — voir finnhub_news). Si la
    fenêtre est large + beaucoup de tickers, le 1er appel peut prendre
    jusqu'à N×1.5s ; les suivants sont cache hits instantanés.
    """
    # Positions OPEN.
    open_set: set[str] = set()
    try:
        df = read_journal_df()
        if df is not None and not df.empty and "Status" in df.columns:
            for _, row in df[df["Status"] == "OPEN"].iterrows():
                t = str(row.get("Ticker") or "").upper().strip()
                if t:
                    open_set.add(t)
    except Exception as exc:
        logger.warning(f"[news/firehose] load_journal fail: {exc}")

    # Watchlist.
    watch_set: set[str] = set()
    try:
        for it in wl_mod.list_watchlist():
            t = (it.get("ticker") or "").upper().strip()
            if t:
                watch_set.add(t)
    except Exception as exc:
        logger.warning(f"[news/firehose] watchlist fail: {exc}")

    tickers = sorted(open_set | watch_set)
    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for t in tickers:
        scope = []
        if t in open_set:    scope.append("position")
        if t in watch_set:   scope.append("watchlist")
        result = finnhub_news.fetch_news(t, days=days, max_items=max_per_ticker)
        if result.get("error"):
            errors.append({"ticker": t, "error": result["error"]})
            continue
        for art in result.get("articles", []):
            items.append({
                "ticker": t,
                "scope":  scope,
                **art,
            })

    items.sort(key=lambda a: a.get("datetime") or "", reverse=True)

    return {
        "items":      items,
        "n_items":    len(items),
        "n_tickers":  len(tickers),
        "scope": {
            "n_open":      len(open_set),
            "n_watchlist": len(watch_set),
        },
        "errors":     errors,
        "days":       days,
    }
