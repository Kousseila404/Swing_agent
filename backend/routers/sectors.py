"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — SECTORS (Rotation Sectorielle Quantamental, 11 GICS)  ║
║  GET /api/sectors         /api/sectors/{sector}                  ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials

from modules import api_core
from modules.log import logger

router = APIRouter(prefix="/api", tags=["sectors"])


@router.get("/sectors")
def get_sectors(
    refresh_momentum: bool = False,
    credentials: HTTPAuthorizationCredentials | None = Security(api_core.bearer_scheme),
):
    """
    Liste des 11 secteurs GICS enrichis :
      • Agrégats robustes (médianes/std) depuis universe.json
      • Distribution des recommandations analystes
      • Top-3 tickers par upside et market cap
      • Momentum 6M via ETF sectoriel SPDR (cache 24h)
      • Rotation Score = 0.4·valuation + 0.3·sentiment + 0.3·upside
        (composantes normalisées min-max cross-sector, scaled 0-100)

    Query params :
      refresh_momentum=true  → force re-download yfinance (bypass cache).
                               Requiert Authorization: Bearer (anti-ban IP).
    """
    # Lecture du cache = public. Bypass cache (yfinance batch) = auth requis,
    # sinon un visiteur anonyme peut flooder yf.download → ban IP serveur.
    if refresh_momentum:
        api_core.check_auth(credentials)
    try:
        from modules.sector_metrics import compute_all
        return compute_all(force_refresh_momentum=bool(refresh_momentum))
    except Exception as e:
        logger.error(f"[API /sectors] compute_all failed: {e}", exc_info=True)
        raise HTTPException(500, f"Agrégation sectorielle impossible: {e}") from e


@router.get("/sectors/{sector}")
def get_sector_detail(sector: str):
    """
    Détail d'un secteur : stats agrégées + tous ses tickers enrichis
    (upside_pct, reco_bucket) pour un rendu direct dans la vue drill-down.
    """
    try:
        from modules.sector_metrics import compute_sector_detail
        result = compute_sector_detail(sector)
    except Exception as e:
        logger.error(f"[API /sectors/{sector}] failed: {e}", exc_info=True)
        raise HTTPException(500, f"Détail secteur impossible: {e}") from e
    if result is None:
        raise HTTPException(404, f"Secteur inconnu: {sector!r}")
    return result
