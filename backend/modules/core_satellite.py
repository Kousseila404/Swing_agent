"""Stratégie cœur-satellite du compte paper (ADR-022, 2026-11-20).

Recherche 2026-11-20 (documentation/audits/2026-11-20-recherche-scoring.md) :
aucun score de sélection n'a d'edge démontré sur 15 ans une fois le biais de
survivance retiré ; le momentum 12-1 est le seul candidat plausible
(0 à 2 %/an). D'où, sur recommandation du validateur statistique :

  • CŒUR      : RSP (S&P 500 équipondéré) — 70 % de l'equity ;
  • SATELLITE : 30 % répartis à parts égales sur 30 titres classés par
                momentum 12-1 (rendement t−252 → t−21 séances, clôtures
                ajustées), z-score INTRA-SECTEUR, 4 titres max par secteur ;
  • rebalance MENSUEL (1ʳᵉ séance ouverte du mois, en séance) ;
  • zone tampon : un titre détenu reste tant que son rang ≤ 60 ;
  • régime : SPY < MM200 à la date du rebalance → le satellite passe en RSP.

Paramètres PRÉ-ENREGISTRÉS (interdiction de les modifier avant 24 mois sauf
critère d'arrêt, cf. ADR-022). Les positions de cette stratégie portent
`Signal = CORE_SATELLITE` dans le journal : le tracker ne leur applique AUCUNE
sortie logicielle (TS, TIMEOUT, LT…) ; seule protection automatique = le stop
catastrophe GTC −35 % posé chez le broker à l'achat.

Pur (testable) : momentum_12_1, sector_zscores, rank_with_cap,
select_satellite, regime_is_bull, target_weights, plan_orders.
IO : build_plan(), run_monthly().
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from modules.log import logger

_BACKEND = Path(__file__).resolve().parents[1]
STATE_PATH = _BACKEND / "data" / ".core_satellite_state.json"

# ── Paramètres pré-enregistrés (ADR-022) ──────────────────────────────────────
CORE_TICKER = "RSP"
CORE_WEIGHT = 0.70
SAT_N = 30
SAT_MAX_PER_SECTOR = 4
SAT_EXIT_RANK = 60
MOM_LOOKBACK = 252
MOM_SKIP = 21
REGIME_TICKER = "SPY"
REGIME_MA = 200
SIGNAL_TAG = "CORE_SATELLITE"
CATASTROPHE_SL = 0.65          # stop GTC à −35 % (aligné CATASTROPHE_DRAWDOWN_THRESHOLD)
TAKE_PROFIT_MULT = 3.0         # TP de bracket hors d'atteinte (+200 %, plafond existant)
MIN_TRADE_USD = 250.0          # ajustements plus petits ignorés (coûts)
MAX_ATTEMPTS_PER_MONTH = 3
MAX_PRICE_STALENESS_DAYS = 5


# ─────────────────────────────────────────────────────────────────────────────
# Fonctions pures
# ─────────────────────────────────────────────────────────────────────────────

def momentum_12_1(
    series: dict[str, tuple[list[str], list[float]]], asof: str | None = None,
) -> dict[str, float]:
    """{ticker: rendement 12-1} — clôtures t−(LOOKBACK+SKIP) → t−SKIP.

    Un ticker sans historique suffisant ou dont la dernière clôture date de
    plus de MAX_PRICE_STALENESS_DAYS avant `asof` est ignoré.
    """
    need = MOM_LOOKBACK + MOM_SKIP + 1
    ref = date.fromisoformat(asof) if asof else None
    out: dict[str, float] = {}
    for t, (dates, vals) in series.items():
        if len(vals) < need:
            continue
        if ref is not None:
            try:
                if (ref - date.fromisoformat(str(dates[-1])[:10])).days > MAX_PRICE_STALENESS_DAYS:
                    continue
            except ValueError:
                continue
        p0, p1 = float(vals[-need]), float(vals[-1 - MOM_SKIP])
        if p0 > 0 and math.isfinite(p0) and math.isfinite(p1):
            out[t] = p1 / p0 - 1.0
    return out


def sector_zscores(values: dict[str, float], sectors: dict[str, str]) -> dict[str, float]:
    """z-score intra-secteur ; secteur < 3 titres → z vs l'univers entier."""
    all_v = list(values.values())
    g_mu = statistics.fmean(all_v) if all_v else 0.0
    g_sd = statistics.pstdev(all_v) if len(all_v) > 1 else 0.0
    by: dict[str, list[float]] = {}
    for t, v in values.items():
        by.setdefault(sectors.get(t) or "Unknown", []).append(v)
    stats = {}
    for sec, vs in by.items():
        if len(vs) >= 3 and statistics.pstdev(vs) > 0:
            stats[sec] = (statistics.fmean(vs), statistics.pstdev(vs))
        else:
            stats[sec] = (g_mu, g_sd)
    out = {}
    for t, v in values.items():
        mu, sd = stats[sectors.get(t) or "Unknown"]
        out[t] = (v - mu) / sd if sd > 0 else 0.0
    return out


def rank_with_cap(scores: dict[str, float], sectors: dict[str, str], cap: int) -> dict[str, int]:
    """Rangs (1 = meilleur) ; au-delà de `cap` titres d'un secteur, relégués
    après tous les titres « dans le quota » (même règle que compute_ranks)."""
    order = sorted(scores, key=lambda t: (-scores[t], t))
    inside, overflow, count = [], [], {}
    for t in order:
        sec = sectors.get(t) or "Unknown"
        (inside if count.get(sec, 0) < cap else overflow).append(t)
        count[sec] = count.get(sec, 0) + 1
    return {t: i + 1 for i, t in enumerate(inside + overflow)}


def select_satellite(
    ranks: dict[str, int], sectors: dict[str, str], held: set[str],
    n: int = SAT_N, exit_rank: int = SAT_EXIT_RANK, cap: int = SAT_MAX_PER_SECTOR,
) -> list[str]:
    """Zone tampon : on garde les détenus de rang ≤ exit_rank (meilleurs
    d'abord), puis on complète avec les mieux classés, plafond sectoriel
    respecté sur le panier final."""
    keep = sorted((t for t in held if t in ranks and ranks[t] <= exit_rank), key=ranks.__getitem__)
    out: list[str] = []
    per_sec: dict[str, int] = {}

    def _add(t: str) -> None:
        sec = sectors.get(t) or "Unknown"
        if len(out) < n and t not in out and per_sec.get(sec, 0) < cap:
            out.append(t)
            per_sec[sec] = per_sec.get(sec, 0) + 1

    for t in keep:
        _add(t)
    for t in sorted(ranks, key=ranks.__getitem__):
        if len(out) >= n:
            break
        _add(t)
    return out


def regime_is_bull(closes: list[float], ma: int = REGIME_MA) -> bool | None:
    """Dernière clôture > moyenne mobile `ma` ; None si historique insuffisant."""
    if len(closes) < ma:
        return None
    return float(closes[-1]) > statistics.fmean(float(x) for x in closes[-ma:])


def target_weights(satellite: list[str], bull: bool | None) -> dict[str, float]:
    """Poids cibles. Régime baissier OU inconnu OU satellite vide → 100 % RSP."""
    if not bull or not satellite:
        return {CORE_TICKER: 1.0}
    w = (1.0 - CORE_WEIGHT) / len(satellite)
    out = {t: w for t in satellite}
    out[CORE_TICKER] = out.get(CORE_TICKER, 0.0) + CORE_WEIGHT
    return out


def plan_orders(
    targets: dict[str, float], equity: float, positions: dict[str, float], prices: dict[str, float],
    min_trade_usd: float = MIN_TRADE_USD,
) -> list[dict[str, Any]]:
    """Ordres pour passer de `positions` (qty) aux poids cibles. Ventes d'abord.

    `kind` : close (sortie totale), open (nouvelle ligne), sell / buy (ajustement).
    Un ajustement < min_trade_usd est ignoré ; une ouverture/fermeture jamais.
    """
    orders: list[dict[str, Any]] = []
    for t in sorted(set(targets) | set(positions)):
        px = prices.get(t)
        cur = float(positions.get(t, 0.0))
        if px is None or px <= 0:
            if cur > 0 and t not in targets:
                orders.append({"ticker": t, "kind": "close", "delta_qty": -cur, "current_qty": cur,
                               "target_qty": 0, "usd": None, "note": "prix indisponible"})
            continue
        tgt = int(math.floor(targets.get(t, 0.0) * equity / px))
        delta = tgt - cur
        if delta == 0:
            continue
        if tgt == 0:
            kind = "close"
        elif cur == 0:
            kind = "open"
        else:
            kind = "buy" if delta > 0 else "sell"
            if abs(delta) * px < min_trade_usd:
                continue
        orders.append({"ticker": t, "kind": kind, "delta_qty": delta, "current_qty": cur,
                       "target_qty": tgt, "usd": round(delta * px, 2)})
    orders.sort(key=lambda o: (0 if o["delta_qty"] < 0 else 1, o["ticker"]))
    return orders


# ─────────────────────────────────────────────────────────────────────────────
# IO
# ─────────────────────────────────────────────────────────────────────────────

def _load_state() -> dict[str, Any]:
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.warning(f"[CoreSatellite] état illisible : {exc}")
    return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _sectors() -> dict[str, str]:
    uni = json.loads((_BACKEND / "data" / "universe.json").read_text(encoding="utf-8"))
    tickers = uni.get("tickers") if isinstance(uni, dict) else None
    return {str(t).upper(): str((r or {}).get("sector") or "Unknown") for t, r in (tickers or {}).items()}


def build_plan(broker: Any | None = None, asof: str | None = None) -> dict[str, Any]:
    """Calcule le plan du mois (aucun ordre). `broker=None` → lecture du broker courant."""
    from modules import price_panel

    asof = asof or date.today().isoformat()
    series = price_panel._series()
    sectors = _sectors()
    universe = {t: s for t, s in series.items() if t in sectors}
    mom = momentum_12_1(universe, asof)
    if len(mom) < 0.8 * max(len(sectors), 1):
        raise RuntimeError(f"momentum disponible pour {len(mom)}/{len(sectors)} tickers seulement "
                           "(price_panel trop court ou périmé) — rebalance annulé")
    z = sector_zscores(mom, sectors)
    ranks = rank_with_cap(z, sectors, SAT_MAX_PER_SECTOR)

    spy = series.get(REGIME_TICKER)
    bull = regime_is_bull(spy[1]) if spy else None

    if broker is None:
        from modules.broker_gateway import get_broker
        broker = get_broker()
    equity = float(broker.get_account_equity())
    open_pos = broker.get_open_positions()
    positions = {p.ticker.upper(): float(p.size) for p in open_pos}
    entries = {p.ticker.upper(): float(p.entry or 0) for p in open_pos}
    held_sat = {t for t in positions if t != CORE_TICKER}

    satellite = select_satellite(ranks, sectors, held_sat)
    targets = target_weights(satellite, bull)
    prices = {t: float(v[-1]) for t, (_, v) in series.items() if v}
    orders = plan_orders(targets, equity, positions, prices)
    return {
        "asof": asof, "equity": round(equity, 2), "regime_bull": bull,
        "satellite": satellite, "satellite_ranks": {t: ranks.get(t) for t in satellite},
        "targets": {t: round(w, 4) for t, w in targets.items()},
        "orders": orders, "entries": entries, "n_momentum": len(mom),
        "params": {"core": CORE_TICKER, "core_weight": CORE_WEIGHT, "n": SAT_N, "cap": SAT_MAX_PER_SECTOR,
                   "exit_rank": SAT_EXIT_RANK, "lookback": MOM_LOOKBACK, "skip": MOM_SKIP, "regime_ma": REGIME_MA},
    }


def _retag_open_row(ticker: str) -> None:
    """Adopte une position existante dans la stratégie (Signal = CORE_SATELLITE)."""
    import pandas as pd
    from filelock import FileLock

    from modules.utils import CSV_LOCK_PATH, CSV_PATH
    with FileLock(str(CSV_LOCK_PATH), timeout=10):
        df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
        mask = (df["Ticker"].str.upper() == ticker.upper()) & (df["Status"] == "OPEN")
        if mask.any() and (df.loc[mask, "Signal"] != SIGNAL_TAG).any():
            df.loc[mask, "Signal"] = SIGNAL_TAG
            df.to_csv(CSV_PATH, index=False)


def _execute(plan: dict[str, Any], broker: Any, allow_buys: bool) -> dict[str, Any]:
    from modules.basket_rotation import _tag_close_reason

    executed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    sectors = _sectors()
    for o in plan["orders"]:
        t, kind, delta = o["ticker"], o["kind"], o["delta_qty"]
        is_buy = delta > 0
        if is_buy and not allow_buys and t != CORE_TICKER:
            skipped.append({**o, "reason": "killswitch : achats satellite gelés"})
            continue
        try:
            if kind == "close":
                if not o.get("usd"):
                    failed.append({**o, "ok": False, "message": "prix indisponible — sortie reportée"})
                    continue
                px = float(o["usd"] / delta)
                entry = float((plan.get("entries") or {}).get(t) or 0)
                status = "WIN" if entry and px > entry else "LOSS"
                ok = broker.close_position(t, px, status, "LONG", update_csv=True)
                if ok:
                    _tag_close_reason(t, "CORE_SATELLITE_REBAL")
                res = {"ok": bool(ok)}
            elif kind == "open":
                px = float(o["usd"] / delta)
                scan = SimpleNamespace(
                    ticker=t, direction="LONG", price=px,
                    stop_loss=round(px * CATASTROPHE_SL, 4), take_profit=round(px * TAKE_PROFIT_MULT, 4),
                    position_size=int(delta), rr_ratio=float("nan"), signal=SIGNAL_TAG,
                    sector_etf=sectors.get(t, ""),
                )
                r = broker.submit_order(scan)
                res = {"ok": bool(getattr(r, "success", False)), "message": getattr(r, "message", "")}
            else:
                adjust = getattr(broker, "adjust_position", None)
                if adjust is None:
                    res = {"ok": False, "message": "broker sans adjust_position"}
                else:
                    res = adjust(t, int(delta))
                    if res.get("ok"):
                        _retag_open_row(t)
        except Exception as exc:
            res = {"ok": False, "message": str(exc)}
        (executed if res.get("ok") else failed).append({**o, **res})
    # Positions conservées sans ordre : adoptées dans la stratégie.
    for t in plan["targets"]:
        if t not in {o["ticker"] for o in plan["orders"]}:
            try:
                _retag_open_row(t)
            except Exception as exc:
                logger.warning(f"[CoreSatellite] retag {t} : {exc}")
    return {"executed": executed, "failed": failed, "skipped": skipped}


def run_monthly(force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Point d'entrée (auto_approve, mode core_satellite). Une fois par mois."""
    import config

    if str(getattr(config, "STRATEGY_MODE", "")).lower() != "core_satellite":
        return {"skipped": "STRATEGY_MODE != core_satellite"}
    state = _load_state()
    month = date.today().strftime("%Y-%m")
    if not force and state.get("done_month") == month:
        return {"skipped": "déjà exécuté ce mois", "last_run": state.get("last_run")}

    from modules.broker_gateway import get_broker
    broker = get_broker()
    status = getattr(broker, "market_status", None)
    if status is None:
        return {"skipped": "broker sans horloge marché (paper local)"}
    try:
        if not bool(status().get("is_open")):
            return {"skipped": "marché fermé"}
    except Exception as exc:
        return {"skipped": "horloge marché illisible", "error": str(exc)}

    # Killswitch fail-closed : si gelé ou illisible → seulement les ventes et
    # l'achat du cœur RSP (dé-risquer reste permis, ajouter du satellite non).
    try:
        from modules.tracker.killswitch import is_trading_allowed
        allow_buys = bool(is_trading_allowed())
    except Exception as exc:
        logger.error(f"[CoreSatellite] killswitch illisible — achats satellite gelés : {exc}")
        allow_buys = False

    try:
        plan = build_plan(broker)
    except Exception as exc:
        logger.error(f"[CoreSatellite] plan impossible : {exc}")
        return {"skipped": "plan impossible", "error": str(exc)}
    if dry_run:
        return {"dry_run": True, **plan}

    res = _execute(plan, broker, allow_buys)
    attempts = (state.get("attempts", 0) if state.get("attempt_month") == month else 0) + 1
    done = not res["failed"] or attempts >= MAX_ATTEMPTS_PER_MONTH
    state.update({"last_run": date.today().isoformat(), "attempt_month": month, "attempts": attempts,
                  "regime_bull": plan["regime_bull"], "satellite": plan["satellite"],
                  "n_orders": len(plan["orders"]), "executed": len(res["executed"]),
                  "failed": len(res["failed"]), "skipped": len(res["skipped"])})
    if done:
        state["done_month"] = month
    _save_state(state)
    logger.warning(f"[CoreSatellite] rebalance {month} : {len(res['executed'])}/{len(plan['orders'])} ordres, "
                   f"régime {'haussier' if plan['regime_bull'] else 'baissier/inconnu → 100 % RSP'}")
    try:
        from modules.alerter import _send_telegram_message
        _send_telegram_message(
            f"⚖️ <b>Cœur-satellite {month}</b> — régime "
            f"{'haussier' if plan['regime_bull'] else 'baissier → 100 % RSP'}\n"
            f"{len(res['executed'])}/{len(plan['orders'])} ordres exécutés, {len(res['failed'])} échec(s)"
        )
    except Exception:
        pass
    return {**plan, **res, "month_done": done}


if __name__ == "__main__":  # pragma: no cover
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Plan / exécution cœur-satellite")
    ap.add_argument("--plan", action="store_true", help="affiche le plan sans ordre")
    args = ap.parse_args()
    import config as _cfg  # noqa: F401 — charge backend/.env
    out = build_plan() if args.plan else run_monthly(dry_run=True)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    sys.exit(0)
