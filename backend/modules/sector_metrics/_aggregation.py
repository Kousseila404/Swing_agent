"""Agrégats sectoriels : group-by + stats + rotation score v1 (legacy).

Chaque secteur est agrégé par moyennes pondérées par market cap sur les
sous-scores TITAN. Rotation Score v1 conservé pour compat frontend — plus
simple mais moins informatif que le composite TITAN.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from ._momentum import SECTOR_TO_ETF
from ._scoring import _weighted_mean_scores
from ._utils import (
    _RECO_BUCKETS,
    _mean,
    _median,
    _minmax_norm,
    _reco_bucket_key,
    _safe_float,
    _stdev,
    _upside_pct,
)

# ── Rotation Score v1 (legacy, conservé pour compat frontend) ─────────
_W_VALUATION = 0.4
_W_SENTIMENT = 0.3
_W_UPSIDE    = 0.3

_TOP_N_UPSIDE     = 3
_TOP_N_MARKETCAP  = 3
_TOP_N_SUBSECTORS = 5


def _group_by_sector(tickers: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Regroupe par secteur GICS. Les tickers sans secteur (orphelins) sont
    exclus — un actif sans classification sectorielle ne peut pas participer
    à une rotation sectorielle institutionnelle.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for ticker_dict in tickers.values():
        sector = ticker_dict.get("sector")
        if not sector or not isinstance(sector, str) or not sector.strip():
            continue
        groups.setdefault(sector.strip(), []).append(ticker_dict)
    return groups


def _compute_per_sector(sector: str, tickers: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrège toutes les stats d'un secteur. Tolérant aux valeurs None."""
    # Market cap
    mcaps = [_safe_float(t.get("market_cap")) for t in tickers]
    mcap_vals = [m for m in mcaps if m is not None]
    mcap_total  = sum(mcap_vals) if mcap_vals else None
    mcap_median = _median(mcaps)

    # Forward P/E
    fpes = [_safe_float(t.get("forward_pe")) for t in tickers]
    fpe_median = _median(fpes)
    fpe_std    = _stdev(fpes)

    # Recommandations
    recos = [_safe_float(t.get("recommendation_mean")) for t in tickers]
    reco_mean = _mean(recos)
    reco_std  = _stdev(recos)

    reco_dist: dict[str, int] = {k: 0 for k, _ in _RECO_BUCKETS}
    for r in recos:
        key = _reco_bucket_key(r)
        if key is not None:
            reco_dist[key] += 1

    # Upside
    upsides = [_upside_pct(t) for t in tickers]
    upside_mean   = _mean(upsides)
    upside_median = _median(upsides)

    # Top 3 par upside
    ranked_up = sorted(
        [(t, u) for t, u in zip(tickers, upsides, strict=False) if u is not None],
        key=lambda x: x[1],
        reverse=True,
    )[:_TOP_N_UPSIDE]
    top_upside = [
        {
            "ticker":       t["ticker"],
            "name":         t.get("name"),
            "upside_pct":   u,
            "target":       _safe_float(t.get("price_target_mean")),
            "price":        _safe_float(t.get("current_price")),
            "reco":         _safe_float(t.get("recommendation_mean")),
        }
        for t, u in ranked_up
    ]

    # Top 3 par market cap
    ranked_mc = sorted(
        [(t, m) for t, m in zip(tickers, mcaps, strict=False) if m is not None],
        key=lambda x: x[1],
        reverse=True,
    )[:_TOP_N_MARKETCAP]
    top_marketcap = [
        {
            "ticker":     t["ticker"],
            "name":       t.get("name"),
            "market_cap": m,
        }
        for t, m in ranked_mc
    ]

    # Sous-secteurs (industry) — top N par count
    industries = Counter(
        (t.get("industry") or "Unknown") for t in tickers
    )
    subsectors = dict(industries.most_common(_TOP_N_SUBSECTORS))

    # Analystes total
    n_analysts = sum(
        int(t.get("num_analysts") or 0) for t in tickers
    )

    # ── TITAN sub-scores agrégés : moyenne pondérée par market cap ─────
    # (requiert que les tickers aient été enrichis par _score_universe en amont)
    titan = _weighted_mean_scores(tickers)

    return {
        "sector":              sector,
        "etf":                 SECTOR_TO_ETF.get(sector),
        "count":               len(tickers),
        "market_cap_total":    mcap_total,
        "market_cap_median":   mcap_median,
        "forward_pe_median":   fpe_median,
        "forward_pe_std":      fpe_std,
        "recommendation_mean": reco_mean,
        "recommendation_std":  reco_std,
        "reco_dist":           reco_dist,
        "upside_mean_pct":     upside_mean,
        "upside_median_pct":   upside_median,
        "top_upside":          top_upside,
        "top_marketcap":       top_marketcap,
        "subsectors":          subsectors,
        "num_analysts_total":  n_analysts,
        # ── Multi-factor TITAN ────────────────────────────────────────
        "quality_score_mean":      titan["quality_score"],
        "value_score_mean":        titan["value_score"],
        "risk_score_mean":         titan["risk_score"],
        "sentiment_score_mean":    titan["sentiment_score"],
        "titan_composite_score":   titan["titan_composite_score"],
        # momentum_6m_pct et rotation_score v1 sont ajoutés dans _post_process.
    }


def _post_process(sectors: dict[str, dict[str, Any]],
                  momentum: dict[str, dict[str, float | None]]) -> dict[str, dict[str, Any]]:
    """Injecte les 3 champs momentum (return, volatility, risk-adjusted) puis
    calcule rotation_score v1 (legacy) cross-sector.

    Rotation Score v1 = 0.4 × valuation + 0.3 × sentiment + 0.3 × upside
      - valuation  = 1 / forward_pe_median       (normalisé min-max)
      - sentiment  = (5 − recommendation_mean)   (normalisé min-max)
      - upside     = upside_mean_pct             (normalisé min-max)
    Chaque composante normalisée cross-sector (ranking relatif) puis
    pondérée et scaled 0-100.
    """
    # 1. Momentum injecté — 3 champs pour exposer return brut + risk-adjusted.
    #    momentum_6m_pct alias conservé (legacy consumers) = return_pct.
    for name, stats in sectors.items():
        mom = momentum.get(name) or {}
        stats["momentum_return_pct"]     = mom.get("return_pct")
        stats["momentum_volatility_pct"] = mom.get("volatility_pct")
        stats["momentum_risk_adjusted"]  = mom.get("risk_adjusted")
        stats["momentum_6m_pct"]         = mom.get("return_pct")  # alias legacy

    # 2. Séries pour normalisation
    val_series: dict[str, float | None] = {}
    sent_series: dict[str, float | None] = {}
    ups_series: dict[str, float | None] = {}
    for name, stats in sectors.items():
        fpe = stats.get("forward_pe_median")
        val_series[name]  = (1.0 / fpe) if (fpe is not None and fpe > 0) else None
        rec = stats.get("recommendation_mean")
        sent_series[name] = (5.0 - rec) if (rec is not None) else None
        ups_series[name]  = stats.get("upside_mean_pct")

    val_norm  = _minmax_norm(val_series)
    sent_norm = _minmax_norm(sent_series)
    ups_norm  = _minmax_norm(ups_series)

    # 3. Score pondéré, scaled 0-100
    for name, stats in sectors.items():
        score = (
            _W_VALUATION * val_norm.get(name, 0.0)
            + _W_SENTIMENT * sent_norm.get(name, 0.0)
            + _W_UPSIDE    * ups_norm.get(name, 0.0)
        ) * 100.0
        stats["rotation_score"] = round(score, 2)
        stats["rotation_components"] = {
            "valuation_norm": round(val_norm.get(name, 0.0), 3),
            "sentiment_norm": round(sent_norm.get(name, 0.0), 3),
            "upside_norm":    round(ups_norm.get(name, 0.0), 3),
        }
    return sectors
