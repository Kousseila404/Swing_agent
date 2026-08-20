"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — MON PORTEFEUILLE (book personnel, hors univers TITAN)  ║
║  GET  /api/my_portfolio                                          ║
║                                                                  ║
║  10 positions long terme statiques (voir modules/my_portfolio_   ║
║  data.py) + prix live pour calculer le poids réel et détecter la ║
║  dérive vs poids cible (rééquilibrage).                          ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Security

from modules import api_core
from modules.log import logger
from modules.my_portfolio_data import (
    CASH_RESERVE_AMOUNT,
    CASH_RESERVE_PCT,
    POSITIONS,
    REBALANCE_DRIFT_THRESHOLD_PCT,
    WATCHLIST,
)
from modules.tracker.market import get_current_price

router = APIRouter(prefix="/api", tags=["my_portfolio"])


def _safe_price(ticker: str) -> float | None:
    try:
        return get_current_price(ticker)
    except Exception as exc:
        logger.warning(f"[my_portfolio] Prix indisponible pour {ticker}: {exc}")
        return None


@router.get("/my_portfolio")
def get_my_portfolio(_auth: None = Security(api_core.require_auth)) -> dict[str, Any]:
    tickers = [p["ticker"] for p in POSITIONS]
    with ThreadPoolExecutor(max_workers=max(1, len(tickers))) as ex:
        prices = dict(zip(tickers, ex.map(_safe_price, tickers), strict=True))

    rows: list[dict[str, Any]] = []
    for p in POSITIONS:
        shares = p["shares"]
        price  = prices.get(p["ticker"])

        if shares <= 0:
            # Pas encore acheté (ex: LNVGY en attente earnings) → 0 réel,
            # pas de fallback sur le montant cible.
            current_value, price_stale = 0.0, False
        elif price is not None:
            current_value, price_stale = shares * price, False
        else:
            # Prix live indisponible → fallback sur le montant cible pour
            # ne pas fausser le total (mieux qu'une position à 0$).
            current_value, price_stale = p["target_amount"], True

        rows.append({
            **p,
            "current_price": price,
            "current_value": round(current_value, 2),
            "price_stale":   price_stale,
            "is_pending":    shares <= 0,
        })

    total_value = sum(r["current_value"] for r in rows) + CASH_RESERVE_AMOUNT

    for r in rows:
        real_weight = (r["current_value"] / total_value * 100) if total_value else 0.0
        target = r["target_weight_pct"]
        # Pas de dérive calculée pour une position pas encore ouverte —
        # le badge "en attente" couvre déjà ce cas.
        if target and not r["is_pending"]:
            drift_pct = (real_weight - target) / target * 100
        else:
            drift_pct = None
        r["real_weight_pct"]   = round(real_weight, 2)
        r["drift_pct"]         = round(drift_pct, 1) if drift_pct is not None else None
        r["rebalance_alert"]   = drift_pct is not None and abs(drift_pct) > REBALANCE_DRIFT_THRESHOLD_PCT

    cash_weight = (CASH_RESERVE_AMOUNT / total_value * 100) if total_value else 0.0

    return {
        "positions": rows,
        "cash_reserve": {
            "target_weight_pct": CASH_RESERVE_PCT,
            "amount":            CASH_RESERVE_AMOUNT,
            "real_weight_pct":   round(cash_weight, 2),
        },
        "watchlist":           WATCHLIST,
        "total_value":         round(total_value, 2),
        "drift_threshold_pct": REBALANCE_DRIFT_THRESHOLD_PCT,
    }
