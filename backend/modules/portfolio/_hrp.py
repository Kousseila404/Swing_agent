"""Hierarchical Risk Parity (López de Prado 2016).

Pourquoi ne pas se contenter de risk-parity 1/σ + correlation_check post-hoc :
  • 1/σ ignore la matrice de covariance — 4 tickers high-corr reçoivent le
    même poids que 4 tickers décorrélés, le σ_p réel est sous-estimé.
  • correlation_check coupe en post-traitement (haircut 50 %), ce qui détruit
    de l'information : HRP intègre la corrélation *dans* l'allocation.
  • Markowitz pur explose sur les Σ mal conditionnées (n=20, T=60 → ratio
    n/T = 0.33, condition number Σ⁻¹ peut dépasser 10⁶). HRP est robuste à ça
    par construction (pas d'inversion).

Algorithme (3 étapes, Σ-conditioning friendly) :
  1. Tree clustering : convertit Σ en distance d_ij = √(½ × (1 − ρ_ij)),
     applique single-linkage hierarchical clustering → ordre quasi-diagonal.
  2. Quasi-diagonalization : permute Σ selon l'ordre du clustering.
  3. Recursive bisection : à chaque niveau, on coupe le cluster en deux,
     calcule la variance inverse pondérée de chaque moitié, alloue le poids
     pro-rata 1/σ_subcluster.

Implémentation : numpy + scipy.cluster.hierarchy. Pas de dépendance lourde
au-delà de scipy déjà présent (requirements). ~120 lignes.

Si scipy absent ou Σ mal conditionnée : fallback vers `_risk_parity.compute_weights`
(comportement legacy 1/σ).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from modules.log import logger

from ._utils import _safe_float

if TYPE_CHECKING:
    from data_providers.base import MarketDataProviderBase

# Fenêtre default — alignée avec correlation_check (60j ≈ 3 mois).
DEFAULT_HRP_WINDOW_DAYS = 60
# Sous ce nombre de tickers le clustering n'a pas de sens — on retombe sur
# l'inverse-vol classique.
_MIN_TICKERS_FOR_HRP = 4
# Sous ce nombre de jours pairwise valides on rejette la paire (NaN dans la
# corr → distance NaN → linkage explose).
_MIN_PAIRWISE_DAYS = 20


def _correlation_distance(corr: np.ndarray) -> np.ndarray:
    """Convertit corrélation [-1, 1] en distance [0, 1].
    d_ij = √(½ × (1 − ρ_ij)) — métrique propre (López de Prado §16.4)."""
    # Clipping pour gérer les NaN / valeurs hors bornes (corruption numérique).
    c = np.clip(corr, -1.0, 1.0)
    d = np.sqrt(0.5 * (1.0 - c))
    np.fill_diagonal(d, 0.0)
    return d


def _quasi_diag_order(linkage_matrix: np.ndarray) -> list[int]:
    """Récupère l'ordre quasi-diagonal des feuilles depuis une matrice de linkage.
    On décompose récursivement chaque cluster en sous-clusters jusqu'aux feuilles.
    """
    link = linkage_matrix.astype(int)
    n = link.shape[0] + 1   # nombre de feuilles
    order: list[int] = [link[-1, 0], link[-1, 1]]

    # Index ≥ n correspond à un cluster intermédiaire ; index < n = feuille.
    while True:
        new_order: list[int] = []
        expanded = False
        for idx in order:
            if idx < n:
                new_order.append(idx)
            else:
                # Cluster non-terminal → décompose
                row = link[idx - n]
                new_order.extend([int(row[0]), int(row[1])])
                expanded = True
        order = new_order
        if not expanded:
            break
    return order


def _cluster_variance(cov: np.ndarray, indices: list[int]) -> float:
    """Variance d'un cluster en allocation inverse-vol interne :
       w_i = (1/σ_i) / Σ(1/σ_j) puis σ²_cluster = w' Σ w.

    Retourne σ² (variance, pas σ). Robuste aux singularités : si la diagonale
    a un zéro on retombe sur la moyenne arithmétique inverse (hack pragmatique)."""
    sub = cov[np.ix_(indices, indices)]
    diag = np.diag(sub)
    inv_var = np.where(diag > 1e-12, 1.0 / diag, 0.0)
    s = inv_var.sum()
    if s <= 0:
        # Sous-cluster dégénéré → equal weight, variance = mean(diag).
        return float(np.mean(diag)) if len(diag) else 0.0
    w = inv_var / s
    return float(w @ sub @ w)


def _recursive_bisection(cov: np.ndarray, sorted_idx: list[int]) -> dict[int, float]:
    """Allocation HRP par bissection récursive sur l'ordre quasi-diag.

    À chaque étape :
      • coupe le cluster courant en deux moitiés contiguës (split du milieu).
      • calcule σ²_left, σ²_right.
      • alloue α = 1 − σ²_left / (σ²_left + σ²_right) au left, (1−α) au right.

    Retourne {idx_position: weight} avec poids sommant à 1.
    """
    weights = {i: 1.0 for i in sorted_idx}
    clusters = [sorted_idx]
    while clusters:
        next_clusters: list[list[int]] = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            mid = len(cluster) // 2
            left = cluster[:mid]
            right = cluster[mid:]

            var_left = _cluster_variance(cov, left)
            var_right = _cluster_variance(cov, right)
            denom = var_left + var_right
            if denom > 0:
                alpha = 1.0 - var_left / denom   # left reçoit (1 - σ²_l/(σ²_l+σ²_r))
            else:
                alpha = 0.5
            for i in left:
                weights[i] *= alpha
            for i in right:
                weights[i] *= (1.0 - alpha)
            next_clusters.extend([left, right])
        clusters = next_clusters
    return weights


def _fetch_returns(
    tickers: list[str],
    market_provider: MarketDataProviderBase,
    window_days: int,
) -> dict[str, pd.Series]:
    """Récupère les log-returns daily 60j+5buf pour chaque ticker.
    Fail-open : un ticker sans data → exclu silencieusement du dict."""
    try:
        history = market_provider.get_daily_history_batch(tickers, window_days + 5)
    except Exception as e:
        logger.warning(f"[HRP] history batch failed: {e}")
        return {}

    out: dict[str, pd.Series] = {}
    for t in tickers:
        s = history.get(t) if isinstance(history, dict) else None
        if s is None:
            continue
        s = pd.Series(s).dropna().astype(float)
        s = s[s > 0]
        if len(s) < _MIN_PAIRWISE_DAYS + 1:
            continue
        r = np.log(s / s.shift(1)).dropna()
        if len(r) >= _MIN_PAIRWISE_DAYS:
            out[t] = r.tail(window_days)
    return out


def compute_hrp_weights(
    tickers: list[str],
    scored: dict[str, dict[str, Any]],
    *,
    market_provider: MarketDataProviderBase | None = None,
    window_days: int = DEFAULT_HRP_WINDOW_DAYS,
) -> tuple[dict[str, float], dict[str, dict[str, Any]], bool, dict[str, Any]]:
    """Compute HRP weights or fallback (équivalent contrat à _risk_parity.compute_weights).

    Returns:
        (weights, vol_diag, equal_weight_fallback, hrp_diag)
        - weights : {ticker: w} sommant à 1.
        - vol_diag : {ticker: {vol, imputed_vol, method}} pour cohérence aval.
        - equal_weight_fallback : True si on a basculé sur equal-weight (pas
          vraiment HRP).
        - hrp_diag : {applied, reason, n_tickers, sorted_order} pour audit UI.

    Fail-open : si market_provider absent, scipy absent, < 4 tickers, ou
    < 4 tickers avec data, retombe sur le risk-parity legacy via
    `_risk_parity.compute_weights`.
    """
    n = len(tickers)
    base_diag = {"method": "hrp", "n_tickers": n, "window_days": window_days}

    # 1. Fallback vers RP legacy si conditions HRP non remplies.
    def _fallback(reason: str) -> tuple[dict[str, float], dict[str, dict[str, Any]], bool, dict[str, Any]]:
        from ._risk_parity import compute_weights as rp_compute
        w, vd, eq = rp_compute(tickers, scored)
        return w, vd, eq, {**base_diag, "applied": False, "fallback_reason": reason}

    if n < _MIN_TICKERS_FOR_HRP:
        return _fallback(f"basket_too_small_{n}")
    if market_provider is None:
        return _fallback("no_market_provider")

    try:
        from scipy.cluster.hierarchy import linkage
        from scipy.spatial.distance import squareform
    except ImportError as e:
        logger.warning(f"[HRP] scipy missing, falling back to RP: {e}")
        return _fallback("scipy_missing")

    # 2. Fetch returns + corrélation/covariance
    returns_map = _fetch_returns(tickers, market_provider, window_days)
    if len(returns_map) < _MIN_TICKERS_FOR_HRP:
        return _fallback(f"only_{len(returns_map)}_tickers_with_data")

    df = pd.DataFrame(returns_map).dropna(how="all")
    if df.shape[0] < _MIN_PAIRWISE_DAYS or df.shape[1] < _MIN_TICKERS_FOR_HRP:
        return _fallback(f"insufficient_aligned_data_{df.shape}")

    corr = df.corr(min_periods=_MIN_PAIRWISE_DAYS)
    cov = df.cov(min_periods=_MIN_PAIRWISE_DAYS)
    if corr.empty or cov.empty or corr.isnull().any().any():
        # Couverture pairwise insuffisante → matrice avec NaN, on ne peut pas
        # passer ça au linkage. Fallback safe.
        return _fallback("nan_in_corr_matrix")

    aligned_tickers = list(corr.columns)

    # 3. Distance + linkage single-linkage
    try:
        dist = _correlation_distance(corr.to_numpy())
        # squareform exige une matrice symétrique avec diag à 0 et finite values.
        condensed = squareform(dist, checks=False)
        if not np.all(np.isfinite(condensed)):
            return _fallback("non_finite_distance")
        link_matrix = linkage(condensed, method="single")
    except Exception as e:
        logger.warning(f"[HRP] linkage failed: {e}")
        return _fallback(f"linkage_error: {str(e)[:80]}")

    # 4. Ordre quasi-diagonal des feuilles
    sorted_idx = _quasi_diag_order(link_matrix)
    sorted_tickers = [aligned_tickers[i] for i in sorted_idx]

    # 5. Recursive bisection
    cov_np = cov.to_numpy()
    raw_weights = _recursive_bisection(cov_np, sorted_idx)
    total = sum(raw_weights.values())
    if total <= 0:
        return _fallback("zero_total_weight")

    weights: dict[str, float] = {}
    for pos, ticker in zip(sorted_idx, sorted_tickers, strict=False):
        weights[ticker] = raw_weights[pos] / total

    # Tickers sans data dans l'aligné → equal-weight 0 (exclus de HRP).
    # Le caller doit re-normaliser après merge si certains sont droppés.
    excluded = [t for t in tickers if t not in weights]
    if excluded:
        # Plutôt qu'exclure, on leur donne le poids RP fallback puis renormalise
        # sur le total. Evite de droper silencieusement des tickers qui ont passé
        # le filtre score+price.
        from ._risk_parity import compute_weights as rp_compute
        rp_w, _vd, _eq = rp_compute(excluded, scored)
        # On les pondère à `1/n` du sous-set excluded × (poids résiduel) :
        # ici on suit le principe simple : équipondéré sur excluded, à hauteur
        # de leur part proportionnelle au panier (excluded count / total count).
        residual_share = len(excluded) / n
        # Rebalance : HRP weights × (1 - residual_share) + excluded × residual
        weights = {t: w * (1.0 - residual_share) for t, w in weights.items()}
        for t in excluded:
            weights[t] = rp_w.get(t, 0.0) * residual_share
        # Renormalise en filet de sécurité
        s = sum(weights.values())
        if s > 0:
            weights = {t: w / s for t, w in weights.items()}

    # 6. Diag par-ticker pour cohérence avec le contrat _risk_parity
    vol_diag: dict[str, dict[str, Any]] = {}
    for t in tickers:
        v = _safe_float((scored.get(t) or {}).get("momentum_volatility_pct"))
        vol_diag[t] = {
            "vol": v,
            "imputed_vol": v is None or v <= 0,
            "method": "hrp",
        }

    hrp_diag = {
        **base_diag,
        "applied": True,
        "fallback_reason": None,
        "n_aligned": len(aligned_tickers),
        "n_excluded": len(excluded),
        "sorted_order": sorted_tickers,
        "excluded": excluded,
    }
    return weights, vol_diag, False, hrp_diag
