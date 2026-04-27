"""Accès aux prix de marché pour le tracker.

Stratégie adaptative :
  - Si BROKER_MODE=alpaca : Alpaca Data API (temps réel, pas de délai).
  - Sinon pendant heures de marché US : yfinance intraday (1m, ~15 min retard).
  - Sinon : yfinance EOD (dernière clôture officielle).
"""
from __future__ import annotations

from datetime import datetime

import yfinance as yf

import config

from .state import FETCH_TIMEOUT, logger


def is_market_hours() -> bool:
    """Retourne True si les marchés US sont actuellement ouverts
    (lun–ven, 09h30–16h00 heure de New York)."""
    try:
        import pytz
        ny  = pytz.timezone("America/New_York")
        now = datetime.now(ny)
        return (
            now.weekday() < 5
            and (now.hour > 9 or (now.hour == 9 and now.minute >= 30))
            and now.hour < 16
        )
    except Exception:
        return False


def get_current_price(ticker: str) -> float | None:
    """Retourne le prix le plus récent disponible pour un ticker.

    Fallback automatique sur EOD si l'appel intraday échoue.
    Retourne None si toutes les tentatives échouent — trade reste OPEN.
    """
    # BROKER_MODE=alpaca → Alpaca Data API (temps réel, pas de délai)
    if getattr(config, "BROKER_MODE", "paper").lower() == "alpaca":
        try:
            from modules.alpaca_data import get_latest_price
            price = get_latest_price(ticker)
            if price is not None and price > 0:
                logger.debug(f"[{ticker}] Prix Alpaca : {price:.4f}")
                return price
        except Exception as _ae:
            logger.debug(f"[{ticker}] Alpaca price fallback yfinance : {_ae}")

    intraday = is_market_hours()
    try:
        if intraday:
            hist = yf.Ticker(ticker).history(
                period="1d", interval="1m", timeout=FETCH_TIMEOUT
            )
        else:
            hist = yf.Ticker(ticker).history(period="1d", timeout=FETCH_TIMEOUT)

        if not hist.empty:
            return float(hist["Close"].iloc[-1])

        logger.warning(f"[{ticker}] Aucune donnée de prix disponible.")
        return None

    except Exception as exc:
        if intraday:
            try:
                hist = yf.Ticker(ticker).history(period="1d", timeout=FETCH_TIMEOUT)
                if not hist.empty:
                    logger.debug(f"[{ticker}] Fallback EOD après échec intraday")
                    return float(hist["Close"].iloc[-1])
            except Exception:
                pass
        logger.error(f"[{ticker}] Erreur yfinance : {exc}")
        return None
