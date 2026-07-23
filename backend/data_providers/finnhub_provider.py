"""Finnhub provider — Revisions / Earnings calendar / Recommendation trends.

Lot 17 — gratuit (60 calls/min sur free tier, suffisant pour 491 tickers en
~9 minutes). Inscription : un email sur https://finnhub.io/register.

Endpoints utilisés (tous free tier) :
  • GET /api/v1/stock/recommendation?symbol=AAPL
       → liste mensuelle des consensus (strongBuy/buy/hold/sell/strongSell)
  • GET /api/v1/stock/earnings?symbol=AAPL
       → 4 derniers earnings : actual/estimate/period/surprise
  • GET /api/v1/calendar/earnings?symbol=AAPL&from=...&to=...
       → prochain earnings + estimés EPS (trié par Finnhub date DESCENDANTE —
         on sélectionne explicitement l'entrée la plus proche, pas `[0]`)

Endpoints RETIRÉS (2026-07-23) — plan payant requis, 403 confirmé sur 489/489
tickers en prod, tout le temps :
  • GET /api/v1/stock/price-target      → `target_price_consensus` (jamais
    peuplé ; `price_target_mean` déjà couvert par yfinance, aucune perte)
  • GET /api/v1/stock/upgrade-downgrade → `analyst_actions` (log nominatif
    daté par firme, jamais peuplé depuis sa création Étape 13 — l'un des 2
    bugs successifs sur ce champ : d'abord mauvais endpoint appelé, puis bon
    endpoint mais hors plan free. `upgrade_downgrade_log`, l'agrégat mensuel
    dérivé de /stock/recommendation, reste fonctionnel et inchangé.)
  Ces 2 calls consommaient ~40 % du budget Finnhub par ticker pour un
  résultat garanti vide — retirés pour accélérer l'enrichissement.

Ce provider **n'écrase PAS** les données yfinance — il les **enrichit**. Le
caller (universe_engine) appelle `enrich_with_finnhub()` après le scrape
yfinance principal.

Activation :
  echo 'FINNHUB_API_KEY=xxxxx' >> backend/.env
  → systemctl restart swing-api.service
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse, request

from modules.log import logger

from ._disk_cache import dir_cache_stats, read_json_cache, write_json_cache

_BASE_URL = "https://finnhub.io/api/v1"
_HTTP_TIMEOUT = 10.0
_MIN_DELAY_SECONDS = 1.05   # 60/min = 1 call/sec, on garde une marge.

# Cache disque 24h : on relit le ticker dans le batch quotidien sans cogner Finnhub.
_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "finnhub_cache"
_CACHE_TTL_SECONDS = 24 * 3600


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


@dataclass
class FinnhubData:
    """Champs enrichis par Finnhub pour un ticker."""
    ticker: str
    upgrades_30d: int | None = None
    downgrades_30d: int | None = None
    upgrades_90d: int | None = None
    downgrades_90d: int | None = None
    revisions_net_score: float | None = None
    earnings_surprise_pct_last: float | None = None
    earnings_surprise_avg_4q: float | None = None
    earnings_beat_rate_8q: float | None = None
    next_earnings_date: str | None = None
    next_earnings_eps_estimate: float | None = None
    # target_price_consensus / analyst_actions : endpoints retirés (2026-07-23,
    # 403 plan payant) — champs conservés pour la stabilité du schéma cache/
    # consommateurs, restent toujours None/[] désormais (jamais peuplés
    # depuis la création de ce provider de toute façon).
    target_price_consensus: float | None = None
    upgrade_downgrade_log: list[dict[str, Any]] = field(default_factory=list)
    analyst_actions: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    fetched_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


_last_call: float = 0.0


def _throttle() -> None:
    global _last_call
    now = time.time()
    wait = _MIN_DELAY_SECONDS - (now - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def _fetch_json(endpoint: str, params: dict[str, Any], api_key: str) -> tuple[Any, str | None]:
    """GET Finnhub. Retourne (payload, error).

    `error` est None même si le payload est vide/légitimement absent (ex :
    ticker sans couverture analyste) — il n'est renseigné que sur un échec
    réseau/HTTP explicite (429, autre code HTTP, timeout, exception réseau),
    pour que l'appelant puisse distinguer "pas de donnée" de "endpoint cassé"
    au lieu de fusionner les deux dans un même `None` (Étape 5 roadmap).
    """
    _throttle()
    params = {**params, "token": api_key}
    qs = parse.urlencode(params)
    url = f"{_BASE_URL}{endpoint}?{qs}"
    req = request.Request(url, headers={"User-Agent": "SwingQuant/1.0"})
    try:
        with request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                logger.debug(f"[finnhub] HTTP {resp.status} on {endpoint}")
                return None, f"http_{resp.status}"
            data = resp.read().decode("utf-8", errors="ignore")
            return json.loads(data), None
    except urlerror.HTTPError as e:
        if e.code == 429:
            logger.warning(f"[finnhub] rate limit hit on {endpoint}")
            return None, "rate_limited"
        logger.debug(f"[finnhub] HTTP error {e.code} on {endpoint}")
        return None, f"http_{e.code}"
    except (urlerror.URLError, TimeoutError, OSError, ValueError) as e:
        logger.debug(f"[finnhub] fetch failed {endpoint}: {e}")
        return None, "fetch_failed"


def _cache_path(ticker: str) -> Path:
    return _CACHE_DIR / f"{ticker.upper()}.json"


# Phase 6 audit (2026-05-06) — version du schéma cache. À chaque ajout de
# champ dans FinnhubData, incrémenter cette constante : les caches d'une
# version antérieure seront ignorés (et re-fetchés) au lieu d'être désérialisés
# en silence avec des champs manquants.
# v2 (Étape 13 roadmap) : ajout de `analyst_actions`.
_CACHE_SCHEMA_VERSION = 2


def _read_cache(ticker: str) -> dict[str, Any] | None:
    return read_json_cache(
        _cache_path(ticker), _CACHE_TTL_SECONDS,
        schema_version=_CACHE_SCHEMA_VERSION, label="finnhub",
    )


def _write_cache(ticker: str, payload: dict[str, Any]) -> None:
    write_json_cache(
        _cache_path(ticker), payload,
        schema_version=_CACHE_SCHEMA_VERSION, label="finnhub",
    )


def cache_stats() -> dict[str, Any]:
    """Stats diagnostiques du cache disque — pour `/api/data_health`."""
    return dir_cache_stats(_CACHE_DIR)


# ─────────────────────────────────────────────────────────────────────────────
# Provider class
# ─────────────────────────────────────────────────────────────────────────────

class FinnhubProvider:
    """Fournit les enrichissements Revisions / Earnings calendar / Targets.

    Pas un FundamentalProviderBase complet (Finnhub free n'expose pas tous
    les champs de FinancialRatios). Sert d'enricher.
    """

    name = "finnhub"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = (api_key or os.getenv("FINNHUB_API_KEY") or "").strip()
        if not self._api_key:
            raise ValueError("FINNHUB_API_KEY absent — provider non utilisable")

    @classmethod
    def is_configured(cls) -> bool:
        """True si FINNHUB_API_KEY est set dans l'env."""
        return bool((os.getenv("FINNHUB_API_KEY") or "").strip())

    def get_revisions_and_earnings(self, ticker: str, *, use_cache: bool = True) -> FinnhubData:
        """Récupère tous les champs Revisions / Earnings d'un ticker.

        Effectue 5 calls Finnhub (recommendation, earnings, calendar,
        price-target, upgrade-downgrade). Délais entre calls = ~5s pour
        respecter la limite 60/min avec marge.

        Fail-open : champs None si un endpoint pète.
        """
        ticker = ticker.upper()
        if use_cache:
            cached = _read_cache(ticker)
            if cached is not None:
                cached.pop("_cached_at", None)
                cached.pop("_schema_version", None)
                try:
                    return FinnhubData(**cached)
                except TypeError:
                    pass

        data = FinnhubData(ticker=ticker)
        data.fetched_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        fetch_errors: list[str] = []

        # 1. Recommendation trends (gratuit free tier) — 4 derniers mois agrégés
        # par bucket (strongBuy/buy/hold/sell/strongSell). On dérive upgrades/
        # downgrades par diff entre mois consécutifs.
        rec, rec_err = _fetch_json("/stock/recommendation", {"symbol": ticker}, self._api_key)
        if rec_err:
            fetch_errors.append(f"recommendation:{rec_err}")
        if isinstance(rec, list) and rec:
            # Tri descending par period (mois le plus récent en [0]).
            try:
                rec_sorted = sorted(rec, key=lambda x: str(x.get("period") or ""), reverse=True)
            except (TypeError, KeyError):
                rec_sorted = rec

            def _bull_count(r: dict[str, Any]) -> int:
                return int((r.get("strongBuy") or 0) + (r.get("buy") or 0))

            def _bear_count(r: dict[str, Any]) -> int:
                return int((r.get("sell") or 0) + (r.get("strongSell") or 0))

            log: list[dict[str, Any]] = []
            up_30 = down_30 = up_90 = down_90 = 0
            if len(rec_sorted) >= 2:
                # 30j = diff entre mois courant (rec_sorted[0]) et mois précédent (rec_sorted[1]).
                bull_now = _bull_count(rec_sorted[0])
                bear_now = _bear_count(rec_sorted[0])
                bull_1m  = _bull_count(rec_sorted[1])
                bear_1m  = _bear_count(rec_sorted[1])
                up_30 = max(0, bull_now - bull_1m)
                down_30 = max(0, bear_now - bear_1m)
                log.append({
                    "period": rec_sorted[0].get("period"),
                    "bull": bull_now, "bear": bear_now, "hold": rec_sorted[0].get("hold"),
                })
            if len(rec_sorted) >= 4:
                # 90j = diff entre mois courant (rec_sorted[0]) et 3 mois plus tôt (rec_sorted[3]).
                bull_now = _bull_count(rec_sorted[0])
                bear_now = _bear_count(rec_sorted[0])
                bull_3m  = _bull_count(rec_sorted[3])
                bear_3m  = _bear_count(rec_sorted[3])
                up_90 = max(0, bull_now - bull_3m)
                down_90 = max(0, bear_now - bear_3m)
                for r in rec_sorted[1:4]:
                    log.append({
                        "period": r.get("period"),
                        "bull": _bull_count(r), "bear": _bear_count(r),
                        "hold": r.get("hold"),
                    })

            data.upgrades_30d = up_30
            data.downgrades_30d = down_30
            data.upgrades_90d = up_90
            data.downgrades_90d = down_90
            denom = up_90 + down_90
            data.revisions_net_score = (up_90 - down_90) / denom if denom > 0 else None
            data.upgrade_downgrade_log = log

        # 2. Earnings history (4 derniers Q : actual/estimate/surprisePercent).
        earn, earn_err = _fetch_json("/stock/earnings", {"symbol": ticker, "limit": 8}, self._api_key)
        if earn_err:
            fetch_errors.append(f"earnings:{earn_err}")
        if isinstance(earn, list) and earn:
            surprises: list[float] = []
            for e in earn:
                if not isinstance(e, dict):
                    continue
                sp = _safe_float(e.get("surprisePercent"))
                if sp is not None:
                    surprises.append(sp)
            if surprises:
                data.earnings_surprise_pct_last = surprises[0]
                data.earnings_surprise_avg_4q = sum(surprises[:4]) / min(4, len(surprises))
                data.earnings_beat_rate_8q = sum(1 for s in surprises[:8] if s > 0) / min(8, len(surprises))

        # 3. Earnings calendar (prochain earnings).
        today = datetime.utcnow().date()
        future = today + timedelta(days=365)
        cal, cal_err = _fetch_json(
            "/calendar/earnings",
            {"symbol": ticker, "from": today.isoformat(), "to": future.isoformat()},
            self._api_key,
        )
        if cal_err:
            fetch_errors.append(f"calendar:{cal_err}")
        if isinstance(cal, dict):
            entries = cal.get("earningsCalendar") or []
            # Finnhub renvoie les entrées triées date DESCENDANTE (la plus
            # lointaine d'abord dans la fenêtre [today, today+365j]) — pas
            # ascendante. `entries[0]` pointait donc systématiquement vers le
            # 4e trimestre futur (~1 an plus tard) au lieu du prochain. Bug
            # confirmé en prod : 479/489 tickers avec next_earnings_date à
            # 250-680 j d'écart de la période fiscale la plus récente connue.
            dated = [e for e in entries if isinstance(e, dict) and e.get("date")]
            nearest = min(dated, key=lambda e: e["date"]) if dated else None
            if nearest is not None:
                data.next_earnings_date = nearest.get("date")
                data.next_earnings_eps_estimate = _safe_float(nearest.get("epsEstimate"))

        # 4/5. Price target consensus + upgrade/downgrade nominatif RETIRÉS
        # (2026-07-23) — /stock/price-target et /stock/upgrade-downgrade
        # renvoient 403 "You don't have access to this resource." sur le
        # plan free, confirmé sur 489/489 tickers en prod à chaque run.
        # `target_price_consensus`/`analyst_actions` restent None/[] (cf.
        # dataclass) ; `price_target_mean` déjà couvert par yfinance et
        # `upgrade_downgrade_log` (agrégat mensuel via /stock/recommendation,
        # gratuit) reste fonctionnel et inchangé.

        # Signal d'échec explicite, distinct d'une simple absence de données
        # (Étape 5 roadmap) — persisté par `finnhub_enrich.py` sous
        # `finnhub_error`, lu par `data_confidence._multi_source_factor`.
        data.error = "; ".join(fetch_errors) if fetch_errors else None

        _write_cache(ticker, data.to_dict())
        return data
