"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — MACRO                                                  ║
║  GET /api/macro  — régime macro courant (lecture cache)          ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from fastapi import APIRouter

from modules import api_core
from modules.api_schemas import MacroResponse

router = APIRouter(prefix="/api", tags=["macro"])


@router.get("/macro", response_model=MacroResponse)
def get_macro():
    """Régime macro-économique depuis macro_state.json."""
    data = api_core.load_macro()
    if not data:
        return {
            "regime": "UNKNOWN", "vix": None, "sp500": None, "ema200": None,
            "sp500_vs_ema200_pct": None, "allowed_directions": ["LONG"],
            "last_update": None,
            "error": "macro_state.json introuvable — lancez python main.py --macro",
        }
    # Expose canonical `regime` (required by schema) tout en conservant
    # les champs historiques via `extra="allow"`.
    if "regime" not in data:
        data = {**data, "regime": data.get("confirmed_regime") or data.get("candidate_regime", "UNKNOWN")}
    return data
