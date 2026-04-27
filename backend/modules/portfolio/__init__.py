"""Package portfolio — moteur d'allocation TITAN."""
from __future__ import annotations

from ._manager import (
    DEFAULT_MAX_HOLDINGS,
    FUNDAMENTAL_STALE_DAYS_SEVERE,
    FUNDAMENTAL_STALE_DAYS_WARNING,
    AllocationResult,
    PortfolioManager,
)
from ._risk_parity import MIN_VALID_VOLS_FOR_RISK_PARITY
from ._sector_cap import DEFAULT_SECTOR_CAP
from ._state import compute_current_exposure, read_open_positions
from ._trade_levels import suggest_trade_levels
from ._utils import _age_days_from_iso

__all__ = [
    "AllocationResult",
    "DEFAULT_MAX_HOLDINGS",
    "DEFAULT_SECTOR_CAP",
    "FUNDAMENTAL_STALE_DAYS_SEVERE",
    "FUNDAMENTAL_STALE_DAYS_WARNING",
    "MIN_VALID_VOLS_FOR_RISK_PARITY",
    "PortfolioManager",
    "_age_days_from_iso",
    "compute_current_exposure",
    "read_open_positions",
    "suggest_trade_levels",
]
