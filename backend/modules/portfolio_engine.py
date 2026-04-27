"""Façade backward-compat pour le moteur portfolio — réexporte `modules.portfolio`.

Le code vit désormais dans le package `modules/portfolio/` :
  - `_utils.py`        : helpers purs (`_safe_float`, `_age_days_from_iso`)
  - `_filters.py`      : `filter_bullish`, `filter_top_by_score`
  - `_risk_parity.py`  : `compute_weights` + imputation σ sectorielle
  - `_sector_cap.py`   : `apply_sector_cap` itératif
  - `_caches.py`       : caches mtime-keyed live price + live momentum
  - `_manager.py`      : `PortfolioManager` + `AllocationResult` (coordinateur)

Ce fichier existe uniquement pour préserver les imports historiques :
  `from modules.portfolio_engine import PortfolioManager`
"""
from __future__ import annotations

from modules.portfolio import (
    DEFAULT_MAX_HOLDINGS,
    DEFAULT_SECTOR_CAP,
    FUNDAMENTAL_STALE_DAYS_SEVERE,
    FUNDAMENTAL_STALE_DAYS_WARNING,
    MIN_VALID_VOLS_FOR_RISK_PARITY,
    AllocationResult,
    PortfolioManager,
    _age_days_from_iso,
)

__all__ = [
    "AllocationResult",
    "DEFAULT_MAX_HOLDINGS",
    "DEFAULT_SECTOR_CAP",
    "FUNDAMENTAL_STALE_DAYS_SEVERE",
    "FUNDAMENTAL_STALE_DAYS_WARNING",
    "MIN_VALID_VOLS_FOR_RISK_PARITY",
    "PortfolioManager",
    "_age_days_from_iso",
]
