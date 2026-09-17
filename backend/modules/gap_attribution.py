"""Attribution de l'écart compte réel vs panier théorique (2026-09-17).

Le Cockpit montre *combien* le compte sous-performe le panier ; ce module dit
*pourquoi*, en points de performance sur la fenêtre live :

  • cash_drag     : capital non investi × rendement du panier (jour par jour ;
                    fraction investie reconstruite depuis le journal et les
                    prix des snapshots quotidiens) ;
  • slippage      : Σ Slippage_Bps × notionnel / equity (entrées) ;
  • stops         : PnL réalisé des sorties SL_HIT / TRAILING_STOP / TIMEOUT /
                    EMERGENCY (le panier n'a pas de stops) ;
  • selection     : résidu = écart total − les trois composantes (choix des
                    titres, timing d'entrée, positions hors panier).

Approximation assumée : le rendement du panier est réparti uniformément sur
les jours de bourse de chaque période hebdomadaire. Pure : `attribute()`.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from modules.log import logger


def _daily_basket_returns(points: list[tuple[str, float]], dates: list[str]) -> dict[str, float]:
    """Indice hebdo → rendement quotidien uniforme par période."""
    out: dict[str, float] = {}
    pts = sorted(points)
    for (d0, v0), (d1, v1) in zip(pts, pts[1:], strict=False):
        days = [d for d in dates if d0 < d <= d1]
        if not days or not v0:
            continue
        r = (v1 / v0) ** (1.0 / len(days)) - 1.0
        for d in days:
            out[d] = r
    return out


def invested_fraction_by_day(
    journal: list[dict[str, Any]],
    equity_by_day: dict[str, float],
    price_lookup,
) -> dict[str, float]:
    """{date: fraction investie} — positions OPEN ce jour (Date ≤ d < Exit_Date)."""
    out: dict[str, float] = {}
    rows = []
    for r in journal:
        try:
            d0 = str(r.get("Date") or "")[:10]
            d1 = str(r.get("Exit_Date") or "")[:10] or None
            size = float(r.get("Size") or 0)
            if not d0 or size <= 0 or str(r.get("Status")) == "CANCELED":
                continue
            rows.append((d0, d1, str(r.get("Ticker")).upper(), size, float(r.get("Entry") or 0)))
        except (TypeError, ValueError):
            continue
    for d, eq in equity_by_day.items():
        if not eq:
            continue
        inv = 0.0
        for d0, d1, t, size, entry in rows:
            if d0 <= d and (d1 is None or d < d1):
                px = price_lookup(t, d) or entry
                inv += size * px
        out[d] = max(0.0, min(1.0, inv / eq))
    return out


def attribute(
    dates: list[str],
    account_idx: list[float | None],
    basket_points: list[tuple[str, float]],
    journal: list[dict[str, Any]],
    equity_by_day: dict[str, float],
    price_lookup,
) -> dict[str, Any]:
    clean = [(d, a) for d, a in zip(dates, account_idx, strict=False) if a]
    if len(clean) < 2 or len(basket_points) < 2:
        return {"available": False, "notes": ["données insuffisantes"]}
    acct_ret = (clean[-1][1] / clean[0][1] - 1.0) * 100.0
    bp = sorted(basket_points)
    basket_ret = (bp[-1][1] / bp[0][1] - 1.0) * 100.0
    gap = acct_ret - basket_ret

    inv = invested_fraction_by_day(journal, equity_by_day, price_lookup)
    rb = _daily_basket_returns(basket_points, dates)
    cash_drag = 0.0
    days_used = 0
    for d in dates:
        r = rb.get(d)
        if r is None:
            continue
        f = inv.get(d)
        if f is None:
            continue
        cash_drag += (1.0 - f) * r * 100.0
        days_used += 1
    avg_invested = (sum(inv.values()) / len(inv) * 100.0) if inv else None

    eq_ref = next((v for v in equity_by_day.values() if v), 100_000.0)
    slippage = 0.0
    stops = 0.0
    n_stop_exits = 0
    for r in journal:
        try:
            size = float(r.get("Size") or 0)
            entry = float(r.get("Entry") or 0)
            bps = float(r.get("Slippage_Bps") or 0)
            if size > 0 and entry > 0 and bps:
                slippage -= abs(bps) / 10_000.0 * size * entry / eq_ref * 100.0
            reason = str(r.get("Close_Reason") or "")
            if reason in ("SL_HIT", "TRAILING_STOP", "TIMEOUT", "EMERGENCY_DD") and r.get("Exit_Price"):
                pnl = (float(r["Exit_Price"]) - entry) * size
                stops += pnl / eq_ref * 100.0
                n_stop_exits += 1
        except (TypeError, ValueError):
            continue
    selection = gap - (-cash_drag) - slippage - stops
    return {
        "available": True,
        "window": {"start": clean[0][0], "end": clean[-1][0], "days": len(clean)},
        "account_return_pct": round(acct_ret, 2),
        "basket_return_pct": round(basket_ret, 2),
        "gap_pct": round(gap, 2),
        "components": {
            "cash_drag_pct": round(-cash_drag, 2),
            "slippage_pct": round(slippage, 2),
            "stops_pct": round(stops, 2),
            "selection_timing_pct": round(selection, 2),
        },
        "details": {
            "avg_invested_pct": round(avg_invested, 1) if avg_invested is not None else None,
            "days_with_basket_return": days_used,
            "n_stop_exits": n_stop_exits,
        },
        "notes": [
            "cash_drag = Σ (1 − investi) × rendement panier, jour par jour",
            "selection_timing = résidu (choix des titres, timing, positions hors panier)",
        ],
    }


def _snapshot_price_lookup():
    """Prix de clôture par (ticker, date) depuis les snapshots quotidiens (cache RAM)."""
    from modules import universe_history
    cache: dict[str, dict[str, float]] = {}
    avail = sorted(d.isoformat() for d in universe_history.list_snapshots())

    def lookup(ticker: str, d: str) -> float | None:
        # snapshot du jour, sinon le plus récent avant d
        key = None
        for s in reversed(avail):
            if s <= d:
                key = s
                break
        if key is None:
            return None
        if key not in cache:
            snap = universe_history.read_snapshot(date.fromisoformat(key)) or {}
            cache[key] = {t: float(r.get("current_price") or 0) for t, r in (snap.get("tickers") or {}).items()}
        px = cache[key].get(ticker)
        return px if px and px > 0 else None
    return lookup


def compute_live_gap(months: int = 6, top_n: int = 20) -> dict[str, Any]:
    from modules import api_core
    from modules.perf_benchmark import build_benchmark, titan_basket_series
    bench = build_benchmark(months=months, top_n=top_n)
    dates = bench.get("dates") or []
    acct = (bench.get("series") or {}).get("account") or []
    raw_eq = (bench.get("raw") or {}).get("account_equity") or []
    equity_by_day = {d: float(v) for d, v in zip(dates, raw_eq, strict=False) if v}
    basket = titan_basket_series(top_n)
    try:
        res = attribute(dates, acct, [(d, v) for d, v in basket.get("points", [])],
                        api_core.load_journal(), equity_by_day, _snapshot_price_lookup())
    except Exception as exc:
        logger.error(f"[GapAttribution] {exc}", exc_info=True)
        res = {"available": False, "notes": [str(exc)]}
    res["generated_at"] = datetime.now().isoformat(timespec="seconds")
    return res
