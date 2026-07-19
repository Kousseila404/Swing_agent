"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE FINNHUB_NEWS — News par ticker (free tier)               ║
║                                                                  ║
║  Endpoint Finnhub :                                              ║
║    GET /api/v1/company-news?symbol=X&from=YYYY-MM-DD&to=...      ║
║                                                                  ║
║  Cache disque 1h (news volatil mais on évite de marteler).      ║
║  Fail-open : retourne {error: ..., articles: []} si KO.          ║
║                                                                  ║
║  Activation : FINNHUB_API_KEY dans backend/.env (déjà partagé    ║
║  avec finnhub_provider).                                         ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse, request

from data_providers._disk_cache import dir_cache_stats, read_json_cache, write_json_cache
from modules.log import logger

_BASE_URL = "https://finnhub.io/api/v1"
_HTTP_TIMEOUT = 10.0
_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "finnhub_cache" / "news"
_CACHE_TTL_SECONDS = 60 * 60  # 1h


def _api_key() -> str:
    return os.getenv("FINNHUB_API_KEY", "").strip()


def is_configured() -> bool:
    """True si FINNHUB_API_KEY est set dans l'env (clé partagée avec finnhub_provider)."""
    return bool(_api_key())


def cache_stats() -> dict[str, Any]:
    """Stats diagnostiques du cache disque — pour `/api/data_health`."""
    return dir_cache_stats(_CACHE_DIR)


def _cache_path(ticker: str, days: int) -> Path:
    return _CACHE_DIR / f"{ticker.upper()}_{days}d.json"


def _read_cache(ticker: str, days: int) -> dict[str, Any] | None:
    return read_json_cache(
        _cache_path(ticker, days), _CACHE_TTL_SECONDS,
        use_mtime=True, label="finnhub_news",
    )


def _write_cache(ticker: str, days: int, payload: dict[str, Any]) -> None:
    write_json_cache(
        _cache_path(ticker, days), payload, stamp=False, label="finnhub_news",
    )


def _normalize_article(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize Finnhub article to compact UI shape."""
    ts = raw.get("datetime") or 0
    iso = None
    try:
        if ts:
            iso = datetime.utcfromtimestamp(int(ts)).isoformat() + "Z"
    except (TypeError, ValueError):
        iso = None
    return {
        "headline": raw.get("headline") or raw.get("title") or "",
        "summary":  raw.get("summary") or "",
        "source":   raw.get("source") or "",
        "url":      raw.get("url") or "",
        "image":    raw.get("image") or "",
        "category": raw.get("category") or "",
        "datetime": iso,
        "id":       str(raw.get("id") or ""),
    }


def fetch_news(ticker: str, days: int = 14, max_items: int = 30) -> dict[str, Any]:
    """Récupère les news d'un ticker sur la fenêtre [today-days, today].

    Output :
      {
        "ticker": "AAPL",
        "from": "...", "to": "...",
        "articles": [{headline, summary, source, url, datetime, ...}],
        "n_articles": int,
        "cached": bool,
        "error": str | None,
      }
    """
    t = (ticker or "").upper().strip()
    if not t:
        return {"ticker": "", "articles": [], "n_articles": 0, "error": "empty ticker"}

    days = max(1, min(60, int(days)))
    cached = _read_cache(t, days)
    if cached is not None:
        cached["cached"] = True
        return cached

    key = _api_key()
    if not key:
        out = {
            "ticker":    t,
            "articles":  [],
            "n_articles": 0,
            "cached":    False,
            "error":     "FINNHUB_API_KEY not configured",
        }
        return out

    today = datetime.utcnow().date()
    start = today - timedelta(days=days)
    qs = parse.urlencode({
        "symbol": t,
        "from":   start.isoformat(),
        "to":     today.isoformat(),
        "token":  key,
    })
    url = f"{_BASE_URL}/company-news?{qs}"

    try:
        with request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        logger.warning(f"[finnhub_news] {t} HTTP {exc.code}")
        return {
            "ticker": t, "articles": [], "n_articles": 0,
            "cached": False, "error": f"HTTP {exc.code}",
        }
    except Exception as exc:
        logger.warning(f"[finnhub_news] {t} fail: {exc}")
        return {
            "ticker": t, "articles": [], "n_articles": 0,
            "cached": False, "error": str(exc),
        }

    if not isinstance(raw, list):
        return {
            "ticker": t, "articles": [], "n_articles": 0,
            "cached": False, "error": "unexpected response shape",
        }

    # Plus récent d'abord, capé à max_items pour ne pas surcharger l'UI.
    articles = sorted(
        (_normalize_article(a) for a in raw),
        key=lambda a: a.get("datetime") or "",
        reverse=True,
    )[:max_items]

    payload = {
        "ticker":     t,
        "from":       start.isoformat(),
        "to":         today.isoformat(),
        "articles":   articles,
        "n_articles": len(articles),
        "cached":     False,
        "error":      None,
    }
    _write_cache(t, days, payload)
    return payload
