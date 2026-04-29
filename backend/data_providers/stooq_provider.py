"""Stooq.com provider — OHLCV daily 30+ ans, gratuit illimité, pure CSV.

Lot 17 — fallback robuste anti-yfinance-crash. Stooq publie les daily OHLCV
de toutes les actions US/EU depuis 1990+ en CSV téléchargeable. Pas de clé
API, pas de rate limit officiel (politesse : ~1 req/sec).

Endpoint :
    https://stooq.com/q/d/?s={ticker}.us&d1={YYYYMMDD}&d2={YYYYMMDD}&i=d&f=csv

Limites :
  • Tickers US uniquement (suffixe .us automatique).
  • Pas de market_cap / fundamentals — uniquement OHLCV.
  • Latence : daily, ferme T+1 (pas live). Acceptable pour momentum LT.

N'implémente que MarketDataProviderBase (pas FundamentalProviderBase) —
sert de backup quand yf_circuit_breaker est OPEN ou que yfinance retourne
des séries vides.
"""
from __future__ import annotations

import csv
import io
import math
import time
from datetime import datetime, timedelta
from typing import Any
from urllib import error as urlerror
from urllib import request

import pandas as pd

from modules.log import logger

from .base import (
    LatestPrice,
    MarketDataProviderBase,
    ProviderUnavailable,
)

_USER_AGENT = (
    "Mozilla/5.0 (compatible; SwingQuantTITAN/1.0; +https://swingquant.local)"
)
_BASE_URL = "https://stooq.com/q/d/?s={t}.us&d1={d1}&d2={d2}&i=d&f=csv"
_HTTP_TIMEOUT = 10.0
# Stooq tolère des bursts mais bannit IP si on flood. 1 call par 0.6s = ~100/min,
# largement suffisant pour 491 tickers en cron quotidien (~5 min total).
_MIN_DELAY_SECONDS = 0.6


def _safe_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _stooq_ticker(ticker: str) -> str:
    """Stooq utilise '.us' suffix + minuscules. Préserve les '-' pour BRK-B."""
    return ticker.lower().replace(".", "-")


def _fetch_csv(url: str) -> str | None:
    """GET CSV brut. Retourne None si HTTP error / timeout."""
    req = request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            return resp.read().decode("utf-8", errors="ignore")
    except (urlerror.URLError, urlerror.HTTPError, TimeoutError, OSError) as e:
        logger.debug(f"[Stooq] HTTP failed {url[:80]}: {e}")
        return None


def _parse_csv(raw: str) -> pd.Series | None:
    """Parse Stooq CSV → pd.Series indexée DatetimeIndex avec Close ajusté.

    Format Stooq :
        Date,Open,High,Low,Close,Volume
        2024-01-02,148.21,149.13,148.05,148.93,55020900

    Retourne None si CSV vide ou colonnes manquantes.
    """
    if not raw or "Date" not in raw[:50]:
        return None
    reader = csv.DictReader(io.StringIO(raw))
    rows: list[tuple[pd.Timestamp, float]] = []
    for r in reader:
        d = r.get("Date")
        c = _safe_float(r.get("Close"))
        if not d or c is None or c <= 0:
            continue
        try:
            ts = pd.Timestamp(d)
        except (ValueError, TypeError):
            continue
        rows.append((ts, c))
    if not rows:
        return None
    rows.sort(key=lambda x: x[0])
    idx = pd.DatetimeIndex([r[0] for r in rows])
    return pd.Series([r[1] for r in rows], index=idx, name="Close")


class StooqProvider(MarketDataProviderBase):
    """Provider OHLCV daily backup. Pas de batch HTTP — Stooq ne supporte qu'un
    ticker par URL. La pseudo-batch boucle séquentiellement avec délai poli.
    """

    name = "stooq"

    def __init__(self, min_delay_seconds: float = _MIN_DELAY_SECONDS) -> None:
        self._min_delay = max(0.0, float(min_delay_seconds))
        self._last_call: float = 0.0

    def _throttle(self) -> None:
        if self._min_delay <= 0:
            return
        now = time.time()
        wait = self._min_delay - (now - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def get_daily_history(self, ticker: str, days: int) -> pd.Series | None:
        self._throttle()
        end = datetime.utcnow().date()
        start = end - timedelta(days=max(1, days) + 5)
        url = _BASE_URL.format(
            t=_stooq_ticker(ticker),
            d1=start.strftime("%Y%m%d"),
            d2=end.strftime("%Y%m%d"),
        )
        raw = _fetch_csv(url)
        if raw is None:
            return None
        series = _parse_csv(raw)
        if series is None:
            return None
        # Garde tail(days) pour respecter le contrat de longueur.
        return series.tail(days)

    def get_daily_history_batch(
        self, tickers: list[str], days: int,
    ) -> dict[str, pd.Series | None]:
        out: dict[str, pd.Series | None] = {}
        consecutive_failures = 0
        for t in tickers:
            try:
                s = self.get_daily_history(t, days)
            except Exception as e:
                logger.debug(f"[Stooq] {t} fetch crashed: {e}")
                s = None
            out[t] = s
            if s is None or len(s) == 0:
                consecutive_failures += 1
                # Si 10 fails consécutifs → probablement IP bannie / endpoint down.
                # On abort en propageant Unavailable pour que le caller fallback.
                if consecutive_failures >= 10:
                    raise ProviderUnavailable(
                        f"Stooq: 10 consecutive failures, aborting batch ({len(out)}/{len(tickers)} done)"
                    )
            else:
                consecutive_failures = 0
        return out

    def get_latest_prices(
        self, tickers: list[str],
    ) -> dict[str, LatestPrice]:
        """Reprend l'impl par défaut (dernière barre du history)."""
        return super().get_latest_prices(tickers)
