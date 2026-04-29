"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — HISTORY (snapshots + série temporelle par ticker)      ║
║  GET  /api/history/snapshots                                     ║
║  GET  /api/history/ticker/{ticker}                               ║
║  POST /api/backtest/quick   — backtest sur une vue filtrée       ║
║                                                                  ║
║  Les anciens endpoints POST /api/backtest* (large-scale compare) ║
║  restent retirés. /quick est minimaliste et pensé pour la page   ║
║  Univers (Tier B #1).                                            ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import math
from dataclasses import asdict
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Security
from pydantic import BaseModel, Field

from modules import api_core, backtest, universe_history
from modules.log import logger

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


# ─────────────────────────────────────────────────────────────────
# POST /api/backtest/quick — backtest sur une whitelist de tickers
# ─────────────────────────────────────────────────────────────────

class QuickBacktestRequest(BaseModel):
    """Backtest minimaliste exposé pour la page Univers (Tier B #1).

    On reste *simple* :
      - Whitelist de tickers (la "vue filtrée" côté UI)
      - top_n borné à 30 pour éviter une vue de ranking dégénérée
      - benchmark optionnel (default SPY)

    Pas d'override de slippage/lag — l'utilisateur expert peut toujours
    appeler `python -m modules.backtest` en CLI pour tuner finement.
    """
    tickers:   list[str] = Field(default_factory=list, max_length=500)
    top_n:     int = Field(default=10, ge=1, le=30)
    benchmark: str | None = Field(default="SPY")
    weighting: str = Field(default="equal", pattern="^(equal|score|risk_parity)$")


@router.post("/backtest/quick")
def backtest_quick(
    req: QuickBacktestRequest,
    _auth: None = Security(api_core.require_auth),
):
    """Lance un backtest TITAN sur la whitelist `tickers` (vue filtrée UI).

    Exigences minimales :
      - ≥ 2 snapshots dans `universe_history` (sinon on ne peut pas calculer
        une période de rebalance)
      - ≥ `top_n` tickers dans la whitelist (sinon ranking dégénéré)

    Retourne {periods, stats, equity_curve, weights_final, n_skipped_periods}
    + meta {n_tickers_input, n_snapshots, benchmark_return}.
    """
    tickers = [t.upper().strip() for t in (req.tickers or []) if t and t.strip()]
    if len(tickers) < req.top_n:
        raise HTTPException(
            422,
            f"Whitelist trop petite ({len(tickers)} tickers) pour top_n={req.top_n}",
        )

    try:
        result = backtest.run_titan_top_n(
            top_n=req.top_n,
            benchmark=req.benchmark,
            weighting=req.weighting,
            restrict_to=tickers,
        )
    except ValueError as e:
        # Cas typique : < 2 snapshots disponibles.
        raise HTTPException(422, str(e)) from e
    except Exception as e:
        logger.error(f"[Backtest /quick] failed: {e}", exc_info=True)
        raise HTTPException(500, f"Backtest a échoué: {e}") from e

    raw: dict[str, Any] = asdict(result)
    # Wrap dans une enveloppe "stats" pour un contrat clean côté UI :
    # {periods, equity_curve, stats, meta}. Évite de polluer le top-level avec
    # 10 champs scalaires éparpillés.
    stats_keys = (
        "total_return", "avg_daily_return", "sharpe_daily", "sharpe_annual",
        "max_drawdown", "hit_rate", "benchmark_return", "alpha",
    )
    payload: dict[str, Any] = {
        "strategy":     raw.get("strategy"),
        "top_n":        raw.get("top_n"),
        "periods":      raw.get("periods", []),
        "equity_curve": raw.get("equity_curve", []),
        "diagnostics":  raw.get("diagnostics", {}),
        "stats": {k: raw.get(k) for k in stats_keys},
        "meta": {
            "n_tickers_input": len(tickers),
            "top_n":           req.top_n,
            "weighting":       req.weighting,
            "benchmark":       req.benchmark,
        },
    }
    return payload
