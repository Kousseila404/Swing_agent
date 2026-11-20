"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — COMPTE eTORO RÉEL (lecture seule)                      ║
║  GET  /api/etoro/overview   snapshot + actions + réconciliation  ║
║  POST /api/etoro/snapshot   ingère un « portfolio summary »      ║
║  POST /api/etoro/sync       relit le compte via l'API publique   ║
║                                                                  ║
║  Aucun ordre eToro n'est jamais passé depuis l'application.      ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Security

from modules import api_core, etoro_sync
from modules.log import logger

router = APIRouter(prefix="/api/etoro", tags=["etoro"])


@router.get("/overview")
def get_overview(_auth: None = Security(api_core.require_review_or_full_auth)) -> dict[str, Any]:
    return etoro_sync.build_overview()


@router.post("/snapshot")
def post_snapshot(
    payload: dict[str, Any] = Body(...),
    _auth: None = Security(api_core.require_auth),
) -> dict[str, Any]:
    try:
        snap = etoro_sync.normalize_summary(payload, source=str(payload.get("source") or "connector"))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    etoro_sync.save_snapshot(snap)
    return {"ok": True, "as_of": snap["as_of"], "split": snap["split"]}


@router.post("/sync")
def post_sync(_auth: None = Security(api_core.require_auth)) -> dict[str, Any]:
    if not etoro_sync.api_configured():
        raise HTTPException(status_code=409, detail="ETORO_API_KEY / ETORO_USER_KEY non configurés")
    try:
        snap = etoro_sync.fetch_snapshot_from_api()
    except Exception as e:
        logger.error(f"[etoro] synchro échouée: {e}")
        raise HTTPException(status_code=502, detail=f"API eToro: {e}") from e
    etoro_sync.save_snapshot(snap)
    return {"ok": True, "as_of": snap["as_of"], "split": snap["split"]}
