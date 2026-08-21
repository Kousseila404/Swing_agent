"""Calendrier d'earnings automatique — book `my_portfolio` (Upgrade 2).

Remplace le badge earnings statique de `my_portfolio_data.py` (dérivait,
jamais revalidé — voir docstring `POSITIONS`) par un `next_earnings_date`
fetché quotidiennement et caché, pour les 10 tickers du book + watchlist
(hors univers TITAN, donc hors `finnhub_enrich.py`/`universe.json`).

Fail-open à deux niveaux :
  1. Par ticker : Finnhub (`FinnhubProvider.get_revisions_and_earnings`,
     logique de sélection "date la plus proche" déjà correcte — voir
     `data_providers/finnhub_provider.py`) → fallback yfinance
     (`_scrape_revisions_and_earnings`, même extraction que le pipeline
     TITAN) si Finnhub absent/erreur/pas de date. Si les deux échouent :
     `next_earnings_date=None`, jamais de valeur codée en dur.
  2. Par run : une entrée déjà en cache est conservée (pas écrasée par un
     None) si le refetch échoue pour ce ticker — évite qu'une panne
     transitoire fasse disparaître un badge valide.

Cache disque : un seul fichier JSON (pas un répertoire par ticker comme
`finnhub_provider` — volume trop faible, 11 tickers, pour justifier ça),
`{ticker: {next_earnings_date, source, fetched_at}}`. Âge évalué par
entrée (pas globalement) : `CACHE_TTL_SECONDS` déclenche un refetch,
`STALE_MAX_AGE_SECONDS` (au-delà) marque l'entrée `stale` côté
`get_earnings_snapshot` sans la supprimer (pattern `STALE_FALLBACK_MAX_AGE_SECONDS`
de `fundamentals_cache.py`).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from data_providers.finnhub_provider import FinnhubProvider
from data_providers.yfinance_provider import _scrape_revisions_and_earnings
from modules.log import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_PATH = _PROJECT_ROOT / "data" / ".my_portfolio_earnings_cache.json"

CACHE_TTL_SECONDS = 24 * 3600
STALE_MAX_AGE_SECONDS = 48 * 3600


def _load_cache() -> dict[str, dict[str, Any]]:
    if not _CACHE_PATH.exists():
        return {}
    try:
        payload = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_cache(entries: dict[str, dict[str, Any]]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(_CACHE_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        tmp.replace(_CACHE_PATH)
    except Exception as e:
        logger.warning(f"[my_portfolio_earnings] cache write failed: {e}")


def _fetch_finnhub(ticker: str) -> str | None:
    if not FinnhubProvider.is_configured():
        return None
    try:
        data = FinnhubProvider().get_revisions_and_earnings(ticker)
    except Exception as e:
        logger.debug(f"[my_portfolio_earnings] finnhub {ticker} failed: {e}")
        return None
    return data.next_earnings_date


def _fetch_yfinance(ticker: str) -> str | None:
    try:
        import yfinance as yf
        out = _scrape_revisions_and_earnings(yf.Ticker(ticker))
    except Exception as e:
        logger.debug(f"[my_portfolio_earnings] yfinance {ticker} failed: {e}")
        return None
    return out.get("next_earnings_date")


def refresh_earnings(symbols: list[str], *, use_cache: bool = True) -> dict[str, dict[str, Any]]:
    """Fetch + cache `next_earnings_date` pour `symbols`. Retourne le cache mergé.

    `symbols` = tickers réellement interrogés côté marché (ex. `0992.HK`
    pour LNVGY, pas le ticker d'affichage) — résolution laissée à
    l'appelant, même convention que `_safe_price`/`price_ticker` dans
    `routers/my_portfolio.py`.
    """
    entries = _load_cache()
    now = time.time()

    for symbol in symbols:
        symbol = symbol.upper()
        cached = entries.get(symbol)
        if use_cache and cached and (now - cached.get("fetched_at", 0)) < CACHE_TTL_SECONDS:
            continue

        date = _fetch_finnhub(symbol)
        source: str | None = "finnhub" if date else None
        if date is None:
            date = _fetch_yfinance(symbol)
            source = "yfinance" if date else None

        if date is not None:
            entries[symbol] = {"next_earnings_date": date, "source": source, "fetched_at": now}
        elif cached is None:
            entries[symbol] = {"next_earnings_date": None, "source": None, "fetched_at": now}
            logger.warning(f"[my_portfolio_earnings] {symbol}: aucune date earnings (Finnhub + yfinance)")
        else:
            logger.warning(f"[my_portfolio_earnings] {symbol}: refetch échoué, cache existant conservé")

    _save_cache(entries)
    return entries


def get_earnings_snapshot(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Lecture pure du cache (aucun fetch réseau) — appelée à chaque requête API.

    Retourne `{symbol: {next_earnings_date, source, stale}}`. Symbole
    absent du cache (jamais rafraîchi, ex. avant le premier cron) →
    `next_earnings_date=None`, `stale=False` (pas d'anomalie à signaler,
    juste rien à afficher).
    """
    entries = _load_cache()
    now = time.time()
    out: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        symbol = symbol.upper()
        entry = entries.get(symbol)
        if entry is None:
            out[symbol] = {"next_earnings_date": None, "source": None, "stale": False}
            continue
        age = now - entry.get("fetched_at", 0)
        out[symbol] = {
            "next_earnings_date": entry.get("next_earnings_date"),
            "source": entry.get("source"),
            "stale": age > STALE_MAX_AGE_SECONDS,
        }
    return out
