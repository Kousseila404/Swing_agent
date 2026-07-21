"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — DATA HEALTH                                            ║
║  GET /api/data_health — observabilité providers + cache         ║
║                                                                  ║
║  Endpoint public read-only — l'UI bandeau s'en sert pour alerter ║
║  l'utilisateur dès qu'un provider dégrade (yf breaker open, FMP  ║
║  quota épuisée, % de tickers manquants > seuil).                 ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Security

from data_providers import finnhub_provider
from modules import api_core, finnhub_news, sec_edgar
from modules.fundamentals_cache import (
    cache_sanitize_stats,
    cache_stats,
    invalidate_tickers,
    list_flagged_tickers,
    list_flagged_tickers_with_tags,
)
from modules.log import logger
from modules.yf_circuit_breaker import yf_breaker

router = APIRouter(prefix="/api", tags=["data_health"])

# Champs critiques surveillés pour le % manquant. Reflet du scoring TITAN.
_CRITICAL_FIELDS = (
    "return_on_equity", "operating_margin",
    "ev_to_ebitda", "free_cash_flow", "operating_cash_flow",
    "debt_to_equity", "current_ratio",
    "recommendation_mean", "price_target_mean",
    "momentum_return_pct",
    # Lot 8 nouveaux (probablement 99% manquants tant que rebuild pas fait)
    "return_on_assets", "net_income", "gross_margin",
)

# Seuils de severity. Au-dessus, l'UI doit montrer un bandeau warning/critical.
_MISSING_WARNING_PCT = 15.0
_MISSING_CRITICAL_PCT = 40.0
_FETCHED_AT_STALE_DAYS_WARNING = 14

# Audit 2026-04-23 — Seuils sanitize flags : au-delà, un refresh est souhaitable.
# Un cache sain post-fix doit être à <5 flags (EV/EBITDA négatifs légitimes).
# 20+ = dérive inexpliquée (nouveaux bugs source) → warning. 100+ = critical.
_SANITIZE_WARNING_N = 20
_SANITIZE_CRITICAL_N = 100
_FETCHED_AT_STALE_DAYS_CRITICAL = 30


def _universe_inventory() -> dict[str, Any]:
    """Lit universe.json + calcule les stats data quality."""
    try:
        if not api_core.UNIVERSE_QUANTAMENTAL_PATH.exists():
            return {"loaded": False, "reason": "universe.json absent"}
        u = json.loads(api_core.UNIVERSE_QUANTAMENTAL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"loaded": False, "reason": f"read_failed: {e}"}

    tickers = u.get("tickers") or {}
    n = len(tickers)
    if n == 0:
        return {"loaded": True, "n_tickers": 0, "fields_missing": {}}

    # % manquants par champ
    fields_missing: dict[str, dict[str, Any]] = {}
    for f in _CRITICAL_FIELDS:
        n_missing = sum(1 for r in tickers.values() if r.get(f) is None)
        pct = n_missing / n * 100.0
        severity = "ok"
        if pct >= _MISSING_CRITICAL_PCT:
            severity = "critical"
        elif pct >= _MISSING_WARNING_PCT:
            severity = "warning"
        fields_missing[f] = {
            "missing": n_missing,
            "missing_pct": round(pct, 2),
            "severity": severity,
        }

    # Distribution age fetched_at
    now = datetime.now(UTC)
    ages_days: list[float] = []
    for r in tickers.values():
        fa = r.get("fetched_at")
        if fa:
            try:
                dt = datetime.strptime(fa, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                ages_days.append((now - dt).total_seconds() / 86400)
            except (ValueError, TypeError):
                continue

    if ages_days:
        ages_sorted = sorted(ages_days)
        median = ages_sorted[len(ages_sorted) // 2]
        oldest = max(ages_sorted)
        n_stale = sum(1 for a in ages_days if a >= _FETCHED_AT_STALE_DAYS_WARNING)
        n_severe = sum(1 for a in ages_days if a >= _FETCHED_AT_STALE_DAYS_CRITICAL)
    else:
        median = oldest = 0
        n_stale = n_severe = 0

    # Source providers (audit Lot 11)
    sources: dict[str, int] = {}
    for r in tickers.values():
        sp = r.get("source_provider") or "unknown"
        sources[sp] = sources.get(sp, 0) + 1

    # Compteurs insider_error/finnhub_error (Étape 0/5) — jusqu'ici lus
    # uniquement par data_confidence._multi_source_factor, jamais agrégés
    # (Étape 6). Étape 14 : en plus du compteur, la liste {ticker, error}
    # elle-même — la chaîne d'erreur est déjà persistée telle quelle dans
    # universe.json, aucun nouveau calcul.
    n_insider_error = sum(1 for r in tickers.values() if r.get("insider_error"))
    n_finnhub_error = sum(1 for r in tickers.values() if r.get("finnhub_error"))
    insider_error_tickers = sorted(
        (
            {"ticker": t, "error": r.get("insider_error")}
            for t, r in tickers.items()
            if r.get("insider_error")
        ),
        key=lambda x: x["ticker"],
    )
    finnhub_error_tickers = sorted(
        (
            {"ticker": t, "error": r.get("finnhub_error")}
            for t, r in tickers.items()
            if r.get("finnhub_error")
        ),
        key=lambda x: x["ticker"],
    )

    return {
        "loaded": True,
        "n_tickers": n,
        "universe_updated_at": u.get("updated_at"),
        "fields_missing": fields_missing,
        "fetched_at": {
            "median_age_days": round(median, 2),
            "oldest_age_days": round(oldest, 2),
            "n_stale": n_stale,
            "n_severe": n_severe,
            "stale_threshold_days": _FETCHED_AT_STALE_DAYS_WARNING,
            "severe_threshold_days": _FETCHED_AT_STALE_DAYS_CRITICAL,
        },
        "sources": sources,
        "enrichment_errors": {
            "insider_error": n_insider_error,
            "finnhub_error": n_finnhub_error,
            "insider_error_tickers": insider_error_tickers,
            "finnhub_error_tickers": finnhub_error_tickers,
        },
    }


def _provider_sources_health() -> dict[str, Any]:
    """Cache stats des sources non-fondamentales (finnhub enrich/insider/SEC
    filings/news) — Étape 0 roadmap : dashboard toutes-sources, pas seulement
    l'univers fondamental yfinance/FMP. Chaque source garde son propre cache
    disque (cf. `data_providers/_disk_cache.py`) ; ici on ne fait qu'agréger
    les stats déjà exposées par chaque module, sans toucher à leur logique
    de fetch/enrichissement.
    """
    return {
        "finnhub": {
            "configured": finnhub_provider.FinnhubProvider.is_configured(),
            "cache": finnhub_provider.cache_stats(),
        },
        "news": {
            "configured": finnhub_news.is_configured(),
            "cache": finnhub_news.cache_stats(),
        },
        "insider": {
            "cache": sec_edgar.insider_cache_stats(),
        },
        "sec_filings": {
            "cache": sec_edgar.filings_cache_stats(),
        },
        "cik_map_age_sec": sec_edgar.cik_map_age_sec(),
    }


def _global_severity(payload: dict[str, Any]) -> str:
    """Calcule la severity globale = pire des composantes."""
    severities = []

    # YF breaker
    yf = payload.get("yf_breaker", {})
    if yf.get("tripped"):
        severities.append("warning")  # warning car auto-reset prévu

    # Universe fields
    inv = payload.get("universe", {})
    if inv.get("loaded"):
        for f in inv.get("fields_missing", {}).values():
            severities.append(f.get("severity", "ok"))
        # Stale tickers
        if inv.get("fetched_at", {}).get("n_severe", 0) > 0:
            severities.append("critical")
        elif inv.get("fetched_at", {}).get("n_stale", 0) > 50:
            severities.append("warning")

    # FMP quota
    fmp = payload.get("fmp", {})
    if fmp.get("quota_exhausted"):
        severities.append("warning")  # warning car auto-reset à minuit UTC

    # Sanitize flags — trop de valeurs aberrantes dans le cache = dérive source.
    n_flagged = (payload.get("sanitize") or {}).get("n_tickers_flagged") or 0
    if n_flagged >= _SANITIZE_CRITICAL_N:
        severities.append("critical")
    elif n_flagged >= _SANITIZE_WARNING_N:
        severities.append("warning")

    if "critical" in severities:
        return "critical"
    if "warning" in severities:
        return "warning"
    return "ok"


@router.get("/data_health")
def get_data_health():
    """État de santé des providers + cache fundamentals + universe.

    Champs principaux :
      - severity_global : ok | warning | critical (calculé sur les composantes)
      - yf_breaker : tripped, age, cooldown, auto_reset_in
      - fundamentals_cache : n_cached, ages
      - universe : fields_missing par champ, fetched_at distribution, sources,
        enrichment_errors (compteurs tickers insider_error/finnhub_error —
        Étape 6 — + liste {ticker, error} par source — Étape 14)
      - fmp : quota_used / quota_max si dispo
      - providers : cache stats finnhub/insider/SEC filings/news (Étape 0 —
        dashboard toutes-sources, pas seulement fondamentaux)

    Endpoint **public** — pas d'info sensible, just observability.
    """
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # 1. YF circuit breaker
    try:
        payload["yf_breaker"] = yf_breaker.snapshot()
    except (OSError, ValueError, KeyError, AttributeError) as e:
        logger.warning(f"[data_health] yf_breaker.snapshot failed: {e}")
        payload["yf_breaker"] = {"error": str(e)}

    # 2. Fundamentals cache stats
    try:
        payload["fundamentals_cache"] = cache_stats()
    except (OSError, ValueError, KeyError) as e:
        logger.warning(f"[data_health] cache_stats failed: {e}")
        payload["fundamentals_cache"] = {"error": str(e)}

    # 2b. Sanitize flags (Niveau 1 qualité data) — compte des valeurs
    # out-of-bounds rejetées et cross-check market_cap en amont du cache.
    try:
        sanitize = cache_sanitize_stats()
        tagged = list_flagged_tickers_with_tags()
        sanitize["tickers"] = [
            {"ticker": ticker, "tags": tags} for ticker, tags in tagged.items()
        ]
        payload["sanitize"] = sanitize
    except (OSError, ValueError, KeyError) as e:
        logger.warning(f"[data_health] cache_sanitize_stats failed: {e}")
        payload["sanitize"] = {"error": str(e)}

    # 3. Universe inventory (data quality par champ)
    try:
        payload["universe"] = _universe_inventory()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
        logger.warning(f"[data_health] universe_inventory failed: {e}")
        payload["universe"] = {"loaded": False, "reason": str(e)}

    # 4. FMP quota — best effort, l'instance n'est pas exposée globalement.
    # On peut juste lire les compteurs si get_providers() a déjà été appelé
    # ce process. Sinon (pas encore initialisé) → null.
    payload["fmp"] = _fmp_status()

    # 4b. Sources non-fondamentales (finnhub/insider/SEC filings/news).
    try:
        payload["providers"] = _provider_sources_health()
    except (OSError, ValueError, KeyError) as e:
        logger.warning(f"[data_health] provider_sources_health failed: {e}")
        payload["providers"] = {"error": str(e)}

    # 5. Severity globale
    payload["severity_global"] = _global_severity(payload)

    return payload


@router.post("/data_health/refresh_flagged")
def refresh_flagged_tickers(
    _auth: None = Security(api_core.require_auth),
):
    """Invalide le cache pour les tickers ayant un flag dq_sanitize= puis
    lance un refresh staggered ciblé (force=<liste>, min_age_days=0).

    Use case : après avoir déployé un fix qualité data (recovery dividend_yield,
    nouveaux bounds, etc.), ce bouton permet de voir l'effet **maintenant**
    sans attendre le cron quotidien de 06:00 UTC.

    Coût : N calls yfinance où N = nombre de tickers flaggés (typiquement
    < 50 après stabilisation, peut monter à 300+ après un changement de bounds).
    Le circuit breaker YF reste actif — si ça trip, le job s'arrête proprement.
    """
    # Guard : un seul job universe à la fois (aligné avec /universe/rebuild).
    existing = api_core.running_universe_rebuild_job()
    if existing:
        raise HTTPException(
            409, f"Rebuild universe déjà en cours (job {existing})",
        )

    flagged = list_flagged_tickers()
    if not flagged:
        return {
            "ok": True,
            "n_flagged": 0,
            "job": None,
            "message": "Aucun ticker flaggé — rien à rafraîchir.",
        }

    removed = invalidate_tickers(flagged)
    logger.info(
        f"[refresh_flagged] invalidated {removed}/{len(flagged)} cache entries "
        f"before refresh"
    )

    # Refresh staggered mais ciblé : budget = len(flagged), force=<list>,
    # min_age_days=0 (tout doit passer). Cap 500 = taille max univers prod.
    budget = min(len(flagged), 500)
    cmd: list[str] = [
        api_core.PYTHON_BIN, "-m", "modules.universe_scheduler",
        "--budget", str(budget),
        "--min-age-days", "0",
        "--force", ",".join(flagged),
        "--reason", f"refresh_flagged (sanitize n={len(flagged)})",
    ]
    label = f"Refresh flagged ({len(flagged)} tickers)"
    meta = api_core.launch_job(cmd, label)

    return {
        "ok": True,
        "n_flagged": len(flagged),
        "n_invalidated": removed,
        "sample": flagged[:10],
        "job": meta,
    }


def _fmp_status() -> dict[str, Any]:
    """Lit l'état FMP best-effort. Renvoie {enabled, configured, quota_used,
    quota_max, quota_exhausted}.
    """
    import os
    api_key_set = bool((os.getenv("FMP_API_KEY") or "").strip())
    enabled = (os.getenv("FMP_ENABLED") or "").strip().lower() in {
        "true", "1", "yes", "on", "enabled",
    }
    out = {
        "configured": api_key_set,
        "enabled":    enabled and api_key_set,
        "quota_used": None,
        "quota_max":  None,
        "quota_exhausted": False,
    }
    # Ne pas instancier de provider ici (effet de bord). On consulte juste
    # les modules existants si déjà chargés.
    try:
        import sys
        mod = sys.modules.get("data_providers.fmp_provider")
        if mod is None:
            return out
        # Pas d'API publique pour récupérer une instance singleton — c'est
        # par design (FMPProvider créé fresh dans get_providers). Donc on
        # ne peut pas lire le compteur live ici. Champ exposé pour future
        # extension (singleton FMP partagé).
    except (ImportError, AttributeError) as e:
        logger.debug(f"[data_health] _fmp_status introspection failed: {e}")
    return out
