"""
Data providers pour le moteur TITAN.

Usage typique (dans universe_engine / sector_metrics) :

    from data_providers import get_providers

    fundamental, market = get_providers()
    engine = UniverseEngine(fundamental_provider=fundamental, market_provider=market)

La factory lit FMP_API_KEY et MARKET_DATA_API_KEY dans l'environnement ;
à défaut, retombe sur YFinanceProvider (double rôle).
"""
from __future__ import annotations

import os

from modules.log import logger

from ._fallback import FallbackFundamentalProvider
from ._market_fallback import FallbackMarketProvider
from .base import (
    FinancialRatios,
    FundamentalProviderBase,
    LatestPrice,
    MarketDataProviderBase,
    ProviderError,
    ProviderQuotaExceeded,
    ProviderUnavailable,
)
from .fmp_provider import FMPProvider
from .polygon_provider import PolygonProvider
from .stooq_provider import StooqProvider
from .yfinance_provider import YFinanceProvider

__all__ = [
    "FallbackFundamentalProvider",
    "FallbackMarketProvider",
    "FinancialRatios",
    "FundamentalProviderBase",
    "LatestPrice",
    "MarketDataProviderBase",
    "ProviderError",
    "ProviderQuotaExceeded",
    "ProviderUnavailable",
    "FMPProvider",
    "PolygonProvider",
    "StooqProvider",
    "YFinanceProvider",
    "get_providers",
]


def _is_truthy_env(name: str, default: bool) -> bool:
    """Lit un env var en mode truthy. Vide ou absent → default. 'false/0/no' → False."""
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"false", "0", "no", "off", "disabled"}:
        return False
    return True


# Module-level sentinel pour ne loguer la config provider qu'une seule fois
# par processus (évite le log spam ~1 ligne/requête API observé en prod).
_PROVIDERS_LOGGED = False


def _log_config_once(*lines: str) -> None:
    """Log les lignes de configuration une seule fois par process."""
    global _PROVIDERS_LOGGED
    if _PROVIDERS_LOGGED:
        return
    for ln in lines:
        logger.info(ln)
    _PROVIDERS_LOGGED = True


def get_providers() -> tuple[FundamentalProviderBase, MarketDataProviderBase]:
    """
    Résout la paire (fondamental, market data) à partir de l'environnement.

    ── Lot 11 (audit data fetch) — changements :
      • `FMP_ENABLED=false` (default) skip FMPProvider même si la clé est set,
        car FMP free tier ne fournit que `/profile` (premium 402 sur les
        endpoints scoring). yfinance fait 100% du boulot, FMP gaspille la quota.
        Mettre `FMP_ENABLED=true` explicitement ssi tu as un tier payant.
      • Le fundamental provider est wrappé par `CachedFundamentalProvider`
        (TTL 24h + stale fallback) → divise par 5-10 les appels API en
        usage normal.

    Règles historiques préservées :
      - Si MARKET_DATA_API_KEY présent → PolygonProvider, sinon yfinance.
      - L'instance yfinance est partagée fundamental ↔ market pour factoriser
        le circuit-breaker yf_breaker (un seul état process-wide).
    """
    from modules.fundamentals_cache import CachedFundamentalProvider

    fmp_key    = (os.getenv("FMP_API_KEY") or "").strip() or None
    fmp_enabled = _is_truthy_env("FMP_ENABLED", default=False)
    market_key = (os.getenv("MARKET_DATA_API_KEY") or "").strip() or None

    # Instance yfinance partagée — créée seulement si au moins un rôle la réclame.
    yf_shared: YFinanceProvider | None = None

    if fmp_key and fmp_enabled:
        yf_shared = YFinanceProvider()
        base_fundamental: FundamentalProviderBase = FallbackFundamentalProvider(
            primary=FMPProvider(fmp_key),
            fallback=yf_shared,
        )
        _log_config_once(
            "[data_providers] Fundamental → FMP (primary, FMP_ENABLED=true) "
            "+ YFinance (fallback) + cache 24h"
        )
    elif fmp_key and not fmp_enabled:
        yf_shared = YFinanceProvider()
        base_fundamental = yf_shared
        _log_config_once(
            "[data_providers] Fundamental → YFinance (FMP_API_KEY présent mais "
            "FMP_ENABLED=false → free tier inutile, économise quota). "
            "Set FMP_ENABLED=true si tier payant. + cache 24h"
        )
    else:
        yf_shared = YFinanceProvider()
        base_fundamental = yf_shared
        _log_config_once(
            "[data_providers] FMP_API_KEY absent — YFinance pour le fondamental + cache 24h."
        )

    # Lot 11 — wrap systématique avec cache disque 24h + stale fallback.
    fundamental: FundamentalProviderBase = CachedFundamentalProvider(base_fundamental)

    if market_key:
        # Polygon free tier limitations :
        #   • /v2/snapshot : bloqué 403 → POLYGON_SNAPSHOT_ENABLED=false par default
        #   • /v2/aggs/ticker : OK mais 5/min + per-ticker = 100 min pour 500
        #     tickers. yfinance.download multi-ticker fait le même travail en 1
        #     call HTTP → POLYGON_DAILY_BATCH_ENABLED=false par default.
        # Polygon reste utile pour les calls unitaires get_daily_history().
        yf_shared = yf_shared or YFinanceProvider()
        poly_snapshot = _is_truthy_env("POLYGON_SNAPSHOT_ENABLED", default=False)
        poly_daily_batch = _is_truthy_env("POLYGON_DAILY_BATCH_ENABLED", default=False)
        market: MarketDataProviderBase = FallbackMarketProvider(
            primary=PolygonProvider(market_key),
            fallback=yf_shared,
            use_primary_for_snapshot=poly_snapshot,
            use_primary_for_daily_batch=poly_daily_batch,
        )
        snapshot_src = "Polygon" if poly_snapshot else "YFinance direct"
        batch_src = "Polygon (5/min, slow)" if poly_daily_batch else "YFinance batch"
        _log_config_once(
            f"[data_providers] Market data → snapshot: {snapshot_src} · "
            f"daily batch: {batch_src} · Polygon unitaire dispo en fallback"
        )
    else:
        yf_shared = yf_shared or YFinanceProvider()
        market = yf_shared
        _log_config_once(
            "[data_providers] MARKET_DATA_API_KEY absent — YFinance pour le market data."
        )

    return fundamental, market
