"""Correlation check post-allocation — détecte la concentration de thèse.

Risk parity + sector cap ne neutralisent pas la **corrélation thématique**.
Exemple typique : un top-N TITAN dominé par 4 commodities (NEM/CF/CTRA/DVN)
qui partagent un même facteur "matières premières + USD faible". Sector cap
n'arrête pas ça car les 4 sont dans des secteurs différents (Materials,
Basic Materials, Energy, Energy).

Stratégie :
  1. Récupère les retours quotidiens 60j pour les tickers retenus.
  2. Calcule la matrice de corrélation (Pearson) sur log-returns.
  3. Pour chaque ticker, calcule la corrélation **moyenne avec tous les autres**.
  4. Si la corrélation moyenne du ticker dépasse `max_avg_corr`, on le flag
     comme `over_correlated`.
  5. Côté caller (PortfolioManager) : on peut sub-pondérer ou substituer.

Ce module est **indicateur** — il ne décide pas. Il rend une cartographie
que `_manager.py` consomme pour redimensionner ou loguer.

Implémentation déterministe, pure pandas/numpy, fail-open : si l'historique
est indisponible pour une majorité de tickers, on retourne un diagnostic
"insufficient_data" sans bloquer la pipeline.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from modules.log import logger

if TYPE_CHECKING:
    from data_providers.base import MarketDataProviderBase

# Fenêtre de calcul corrélation. 60j ≈ 3 mois — capture un cycle thématique
# sans être pollué par du noise très court terme.
DEFAULT_CORR_WINDOW_DAYS = 60
# Seuil par défaut au-dessus duquel un ticker est dit over-correlated avec
# le panier. 0.55 ≈ corrélation raisonnable mais nette ; 0.7+ = très forte.
DEFAULT_MAX_AVG_CORR = 0.55
# Min tickers pour que le signal corrélation soit pertinent.
_MIN_BASKET_SIZE = 4
# Minimum de jours communs pour qu'une paire ticker-ticker soit utilisable.
_MIN_PAIRWISE_DAYS = 20


@dataclass
class CorrelationResult:
    applied: bool
    avg_corr_basket: float | None = None
    max_avg_corr_threshold: float = DEFAULT_MAX_AVG_CORR
    per_ticker_avg: dict[str, float | None] = field(default_factory=dict)
    over_correlated: list[str] = field(default_factory=list)
    n_tickers: int = 0
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied":              self.applied,
            "avg_corr_basket":      (round(self.avg_corr_basket, 3)
                                     if self.avg_corr_basket is not None else None),
            "max_avg_corr_threshold": self.max_avg_corr_threshold,
            "per_ticker_avg":       {k: (round(v, 3) if v is not None else None)
                                     for k, v in self.per_ticker_avg.items()},
            "over_correlated":      list(self.over_correlated),
            "n_tickers":            self.n_tickers,
            "reason":               self.reason,
        }


def _to_returns(series: pd.Series) -> pd.Series:
    """Log-returns nettoyés. Index DatetimeIndex préservé."""
    s = pd.Series(series).dropna().astype(float)
    if len(s) < 2:
        return pd.Series(dtype=float)
    s = s[s > 0]
    if len(s) < 2:
        return pd.Series(dtype=float)
    r = np.log(s / s.shift(1)).dropna()
    return r


def compute_correlation(
    tickers: list[str],
    market_provider: MarketDataProviderBase | None,
    *,
    window_days: int = DEFAULT_CORR_WINDOW_DAYS,
    max_avg_corr: float = DEFAULT_MAX_AVG_CORR,
) -> CorrelationResult:
    """Retourne la cartographie corrélation pour le panier de tickers.

    Fail-open : si market_provider absent ou data insuffisante, retourne
    `applied=False` avec `reason` set. **Ne lève jamais.**
    """
    n = len(tickers)
    if n < _MIN_BASKET_SIZE:
        return CorrelationResult(
            applied=False, n_tickers=n,
            max_avg_corr_threshold=max_avg_corr,
            reason="basket_too_small",
        )
    if market_provider is None:
        return CorrelationResult(
            applied=False, n_tickers=n,
            max_avg_corr_threshold=max_avg_corr,
            reason="no_market_provider",
        )

    try:
        history = market_provider.get_daily_history_batch(tickers, window_days + 5)
    except Exception as e:
        logger.warning(f"[correlation_check] history fetch failed: {e}")
        return CorrelationResult(
            applied=False, n_tickers=n,
            max_avg_corr_threshold=max_avg_corr,
            reason=f"history_failed: {str(e)[:80]}",
        )

    series_map: dict[str, pd.Series] = {}
    for t in tickers:
        s = history.get(t) if isinstance(history, dict) else None
        if s is None:
            continue
        r = _to_returns(s.tail(window_days + 1))
        if len(r) >= _MIN_PAIRWISE_DAYS:
            series_map[t] = r

    if len(series_map) < _MIN_BASKET_SIZE:
        return CorrelationResult(
            applied=False, n_tickers=n,
            max_avg_corr_threshold=max_avg_corr,
            reason=f"only_{len(series_map)}_tickers_with_data",
        )

    # Construit DF aligné sur l'union des dates et calcule corr en pairwise.
    df = pd.DataFrame(series_map)
    corr = df.corr(min_periods=_MIN_PAIRWISE_DAYS)
    if corr.empty:
        return CorrelationResult(
            applied=False, n_tickers=n,
            max_avg_corr_threshold=max_avg_corr,
            reason="empty_corr_matrix",
        )

    per_ticker_avg: dict[str, float | None] = {}
    for t in tickers:
        if t not in corr.columns:
            per_ticker_avg[t] = None
            continue
        row = corr[t].drop(labels=[t], errors="ignore")
        row = row.dropna()
        if row.empty:
            per_ticker_avg[t] = None
            continue
        per_ticker_avg[t] = float(row.mean())

    valid_avgs = [v for v in per_ticker_avg.values() if v is not None]
    if not valid_avgs:
        return CorrelationResult(
            applied=False, n_tickers=n,
            max_avg_corr_threshold=max_avg_corr,
            per_ticker_avg=per_ticker_avg,
            reason="no_valid_averages",
        )
    avg_basket = float(np.mean(valid_avgs))
    over = sorted(
        [t for t, v in per_ticker_avg.items() if v is not None and v > max_avg_corr],
        key=lambda t: per_ticker_avg[t] or 0.0,
        reverse=True,
    )

    return CorrelationResult(
        applied=True,
        avg_corr_basket=avg_basket,
        max_avg_corr_threshold=max_avg_corr,
        per_ticker_avg=per_ticker_avg,
        over_correlated=over,
        n_tickers=n,
        reason=None if not over else f"{len(over)}_over_correlated",
    )


def downsize_over_correlated(
    weights: dict[str, float],
    correlation: CorrelationResult,
    *,
    haircut: float = 0.5,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Sub-pondère les tickers over_correlated et redistribue pro-rata aux autres.

    Args:
      weights: dict {ticker: weight} sommant à 1 (ou ≈).
      correlation: résultat de compute_correlation.
      haircut: 0.5 = on coupe de moitié les over-correlated ; 0 = on les retire.

    Returns:
      (new_weights, diag) — diag contient `applied`, `haircut_total`, `redistributed_to`.
    """
    if not correlation.applied or not correlation.over_correlated:
        return weights, {
            "applied": False,
            "reason": correlation.reason or "no_over_correlated",
        }

    # Tickers à haircut intersection avec les présents dans weights.
    targets = [t for t in correlation.over_correlated if t in weights]
    if not targets:
        return weights, {"applied": False, "reason": "no_intersection"}

    total = sum(weights.values())
    if total <= 0:
        return weights, {"applied": False, "reason": "zero_total_weight"}

    haircut_amount = sum(weights[t] * haircut for t in targets)
    if haircut_amount <= 0:
        return weights, {"applied": False, "reason": "no_haircut_to_apply"}

    others = [t for t in weights if t not in targets]
    others_total = sum(weights[t] for t in others)
    new_w = dict(weights)
    for t in targets:
        new_w[t] = weights[t] * (1.0 - haircut)
    if others and others_total > 0:
        # Redistribue pro-rata aux non-cappés.
        for t in others:
            new_w[t] += haircut_amount * (weights[t] / others_total)
    else:
        # Pas d'autre — on remet le haircut à zéro (pas redistribuable).
        # On accepte que le total descende sous 1 (cash residual ↑).
        pass

    # Renormalisation finale (drift numérique)
    new_total = sum(new_w.values())
    if new_total > 0:
        new_w = {t: w / new_total for t, w in new_w.items()}

    return new_w, {
        "applied": True,
        "haircut_pct": haircut,
        "haircut_total": round(haircut_amount, 4),
        "downsized": list(targets),
        "redistributed_to_n": len(others),
    }


def _is_finite(v: Any) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False
