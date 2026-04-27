"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  MODULE — SECTOR METRICS (Quantamental Multi-Factor Scoring)                ║
║                                                                              ║
║  Agrège data/universe.json par secteur GICS + calcule le TITAN Composite    ║
║  Score via une notation multi-facteurs (Quality / Value / Risk / Sentiment).║
║                                                                              ║
║  ── Scoring pipeline (par ticker, cross-universe) ────────────────────────── ║
║    Étape 1 — Percentile-rank cross-universe sur chaque métrique brute       ║
║              (plus robuste aux outliers qu'un z-score).                     ║
║    Étape 2 — Agrégation en 4 sous-scores 0-100 :                            ║
║      • quality_score   = mean(ROE, Operating Margin)                        ║
║      • value_score     = mean(EV/EBITDA fallback Fwd P/E, FCF yield)        ║
║      • risk_score      = mean(Debt/Equity inv., Current Ratio)              ║
║      • sentiment_score = mean(Reco inv., Upside Target %)                   ║
║    Étape 3 — TITAN Composite = 0.30·Q + 0.25·V + 0.20·R + 0.25·S            ║
║                                                                              ║
║  ── Agrégats sectoriels ──────────────────────────────────────────────────── ║
║    • Moyennes PONDÉRÉES PAR MARKET CAP (smart-beta institutionnel) de       ║
║      chaque sous-score + composite → pas de dictature des small caps.       ║
║    • Rotation Score v1 (legacy) conservé pour compat frontend.              ║
║    • Momentum 6M via ETF SPDR sectoriels, cache 24h.                        ║
║    • Distribution reco, sous-secteurs, top-upside/marketcap.                ║
║                                                                              ║
║  Cache momentum : data/sector_momentum.json, TTL 24h. Batch yfinance de      ║
║  11 ETFs sectoriels, borne I/O sans impacter /api/sectors à chaque call.    ║
║                                                                              ║
║  ── Organisation du package ─────────────────────────────────────────────── ║
║    _utils.py       : helpers purs (safe_float, median, stdev, reco buckets) ║
║    _momentum.py    : math momentum (pct_change, vol annualisée, risk-adj)   ║
║    _scoring.py     : TITAN scoring cross-universe (percentile + piliers)    ║
║    _aggregation.py : group-by secteur + stats + rotation score v1 legacy    ║
║    __init__.py     : orchestration (caches univers/momentum, public API)    ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from data_providers import (
    MarketDataProviderBase,
    ProviderQuotaExceeded,
    get_providers,
)
from modules.log import logger

from ._aggregation import (
    _W_SENTIMENT,
    _W_UPSIDE,
    _W_VALUATION,
    _compute_per_sector,
    _group_by_sector,
    _post_process,
)
from ._momentum import (
    _EMPTY_MOMENTUM,
    _MOMENTUM_HISTORY_DAYS,
    SECTOR_TO_ETF,
    _compute_momentum_stats,
    _extract_close_series,
)
from ._scoring import (
    _MIN_DATA_QUALITY,
    _NEUTRAL_SCORE,
    _TITAN_SCORING_FIELDS,
    _W_TITAN_GROWTH,
    _W_TITAN_MOMENTUM,
    _W_TITAN_PIOTROSKI,
    _W_TITAN_QUALITY,
    _W_TITAN_RISK,
    _W_TITAN_SENTIMENT,
    _W_TITAN_VALUE,
    _compute_data_quality,
    _percentile_rank,
    _pillar_score,
    _score_universe,
    _weighted_mean_scores,
)
from ._utils import (
    _RECO_BUCKETS,
    _mean,
    _median,
    _minmax_norm,
    _now_iso,
    _reco_bucket_key,
    _safe_float,
    _stdev,
    _upside_pct,
)

# ─────────────────────────────────────────────────────────────────────────────
# PATHS & CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

_PROJECT_ROOT  = Path(__file__).resolve().parent.parent.parent
_UNIVERSE_PATH = _PROJECT_ROOT / "data" / "universe.json"
_MOMENTUM_CACHE_PATH = _PROJECT_ROOT / "data" / "sector_momentum.json"

# TTL du cache momentum (24h). Les ETF sector bougent trop lentement pour
# justifier un re-download à chaque appel d'endpoint.
_MOMENTUM_TTL_SECONDS = 86_400

# Schema version du cache momentum. Bump → invalide les caches au format
# précédent (ici v1 = {sector: pct scalaire} → v2 = {sector: {return, vol, ra}}).
_MOMENTUM_SCHEMA = 2


# ─────────────────────────────────────────────────────────────────────────────
# MOMENTUM 6M — batch provider + cache disque 24h
# ─────────────────────────────────────────────────────────────────────────────

def _load_momentum_cache() -> dict[str, Any] | None:
    """Charge le cache momentum si présent, non-expiré, et au bon schema.
    Les caches au format v1 (pre risk-adjusted) sont invalidés silencieusement.
    """
    if not _MOMENTUM_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_MOMENTUM_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[SectorMetrics] momentum cache unreadable: {e}")
        return None
    # Schema guard — ancien cache = rendements scalaires, incompatible.
    if data.get("schema") != _MOMENTUM_SCHEMA:
        logger.info(
            f"[SectorMetrics] momentum cache schema mismatch "
            f"(got {data.get('schema')}, need {_MOMENTUM_SCHEMA}) — refresh"
        )
        return None
    ts = data.get("updated_at_epoch")
    if not isinstance(ts, (int, float)):
        return None
    age = datetime.now(UTC).timestamp() - ts
    if age > _MOMENTUM_TTL_SECONDS:
        return None
    return data


def _persist_momentum_cache(sectors: dict[str, dict[str, float | None]]) -> None:
    now = datetime.now(UTC)
    payload = {
        "schema":           _MOMENTUM_SCHEMA,
        "updated_at":       now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_at_epoch": now.timestamp(),
        "ttl_seconds":      _MOMENTUM_TTL_SECONDS,
        "sectors":          sectors,
    }
    try:
        _MOMENTUM_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _MOMENTUM_CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(_MOMENTUM_CACHE_PATH)
    except Exception as e:
        logger.error(f"[SectorMetrics] momentum cache write failed: {e}")


def _compute_momentum_fresh(
    market_provider: MarketDataProviderBase,
) -> dict[str, dict[str, float | None]]:
    """Batch market_provider pour les 11 ETF sectoriels sur ~220 jours de trading.
    Par secteur retourne {return_pct, volatility_pct, risk_adjusted} — jamais-crash.
      - return_pct     : rendement brut 6M (%)
      - volatility_pct : σ(daily_returns) × √252 × 100 (annualisée %)
      - risk_adjusted  : return_pct / volatility_pct (approx Sharpe 6M)
    """
    etfs = list(SECTOR_TO_ETF.values())
    out: dict[str, dict[str, float | None]] = {
        s: dict(_EMPTY_MOMENTUM) for s in SECTOR_TO_ETF
    }
    try:
        series_by_etf = market_provider.get_daily_history_batch(
            etfs, days=_MOMENTUM_HISTORY_DAYS,
        )
    except ProviderQuotaExceeded as e:
        logger.critical(
            f"[SectorMetrics] Momentum batch skipped — quota provider atteint : {e}"
        )
        return out
    except Exception as e:
        logger.error(f"[SectorMetrics] market provider batch failed: {e}")
        return out

    for sector, etf in SECTOR_TO_ETF.items():
        try:
            out[sector] = _compute_momentum_stats(series_by_etf.get(etf))
        except Exception as e:
            logger.warning(f"[SectorMetrics] {sector}/{etf} momentum parse fail: {e}")
    return out


def get_momentum_6m(
    force_refresh: bool = False,
    market_provider: MarketDataProviderBase | None = None,
) -> tuple[dict[str, dict[str, float | None]], str, bool]:
    """Retourne (sectors_map, updated_at_iso, cache_hit).
    sectors_map = {sector: {return_pct, volatility_pct, risk_adjusted}}.
    force_refresh=True → bypass cache, redownload via market_provider.
    Si market_provider est None, on résout via get_providers() (factory .env).
    """
    if not force_refresh:
        cached = _load_momentum_cache()
        if cached is not None:
            return cached.get("sectors", {}), cached.get("updated_at", ""), True
    if market_provider is None:
        _, market_provider = get_providers()
    fresh = _compute_momentum_fresh(market_provider)
    _persist_momentum_cache(fresh)
    return fresh, _now_iso(), False


# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSE CACHE — mtime-keyed, thread-safe
# ─────────────────────────────────────────────────────────────────────────────

# Cache mémoire pour l'univers chargé + scoring cross-universe.
# Invalidé automatiquement dès que universe.json change (mtime OU size).
# Évite de re-percentile-rank 500 tickers × 9 métriques à chaque clic secteur.
_UNIVERSE_CACHE_LOCK = threading.Lock()
_UNIVERSE_CACHE: dict[str, Any] = {
    "key":      None,  # (mtime_ns, size) ou None si pas de fichier
    "universe": None,  # dict brut
    "scored":   None,  # {ticker: {..., *_score}}
}


def _universe_cache_key() -> tuple[int, int] | None:
    """mtime + size du universe.json. None si le fichier n'existe pas."""
    try:
        st = _UNIVERSE_PATH.stat()
    except (FileNotFoundError, OSError):
        return None
    return (st.st_mtime_ns, st.st_size)


def _load_universe_raw() -> dict[str, Any]:
    """Read-through sans cache (usage interne + tests qui patchent le path)."""
    if not _UNIVERSE_PATH.exists():
        return {"tickers": {}, "updated_at": None, "stats": {}}
    try:
        return json.loads(_UNIVERSE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"[SectorMetrics] universe.json unreadable: {e}")
        return {"tickers": {}, "updated_at": None, "stats": {}}


def _load_universe() -> dict[str, Any]:
    return _load_universe_raw()


def _get_universe_and_scored() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Retourne (universe_raw, scored_tickers) avec cache invalidé sur mtime/size.
    Thread-safe — plusieurs requêtes /api/sectors en parallèle ne scorent
    l'univers qu'une fois.

    NOTE — la lookup `_score_universe` se fait via les globals de ce module
    (`sector_metrics.__init__`) pour que les tests puissent le monkey-patcher
    par `monkeypatch.setattr(sector_metrics, "_score_universe", ...)`.
    """
    key = _universe_cache_key()
    with _UNIVERSE_CACHE_LOCK:
        if (
            key is not None
            and _UNIVERSE_CACHE["key"] == key
            and _UNIVERSE_CACHE["scored"] is not None
            and _UNIVERSE_CACHE["universe"] is not None
        ):
            return _UNIVERSE_CACHE["universe"], _UNIVERSE_CACHE["scored"]

        universe = _load_universe_raw()
        tickers: dict[str, dict[str, Any]] = universe.get("tickers") or {}
        scored = _score_universe(tickers)

        _UNIVERSE_CACHE["key"] = key
        _UNIVERSE_CACHE["universe"] = universe
        _UNIVERSE_CACHE["scored"] = scored

        # Lot 6 — Snapshot historique sur cache miss (ie. universe.json a changé,
        # typiquement après rebuild quotidien). Le module dedup par date donc
        # un éventuel restart API au cours de la journée écrasera le snapshot
        # du jour avec les nouvelles données — comportement OK car on garde
        # toujours l'état le plus récent par jour.
        # Lazy import pour éviter le circular (universe_history dépend de
        # api_core qui dépend de sector_metrics indirectement via routers).
        if scored:
            try:
                from modules.universe_history import write_snapshot_safe
                # Macro lue best-effort — si KO le snapshot continue sans contexte.
                try:
                    from modules import api_core as _api_core
                    _macro = _api_core.load_macro()
                except Exception:
                    _macro = {}
                write_snapshot_safe(
                    scored,
                    universe_updated_at=universe.get("updated_at"),
                    macro=_macro,
                )
            except Exception as exc:
                logger.warning(f"[SectorMetrics] universe_history hook failed: {exc}")

        return universe, scored


def _clear_universe_cache() -> None:
    """Usage tests — force le recalcul au prochain appel."""
    with _UNIVERSE_CACHE_LOCK:
        _UNIVERSE_CACHE["key"] = None
        _UNIVERSE_CACHE["universe"] = None
        _UNIVERSE_CACHE["scored"] = None


def get_scored_universe() -> dict[str, dict[str, Any]]:
    """API publique : retourne {ticker: fundamentals + titan_*_score}.
    Utilise le cache mtime-keyed — un seul scoring par snapshot universe.json.
    Consommé par PortfolioManager (endpoint /api/portfolio/recommendations).
    """
    _, scored = _get_universe_and_scored()
    return scored


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def compute_all(force_refresh_momentum: bool = False) -> dict[str, Any]:
    """Point d'entrée principal : produit le payload complet /api/sectors.
    Le scoring cross-universe est mis en cache mémoire (invalidé au mtime
    de universe.json) — aucun re-calcul inutile entre deux requêtes tant
    que l'univers n'a pas été reconstruit.
    """
    universe, scored_tickers = _get_universe_and_scored()
    tickers: dict[str, dict[str, Any]] = universe.get("tickers", {}) or {}

    groups = _group_by_sector(scored_tickers)
    sectors: dict[str, dict[str, Any]] = {
        name: _compute_per_sector(name, tks)
        for name, tks in groups.items()
    }

    momentum, mom_updated_at, mom_cache_hit = get_momentum_6m(
        force_refresh=force_refresh_momentum
    )
    sectors = _post_process(sectors, momentum)

    # Freshness de l'univers source
    universe_updated = universe.get("updated_at")
    freshness_days: float | None = None
    if universe_updated:
        try:
            dt = datetime.strptime(universe_updated, "%Y-%m-%dT%H:%M:%SZ")
            dt = dt.replace(tzinfo=UTC)
            delta = datetime.now(UTC) - dt
            freshness_days = round(delta.total_seconds() / 86_400, 2)
        except Exception:
            freshness_days = None

    return {
        "universe_updated_at":  universe_updated,
        "universe_count":       len(tickers),
        "freshness_days":       freshness_days,
        "momentum_updated_at":  mom_updated_at,
        "momentum_cache_hit":   mom_cache_hit,
        "momentum_ttl_seconds": _MOMENTUM_TTL_SECONDS,
        "sectors":              sectors,
        "weights": {
            # Rotation Score v1 (legacy, conservé pour compat frontend)
            "valuation": _W_VALUATION,
            "sentiment": _W_SENTIMENT,
            "upside":    _W_UPSIDE,
            # TITAN Composite Score (7 piliers smart-beta)
            "titan": {
                "quality":   _W_TITAN_QUALITY,
                "value":     _W_TITAN_VALUE,
                "risk":      _W_TITAN_RISK,
                "sentiment": _W_TITAN_SENTIMENT,
                "momentum":  _W_TITAN_MOMENTUM,
                "piotroski": _W_TITAN_PIOTROSKI,
                "growth":    _W_TITAN_GROWTH,
            },
        },
        "generated_at": _now_iso(),
    }


def compute_sector_detail(sector_name: str) -> dict[str, Any] | None:
    """Retourne le détail d'un secteur : stats agrégées + tous ses tickers
    enrichis d'upside_pct et bucketés par recommandation. None si absent.

    Réutilise le cache universe+scoring (une seule passe de percentile-ranks
    pour la paire /api/sectors + /api/sectors/{X}).
    """
    payload = compute_all(force_refresh_momentum=False)
    sectors: dict[str, dict[str, Any]] = payload.get("sectors", {})
    if sector_name not in sectors:
        return None

    _universe, scored_all = _get_universe_and_scored()
    sector_tickers = [
        t for t in scored_all.values()
        if (t.get("sector") or "Unknown") == sector_name
    ]

    # Enrichit chaque ticker d'upside_pct et reco_bucket pour un rendu direct.
    enriched: list[dict[str, Any]] = [
        {
            **t,
            "upside_pct":  _upside_pct(t),
            "reco_bucket": _reco_bucket_key(_safe_float(t.get("recommendation_mean"))),
        }
        for t in sector_tickers
    ]

    return {
        "sector":              sector_name,
        "universe_updated_at": payload.get("universe_updated_at"),
        "momentum_updated_at": payload.get("momentum_updated_at"),
        "stats":               sectors[sector_name],
        "tickers":             enriched,
    }
