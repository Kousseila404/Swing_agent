"""Risk parity : W_i = (1/σ_i) / Σ(1/σ_j) avec imputation σ manquante."""
from __future__ import annotations

import statistics
from typing import Any

from ._utils import _safe_float

# Minimum de σ valides pour que la risk parity soit statistiquement crédible.
MIN_VALID_VOLS_FOR_RISK_PARITY = 3
# Minimum de σ valides dans un secteur pour utiliser sa médiane comme imputation.
# Sous ce seuil, on retombe sur la médiane cross-portfolio.
MIN_VALID_VOLS_PER_SECTOR = 3


def compute_sector_vol_medians(
    scored: dict[str, dict[str, Any]],
) -> dict[str, float]:
    """Médiane σ cross-univers par secteur GICS — calculée sur scored COMPLET
    (pas seulement le portfolio). Un secteur avec < MIN_VALID_VOLS_PER_SECTOR
    échantillons ne produit pas de clé (le caller retombera sur la médiane
    cross-portfolio comme fallback).
    """
    sector_vols: dict[str, list[float]] = {}
    for row in scored.values():
        if not isinstance(row, dict):
            continue
        sec = row.get("sector") or "Unknown"
        v = _safe_float(row.get("momentum_volatility_pct"))
        if v is not None and v > 0:
            sector_vols.setdefault(sec, []).append(v)
    return {
        sec: statistics.median(vols)
        for sec, vols in sector_vols.items()
        if len(vols) >= MIN_VALID_VOLS_PER_SECTOR
    }


def compute_weights(
    tickers: list[str],
    scored: dict[str, dict[str, Any]],
) -> tuple[dict[str, float], dict[str, dict[str, Any]], bool]:
    """Retourne ({ticker: weight}, {ticker: vol_diag}, equal_weight_fallback).

    Risk parity :  W_i = (1/σ_i) / Σ_j (1/σ_j)

    Si moins de MIN_VALID_VOLS_FOR_RISK_PARITY tickers ont σ valide,
    on bascule sur equal weight pour TOUT le portefeuille (signal insuffisant).

    Imputation σ manquante :
      1. Priorité : médiane du MÊME secteur (cross-universe). Un secteur doit
         avoir ≥ MIN_VALID_VOLS_PER_SECTOR échantillons valides.
      2. Fallback : médiane cross-portfolio.
      La priorité sectorielle reflète le fait qu'un ticker manquant d'historique
      s'aligne plus probablement sur sa classe de risque sectorielle que sur un
      pool mixte (Utility ≠ Tech en σ).
    """
    vol_raw: dict[str, float | None] = {}
    sector_by_ticker: dict[str, str] = {}
    for t in tickers:
        row = scored.get(t) or {}
        vol_raw[t] = _safe_float(row.get("momentum_volatility_pct"))
        sector_by_ticker[t] = row.get("sector") or "Unknown"

    valid_vols = [v for v in vol_raw.values() if v is not None and v > 0]
    n_valid = len(valid_vols)

    diag: dict[str, dict[str, Any]] = {}
    if n_valid < MIN_VALID_VOLS_FOR_RISK_PARITY or not tickers:
        eq = 1.0 / len(tickers) if tickers else 0.0
        weights = {t: eq for t in tickers}
        for t in tickers:
            diag[t] = {
                "vol": vol_raw[t],
                "imputed_vol": False,
                "method": "equal_weight_fallback",
            }
        return weights, diag, True

    sector_medians = compute_sector_vol_medians(scored)
    portfolio_median = statistics.median(valid_vols)

    vol_used: dict[str, float] = {}
    for t in tickers:
        v = vol_raw[t]
        if v is None or v <= 0:
            sec = sector_by_ticker[t]
            sec_med = sector_medians.get(sec)
            if sec_med is not None and sec_med > 0:
                vol_used[t] = sec_med
                diag[t] = {
                    "vol": v, "imputed_vol": True,
                    "method": "sector_median_impute",
                    "imputation_sector": sec,
                    "imputed_value": round(sec_med, 4),
                }
            else:
                vol_used[t] = portfolio_median
                diag[t] = {
                    "vol": v, "imputed_vol": True,
                    "method": "portfolio_median_impute",
                    "imputed_value": round(portfolio_median, 4),
                }
        else:
            vol_used[t] = v
            diag[t] = {"vol": v, "imputed_vol": False, "method": "risk_parity"}

    inv = {t: 1.0 / vol_used[t] for t in tickers}
    denom = sum(inv.values())
    weights = {t: (inv[t] / denom) if denom > 0 else 0.0 for t in tickers}
    return weights, diag, False
