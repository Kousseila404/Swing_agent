"""Shadow portfolios — forward-test A/B de plusieurs profils de scoring (2026-09-17).

Le Scoring Lab compare des profils *rétrospectivement*. Ici, chaque profil
gère un portefeuille virtuel **au fil de l'eau**, avec les mêmes règles que
la stratégie live (top-N, 1/N, rotation hebdomadaire, 10 bps de frais) et les
prix du jour — sans broker, sans look-ahead, sans deuxième compte. Après
3 mois, on sait si l'écart mesuré en backtest tient hors échantillon.

État : `data/shadow_portfolios.json` — par profil : holdings {ticker: shares},
cash, nav (historique quotidien), last_rebalance. Pas de deuxième clé Alpaca
requise ; pour brancher un vrai compte paper B, voir documentation/strategie.md.

CLI (quotidien, run_titan.sh après le refresh univers) :
    python -m modules.shadow_portfolios
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from modules.log import logger

_BACKEND = Path(__file__).resolve().parents[1]
STATE_PATH = _BACKEND / "data" / "shadow_portfolios.json"

START_NAV = 100_000.0
TOP_N = 20
COST_BPS = 10.0
REBALANCE_EVERY_DAYS = 7

# Profils suivis (clé = nom dans _scoring.WEIGHT_PROFILES ou pseudo-profil).
PROFILES: dict[str, dict[str, float] | None] = {
    "v14_1": None,          # rempli depuis WEIGHT_PROFILES à l'exécution
    "equal_7": None,
    "momentum_tilt": None,
    "momentum_only": {"momentum_score": 1.0},
    "universe_ew": {},      # {} = tout l'univers équipondéré (référence)
}


def _profile_weights(name: str) -> dict[str, float] | None:
    explicit = PROFILES.get(name)
    if explicit is not None:
        return explicit
    try:
        from modules.sector_metrics._scoring import WEIGHT_PROFILES
        raw = WEIGHT_PROFILES.get(name) or {}
        return {f"{k}_score": float(v) for k, v in raw.items() if v and k != "sentiment"}
    except Exception:
        return None


def _score(row: dict[str, Any], weights: dict[str, float]) -> float | None:
    num = den = 0.0
    for k, w in weights.items():
        v = row.get(k)
        if v is None or w <= 0:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(fv):
            continue
        num += w * fv
        den += w
    return num / den if den > 0 else None


def select_basket(scored: dict[str, dict[str, Any]], weights: dict[str, float], top_n: int) -> list[str]:
    """Top-N tickers pour un profil ({} → tout l'univers avec prix)."""
    priced = {t: r for t, r in scored.items() if float((r or {}).get("current_price") or 0) > 0}
    if not weights:
        return sorted(priced)
    ranked = [(t, s) for t, r in priced.items() if (s := _score(r, weights)) is not None]
    ranked.sort(key=lambda x: -x[1])
    return [t for t, _ in ranked[:top_n]]


def mark_to_market(port: dict[str, Any], prices: dict[str, float]) -> float:
    nav = float(port.get("cash") or 0)
    last = port.setdefault("last_prices", {})
    for t, sh in (port.get("holdings") or {}).items():
        px = prices.get(t) or last.get(t)
        if px:
            last[t] = px
            nav += float(sh) * float(px)
    return nav


def rebalance(port: dict[str, Any], basket: list[str], prices: dict[str, float], cost_bps: float) -> dict[str, Any]:
    """Réalloue 1/N sur `basket` ; frais = cost_bps × turnover notionnel."""
    nav = mark_to_market(port, prices)
    tradable = [t for t in basket if prices.get(t)]
    if not tradable or nav <= 0:
        return {"turnover_usd": 0.0}
    target_usd = nav / len(tradable)
    old_val = {t: float(sh) * prices.get(t, port["last_prices"].get(t, 0)) for t, sh in (port.get("holdings") or {}).items()}
    new_holdings: dict[str, float] = {}
    turnover = 0.0
    for t in tradable:
        sh = target_usd / prices[t]
        new_holdings[t] = sh
        turnover += abs(sh * prices[t] - old_val.get(t, 0.0))
    for t, v in old_val.items():
        if t not in new_holdings:
            turnover += abs(v)
    cost = turnover * cost_bps / 10_000.0
    port["holdings"] = new_holdings
    port["cash"] = nav - sum(sh * prices[t] for t, sh in new_holdings.items()) - cost
    port["cumulative_cost"] = float(port.get("cumulative_cost") or 0) + cost
    return {"turnover_usd": round(turnover, 2), "cost_usd": round(cost, 2), "n": len(tradable)}


def step(state: dict[str, Any], scored: dict[str, dict[str, Any]], today: str) -> dict[str, Any]:
    """Un pas quotidien pour tous les profils (pure : état in/out)."""
    prices = {t: float(r["current_price"]) for t, r in scored.items() if float((r or {}).get("current_price") or 0) > 0}
    out = state.setdefault("profiles", {})
    report: dict[str, Any] = {}
    for name in PROFILES:
        weights = _profile_weights(name)
        if weights is None:
            continue
        port = out.setdefault(name, {"cash": START_NAV, "holdings": {}, "nav": [], "last_rebalance": None,
                                     "last_prices": {}, "cumulative_cost": 0.0, "started": today})
        if port.get("nav") and port["nav"][-1][0] == today:
            report[name] = {"skipped": "déjà valorisé aujourd'hui"}
            continue
        last_rb = port.get("last_rebalance")
        due = last_rb is None or (date.fromisoformat(today) - date.fromisoformat(last_rb)).days >= REBALANCE_EVERY_DAYS
        info: dict[str, Any] = {}
        if due:
            basket = select_basket(scored, weights, TOP_N)
            info = rebalance(port, basket, prices, COST_BPS)
            port["last_rebalance"] = today
            port["basket"] = basket[:TOP_N] if weights else f"universe:{len(basket)}"
        nav = mark_to_market(port, prices)
        port["nav"].append([today, round(nav, 2)])
        port["nav"] = port["nav"][-750:]
        report[name] = {"nav": round(nav, 2), "rebalanced": due, **info}
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return report


def summary(state: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for name, port in (state.get("profiles") or {}).items():
        nav = port.get("nav") or []
        if not nav:
            continue
        first, last = nav[0][1], nav[-1][1]
        peak = 0.0
        mdd = 0.0
        for _, v in nav:
            peak = max(peak, v)
            if peak > 0:
                mdd = min(mdd, v / peak - 1)
        d30 = [v for d, v in nav if d >= (date.fromisoformat(nav[-1][0]) - timedelta(days=30)).isoformat()]
        rows.append({
            "profile": name, "started": nav[0][0], "days": len(nav), "nav": last,
            "return_pct": round((last / first - 1) * 100, 2),
            "return_30d_pct": round((last / d30[0] - 1) * 100, 2) if len(d30) > 1 else None,
            "max_drawdown_pct": round(mdd * 100, 2),
            "cost_usd": round(float(port.get("cumulative_cost") or 0), 2),
            "n_holdings": len(port.get("holdings") or {}),
        })
    rows.sort(key=lambda r: -r["return_pct"])
    return {"updated_at": state.get("updated_at"), "rows": rows, "top_n": TOP_N, "cost_bps": COST_BPS,
            "rebalance_days": REBALANCE_EVERY_DAYS}


def load_state() -> dict[str, Any]:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[Shadow] état illisible : {exc}")
    return {}


def save_state(state: dict[str, Any]) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(STATE_PATH)


def run_daily() -> dict[str, Any]:
    from modules.sector_metrics import get_scored_universe
    scored = get_scored_universe() or {}
    if not scored:
        return {"skipped": "univers vide"}
    state = load_state()
    report = step(state, scored, date.today().isoformat())
    save_state(state)
    logger.info(f"[Shadow] {len(report)} profils valorisés : " + ", ".join(f"{k}={v.get('nav')}" for k, v in report.items()))
    return report


if __name__ == "__main__":
    print(json.dumps(run_daily(), indent=1, ensure_ascii=False))
    print(json.dumps(summary(load_state()), indent=1, ensure_ascii=False))
