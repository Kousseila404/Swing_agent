"""Accès aux prix de marché pour le tracker.

Stratégie adaptative :
  - Si BROKER_MODE=alpaca : Alpaca Data API (temps réel, pas de délai).
  - Sinon pendant heures de marché US : yfinance intraday (1m, ~15 min retard).
  - Sinon : yfinance EOD (dernière clôture officielle).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import yfinance as yf

import config

from .state import FETCH_TIMEOUT, logger

# Cache mémoire des taux de change (5 min) — évite de marteler yfinance à
# chaque requête dashboard pour une donnée qui ne bouge pas seconde par
# seconde. Contrairement aux prix actions (aucune couche de cache, voir
# get_current_price_detailed), un taux FX stale de quelques minutes est
# un compromis acceptable.
_FX_CACHE: dict[str, tuple[float, float]] = {}  # pair -> (rate, fetched_epoch)
_FX_CACHE_TTL_SECONDS = 300.0


def get_fx_rate(pair: str) -> float | None:
    """Taux de change live pour une paire yfinance (ex: "EURUSD=X", "USDHKD=X").

    Retourne le dernier taux connu (même expiré) si le fetch échoue, plutôt
    que None, pour éviter de casser l'affichage sur un simple hoquet réseau.
    """
    now = time.time()
    cached = _FX_CACHE.get(pair)
    if cached is not None and (now - cached[1]) < _FX_CACHE_TTL_SECONDS:
        return cached[0]

    try:
        hist = yf.Ticker(pair).history(period="1d", timeout=FETCH_TIMEOUT)
        if not hist.empty:
            rate = float(hist["Close"].iloc[-1])
            _FX_CACHE[pair] = (rate, now)
            return rate
        logger.warning(f"[FX] Aucune donnée pour {pair}.")
    except Exception as exc:
        logger.warning(f"[FX] Erreur taux {pair} : {exc}")

    return cached[0] if cached is not None else None


def is_market_hours() -> bool:
    """Retourne True si les marchés US sont actuellement ouverts.

    Phase 7 audit (2026-05-06) — robustesse DST + jours fériés US :
      1. Si AlpacaBroker disponible et configuré → délègue à `clock.is_open`
         (autorité officielle, gère DST + holidays + half-days).
      2. Sinon : heuristique zoneinfo (pas pytz, qui devine mal le DST gap).
         Lun-ven 09:30-16:00 NY. NOTE : ne gère PAS les jours fériés US, ne
         gère PAS les half-days (close 13:00 veille de Noël/Thanksgiving).
         Pour ces cas-là, configurer Alpaca.
    """
    # 1) Source de vérité : Alpaca clock si dispo
    try:
        from modules.broker_gateway import AlpacaBroker, get_broker
        broker = get_broker()
        if isinstance(broker, AlpacaBroker):
            client = broker._get_client()
            clock = client.get_clock()
            return bool(clock.is_open)
    except Exception:
        pass  # fallback heuristique

    # 2) Heuristique zoneinfo (Python 3.9+, gère DST natif)
    try:
        try:
            from zoneinfo import ZoneInfo  # py3.9+
        except ImportError:
            import pytz as _pytz
            ZoneInfo = _pytz.timezone  # type: ignore[misc,assignment]  # noqa: N806  (fallback py<3.9)

        ny = ZoneInfo("America/New_York")
        now = datetime.now(ny)
        if now.weekday() >= 5:
            return False
        # Open : 09:30 → close : 16:00 (heure NY)
        minutes_since_midnight = now.hour * 60 + now.minute
        return 9 * 60 + 30 <= minutes_since_midnight < 16 * 60
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
