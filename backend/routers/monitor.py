"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — MONITOR (alertes Telegram à la demande)                ║
║  GET  /api/monitor/preview         → dry run (pas d'envoi)       ║
║  POST /api/monitor/run             → scan + send Telegram        ║
║  GET  /api/thesis_status           → vue cockpit positions OPEN  ║
║  GET  /api/lt_decision             → décisions Buffett portfolio ║
║  GET  /api/lt_decision/{ticker}    → décision unique             ║
║                                                                  ║
║  Le mode dry-run permet de tester avant de spammer Telegram.     ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, HTTPException, Security

from modules import api_core, lt_exit_policy, monitor_alerts
from modules.data_confidence import compute_confidence
from modules.fundamentals_levels import compute_fundamental_levels, compute_trailing_fundamental_sl
from modules.portfolio import suggest_trade_levels
from modules.portfolio._sizing_buffett import _tilt_factor
from modules.sector_metrics import get_scored_universe
from modules.thesis_stop import compute_sector_drift_baseline, compute_thesis_status
from modules.tracker.market import get_current_price

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


def _build_decisions_for_open_positions() -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Construit la liste des décisions LT pour toutes les positions OPEN.

    Helper partagé entre l'endpoint agrégé et l'endpoint par-ticker pour
    garantir une lecture cohérente du sector baseline.
    """
    from modules.duckdb_journal import read_journal_df

    df = read_journal_df()
    if df is None or df.empty or "Status" not in df.columns:
        return [], {}
    open_df = df[df["Status"] == "OPEN"]
    if open_df.empty:
        return [], {}

    universe = get_scored_universe() or {}

    # Sector drift baseline (entry → now) sur l'ensemble des positions OPEN.
    positions_for_drift: list[dict[str, Any]] = []
    for _, r in open_df.iterrows():
        t = str(r.get("Ticker") or "").upper().strip()
        if not t:
            continue
        info = universe.get(t) or {}
        positions_for_drift.append({
            "sector":        info.get("sector"),
            "entry_titan":   r.get("Titan_Score_Entry"),
            "current_titan": info.get("titan_composite_score"),
        })
    sector_baselines = compute_sector_drift_baseline(positions_for_drift, min_n=3)

    decisions: list[dict[str, Any]] = []
    for _, r in open_df.iterrows():
        t = str(r.get("Ticker") or "").upper().strip()
        if not t:
            continue
        info = universe.get(t) or {}
        sector = info.get("sector")
        baseline = sector_baselines.get(str(sector)) if sector else None
        try:
            thesis = compute_thesis_status(
                entry={
                    "Titan_Score_Entry": r.get("Titan_Score_Entry"),
                    "Quality_Entry":     r.get("Quality_Entry"),
                    "Value_Entry":       r.get("Value_Entry"),
                    "Risk_Entry":        r.get("Risk_Entry"),
                    "Momentum_Entry":    r.get("Momentum_Entry"),
                    "Piotroski_Entry":   r.get("Piotroski_Entry"),
                    "Growth_Entry":      r.get("Growth_Entry"),
                    "F_Score_Entry":     r.get("F_Score_Entry"),
                    "Tilt_Flags_Entry":  r.get("Tilt_Flags_Entry") or "",
                },
                current=info,
                sector_drift_baseline=baseline,
            ) if info else None
        except Exception:
            thesis = None

        # Prix courant : on tente le live, fallback sur le scored_universe.
        try:
            current_price = get_current_price(t)
        except Exception:
            current_price = None
        if current_price is None:
            current_price = info.get("price") or info.get("close")
        try:
            current_price = float(current_price) if current_price is not None else None
            if current_price is not None and (math.isnan(current_price) or current_price <= 0):
                current_price = None
        except (TypeError, ValueError):
            current_price = None

        try:
            entry_price = float(r.get("Entry"))
        except (TypeError, ValueError):
            continue
        try:
            sl = float(r.get("Stop_Loss")) if r.get("Stop_Loss") else None
            if sl is not None and (math.isnan(sl) or sl <= 0):
                sl = None
        except (TypeError, ValueError):
            sl = None

        # Refonte 2026-04-29 (étape 1 Buffett) — niveaux fundamentals-anchored.
        # On les calcule AVANT lt_exit_policy.decide pour pouvoir alimenter le
        # signal EXIT_OVERVALUED_VS_FAIRVALUE (étape 2). Note : le scored_universe
        # expose `f_score` (0-9 brut) et `piotroski_score` (pillar 0-100) — on
        # passe `f_score` au module fundamentals_levels.
        buffett = compute_fundamental_levels(
            current_price if current_price else entry_price,
            quality_score=info.get("quality_score") if info else None,
            piotroski_score=info.get("f_score") if info else None,
            value_score=info.get("value_score") if info else None,
            peg_ratio=info.get("peg_ratio") if info else None,
            direction=str(r.get("Direction") or "LONG"),
        )

        # Étape 4 Buffett — détection de dérive catégorie. On reconstitue la
        # catégorie à l'entrée depuis Quality_Entry/F_Score_Entry (déjà
        # capturés par /proposals/approve_batch dans le journal) et on la
        # compare avec la catégorie courante calculée juste plus haut.
        def _parse_f(v):
            try:
                if v is None:
                    return None
                s = str(v).strip()
                if "/" in s:
                    return int(s.split("/")[0])
                return int(float(s))
            except (TypeError, ValueError):
                return None

        entry_q = r.get("Quality_Entry")
        entry_f = _parse_f(r.get("F_Score_Entry"))
        try:
            entry_q = float(entry_q) if entry_q is not None else None
        except (TypeError, ValueError):
            entry_q = None
        entry_category = _tilt_factor(entry_q, entry_f)[1] if (entry_q or entry_f) else None
        current_category = _tilt_factor(
            info.get("quality_score") if info else None,
            info.get("f_score") if info else None,
        )[1]

        # Phase 1 data hardening — score de confiance des inputs.
        confidence = compute_confidence(info)

        # Trailing SL fondamental — précalculé pour insertion dans decisions.
        _trailing = compute_trailing_fundamental_sl(
            entry_price=entry_price,
            entry_quality=r.get("Quality_Entry"),
            entry_piotroski=_parse_f(r.get("F_Score_Entry")),
            current_quality=info.get("quality_score") if info else None,
            current_piotroski=info.get("f_score") if info else None,
        )

        d = lt_exit_policy.decide(
            ticker=t,
            entry_price=entry_price,
            current_price=current_price,
            stop_loss=sl,
            direction=str(r.get("Direction") or "LONG"),
            thesis=thesis,
            entry_value_pillar=r.get("Value_Entry"),
            current_value_pillar=info.get("value_score"),
            current_peg_ratio=info.get("peg_ratio"),
            current_forward_pe=info.get("forward_pe"),
            support_level=info.get("support_level"),
            # Étape 2 Buffett — fair value ceiling fundamentals-anchored.
            buffett_tp=buffett.get("tp"),
            let_it_ride=bool(buffett.get("let_it_ride")),
            # Étape 4 Buffett — dérive catégorie entry → now.
            entry_category=entry_category,
            current_category=current_category,
            # Phase 1 data hardening — confidence courante + entrée capturée.
            confidence_score=confidence["score"],
            entry_confidence=(int(float(r.get("Confidence_Entry")))
                              if r.get("Confidence_Entry") not in (None, "", "nan") else None),
            # Insider signal Buffett — net sells persistents = TRIM.
            insider_score=info.get("insider_score") if info else None,
        )

        # Niveaux théoriques σ-scaled (catastrophe_floor) — informatif.
        try:
            tp = float(r.get("Take_Profit")) if r.get("Take_Profit") else None
            if tp is not None and (math.isnan(tp) or tp <= 0):
                tp = None
        except (TypeError, ValueError):
            tp = None
        vol_pct = (
            (info.get("momentum_volatility_pct") if info else None)
            or (info.get("volatility_pct") if info else None)
        )
        theoretical = suggest_trade_levels(
            entry_price, vol_pct, direction=str(r.get("Direction") or "LONG"),
        )

        decisions.append({
            **d.to_dict(),
            "entry_price":   round(entry_price, 4),
            "current_price": round(current_price, 4) if current_price else None,
            "stop_loss":     round(sl, 4) if sl else None,
            "take_profit":   round(tp, 4) if tp else None,
            "sector":        sector,
            "entry_date":    str(r.get("Date") or "")[:10],
            # Niveaux théoriques sous la refonte Buffett-LT (2026-04-29).
            "theoretical_sl":     theoretical.get("sl"),
            "theoretical_tp":     theoretical.get("tp"),
            "theoretical_sl_pct": theoretical.get("sl_pct"),
            "theoretical_tp_pct": theoretical.get("tp_pct"),
            "theoretical_method": theoretical.get("method"),
            # Niveaux Buffett-anchored (étape 1 — informatif, n'altère pas le broker).
            "buffett_sl":          buffett.get("sl"),
            "buffett_tp":          buffett.get("tp"),
            "buffett_sl_pct":      buffett.get("sl_pct"),
            "buffett_tp_pct":      buffett.get("tp_pct"),
            "buffett_method":      buffett.get("method"),
            "buffett_let_it_ride": buffett.get("let_it_ride"),
            "buffett_breakdown":   buffett.get("breakdown"),
            # Catégorie de sizing (étape 3) — compounder/high_quality/baseline/junior/junk.
            "buffett_category":    _tilt_factor(
                info.get("quality_score") if info else None,
                info.get("f_score") if info else None,
            )[1],
            "buffett_size_factor": _tilt_factor(
                info.get("quality_score") if info else None,
                info.get("f_score") if info else None,
            )[0],
            # Phase 1 data hardening — score de confiance.
            "confidence_score":     confidence["score"],
            "confidence_tier":      confidence["tier"],
            "confidence_breakdown": confidence["breakdown"],
            # Trailing fundamental SL — se resserre si la qualité s'érode
            # depuis l'entrée. Indicatif (n'altère pas le SL broker).
            **{
                f"trailing_{k}": v for k, v in _trailing.items()
            },
        })

    # Tri : sévérité desc, puis ticker.
    decisions.sort(key=lambda x: (-int(x.get("severity") or 0), x.get("ticker") or ""))
    return decisions, sector_baselines


@router.get("/lt_decision")
def lt_decision_all() -> dict[str, Any]:
    """Décisions LT (refonte 2026-04-29) pour toutes les positions OPEN.

    Agrège thesis_stop + drawdown + survalorisation + signal Buffett d'ADD_ON
    en une recommandation unique par position. Source de vérité pour le badge
    cockpit Portfolio.
    """
    decisions, sector_baselines = _build_decisions_for_open_positions()
    if not decisions:
        return {
            "items": [],
            "summary": {
                "counts": {}, "actionable_count": 0,
                "highest_severity": 0, "tickers_by_action": {},
            },
            "sector_baselines": sector_baselines,
        }

    # Agrégation portfolio (counts, severity max, tickers par action).
    decisions_dc = [
        lt_exit_policy.LTDecision(
            ticker=d["ticker"], action=d["action"], severity=int(d["severity"]),
        )
        for d in decisions
    ]
    summary = lt_exit_policy.aggregate_portfolio(decisions_dc)
    return {
        "items": decisions,
        "summary": summary,
        "sector_baselines": sector_baselines,
    }


@router.get("/lt_decision/{ticker}")
def lt_decision_one(ticker: str) -> dict[str, Any]:
    """Décision LT pour un ticker précis (pour TickerAnalysisModal)."""
    ticker = (ticker or "").upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker manquant")
    decisions, _ = _build_decisions_for_open_positions()
    for d in decisions:
        if d["ticker"] == ticker:
            return d
    raise HTTPException(status_code=404, detail=f"{ticker} non trouvé en position OPEN")
