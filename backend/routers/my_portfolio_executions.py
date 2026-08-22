"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — JOURNAL D'EXÉCUTION MON PORTEFEUILLE (Upgrade 4)        ║
║  POST /api/my_portfolio/executions   GET /api/my_portfolio/executions ║
╚══════════════════════════════════════════════════════════════════╝

Saisie manuelle assistée d'un fill (pas d'intégration eToro, voir
docs/UPGRADES_MY_PORTFOLIO.md Upgrade 4) : le POST calcule le prix de
référence historique + le slippage, le GET liste le journal + des agrégats
(coût cumulé, % hors séance, pires exécutions).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Security
from pydantic import BaseModel

from modules import api_core
from modules.my_portfolio_executions import get_summary, load_executions, log_execution

router = APIRouter(prefix="/api/my_portfolio", tags=["my_portfolio"])


class LogExecutionRequest(BaseModel):
    ticker: str
    direction: str
    shares: float
    fill_price_native: float
    executed_at: datetime
    notes: str = ""


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (None if v is not None and isinstance(v, float) and v != v else v) for k, v in row.items()}


@router.post("/executions")
def post_execution(req: LogExecutionRequest, _auth: None = Security(api_core.require_auth)) -> dict[str, Any]:
    ticker = req.ticker.upper().strip()
    if not ticker:
        raise HTTPException(400, "Ticker vide")
    if req.direction not in ("BUY", "SELL"):
        raise HTTPException(400, "direction doit être BUY ou SELL")
    if req.shares <= 0:
        raise HTTPException(400, "shares doit être > 0")
    if req.fill_price_native <= 0:
        raise HTTPException(400, "fill_price_native doit être > 0")
    if req.executed_at.tzinfo is None:
        raise HTTPException(400, "executed_at doit inclure un offset de fuseau explicite (ex: +02:00)")

    row = log_execution(ticker, req.direction, req.shares, req.fill_price_native, req.executed_at, req.notes)
    return _row_to_dict(row)


@router.get("/executions")
def get_executions(_auth: None = Security(api_core.require_auth)) -> dict[str, Any]:
    df = load_executions()
    executions = [_row_to_dict(row) for row in df.to_dict(orient="records")]
    return {"executions": executions, "summary": get_summary(df)}
