"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — WATCHLIST + NOTES                                      ║
║  GET  /api/watchlist            POST /api/watchlist (ajout/upsert)║
║  DELETE /api/watchlist/{ticker}                                  ║
║  GET  /api/notes/{ticker}       POST /api/notes/{ticker} (créer) ║
║  PUT  /api/notes/{note_id}      DELETE /api/notes/{note_id}      ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Security
from pydantic import BaseModel, Field

from modules import api_core
from modules import price_alerts as pa_mod
from modules import titan_alerts as ta_mod
from modules import watchlist as wl_mod
from modules.log import logger

router = APIRouter(prefix="/api", tags=["watchlist"])


# ─── Pydantic ────────────────────────────────────────────────────

class WatchlistAddRequest(BaseModel):
    ticker:     str
    tag:        str | None = None
    target_buy: float | None = None
    comment:    str | None = None


class NoteCreateRequest(BaseModel):
    body: str = Field(..., min_length=1)


class NoteUpdateRequest(BaseModel):
    body: str = Field(..., min_length=1)


# ─── WATCHLIST ───────────────────────────────────────────────────

@router.get("/watchlist")
def get_watchlist() -> dict[str, Any]:
    try:
        items = wl_mod.list_watchlist()
        notes_count = wl_mod.count_notes_by_ticker()
        for it in items:
            it["n_notes"] = notes_count.get(it["ticker"], 0)
        return {"items": items, "count": len(items)}
    except Exception as exc:
        logger.error(f"[watchlist] GET failed: {exc}")
        raise HTTPException(status_code=503, detail="Watchlist storage error") from exc


@router.post("/watchlist")
def post_watchlist(
    req: WatchlistAddRequest,
    _auth: None = Security(api_core.require_auth),
) -> dict[str, Any]:
    try:
        item = wl_mod.add_to_watchlist(
            ticker=req.ticker,
            tag=req.tag,
            target_buy=req.target_buy,
            comment=req.comment,
        )
        return {"ok": True, "item": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"[watchlist] POST failed: {exc}")
        raise HTTPException(status_code=503, detail="Watchlist storage error") from exc


@router.delete("/watchlist/{ticker}")
def delete_watchlist(
    ticker: str,
    _auth: None = Security(api_core.require_auth),
) -> dict[str, Any]:
    try:
        removed = wl_mod.remove_from_watchlist(ticker)
        return {"ok": True, "removed": removed}
    except Exception as exc:
        logger.error(f"[watchlist] DELETE failed: {exc}")
        raise HTTPException(status_code=503, detail="Watchlist storage error") from exc


# ─── NOTES ───────────────────────────────────────────────────────

@router.get("/notes/{ticker}")
def get_notes(ticker: str) -> dict[str, Any]:
    try:
        notes = wl_mod.list_notes(ticker)
        return {"ticker": ticker.upper().strip(), "notes": notes, "count": len(notes)}
    except Exception as exc:
        logger.error(f"[notes] GET failed: {exc}")
        raise HTTPException(status_code=503, detail="Notes storage error") from exc


@router.post("/notes/{ticker}")
def post_note(
    ticker: str,
    req: NoteCreateRequest,
    _auth: None = Security(api_core.require_auth),
) -> dict[str, Any]:
    try:
        note = wl_mod.add_note(ticker, req.body)
        return {"ok": True, "note": note}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"[notes] POST failed: {exc}")
        raise HTTPException(status_code=503, detail="Notes storage error") from exc


@router.put("/notes/{note_id}")
def put_note(
    note_id: str,
    req: NoteUpdateRequest,
    _auth: None = Security(api_core.require_auth),
) -> dict[str, Any]:
    try:
        note = wl_mod.update_note(note_id, req.body)
        if note is None:
            raise HTTPException(status_code=404, detail="Note introuvable")
        return {"ok": True, "note": note}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[notes] PUT failed: {exc}")
        raise HTTPException(status_code=503, detail="Notes storage error") from exc


@router.delete("/notes/{note_id}")
def delete_note_endpoint(
    note_id: str,
    _auth: None = Security(api_core.require_auth),
) -> dict[str, Any]:
    try:
        ok = wl_mod.delete_note(note_id)
        return {"ok": True, "deleted": ok}
    except Exception as exc:
        logger.error(f"[notes] DELETE failed: {exc}")
        raise HTTPException(status_code=503, detail="Notes storage error") from exc


# ─── TITAN ALERTS — seuils utilisateur (Tier B #3) ──────────────

class TitanAlertCreate(BaseModel):
    ticker:    str
    direction: str = Field(default="above", pattern="^(above|below)$")
    threshold: float = Field(..., ge=0, le=100)
    note:      str | None = Field(default=None, max_length=200)


@router.get("/titan_alerts")
def get_titan_alerts() -> dict[str, Any]:
    items = ta_mod.list_alerts()
    return {"items": items, "count": len(items)}


@router.post("/titan_alerts")
def post_titan_alert(
    req: TitanAlertCreate,
    _auth: None = Security(api_core.require_auth),
) -> dict[str, Any]:
    try:
        item = ta_mod.add_alert(
            ticker=req.ticker,
            direction=req.direction,
            threshold=req.threshold,
            note=req.note,
        )
        return {"ok": True, "alert": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/titan_alerts/{alert_id}")
def delete_titan_alert(
    alert_id: str,
    _auth: None = Security(api_core.require_auth),
) -> dict[str, Any]:
    removed = ta_mod.remove_alert(alert_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Alert introuvable")
    return {"ok": True, "removed": True}


# ─── PRICE ALERTS — seuils prix multi-niveaux (entry_plan tiers) ───

class PriceAlertCreate(BaseModel):
    ticker:       str
    target_price: float = Field(..., gt=0)
    direction:    str = Field(default="below", pattern="^(below|above)$")
    weight_pct:   float | None = Field(default=None, ge=0, le=100)
    note:         str | None = Field(default=None, max_length=200)
    ttl_days:     int | None = Field(default=None, ge=1, le=365)


@router.get("/price_alerts")
def get_price_alerts(ticker: str | None = None) -> dict[str, Any]:
    items = pa_mod.list_alerts(ticker=ticker, include_expired=True)
    return {"items": items, "count": len(items)}


@router.get("/price_alerts/stats")
def get_price_alerts_stats() -> dict[str, Any]:
    """Stats agrégées : hit rate, median delay-to-fire, median discount capté.

    Permet de juger l'efficacité du wait-pullback : si hit_rate < 30 % et
    median_days_to_fire élevé, les targets sont peut-être trop ambitieuses.
    """
    return pa_mod.compute_stats()


@router.post("/price_alerts")
def post_price_alert(
    req: PriceAlertCreate,
    _auth: None = Security(api_core.require_auth),
) -> dict[str, Any]:
    try:
        item = pa_mod.add_alert(
            ticker=req.ticker,
            target_price=req.target_price,
            direction=req.direction,
            weight_pct=req.weight_pct,
            note=req.note,
            ttl_days=req.ttl_days,
        )
        return {"ok": True, "alert": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/price_alerts/{alert_id}")
def delete_price_alert(
    alert_id: str,
    _auth: None = Security(api_core.require_auth),
) -> dict[str, Any]:
    removed = pa_mod.remove_alert(alert_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Alert introuvable")
    return {"ok": True, "removed": True}
