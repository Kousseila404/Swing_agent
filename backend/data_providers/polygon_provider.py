"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  PROVIDER — Polygon.io (market data)                                         ║
║                                                                              ║
║  Source pour les prix ajustés (daily) consommés par momentum / SMA / RSI.   ║
║                                                                              ║
║  Endpoints (v2) :                                                            ║
║    • /v2/aggs/ticker/{t}/range/1/day/{from}/{to}?adjusted=true              ║
║                                                                              ║
║  Free tier : 5 requêtes / minute.                                            ║
║    → token-bucket in-memory qui sleep()e si on franchit le cap.             ║
║    → pour un run universe-wide (500 tickers), prévoir ~100 min ou upgrade. ║
║    → en cas de 429, on lève ProviderQuotaExceeded (pas de retry en boucle). ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

from modules.log import logger

from .base import (
    LatestPrice,
    MarketDataProviderBase,
    ProviderQuotaExceeded,
    ProviderUnavailable,
)

_POLY_BASE = "https://api.polygon.io"
_POLY_TIMEOUT = 15.0

# Free tier Polygon : 5 req/min. Les tiers payants levent ça ;
# injecter `rate_limit_per_minute=None` désactive le throttle.
_DEFAULT_RATE_LIMIT_PER_MIN = 5


class PolygonProvider(MarketDataProviderBase):
    """OHLCV ajustés daily via Polygon. Implémente uniquement MarketData."""

    name = "polygon"

    def __init__(
        self,
        api_key: str,
        rate_limit_per_minute: int | None = _DEFAULT_RATE_LIMIT_PER_MIN,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key:
            raise ValueError(
                "PolygonProvider: api_key requis (MARKET_DATA_API_KEY manquante)."
            )
        self._api_key = api_key
        self._rate_limit = rate_limit_per_minute
        self._session = session or requests.Session()

        # Token-bucket simple : deque des timestamps des N derniers appels.
        # Avant chaque call, on trim les > 60 s ; si len >= rate_limit → sleep.
        self._call_lock = threading.Lock()
        self._recent_calls: deque[float] = deque()

    # ── Rate limit (token-bucket 60 s) ───────────────────────────────────

    def _throttle(self) -> None:
        if self._rate_limit is None:
            return
        with self._call_lock:
            now = time.time()
            # Purge les appels > 60 s.
            while self._recent_calls and now - self._recent_calls[0] > 60.0:
                self._recent_calls.popleft()
            if len(self._recent_calls) >= self._rate_limit:
                sleep_for = 60.0 - (now - self._recent_calls[0]) + 0.05
                if sleep_for > 0:
                    logger.debug(f"[Polygon] rate-limit sleep {sleep_for:.1f}s")
                    time.sleep(sleep_for)
                # Re-purge post-sleep.
                now2 = time.time()
                while self._recent_calls and now2 - self._recent_calls[0] > 60.0:
                    self._recent_calls.popleft()
            self._recent_calls.append(time.time())

    # ── HTTP helper ─────────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self._throttle()
        url = f"{_POLY_BASE}{path}"
        merged = {"apiKey": self._api_key, **(params or {})}
        try:
            r = self._session.get(url, params=merged, timeout=_POLY_TIMEOUT)
        except requests.RequestException as e:
            raise ProviderUnavailable(f"[Polygon] network: {e}") from e

        if r.status_code == 429:
            raise ProviderQuotaExceeded(f"[Polygon] HTTP 429 sur {path}")
        if r.status_code in (401, 403):
            raise ProviderUnavailable(f"[Polygon] HTTP {r.status_code} sur {path} — clé invalide ?")
        if r.status_code >= 500:
            raise ProviderUnavailable(f"[Polygon] HTTP {r.status_code} sur {path}")
        if r.status_code != 200:
            logger.warning(f"[Polygon] HTTP {r.status_code} sur {path}: {r.text[:150]}")
            return None
        try:
            return r.json()
        except ValueError:
            return None

    # ── API publique ────────────────────────────────────────────────────

    def get_daily_history(self, ticker: str, days: int) -> pd.Series | None:
        # Polygon attend `from`/`to` en YYYY-MM-DD. On prend un buffer calendaire
        # de ~1.6× days pour couvrir les weekends / jours fériés sans
        # sous-dimensionner la série rendue.
        end = datetime.utcnow().date()
        start = end - timedelta(days=max(1, int(days * 1.6)))
        path = f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}"
        try:
            payload = self._get(path, {"adjusted": "true", "sort": "asc", "limit": 50_000})
        except ProviderQuotaExceeded:
            raise
        except ProviderUnavailable as e:
            logger.warning(f"[Polygon] {ticker} unavailable: {e}")
            return None

        if not isinstance(payload, dict):
            return None
        results = payload.get("results") or []
        if not results:
            return None

        # Polygon retourne `t` en timestamp ms (UNIX), `c` = close ajusté.
        try:
            idx = pd.to_datetime([bar["t"] for bar in results], unit="ms", utc=True)
            closes = [float(bar["c"]) for bar in results]
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"[Polygon] {ticker} parse fail: {e}")
            return None

        series = pd.Series(closes, index=idx, name=ticker).dropna()
        if len(series) > days:
            series = series.iloc[-days:]
        return series if not series.empty else None

    def get_daily_history_batch(
        self, tickers: list[str], days: int,
    ) -> dict[str, pd.Series | None]:
        """
        Boucle sur /v2/aggs/ticker — un ticker par call. Le free tier
        (5/min) rend cette boucle lente pour 500 tickers ; upgrade Polygon
        ou utilisez YFinanceProvider pour le batch massif.
        """
        out: dict[str, pd.Series | None] = {}
        for i, t in enumerate(tickers, start=1):
            try:
                out[t] = self.get_daily_history(t, days)
            except ProviderQuotaExceeded:
                # Breaker métier — on arrête tout, le caller gère.
                raise
            except Exception as e:
                logger.debug(f"[Polygon] {t} batch entry failed: {e}")
                out[t] = None
            if i % 20 == 0:
                logger.info(f"[Polygon] batch progress {i}/{len(tickers)}")
        return out

    # ── Snapshot live (sizing portfolio) ────────────────────────────────
    #
    # /v2/snapshot/locale/us/markets/stocks/tickers accepte une liste CSV
    # de tickers et retourne l'état marché en 1 call. Priorité de choix :
    # 1. lastTrade.p (trade le plus récent, intraday)
    # 2. min.c       (fin de la minute courante, intraday)
    # 3. day.c       (close du jour, ≤ 15 min de décalage)
    # 4. prevDay.c   (close de la veille, fallback)
    #
    # Consomme 1 seul "request" au token-bucket (5/min free tier) peu importe
    # le nombre de tickers demandés → idéal pour /api/portfolio/recommendations.
    def get_latest_prices(
        self, tickers: list[str],
    ) -> dict[str, LatestPrice]:
        import time as _time
        now_epoch = _time.time()

        # Sortie par défaut : price=None pour tous (degrade silencieux si l'appel foire).
        out: dict[str, LatestPrice] = {
            t: LatestPrice(ticker=t, price=None, fetched_at_epoch=now_epoch,
                           source=self.name, as_of=None)
            for t in tickers
        }
        if not tickers:
            return out

        # Polygon accepte plusieurs tickers via ?tickers=AAPL,MSFT,...
        # En pratique >200 symboles par call rencontrent parfois une 414 ;
        # on chunk défensivement à 150 — reste 1 call pour S&P500 en 4 chunks.
        CHUNK = 150
        path = "/v2/snapshot/locale/us/markets/stocks/tickers"
        for i in range(0, len(tickers), CHUNK):
            chunk = tickers[i:i + CHUNK]
            try:
                payload = self._get(path, {"tickers": ",".join(chunk)})
            except ProviderQuotaExceeded:
                raise
            except ProviderUnavailable as e:
                logger.warning(f"[Polygon] snapshot batch unavailable: {e}")
                continue
            if not isinstance(payload, dict):
                continue
            for entry in payload.get("tickers") or []:
                if not isinstance(entry, dict):
                    continue
                t = entry.get("ticker")
                if not isinstance(t, str):
                    continue
                price: float | None = None
                as_of_ns: float | None = None
                # Priorité intraday → daily → prevDay.
                last_trade = entry.get("lastTrade") or {}
                minute_bar = entry.get("min") or {}
                day_bar = entry.get("day") or {}
                prev_bar = entry.get("prevDay") or {}
                for candidate, ts_field in (
                    (last_trade.get("p"), last_trade.get("t")),
                    (minute_bar.get("c"), minute_bar.get("t")),
                    (day_bar.get("c"),    day_bar.get("t")),
                    (prev_bar.get("c"),   prev_bar.get("t")),
                ):
                    try:
                        pf = float(candidate) if candidate is not None else None
                    except (TypeError, ValueError):
                        pf = None
                    if pf is not None and pf > 0:
                        price = pf
                        as_of_ns = ts_field if isinstance(ts_field, (int, float)) else None
                        break
                as_of_iso: str | None = None
                if as_of_ns:
                    try:
                        # Polygon lastTrade.t = ns, day.t/min.t/prevDay.t = ms.
                        # Heuristique : > 1e15 ⇒ nanoseconds ; sinon ms.
                        ts_sec = float(as_of_ns) / 1e9 if as_of_ns > 1e15 else float(as_of_ns) / 1e3
                        as_of_iso = datetime.utcfromtimestamp(ts_sec).strftime("%Y-%m-%dT%H:%M:%SZ")
                    except Exception:
                        as_of_iso = None
                out[t] = LatestPrice(
                    ticker=t, price=price, fetched_at_epoch=now_epoch,
                    source=self.name, as_of=as_of_iso,
                )
        return out
