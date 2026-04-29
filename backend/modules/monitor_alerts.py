"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE MONITOR_ALERTS — Surveillance quotidienne LT             ║
║                                                                  ║
║  Détecte 3 signaux sur les positions OPEN et émet 1 message      ║
║  Telegram consolidé :                                            ║
║                                                                  ║
║   1. TITAN drop ≥5 sur 7 derniers snapshots universe_history     ║
║   2. Drift thèse : |TITAN now − TITAN entry| ≥ 15                ║
║   3. Support break : level passe à OFF_SUPPORT                   ║
║                                                                  ║
║  Pure read — n'écrit pas dans le portfolio. Idéal cron daily.    ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from modules import alerter, universe_history
from modules.duckdb_journal import read_journal_df
from modules.log import logger
from modules.sector_metrics import get_scored_universe
from modules.thesis_stop import compute_sector_drift_baseline, compute_thesis_status

DROP_7D_THRESHOLD     = 5.0    # TITAN drop ≥5 points sur 7 jours
DRIFT_THESIS_THRESHOLD = 15.0  # |entry - now| ≥ 15
SUPPORT_BREAK_LEVELS  = {"OFF_SUPPORT", "BELOW_SUPPORT"}


def _open_positions() -> list[dict[str, Any]]:
    df = read_journal_df()
    if df is None or df.empty or "Status" not in df.columns:
        return []
    open_df = df[df["Status"] == "OPEN"]
    rows: list[dict[str, Any]] = []
    for _, r in open_df.iterrows():
        ticker = str(r.get("Ticker") or "").upper().strip()
        if not ticker:
            continue
        try:
            entry_titan = float(r.get("Titan_Score_Entry"))
        except (TypeError, ValueError):
            entry_titan = None
        rows.append({
            "ticker":      ticker,
            "entry_date":  str(r.get("Date") or "")[:10],
            "entry_titan": entry_titan,
            "sector":      r.get("Sector"),
            # Tous les *_Entry passés tels quels au compute_thesis_status.
            "entry_scores": {
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
        })
    return rows


def _titan_drop_7d(ticker: str, current: float | None) -> tuple[float | None, float | None]:
    """Retourne (titan_7d_ago, drop_pts). drop_pts > 0 = baisse."""
    if current is None:
        return (None, None)
    try:
        end = date.today()
        start = end - timedelta(days=10)  # marge si pas de snapshot exact à J-7
        rows = universe_history.ticker_history(
            ticker, start=start, end=end, fields=["titan_composite_score"],
        )
    except Exception as exc:
        logger.warning(f"[monitor_alerts] hist {ticker} fail: {exc}")
        return (None, None)
    if not rows:
        return (None, None)
    # Plus ancienne valeur dans la fenêtre.
    rows_sorted = sorted(rows, key=lambda r: r.get("date") or "")
    first = rows_sorted[0].get("titan_composite_score")
    if not isinstance(first, (int, float)):
        return (None, None)
    return (float(first), float(first) - float(current))


def collect_alerts() -> list[dict[str, Any]]:
    """Scanne positions OPEN et retourne les signaux à alerter."""
    positions = _open_positions()
    if not positions:
        return []

    universe = get_scored_universe() or {}

    # Pré-calcul des baselines sectorielles (drift médian TITAN par secteur)
    # pour évaluer les cassures de thèse en *relatif* — un name qui suit son
    # secteur n'a pas cassé sa thèse propre.
    enriched_for_baseline = []
    for p in positions:
        info = universe.get(p["ticker"]) or {}
        enriched_for_baseline.append({
            **p,
            "current_titan": info.get("titan_composite_score"),
        })
    sector_baselines = compute_sector_drift_baseline(enriched_for_baseline, min_n=3)

    alerts: list[dict[str, Any]] = []

    for p in positions:
        ticker = p["ticker"]
        info = universe.get(ticker) or {}
        current = info.get("titan_composite_score")
        if not isinstance(current, (int, float)):
            continue
        entry = p.get("entry_titan")
        signals: list[str] = []

        # Drift entry → now
        if isinstance(entry, (int, float)):
            drift = float(current) - float(entry)
            if abs(drift) >= DRIFT_THESIS_THRESHOLD:
                arrow = "↑" if drift > 0 else "↓"
                signals.append(
                    f"Drift thèse {arrow} {abs(drift):.1f} pts "
                    f"(entry {entry:.0f} → now {float(current):.0f})"
                )

        # Drop 7d
        prev, drop = _titan_drop_7d(ticker, current)
        if drop is not None and drop >= DROP_7D_THRESHOLD:
            signals.append(
                f"TITAN −{drop:.1f} pts sur 7j "
                f"({prev:.0f} → {float(current):.0f})"
            )

        # Support break (depuis support si présent dans universe info)
        support_level = (info.get("support") or {}).get("level")
        if support_level and support_level in SUPPORT_BREAK_LEVELS:
            signals.append(f"Support broken : {support_level}")

        # Thesis stop fondamental — drift cumulé sur Q/V/R/M/P + tilts apparus.
        # On surface UNIQUEMENT BROKEN ici (les WARN sont vus dans la modal).
        # Évite le spam Telegram pendant les phases de bruit normales.
        # Drift TITAN évalué en relatif au baseline sectoriel quand disponible.
        thesis_status = None
        try:
            sector = p.get("sector")
            baseline = sector_baselines.get(str(sector)) if sector else None
            thesis = compute_thesis_status(
                p.get("entry_scores") or {}, info,
                sector_drift_baseline=baseline,
            )
            thesis_status = thesis.get("status")
            if thesis_status == "BROKEN":
                top = thesis.get("reasons_break") or []
                detail = " · ".join(top[:2]) if top else "drift fondamental"
                signals.append(f"Thèse cassée : {detail}")
        except Exception as exc:
            logger.debug(f"[monitor_alerts] thesis_status {ticker} fail: {exc}")

        if signals:
            alerts.append({
                "ticker":   ticker,
                "current":  float(current),
                "entry":    entry,
                "sector":   p.get("sector"),
                "signals":  signals,
                "thesis_status": thesis_status,
            })

    return alerts


def format_alert_message(alerts: list[dict[str, Any]]) -> str:
    """Compose le message Telegram HTML consolidé."""
    if not alerts:
        return ""
    lines: list[str] = ["<b>🛰 SwingQuant — Monitor positions LT</b>", ""]
    for a in alerts:
        lines.append(
            f"<b>{a['ticker']}</b> · TITAN {a['current']:.0f}"
            + (f" · {a['sector']}" if a.get("sector") else "")
        )
        for s in a["signals"]:
            lines.append(f"  • {s}")
        lines.append("")
    lines.append(f"<i>{len(alerts)} position(s) signalée(s)</i>")
    return "\n".join(lines).strip()


def run_daily_monitor(*, dry_run: bool = False) -> dict[str, Any]:
    """Point d'entrée : scanne (positions OPEN + alertes seuil utilisateur),
    formate, envoie via Telegram. Retourne diag."""
    alerts = collect_alerts()

    # Tier B #3 — alertes seuil utilisateur (TITAN ≥/≤ X). Évalue contre le
    # même scored_universe pour cohérence. Best-effort : un échec ici ne casse
    # pas l'envoi du monitor classique.
    user_alerts: list[dict[str, Any]] = []
    try:
        from modules import titan_alerts as _ta
        user_alerts = _ta.evaluate_alerts(get_scored_universe() or {}) or []
    except Exception as exc:
        logger.warning(f"[monitor_alerts] user alerts eval fail: {exc}")

    # Price alerts (entry_plan tiers) — lookup OHLCV daily close pour les
    # tickers ayant au moins une alerte active.
    price_alerts_fired: list[dict[str, Any]] = []
    try:
        from modules import price_alerts as _pa
        from modules.market_db import read_ohlcv as _read
        active = _pa.list_alerts(include_expired=False)
        tickers_to_price = {it["ticker"] for it in active if it.get("ticker")}
        prices: dict[str, float] = {}
        for t in tickers_to_price:
            try:
                df = _read(t, days=5)
                if df is not None and not df.empty and "Close" in df.columns:
                    last = df["Close"].dropna()
                    if not last.empty:
                        prices[t] = float(last.iloc[-1])
            except Exception:
                continue
        price_alerts_fired = _pa.evaluate_alerts(prices) or []
    except Exception as exc:
        logger.warning(f"[monitor_alerts] price alerts eval fail: {exc}")

    if not alerts and not user_alerts and not price_alerts_fired:
        return {"ok": True, "n_alerts": 0, "sent": False, "reason": "no_signals"}

    msg = format_alert_message(alerts)
    if user_alerts:
        ua_lines = ["", "<b>🔔 Alertes seuil utilisateur</b>", ""]
        for a in user_alerts:
            arrow = "≥" if a.get("direction") == "above" else "≤"
            ua_lines.append(
                f"• <b>{a['ticker']}</b> TITAN {a['current_titan']:.0f} "
                f"{arrow} {a['threshold']:.0f}"
                + (f" — {a['note']}" if a.get("note") else "")
            )
        msg = ((msg + "\n") if msg else "<b>🛰 SwingQuant — Alertes</b>\n") + "\n".join(ua_lines).strip()

    if price_alerts_fired:
        pa_lines = ["", "<b>🎯 Niveaux prix touchés (entry plan)</b>", ""]
        for a in price_alerts_fired:
            arrow = "≤" if a.get("direction") == "below" else "≥"
            pa_lines.append(
                f"• <b>{a['ticker']}</b> {a['current_price']:.2f} "
                f"{arrow} {a['target_price']:.2f}"
                + (f" — {a['note']}" if a.get("note") else "")
            )
        msg = ((msg + "\n") if msg else "<b>🛰 SwingQuant — Alertes</b>\n") + "\n".join(pa_lines).strip()

    n_total = len(alerts) + len(user_alerts) + len(price_alerts_fired)

    if dry_run:
        return {"ok": True, "n_alerts": n_total, "sent": False,
                "reason": "dry_run", "preview": msg,
                "alerts": alerts, "user_alerts": user_alerts,
                "price_alerts": price_alerts_fired}

    try:
        alerter._send_telegram_message(msg)
        return {"ok": True, "n_alerts": n_total, "sent": True,
                "alerts": alerts, "user_alerts": user_alerts,
                "price_alerts": price_alerts_fired}
    except Exception as exc:
        logger.error(f"[monitor_alerts] telegram send fail: {exc}")
        return {"ok": False, "n_alerts": n_total, "sent": False,
                "error": str(exc),
                "alerts": alerts, "user_alerts": user_alerts,
                "price_alerts": price_alerts_fired}
