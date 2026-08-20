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
    DEPLOYMENT_THRESHOLD_PCT,
    POSITIONS,
    REBALANCE_DRIFT_THRESHOLD_PCT,
    TOTAL_ENVELOPE_AMOUNT,
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

        # P&L — axe de tracking séparé du poids/dérive : "combien j'ai
        # gagné/perdu vs mon prix d'entrée réel", indépendant de l'allocation
        # cible. Nécessite un prix d'entrée connu (None pour LNVGY, pas
        # encore ouverte) et un prix live valide (pas de fallback stale).
        entry_price = p["entry_price"]
        if entry_price and shares > 0 and price is not None:
            pnl_usd = (price - entry_price) * shares
            pnl_pct = (price / entry_price - 1) * 100
        else:
            pnl_usd, pnl_pct = None, None

        rows.append({
            **p,
            "current_price": price,
            "current_value": round(current_value, 2),
            "price_stale":   price_stale,
            "is_pending":    shares <= 0,
            "pnl_usd":       round(pnl_usd, 2) if pnl_usd is not None else None,
            "pnl_pct":       round(pnl_pct, 1) if pnl_pct is not None else None,
        })

    # Valeur actuelle réelle du book — purement informative (tuile "Valeur
    # totale"). NE DOIT JAMAIS servir de dénominateur au poids réel : cette
    # somme varie avec le déploiement des positions (PSX/LNVGY pas encore
    # pleinement investies), ce qui gonflerait artificiellement le poids
    # réel de toutes les autres lignes.
    total_current_value = sum(r["current_value"] for r in rows) + CASH_RESERVE_AMOUNT

    # P&L global — somme des P&L $ des lignes ouvertes avec un prix d'entrée
    # connu (LNVGY exclue : pas de position, pas de P&L calculable).
    total_pnl_usd = sum(r["pnl_usd"] for r in rows if r["pnl_usd"] is not None)

    for r in rows:
        # Dénominateur FIXE — enveloppe totale $2000, jamais la somme
        # variable des valeurs actuellement investies (voir commentaire
        # TOTAL_ENVELOPE_AMOUNT dans my_portfolio_data.py).
        real_weight = r["current_value"] / TOTAL_ENVELOPE_AMOUNT * 100
        target = r["target_weight_pct"]
        target_amount = r["target_amount"]

        # % du montant cible effectivement déployé (prix live × shares vs
        # target_amount). LNVGY à 0 part (0%) est le cas extrême de cette
        # même logique — pas un cas à part.
        deployment_pct = (r["current_value"] / target_amount * 100) if target_amount else 100.0
        is_deploying = deployment_pct < DEPLOYMENT_THRESHOLD_PCT

        # Une dérive n'a de sens que sur une position pleinement déployée :
        # sous le seuil de déploiement, un écart au poids cible reflète un
        # DCA pas terminé, pas un besoin de rééquilibrage.
        if target and not is_deploying:
            drift_pct = (real_weight - target) / target * 100
        else:
            drift_pct = None

        r["real_weight_pct"]  = round(real_weight, 2)
        r["deployment_pct"]   = round(deployment_pct, 1)
        r["is_deploying"]     = is_deploying
        r["drift_pct"]        = round(drift_pct, 1) if drift_pct is not None else None
        r["rebalance_alert"]  = drift_pct is not None and abs(drift_pct) > REBALANCE_DRIFT_THRESHOLD_PCT

    cash_weight = CASH_RESERVE_AMOUNT / TOTAL_ENVELOPE_AMOUNT * 100

    return {
        "positions": rows,
        "cash_reserve": {
            "target_weight_pct": CASH_RESERVE_PCT,
            "amount":            CASH_RESERVE_AMOUNT,
            "real_weight_pct":   round(cash_weight, 2),
        },
        "watchlist":              WATCHLIST,
        "total_value":            round(total_current_value, 2),
        "total_pnl_usd":          round(total_pnl_usd, 2),
        "drift_threshold_pct":    REBALANCE_DRIFT_THRESHOLD_PCT,
        "deployment_threshold_pct": DEPLOYMENT_THRESHOLD_PCT,
    }
