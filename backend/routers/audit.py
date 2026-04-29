"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — AUDIT (delisted registry + WFO weights + history)      ║
║  GET /api/delisted     — registry point-in-time tickers retirés  ║
║  GET /api/wfo          — résultat le plus récent (poids OOS)     ║
║  GET /api/wfo/history  — série temporelle IC test (jsonl)        ║
║                                                                  ║
║  Audit S1.1 + S1.3 (2026-04-27). Endpoints public read-only,    ║
║  pas d'auth (mêmes données que /api/data_health).                ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from modules import audit_summary, delisted, wfo_calibration, wfo_monitor

router = APIRouter(prefix="/api", tags=["audit"])


# ─────────────────────────────────────────────────────────────────
# /api/audit/full — verdict global + checklist + sources brutes
# ─────────────────────────────────────────────────────────────────

@router.get("/audit/full")
def get_audit_full(
    refresh: bool = Query(
        default=False,
        description="Si true, force le re-run du backtest (sinon cache 24h).",
    ),
) -> dict[str, Any]:
    """Snapshot complet pour la page Audit React.

    Agrège backtest TITAN top-N + WFO + registry delisted + historique
    snapshots, puis construit la checklist et le verdict global
    "TITAN bat-il le marché ?".
    """
    return audit_summary.compute_full_audit(refresh=refresh)


# ─────────────────────────────────────────────────────────────────
# /api/delisted
# ─────────────────────────────────────────────────────────────────

@router.get("/delisted")
def get_delisted() -> dict[str, Any]:
    """Registry des tickers historiquement présents dans universe.json.

    Sortie :
        {
          "n_total":     int,        # tickers ever vu (inclut actifs)
          "n_delisted":  int,        # actuellement marqués delisted
          "n_active":    int,        # actuellement actifs
          "delisted":    [...],      # détail des tickers retirés (trié desc)
          "registry_path": str,
        }
    """
    registry = delisted.load_registry()
    tickers_meta: dict[str, dict[str, Any]] = registry.get("tickers") or {}
    delisted_rows = delisted.list_delisted()
    n_active = sum(1 for m in tickers_meta.values() if not m.get("removed_at"))
    return {
        "n_total":       len(tickers_meta),
        "n_delisted":    len(delisted_rows),
        "n_active":      n_active,
        "delisted":      delisted_rows,
        "registry_path": str(delisted.DELISTED_PATH.name),
    }


# ─────────────────────────────────────────────────────────────────
# /api/wfo
# ─────────────────────────────────────────────────────────────────

@router.get("/wfo")
def get_wfo_weights() -> dict[str, Any]:
    """Dernier résultat de `python -m modules.wfo_calibration`.

    Lit `data/wfo_weights.json` si présent. 404 si jamais exécuté.

    Inclut une comparaison directe avec les poids prod hardcodés dans
    `sector_metrics/_scoring.py` (utile pour repérer un drift).
    """
    if not wfo_calibration.WFO_OUTPUT_PATH.exists():
        raise HTTPException(
            404,
            "wfo_weights.json absent — lancer "
            "`python -m modules.wfo_calibration` d'abord.",
        )
    try:
        payload = json.loads(
            wfo_calibration.WFO_OUTPUT_PATH.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as e:
        raise HTTPException(503, f"wfo_weights.json corrompu : {e}") from e

    # Comparaison avec les poids prod actuels — extrait depuis _scoring.
    try:
        from modules.sector_metrics import _scoring
        prod_weights = {
            "quality_score":    _scoring._W_TITAN_QUALITY,
            "value_score":      _scoring._W_TITAN_VALUE,
            "risk_score":       _scoring._W_TITAN_RISK,
            "sentiment_score":  _scoring._W_TITAN_SENTIMENT,
            "momentum_score":   _scoring._W_TITAN_MOMENTUM,
            "piotroski_score":  _scoring._W_TITAN_PIOTROSKI,
            "growth_score":     _scoring._W_TITAN_GROWTH,
        }
    except Exception:
        prod_weights = {}

    avg = payload.get("avg_weights") or {}
    deltas: dict[str, float] = {}
    for p, w_opt in avg.items():
        w_prod = prod_weights.get(p, 0.0)
        deltas[p] = round(w_opt - w_prod, 4)

    payload["prod_weights"] = prod_weights
    payload["weight_deltas"] = deltas
    return payload


# ─────────────────────────────────────────────────────────────────
# /api/wfo/history
# ─────────────────────────────────────────────────────────────────

@router.get("/wfo/history")
def get_wfo_history(
    limit: int = Query(
        default=50, ge=1, le=500,
        description="Nombre d'entrées historiques à retourner (les plus récentes).",
    ),
) -> dict[str, Any]:
    """Série temporelle des runs `wfo_monitor` — utile pour grapher
    l'évolution de l'IC composite et le drift des poids dans le temps.

    Source : `data/wfo_history.jsonl` (1 ligne JSON par run, append-only).

    Sortie :
        {
          "n_total":   int,     # nb total d'entrées dans le fichier
          "n_returned":int,     # nb effectivement servi (≤ limit)
          "history":   [...],   # entries triées chronologiquement croissant
          "latest":    dict | None,  # dernière entry (le plus récent)
          "threshold_ic": float,
        }
    """
    entries = wfo_monitor._read_history(last_n=limit)
    n_total = 0
    if wfo_monitor.WFO_HISTORY_PATH.exists():
        try:
            n_total = sum(
                1 for ln in wfo_monitor.WFO_HISTORY_PATH.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            )
        except OSError:
            n_total = len(entries)

    return {
        "n_total":      n_total,
        "n_returned":   len(entries),
        "history":      entries,
        "latest":       entries[-1] if entries else None,
        "threshold_ic": wfo_monitor._IC_DEGRADATION_THRESHOLD,
    }
