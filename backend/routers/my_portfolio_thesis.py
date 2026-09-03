"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — THÈSE STRUCTURÉE MON PORTEFEUILLE                       ║
║  PATCH /api/my_portfolio/{ticker}/thesis                          ║
╚══════════════════════════════════════════════════════════════════╝

Édition des 3 blocs (why_bought / sell_signals / verification) documentés
dans `modules/my_portfolio_thesis.py`. Lecture : les champs sont déjà
mergés dans chaque ligne de `GET /api/my_portfolio` (routers/my_portfolio.py)
— pas de GET séparé ici, pour ne pas dupliquer la source de vérité.

Écriture éditoriale pure — voir la contrainte non négociable en tête de
`modules/my_portfolio_thesis.py` : ce router ne fait que valider la forme
du body et déléguer la persistance, jamais calculer/déduire un statut.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Security
from pydantic import BaseModel

from modules import api_core
from modules.my_portfolio_data import POSITIONS
from modules.my_portfolio_thesis import update_thesis

router = APIRouter(prefix="/api/my_portfolio", tags=["my_portfolio"])

_VALID_TICKERS = {p["ticker"] for p in POSITIONS}


class WhyBoughtPatch(BaseModel):
    catalyseurs: list[str] | None = None
    valorisation: str | None = None
    role_portefeuille: str | None = None


class SellSignalPatch(BaseModel):
    id: str | None = None
    libelle: str
    statut: str
    note: str | None = None
    date_maj: str | None = None


class VerificationPatch(BaseModel):
    derniere_verification: str
    verdict: str


class ThesisPatchRequest(BaseModel):
    why_bought: WhyBoughtPatch | None = None
    sell_signals: list[SellSignalPatch] | None = None
    verification: VerificationPatch | None = None


def _resolve_ticker(ticker: str) -> str:
    """Résout la casse d'entrée vers le ticker canonique de POSITIONS
    (même convention que `/my_portfolio/{ticker}/price_history`)."""
    upper = ticker.upper()
    for t in _VALID_TICKERS:
        if t.upper() == upper:
            return t
    raise HTTPException(status_code=404, detail=f"Ticker {ticker!r} introuvable dans Mon Portefeuille")


@router.patch("/{ticker}/thesis")
def patch_thesis(
    ticker: str, req: ThesisPatchRequest, _auth: None = Security(api_core.require_auth),
) -> dict[str, Any]:
    resolved = _resolve_ticker(ticker)

    if req.why_bought is None and req.sell_signals is None and req.verification is None:
        raise HTTPException(status_code=400, detail="Au moins un bloc (why_bought/sell_signals/verification) requis")

    why_bought = req.why_bought.model_dump(exclude_unset=True) if req.why_bought is not None else None
    sell_signals = (
        [s.model_dump() for s in req.sell_signals] if req.sell_signals is not None else None
    )
    verification = req.verification.model_dump() if req.verification is not None else None

    try:
        updated = update_thesis(
            resolved, why_bought=why_bought, sell_signals=sell_signals, verification=verification,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {"ticker": resolved, **updated}
