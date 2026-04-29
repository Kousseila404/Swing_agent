"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — MONITOR (alertes Telegram à la demande)                ║
║  GET  /api/monitor/preview         → dry run (pas d'envoi)       ║
║  POST /api/monitor/run             → scan + send Telegram        ║
║  GET  /api/thesis_status           → vue cockpit positions OPEN  ║
║                                                                  ║
║  Le mode dry-run permet de tester avant de spammer Telegram.     ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Security

from modules import api_core, monitor_alerts
from modules.sector_metrics import get_scored_universe
from modules.thesis_stop import compute_sector_drift_baseline, compute_thesis_status

router = APIRouter(prefix="/api", tags=["monitor"])


@router.get("/monitor/preview")
def preview_alerts() -> dict[str, Any]:
    """Dry run : scanne positions OPEN et retourne les signaux sans envoyer."""
    return monitor_alerts.run_daily_monitor(dry_run=True)


@router.post("/monitor/run")
def run_alerts(
    _auth: None = Security(api_core.require_auth),
) -> dict[str, Any]:
    """Run réel : envoie le message Telegram s'il y a des signaux."""
    return monitor_alerts.run_daily_monitor(dry_run=False)


@router.get("/thesis_status")
def thesis_status_all() -> dict[str, Any]:
    """Vue cockpit : thesis_status pour toutes les positions OPEN.

    Permet à la UI Portfolio de surfacer en un coup d'œil les positions BROKEN
    sans avoir à ouvrir chaque modal. Retourne :

        {
          "items": [
            {ticker, status, severity, drift, reasons_break[], reasons_warn[]},
            ...
          ],
          "summary": {n_total, n_intact, n_warn, n_broken, n_no_data}
        }
    """
    positions = monitor_alerts._open_positions()
    if not positions:
        return {
            "items": [],
            "summary": {"n_total": 0, "n_intact": 0, "n_warn": 0,
                        "n_broken": 0, "n_no_data": 0},
        }

    universe = get_scored_universe() or {}

    # Pré-calcul du baseline sectoriel : drift TITAN médian par secteur sur
    # toutes les positions OPEN avec entry+current valides. Permet d'évaluer
    # les drifts individuels en *relatif* (un name qui suit son secteur n'a
    # pas cassé sa thèse propre).
    enriched = []
    for p in positions:
        ticker = p["ticker"]
        info = universe.get(ticker) or {}
        enriched.append({
            **p,
            "current_titan": info.get("titan_composite_score"),
            "_universe_info": info,
        })
    sector_baselines = compute_sector_drift_baseline(enriched, min_n=3)

    items: list[dict[str, Any]] = []
    counts = {"INTACT": 0, "WARN": 0, "BROKEN": 0, "NO_DATA": 0}
    for p in enriched:
        ticker = p["ticker"]
        info = p["_universe_info"]
        sector = p.get("sector")
        baseline = sector_baselines.get(str(sector)) if sector else None
        try:
            thesis = compute_thesis_status(
                p.get("entry_scores") or {}, info,
                sector_drift_baseline=baseline,
            )
        except Exception:
            thesis = {
                "status": "NO_DATA", "severity": 0,
                "reasons_break": [], "reasons_warn": [], "drift": {},
            }
        st = thesis.get("status") or "NO_DATA"
        counts[st] = counts.get(st, 0) + 1
        items.append({
            "ticker":         ticker,
            "sector":         sector,
            "entry_date":     p.get("entry_date"),
            "entry_titan":    p.get("entry_titan"),
            "current_titan":  info.get("titan_composite_score"),
            **thesis,
        })

    # Ordre : BROKEN → WARN → INTACT → NO_DATA, puis severity desc.
    order = {"BROKEN": 3, "WARN": 2, "INTACT": 1, "NO_DATA": 0}
    items.sort(key=lambda x: (-order.get(x["status"], 0), x["ticker"]))

    return {
        "items": items,
        "summary": {
            "n_total":   len(items),
            "n_intact":  counts.get("INTACT", 0),
            "n_warn":    counts.get("WARN", 0),
            "n_broken":  counts.get("BROKEN", 0),
            "n_no_data": counts.get("NO_DATA", 0),
        },
        "sector_baselines": sector_baselines,
    }
