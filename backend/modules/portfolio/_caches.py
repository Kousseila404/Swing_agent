"""Caches mtime-keyed pour prix live + momentum live.

Les prix scorés (`current_price`) et le momentum (`return_pct`, `volatility_pct`)
proviennent d'un run TITAN figé à T-6h voire T-24h — inacceptable pour sizer
quand le prix a bougé de 3 % ou quand un ticker a décalé en intraday
(ex: earnings miss). On refresh juste avant le sizing via les providers injectés.

TTL 5 min : horizon rebalance hebdo/mensuel, sous 5 min le quota provider
(Polygon 5 req/min free tier) est cramé sans gain perceptible.
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from modules.log import logger

if TYPE_CHECKING:
    from data_providers.base import LatestPrice, MarketDataProviderBase


_LATEST_PRICE_TTL_SECONDS = 300.0
_latest_price_cache: dict[
    tuple[str, ...], tuple[float, dict[str, LatestPrice]]
] = {}
_latest_price_cache_lock = threading.Lock()


_MOMENTUM_LIVE_TTL_SECONDS = 300.0
_MOMENTUM_LIVE_DAYS = 180
# Clé = (provider_name, sorted_tickers_tuple) pour éviter des collisions si le
# même processus tourne avec deux providers différents en parallèle.
_momentum_live_cache: dict[
    tuple[str, tuple[str, ...]],
    tuple[float, dict[str, dict[str, float | None]]],
] = {}
_momentum_live_cache_lock = threading.Lock()


def fetch_latest_prices_cached(
    market_provider: MarketDataProviderBase | None,
    tickers: list[str],
) -> dict[str, LatestPrice]:
    """Snapshot des prix live pour `tickers` juste avant sizing.

    Retourne {ticker: LatestPrice} — si pas de provider ou quota provider
    dépassé, dict vide (sizing retombera sur `current_price` scoré).
    Cache 5 min keyé sur tuple trié des tickers.
    """
    if not market_provider or not tickers:
        return {}
    key = tuple(sorted(tickers))
    now = time.time()
    with _latest_price_cache_lock:
        entry = _latest_price_cache.get(key)
        if entry and (now - entry[0]) < _LATEST_PRICE_TTL_SECONDS:
            return entry[1]
    try:
        latest = market_provider.get_latest_prices(list(key))
    except Exception as e:
        # ProviderQuotaExceeded inclus — degrade silencieux (current_price
        # historique évite un crash complet).
        logger.warning(f"[PortfolioManager] live price fetch failed: {e}")
        return {}
    with _latest_price_cache_lock:
        _latest_price_cache[key] = (now, latest)
    return latest


def refresh_momentum_live_cached(
    momentum_provider: MarketDataProviderBase | None,
    tickers: list[str],
) -> dict[str, dict[str, float | None]]:
    """Recharge (return_pct, volatility_pct) via
    `momentum_provider.get_daily_history_batch(days=180)`.

    Retourne {ticker: {return_pct, volatility_pct}} — ne contient PAS les
    tickers pour lesquels le fetch a échoué (le caller retombera sur les
    valeurs figées). Cache 5 min. Key inclut le nom du provider pour ne pas
    mélanger sources.
    """
    if not momentum_provider or not tickers:
        return {}
    key = (momentum_provider.name, tuple(sorted(tickers)))
    now = time.time()
    with _momentum_live_cache_lock:
        entry = _momentum_live_cache.get(key)
        if entry and (now - entry[0]) < _MOMENTUM_LIVE_TTL_SECONDS:
            return entry[1]

    try:
        series_by_ticker = momentum_provider.get_daily_history_batch(
            list(key[1]), days=_MOMENTUM_LIVE_DAYS,
        )
    except Exception as e:
        logger.warning(f"[PortfolioManager] live momentum fetch failed: {e}")
        return {}

    # Délégation à l'implémentation canonique pour éviter la dérive entre
    # sector_metrics (scoring) et portfolio (filtre bullish).
    from modules.sector_metrics import _compute_momentum_stats

    fresh: dict[str, dict[str, float | None]] = {}
    for t in key[1]:
        series = series_by_ticker.get(t)
        if series is None or len(series) < 2:
            continue
        try:
            stats = _compute_momentum_stats(series)
        except Exception as e:
            logger.debug(f"[PortfolioManager] {t} momentum parse fail: {e}")
            continue
        # Accepter si return_pct calculable (vol peut être None pour série
        # courte mais valide → l'imputation vol joue aval).
        if stats.get("return_pct") is not None:
            fresh[t] = stats

    with _momentum_live_cache_lock:
        _momentum_live_cache[key] = (now, fresh)
    return fresh
