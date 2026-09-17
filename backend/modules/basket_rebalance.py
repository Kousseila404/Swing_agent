"""Rebalance des poids vers 1/N (stratégie basket, 2026-09-17).

La rotation gère *qui* est dans le panier ; ce module gère *combien* : une
ligne qui a doublé pèse 12 %, une autre 3 % — le backtest qui a de l'edge
est équipondéré. Mensuel (premier jour de bourse du mois), on ramène chaque
ligne vers 1/N du notionnel investi si son poids s'écarte de plus de
`REBALANCE_BAND_PTS` points, avec un minimum de `REBALANCE_MIN_TRADE_USD`
par ordre (évite le churn). Ventes d'abord (libèrent du cash), achats ensuite.

Pure : `compute_rebalance()` (testable). IO : `run_rebalance()` (état
`data/.basket_rebalance_state.json`, broker `adjust_position`).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import config
from modules.log import logger

_BACKEND = Path(__file__).resolve().parents[1]
STATE_PATH = _BACKEND / "data" / ".basket_rebalance_state.json"


def compute_rebalance(
    positions: list[dict[str, Any]],
    *,
    band_pts: float = 2.0,
    min_trade_usd: float = 300.0,
) -> list[dict[str, Any]]:
    """Ordres de rééquilibrage vers 1/N du notionnel investi.

    positions : [{ticker, qty, price}] (prix courant > 0).
    Retourne [{ticker, delta_qty, from_pct, to_pct, usd}] triés ventes → achats.
    """
    live = [p for p in positions if float(p.get("price") or 0) > 0 and float(p.get("qty") or 0) > 0]
    n = len(live)
    if n < 2:
        return []
    total = sum(float(p["qty"]) * float(p["price"]) for p in live)
    if total <= 0:
        return []
    target_w = 1.0 / n
    orders: list[dict[str, Any]] = []
    for p in live:
        qty = float(p["qty"])
        px = float(p["price"])
        w = qty * px / total
        if abs(w - target_w) * 100.0 <= band_pts:
            continue
        target_qty = int(round(target_w * total / px))
        delta = target_qty - int(qty)
        usd = abs(delta) * px
        if delta == 0 or usd < min_trade_usd:
            continue
        orders.append({
            "ticker": str(p["ticker"]).upper(), "delta_qty": delta, "price": px,
            "from_pct": round(w * 100.0, 2), "to_pct": round(target_qty * px / total * 100.0, 2),
            "usd": round(usd, 2),
        })
    orders.sort(key=lambda o: (o["delta_qty"] > 0, -o["usd"]))  # ventes d'abord, plus grosses d'abord
    return orders


def _is_first_trading_day(today: date, last_run_month: str | None) -> bool:
    return today.strftime("%Y-%m") != (last_run_month or "")


def _load_state() -> dict[str, Any]:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(STATE_PATH)
    except Exception as exc:
        logger.warning(f"[Rebalance] état non persisté : {exc}")


def preview() -> dict[str, Any]:
    """Aperçu (lecture seule) des ordres de rebalance sur l'état courant."""
    from modules import api_core
    eq = api_core.load_equity() or {}
    positions = [
        {"ticker": p.get("ticker"), "qty": p.get("size"), "price": p.get("current_price") or p.get("entry")}
        for p in eq.get("open_positions", [])
    ]
    orders = compute_rebalance(
        positions,
        band_pts=float(getattr(config, "REBALANCE_BAND_PTS", 2.0)),
        min_trade_usd=float(getattr(config, "REBALANCE_MIN_TRADE_USD", 300.0)),
    )
    state = _load_state()
    return {
        "n_positions": len(positions), "orders": orders,
        "band_pts": getattr(config, "REBALANCE_BAND_PTS", 2.0),
        "last_run": state.get("last_run"), "due": _is_first_trading_day(date.today(), state.get("last_run_month")),
    }


def run_rebalance(force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Point d'entrée (auto_approve, mode basket, après la rotation)."""
    if str(getattr(config, "STRATEGY_MODE", "basket")).lower() != "basket":
        return {"skipped": "STRATEGY_MODE != basket"}
    state = _load_state()
    today = date.today()
    if not force and not _is_first_trading_day(today, state.get("last_run_month")):
        return {"skipped": "déjà exécuté ce mois", "last_run": state.get("last_run")}
    try:
        from modules.tracker.killswitch import is_trading_allowed
        if not is_trading_allowed():
            return {"skipped": "killswitch"}
    except Exception:
        pass
    pv = preview()
    orders = pv["orders"]
    result: dict[str, Any] = {"date": today.isoformat(), "orders": orders, "executed": [], "failed": []}
    if dry_run:
        return result
    if orders:
        from modules.broker_gateway import get_broker
        broker = get_broker()
        adjust = getattr(broker, "adjust_position", None)
        if adjust is None:
            return {**result, "skipped": "broker sans adjust_position (paper)"}
        for o in orders:
            r = adjust(o["ticker"], int(o["delta_qty"]))
            (result["executed"] if r.get("ok") else result["failed"]).append(r)
    state.update({"last_run": today.isoformat(), "last_run_month": today.strftime("%Y-%m"),
                  "n_orders": len(orders), "executed": len(result["executed"])})
    _save_state(state)
    if orders:
        logger.warning(f"[Rebalance] {len(result['executed'])}/{len(orders)} ordre(s) exécuté(s) : "
                       + ", ".join(f"{o['ticker']} {o['delta_qty']:+d}" for o in orders))
        try:
            from modules.alerter import _send_telegram_message
            _send_telegram_message(
                "⚖️ <b>Rebalance mensuel 1/N</b>\n"
                + "\n".join(f"• {o['ticker']} {o['delta_qty']:+d} ({o['from_pct']:.1f} % → {o['to_pct']:.1f} %)" for o in orders)
            )
        except Exception:
            pass
    return result


if __name__ == "__main__":
    import sys
    print(json.dumps(run_rebalance(force="--force" in sys.argv, dry_run="--apply" not in sys.argv), indent=1, ensure_ascii=False))
