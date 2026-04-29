"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — PERFORMANCE ATTRIBUTION                                ║
║  GET /api/attribution                                            ║
║                                                                  ║
║  Calcule le win rate par bucket de score TITAN à l'entrée,       ║
║  pour chaque pilier. Mode preview ok dès le 1er trade clos       ║
║  (drapeau "stable" si ≥20 trades).                               ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from modules import performance_attribution

router = APIRouter(prefix="/api", tags=["attribution"])


@router.get("/attribution")
def get_attribution() -> dict[str, Any]:
    return performance_attribution.compute_attribution()
