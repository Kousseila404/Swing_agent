"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — COCKPIT (audit 2026-09-17, Lot 6)                      ║
║  GET  /api/performance/benchmark   compte vs SPY vs panier TITAN  ║
║  GET  /api/portfolio/protection    stops broker par position      ║
║  GET  /api/system/health           tracker / killswitch / CB /    ║
║                                    crons / broker                 ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Security

from modules import api_core
from modules.log import logger

router = APIRouter(prefix="/api", tags=["cockpit"])

_BACKEND = Path(__file__).resolve().parents[1]
_LOGS = _BACKEND / "logs"

_PROTECTION_TTL_SEC = 60
_protection_cache: dict[str, Any] = {"at": 0.0, "data": None}


# ─────────────────────────────────────────────────────────────────
# /performance/benchmark
# ─────────────────────────────────────────────────────────────────

@router.get("/performance/benchmark")
def get_performance_benchmark(
    months: int = Query(6, ge=1, le=12),
    top_n: int = Query(20, ge=5, le=50),
    _auth: None = Security(api_core.require_auth),
):
    """Compte réel (Alpaca) vs SPY vs panier TITAN top-N théorique, rebasés à 100.

    Le panier est calculé sur la fenêtre live uniquement (snapshots ≥ 2026-04-22,
    sans look-ahead) et mis en cache 12 h (`data/.perf_benchmark_cache.json`).
    """
    from modules.perf_benchmark import build_benchmark
    try:
        return build_benchmark(months=months, top_n=top_n)
    except Exception as exc:
        logger.error(f"[/performance/benchmark] {exc}", exc_info=True)
        return {"dates": [], "series": {}, "summary": {"notes": [f"erreur : {exc}"]}}


# ─────────────────────────────────────────────────────────────────
# /portfolio/protection
# ─────────────────────────────────────────────────────────────────

def _f(v) -> float | None:
    try:
        x = float(v)
        return x if x == x and x > 0 else None
    except (TypeError, ValueError):
        return None


def compute_protection(open_rows: list[dict[str, Any]], broker) -> dict[str, Any]:
    """Pour chaque position OPEN : stop broker présent ? niveau ? écart vs journal ?

    `broker` : AlpacaBroker (ou None → mode paper, protection = journal seulement).
    Pure vis-à-vis du cache — testable avec un faux broker.
    """
    items: list[dict[str, Any]] = []
    positions: dict[str, Any] = {}
    client = None
    if broker is not None:
        try:
            client = broker._get_client()
            positions = {str(p.symbol).upper(): p for p in client.get_all_positions()}
        except Exception as exc:
            logger.warning(f"[/portfolio/protection] Alpaca indisponible : {exc}")
            client = None

    for row in open_rows:
        ticker = str(row.get("Ticker") or row.get("ticker") or "").upper()
        if not ticker:
            continue
        direction = str(row.get("Direction") or row.get("direction") or "LONG").upper()
        entry = _f(row.get("Entry") or row.get("entry"))
        sl_journal = _f(row.get("Stop_Loss") or row.get("stop_loss"))
        tp_journal = _f(row.get("Take_Profit") or row.get("take_profit"))
        current = _f(row.get("current_price"))
        item: dict[str, Any] = {
            "ticker": ticker, "direction": direction, "entry": entry,
            "current_price": current, "size": row.get("Size") or row.get("size"),
            "sl_journal": sl_journal, "tp_journal": tp_journal,
            "broker_position": ticker in positions if client else None,
            "broker_stop": None, "broker_tp": None, "broker_order_class": None,
            "protected": None, "drift_pct": None, "pct_to_stop": None, "status": "unknown",
        }
        if client is not None:
            if ticker not in positions:
                item["status"] = "no_broker_position"
                item["protected"] = False
            else:
                try:
                    orders = broker._open_orders_for(client, ticker)
                    stop_o = broker._find_open_stop(orders, direction)
                    limit_o = broker._find_open_limit(orders, direction)
                    if stop_o is not None:
                        item["broker_stop"] = _f(getattr(stop_o, "stop_price", None))
                        cls = getattr(stop_o, "order_class", None)
                        item["broker_order_class"] = str(getattr(cls, "value", cls) or "simple").lower()
                    if limit_o is not None:
                        item["broker_tp"] = _f(getattr(limit_o, "limit_price", None))
                    if item["broker_stop"]:
                        item["protected"] = True
                        item["status"] = "protected"
                        if sl_journal:
                            item["drift_pct"] = round((item["broker_stop"] - sl_journal) / sl_journal * 100.0, 2)
                            if abs(item["drift_pct"]) > 1.0:
                                item["status"] = "drift"
                    else:
                        item["protected"] = False
                        item["status"] = "unprotected"
                except Exception as exc:
                    logger.warning(f"[/portfolio/protection] {ticker} : {exc}")
                    item["status"] = "error"
        else:
            item["protected"] = bool(sl_journal)
            item["status"] = "journal_only" if sl_journal else "unprotected"
        ref_stop = item["broker_stop"] or sl_journal
        if ref_stop and current:
            item["pct_to_stop"] = round((current - ref_stop) / current * 100.0, 2)
        items.append(item)

    n_unprotected = sum(1 for i in items if i["protected"] is False)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "broker": getattr(broker, "name", "PaperBroker") if broker is not None else "PaperBroker",
        "n_positions": len(items),
        "n_unprotected": n_unprotected,
        "all_protected": n_unprotected == 0 and all(i["protected"] for i in items) if items else True,
        "items": items,
    }


@router.get("/portfolio/protection")
def get_portfolio_protection(
    refresh: bool = Query(False),
    _auth: None = Security(api_core.require_auth),
):
    """État de protection (stop broker) de chaque position OPEN. Cache 60 s."""
    now = time.time()
    if not refresh and _protection_cache["data"] is not None and now - _protection_cache["at"] < _PROTECTION_TTL_SEC:
        return _protection_cache["data"]
    equity = api_core.load_equity() or {}
    live_px = {str(p.get("ticker")).upper(): p.get("current_price") for p in equity.get("open_positions", [])}
    rows = [r for r in api_core.load_journal() if str(r.get("Status")) == "OPEN"]
    for r in rows:
        r["current_price"] = live_px.get(str(r.get("Ticker")).upper())
    broker = None
    try:
        import config
        if str(getattr(config, "BROKER_MODE", "paper")).lower() == "alpaca":
            from modules.broker_gateway import AlpacaBroker, get_broker
            b = get_broker()
            broker = b if isinstance(b, AlpacaBroker) else None
    except Exception as exc:
        logger.warning(f"[/portfolio/protection] broker : {exc}")
    data = compute_protection(rows, broker)
    _protection_cache["at"] = now
    _protection_cache["data"] = data
    return data


# ─────────────────────────────────────────────────────────────────
# /system/health
# ─────────────────────────────────────────────────────────────────

def _mtime_age(path: Path) -> float | None:
    try:
        return round(time.time() - path.stat().st_mtime, 1) if path.exists() else None
    except Exception:
        return None


def _last_log_line(path: Path) -> str | None:
    try:
        if not path.exists():
            return None
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 4096))
            tail = fh.read().decode("utf-8", errors="replace").strip().splitlines()
        return tail[-1][:200] if tail else None
    except Exception:
        return None


@router.get("/system/health")
def get_system_health(_auth: None = Security(api_core.require_auth)):
    """Vue « tout va bien ? » : tracker (heartbeat), killswitch, circuit breaker,
    dernière exécution des crons (mtime des logs), broker, données."""
    hb = api_core.read_json(api_core.TRACKER_HEARTBEAT_PATH, {}) or {}
    hb_age = None
    try:
        hb_age = round(time.time() - float(hb.get("epoch") or 0), 1) if hb.get("epoch") else None
    except (TypeError, ValueError):
        hb_age = None
    trading = api_core.read_json(api_core.TRADING_PATH, {}) or {}
    cb = api_core.read_json(_BACKEND / "data" / "circuit_breaker_state.json", {}) or {}
    macro = api_core.load_macro() or {}
    crons = {
        name: {"log_age_sec": _mtime_age(_LOGS / f"{fname}"), "last_line": _last_log_line(_LOGS / f"{fname}")}
        for name, fname in (
            ("tracker", "tracker.log"), ("alpaca_sync", "alpaca_sync.log"),
            ("auto_approve", "auto_approve.log"), ("daily_digest", "daily_digest.log"),
            ("monitor", "monitor.log"),
        )
    }
    titan_log = _BACKEND.parent / "logs" / "titan_daily.log"
    crons["titan_daily"] = {"log_age_sec": _mtime_age(titan_log), "last_line": _last_log_line(titan_log)}
    universe = api_core.read_json(api_core.UNIVERSE_QUANTAMENTAL_PATH, {}) or {}
    import config
    checks = []
    if hb_age is None or hb_age > 15 * 60:
        checks.append({"level": "danger", "msg": "Tracker silencieux (> 15 min)"})
    if trading.get("blocked"):
        checks.append({"level": "warning", "msg": "Killswitch actif — nouvelles entrées gelées"})
    if cb.get("is_paused"):
        checks.append({"level": "warning", "msg": "Circuit breaker en pause"})
    if str(macro.get("last_update") or "")[:10] < (datetime.now().date().isoformat()):
        checks.append({"level": "warning", "msg": "Régime macro non rafraîchi aujourd'hui"})
    try:
        u_age_h = (datetime.now() - datetime.fromisoformat(str(universe.get("updated_at")).replace("Z", "+00:00")).replace(tzinfo=None)).total_seconds() / 3600
        if u_age_h > 36:
            checks.append({"level": "warning", "msg": f"Univers scoré vieux de {u_age_h:.0f} h"})
    except Exception:
        pass
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "tracker": {"heartbeat": hb, "age_sec": hb_age, "alive": hb_age is not None and hb_age <= 15 * 60},
        "killswitch": {"blocked": bool(trading.get("blocked")), "state": trading,
                       "action": getattr(config, "KILLSWITCH_ACTION", "freeze"),
                       "max_daily_dd_pct": getattr(config, "MAX_DAILY_DRAWDOWN_PCT", None)},
        "circuit_breaker": cb,
        "macro": {"regime": macro.get("confirmed_regime"), "vix": macro.get("vix"), "last_update": macro.get("last_update")},
        "universe": {"updated_at": universe.get("updated_at"), "count": len(universe.get("tickers") or {})},
        "broker": {"mode": getattr(config, "BROKER_MODE", "paper"), "base_url": getattr(config, "ALPACA_BASE_URL", None)},
        "crons": crons,
        "checks": checks,
        "ok": not any(c["level"] == "danger" for c in checks),
    }


# ─────────────────────────────────────────────────────────────────
# /scoring/lab
# ─────────────────────────────────────────────────────────────────

@router.get("/scoring/lab")
def get_scoring_lab(_auth: None = Security(api_core.require_auth)):
    """Edge du scoring sur la fenêtre live (paniers, piliers seuls, profils de
    poids) — lecture de `data/.scoring_lab.json` produit par
    `python -m modules.scoring_lab` (run_titan.sh). 404 si jamais calculé."""
    from fastapi import HTTPException

    from modules.scoring_lab import load_lab
    data = load_lab()
    if not data:
        raise HTTPException(404, "scoring_lab jamais exécuté — lancer `python -m modules.scoring_lab`")
    return data


# ─────────────────────────────────────────────────────────────────
# /shadow · /performance/gap · /rebalance/preview
# ─────────────────────────────────────────────────────────────────

@router.get("/shadow")
def get_shadow(_auth: None = Security(api_core.require_auth)):
    """Forward-test A/B : NAV des portefeuilles virtuels par profil de scoring."""
    from modules.shadow_portfolios import load_state, summary
    state = load_state()
    out = summary(state)
    out["nav"] = {name: (p.get("nav") or [])[-250:] for name, p in (state.get("profiles") or {}).items()}
    return out


@router.get("/performance/gap")
def get_performance_gap(months: int = Query(6, ge=1, le=12), top_n: int = Query(20, ge=5, le=50),
                        _auth: None = Security(api_core.require_auth)):
    """Attribution de l'écart compte vs panier : cash drag, slippage, stops, sélection."""
    from modules.gap_attribution import compute_live_gap
    return compute_live_gap(months=months, top_n=top_n)


@router.get("/rebalance/preview")
def get_rebalance_preview(_auth: None = Security(api_core.require_auth)):
    """Ordres de rebalance 1/N qui seraient passés (lecture seule)."""
    from modules.basket_rebalance import preview
    return preview()


__all__ = ["router", "compute_protection"]
