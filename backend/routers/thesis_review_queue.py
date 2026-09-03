"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — FILE DE RÉVISION DE THÈSE (propositions non validées)   ║
║  POST/GET /api/my_portfolio/{ticker}/thesis_review_queue          ║
║  PATCH    /api/my_portfolio/{ticker}/thesis_review_queue/{id}     ║
╚══════════════════════════════════════════════════════════════════╝

Séparation d'authentification volontaire (voir modules/api_core.py) :

  - POST/GET  → `require_review_or_full_auth` (token de revue restreint OU
    token complet). C'est par CE endpoint qu'une routine cloud (ex: revue
    mensuelle BNP.PA) soumet ses constats — jamais via PATCH .../thesis.
  - PATCH .../{id} (validation/rejet humain) → `require_auth` (token complet
    UNIQUEMENT). Le token de revue restreint échoue ici — une routine ne
    peut donc jamais marquer sa propre proposition "validated" toute seule.

Ce router n'importe jamais `modules.my_portfolio_thesis` et n'appelle jamais
`update_thesis` — il ne peut techniquement pas écrire dans `verification.*`,
même par erreur de code future (aucun import qui le permettrait).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Security
from pydantic import BaseModel

from modules import api_core
from modules.my_portfolio_data import POSITIONS
from modules.thesis_review_queue import add_entry, list_entries, update_entry_status

router = APIRouter(prefix="/api/my_portfolio", tags=["my_portfolio"])

_VALID_TICKERS = {p["ticker"] for p in POSITIONS}


class FindingItem(BaseModel):
    topic: str | None = None
    constat: str
    source: str | None = None


class ReviewQueueSubmission(BaseModel):
    execution_type: str
    findings: list[FindingItem] = []
    proposed_verdict: str | None = None
    run_date: str | None = None


class ReviewQueueStatusUpdate(BaseModel):
    status: str


def _resolve_ticker(ticker: str) -> str:
    upper = ticker.upper()
    for t in _VALID_TICKERS:
        if t.upper() == upper:
            return t
    raise HTTPException(status_code=404, detail=f"Ticker {ticker!r} introuvable dans Mon Portefeuille")


@router.post("/{ticker}/thesis_review_queue")
def post_review_entry(
    ticker: str, req: ReviewQueueSubmission,
    _auth: None = Security(api_core.require_review_or_full_auth),
) -> dict[str, Any]:
    resolved = _resolve_ticker(ticker)
    try:
        entry = add_entry(
            resolved,
            execution_type=req.execution_type,
            findings=[f.model_dump() for f in req.findings],
            proposed_verdict=req.proposed_verdict,
            run_date=req.run_date,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return entry


@router.get("/{ticker}/thesis_review_queue")
def get_review_queue(
    ticker: str, _auth: None = Security(api_core.require_review_or_full_auth),
) -> dict[str, Any]:
    resolved = _resolve_ticker(ticker)
    return {"ticker": resolved, "entries": list_entries(resolved)}


@router.patch("/{ticker}/thesis_review_queue/{entry_id}")
def patch_review_entry_status(
    ticker: str, entry_id: str, req: ReviewQueueStatusUpdate,
    _auth: None = Security(api_core.require_auth),
) -> dict[str, Any]:
    resolved = _resolve_ticker(ticker)
    try:
        return update_entry_status(resolved, entry_id, req.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
