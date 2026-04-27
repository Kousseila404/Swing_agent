"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — TRADES (saisie manuelle + clôture)                     ║
║  POST /api/trade/add          /api/trade/close                   ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pandas as pd
from fastapi import APIRouter, HTTPException, Security
from filelock import FileLock
from pydantic import BaseModel

from modules import api_core
from modules.api_schemas import GenericOkResponse
from modules.duckdb_journal import shadow_insert, shadow_update_status
from modules.log import logger

router = APIRouter(prefix="/api", tags=["trades"])


class AddTradeRequest(BaseModel):
    ticker: str
    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    size: int
    signal: str = "MANUAL"
    sector: str = ""


class CloseTradeRequest(BaseModel):
    ticker: str
    exit_price: float
    result: str


@router.post("/trade/add", response_model=GenericOkResponse)
def add_trade(req: AddTradeRequest, _auth: None = Security(api_core.require_auth)):
    """Ajoute un trade manuel dans le journal CSV."""
    ticker = req.ticker.upper().strip()
    if not ticker:
        raise HTTPException(400, "Ticker vide")
    if req.entry <= 0 or req.stop_loss <= 0 or req.take_profit <= 0:
        raise HTTPException(400, "Prix invalide (≤ 0)")
    if req.size <= 0:
        raise HTTPException(400, "Size doit être > 0")

    if req.direction == "LONG":
        if req.stop_loss >= req.entry:
            raise HTTPException(400, "SL doit être < Entry pour un LONG")
        if req.take_profit <= req.entry:
            raise HTTPException(400, "TP doit être > Entry pour un LONG")
        rr = round((req.take_profit - req.entry) / (req.entry - req.stop_loss), 2)
    else:
        if req.stop_loss <= req.entry:
            raise HTTPException(400, "SL doit être > Entry pour un SHORT")
        if req.take_profit >= req.entry:
            raise HTTPException(400, "TP doit être < Entry pour un SHORT")
        rr = round((req.entry - req.take_profit) / (req.stop_loss - req.entry), 2)

    try:
        from modules.utils import CSV_SCHEMA
        row = {col: "" for col in CSV_SCHEMA}
    except Exception:
        row = {}

    row.update({
        "Date":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Ticker":     ticker,
        "Direction":  req.direction,
        "Entry":      str(round(req.entry, 4)),
        "Stop_Loss":  str(round(req.stop_loss, 4)),
        "Initial_SL": str(round(req.stop_loss, 4)),
        "Take_Profit": str(round(req.take_profit, 4)),
        "Size":       str(req.size),
        "RR":         str(abs(rr)),
        "Status":     "OPEN",
        "Order_ID":   f"MANUAL_{uuid.uuid4().hex[:6].upper()}",
        "Signal":     req.signal,
        "Sector":     req.sector,
    })

    try:
        with FileLock(str(api_core.CSV_LOCK_PATH), timeout=10):
            if api_core.CSV_PATH.exists() and api_core.CSV_PATH.stat().st_size > 0:
                df = pd.read_csv(api_core.CSV_PATH, dtype=str)
            else:
                df = pd.DataFrame(columns=list(row.keys()))
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
            df.to_csv(api_core.CSV_PATH, index=False)
        # Dual-write DuckDB (fail-open — CSV reste source de vérité)
        shadow_insert(row)
        return {"ok": True, "message": f"Trade {ticker} {req.direction} ajouté (RR {abs(rr):.2f})"}
    except Exception as e:
        logger.error(f"[API /trade/add] {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Erreur serveur interne") from e


@router.post("/trade/close", response_model=GenericOkResponse)
def close_trade(req: CloseTradeRequest, _auth: None = Security(api_core.require_auth)):
    """Clôture la dernière position OPEN d'un ticker."""
    ticker = req.ticker.upper().strip()
    try:
        with FileLock(str(api_core.CSV_LOCK_PATH), timeout=10):
            df = pd.read_csv(api_core.CSV_PATH, dtype=str)
            mask = (df["Ticker"].str.upper() == ticker) & (df["Status"] == "OPEN")
            if not mask.any():
                raise HTTPException(404, f"Aucune position OPEN pour {ticker}")
            idx = df[mask].index[-1]
            df.at[idx, "Status"]     = req.result
            df.at[idx, "Exit_Price"] = str(round(req.exit_price, 4))
            exit_date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            df.at[idx, "Exit_Date"]  = exit_date_str
            df.to_csv(api_core.CSV_PATH, index=False)
        shadow_update_status(ticker, req.result, req.exit_price, exit_date_str)
        return {"ok": True, "message": f"{ticker} clôturé à {req.exit_price:.4f} → {req.result}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API /trade/close] {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Erreur serveur interne") from e
