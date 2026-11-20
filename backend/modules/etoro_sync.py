"""Synchro du compte eToro RÉEL — lecture seule (audit 2026-11-18, P0 #12/#13).

Pourquoi : l'argent réel (~10 k$) vit sur eToro, dont ~59 % en copy-trading
que l'application ne voyait pas du tout ; le book « Mon Portefeuille »
(`my_portfolio_data.py`) est saisi à la main et dimensionné pour 2 000 $.

Ce module produit un *snapshot normalisé* du compte, persisté dans
`data/etoro/` :
  - `latest.json`  : dernier snapshot complet (totaux, lignes directes,
    copy-traders) ;
  - `history.json` : une ligne de totaux par jour (base d'une future TWR).

Deux sources, même format de sortie :
  1. API publique eToro (`fetch_snapshot_from_api`) — si `ETORO_API_KEY` +
     `ETORO_USER_KEY` sont dans `backend/.env`. Lancée par le cron
     (`python -m modules.etoro_sync`) ; sans clés, sort proprement (rc 0).
  2. Ingestion d'une réponse « portfolio summary » (forme du connecteur
     eToro de Claude) via `POST /api/etoro/snapshot` — permet à une session
     Claude ou à l'agent sq-etoro-analyst de pousser l'état du compte.

Jamais d'ordre : aucune route d'écriture eToro n'est appelée ici.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from modules.log import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
ETORO_DIR = _PROJECT_ROOT / "data" / "etoro"
LATEST_PATH = ETORO_DIR / "latest.json"
HISTORY_PATH = ETORO_DIR / "history.json"
_LOCK_PATH = ETORO_DIR / ".lock"

SCHEMA_VERSION = 1
STALE_AFTER_HOURS = 36.0
HISTORY_MAX_DAYS = 3 * 366

_PNL_PATH = "/api/v1/trading/info/real/pnl"
_INSTRUMENTS_PATH = "/api/v1/market-data/instruments"

# Tolérance de réconciliation book ↔ eToro (unités) : les fractions eToro ont
# 6 décimales, le book en a parfois 5 → 0,5 % absorbe l'arrondi sans masquer
# un vrai achat/vente.
_UNITS_TOLERANCE = 0.005


def _f(x: Any) -> float:
    try:
        return float(x) if x is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation
# ─────────────────────────────────────────────────────────────────────────────

def _finalize(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Ajoute les agrégats dérivés (split direct / copy / cash) et trie."""
    direct = sorted(snapshot["direct"], key=lambda r: -r["value"])
    copy = sorted(snapshot["copy"], key=lambda r: -r["value"])
    direct_value = sum(r["value"] for r in direct)
    copy_value = sum(r["value"] for r in copy)
    cash = snapshot["totals"]["cash"]
    total = snapshot["totals"]["total_value"] or (direct_value + copy_value + cash)

    def pct(v: float) -> float | None:
        return round(v / total * 100, 1) if total > 0 else None

    for r in direct:
        r["weight_pct"] = pct(r["value"])
    for c in copy:
        c["weight_pct"] = pct(c["value"])

    return {
        **snapshot,
        "schema_version": SCHEMA_VERSION,
        "direct": direct,
        "copy": copy,
        "totals": {**snapshot["totals"], "total_value": round(total, 2)},
        "split": {
            "direct_value": round(direct_value, 2),
            "copy_value": round(copy_value, 2),
            "cash": round(cash, 2),
            "direct_pct": pct(direct_value),
            "copy_pct": pct(copy_value),
            "cash_pct": pct(cash),
        },
    }


def normalize_summary(payload: dict[str, Any], source: str = "connector") -> dict[str, Any]:
    """Réponse « get-my-portfolio-summary » (connecteur eToro) → snapshot.

    Lève ValueError si la forme est inattendue — jamais un snapshot à 0 $
    silencieux (un 0 serait lu comme une perte totale).
    """
    if not isinstance(payload, dict):
        raise ValueError("payload doit être un objet JSON")
    totals = payload.get("totals")
    holdings = payload.get("holdings")
    if not isinstance(totals, dict) or not isinstance(holdings, list):
        raise ValueError("champs 'totals' (objet) et 'holdings' (liste) requis")
    if totals.get("totalValue") is None:
        raise ValueError("totals.totalValue manquant")
    page = payload.get("page") or {}
    if page.get("complete") is False:
        raise ValueError("snapshot paginé incomplet (page.complete=false) — relire sans limit")

    direct: list[dict[str, Any]] = []
    for h in holdings:
        m = h.get("market") or {}
        direct.append({
            "symbol": m.get("symbol"),
            "instrument_id": m.get("instrumentId"),
            "name": m.get("name"),
            "units": _f(h.get("units")),
            "invested": round(_f(h.get("invested")), 2),
            "value": round(_f(h.get("value")), 2),
            "pnl": round(_f(h.get("pnl")), 2),
            "pnl_pct": h.get("pnlPercent"),
            "avg_open_rate": h.get("avgOpenRate"),
            "current_rate": h.get("currentRate"),
            "leverage": h.get("avgLeverage"),
        })

    copy: list[dict[str, Any]] = []
    for c in payload.get("copiedTraders") or []:
        invested = _f(c.get("invested"))
        open_pnl = _f(c.get("openPnl"))
        copy.append({
            "mirror_id": c.get("mirrorId"),
            "username": c.get("username"),
            "invested": round(invested, 2),
            "value": round(_f(c.get("value")), 2),
            "open_pnl": round(open_pnl, 2),
            "open_pnl_pct": round(open_pnl / invested * 100, 2) if invested > 0 else None,
            "closed_pnl": round(_f(c.get("closedPnl")), 2),
            "available_cash": round(_f(c.get("availableCash")), 2),
            "stop_loss_pct": c.get("stopLossPercentage"),
        })

    return _finalize({
        "as_of": payload.get("timestamp") or page.get("asOf") or _now_iso(),
        "source": source,
        "account_currency": payload.get("accountCurrency", "USD"),
        "totals": {
            "total_value": round(_f(totals.get("totalValue")), 2),
            "cash": round(_f(totals.get("availableCash", totals.get("balance"))), 2),
            "unrealized_pnl": round(_f(totals.get("unrealizedPnl")), 2),
        },
        "direct": direct,
        "copy": copy,
    })


def _pos_value(p: dict[str, Any]) -> tuple[float, float]:
    """(valeur, pnl latent) d'une position de l'API publique, en USD."""
    upnl = p.get("unrealizedPnL") or {}
    pnl = _f(upnl.get("pnL"))
    return _f(p.get("amount")) + pnl, pnl


def normalize_public_api(
    pnl_payload: dict[str, Any], instruments: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Réponse `GET /api/v1/trading/info/real/pnl` → snapshot.

    Les positions directes sont agrégées par instrument (eToro ouvre une
    position par achat DCA). Valeur = montant investi + PnL latent (levier 1 ;
    en levier >1 ce serait la marge + PnL, ce qui reste la valeur liquidative).
    """
    cp = (pnl_payload or {}).get("clientPortfolio")
    if not isinstance(cp, dict):
        raise ValueError("clientPortfolio manquant dans la réponse eToro")
    instruments = instruments or {}

    by_inst: dict[int, dict[str, Any]] = {}
    for p in cp.get("positions") or []:
        if p.get("mirrorID") or p.get("mirrorId"):
            continue  # position d'un copy-trader : comptée dans son mirror
        iid = int(p.get("instrumentID") or p.get("instrumentId") or 0)
        value, pnl = _pos_value(p)
        units = _f(p.get("units"))
        agg = by_inst.setdefault(iid, {"units": 0.0, "invested": 0.0, "value": 0.0, "pnl": 0.0,
                                       "cost_units": 0.0, "leverage": p.get("leverage")})
        agg["units"] += units
        agg["invested"] += _f(p.get("amount"))
        agg["value"] += value
        agg["pnl"] += pnl
        agg["cost_units"] += _f(p.get("openRate")) * units
        close_rate = (p.get("unrealizedPnL") or {}).get("closeRate")
        if close_rate is not None:
            agg["current_rate"] = close_rate

    direct: list[dict[str, Any]] = []
    for iid, a in by_inst.items():
        meta = instruments.get(iid, {})
        direct.append({
            "symbol": meta.get("symbolFull") or f"#{iid}",
            "instrument_id": iid,
            "name": meta.get("instrumentDisplayName"),
            "units": round(a["units"], 6),
            "invested": round(a["invested"], 2),
            "value": round(a["value"], 2),
            "pnl": round(a["pnl"], 2),
            "pnl_pct": round(a["pnl"] / a["invested"] * 100, 2) if a["invested"] > 0 else None,
            "avg_open_rate": round(a["cost_units"] / a["units"], 4) if a["units"] > 0 else None,
            "current_rate": a.get("current_rate"),
            "leverage": a.get("leverage"),
        })

    copy: list[dict[str, Any]] = []
    for m in cp.get("mirrors") or []:
        values = [_pos_value(p) for p in m.get("positions") or []]
        open_pnl = sum(v[1] for v in values)
        value = _f(m.get("availableAmount")) + sum(v[0] for v in values)
        invested = (_f(m.get("initialInvestment")) + _f(m.get("depositSummary"))
                    - _f(m.get("withdrawalSummary")))
        sl_amount = _f(m.get("stopLossAmount"))
        copy.append({
            "mirror_id": m.get("mirrorID") or m.get("mirrorId"),
            "username": m.get("parentUsername"),
            "invested": round(invested, 2),
            "value": round(value, 2),
            "open_pnl": round(open_pnl, 2),
            "open_pnl_pct": round(open_pnl / invested * 100, 2) if invested > 0 else None,
            "closed_pnl": round(_f(m.get("closedPositionsNetProfit")), 2),
            "available_cash": round(_f(m.get("availableAmount")), 2),
            "stop_loss_pct": m.get("stopLossPercentage"),
            "stop_loss_amount": round(sl_amount, 2) if sl_amount else None,
            "paused": bool(m.get("isPaused")),
            "started": m.get("startedCopyDate"),
        })

    cash = _f(cp.get("credit"))
    total = cash + sum(r["value"] for r in direct) + sum(c["value"] for c in copy)
    return _finalize({
        "as_of": _now_iso(),
        "source": "public_api",
        "account_currency": "USD",
        "totals": {
            "total_value": round(total, 2),
            "cash": round(cash, 2),
            "unrealized_pnl": round(_f(cp.get("unrealizedPnL")), 2),
        },
        "direct": direct,
        "copy": copy,
    })


# ─────────────────────────────────────────────────────────────────────────────
# API publique eToro (lecture seule)
# ─────────────────────────────────────────────────────────────────────────────

def api_configured() -> bool:
    return bool(os.getenv("ETORO_API_KEY") and os.getenv("ETORO_USER_KEY"))


def _api_get(client: Any, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {
        "x-api-key": os.environ["ETORO_API_KEY"],
        "x-user-key": os.environ["ETORO_USER_KEY"],
        "x-request-id": str(uuid.uuid4()),
    }
    base = os.getenv("ETORO_API_BASE", "https://public-api.etoro.com").rstrip("/")
    r = client.get(f"{base}{path}", headers=headers, params=params)
    r.raise_for_status()
    data: dict[str, Any] = r.json()
    return data


def fetch_snapshot_from_api() -> dict[str, Any]:
    """Lit le compte réel via l'API publique. Lève RuntimeError sans clés."""
    if not api_configured():
        raise RuntimeError("ETORO_API_KEY / ETORO_USER_KEY absents de l'environnement")
    import httpx

    with httpx.Client(timeout=20.0) as client:
        pnl = _api_get(client, _PNL_PATH)
        cp = pnl.get("clientPortfolio") or {}
        ids = sorted({
            int(p.get("instrumentID") or p.get("instrumentId") or 0)
            for p in cp.get("positions") or []
            if not (p.get("mirrorID") or p.get("mirrorId"))
        } - {0})
        instruments: dict[int, dict[str, Any]] = {}
        if ids:
            try:
                meta = _api_get(client, _INSTRUMENTS_PATH,
                                {"instrumentIds": ",".join(map(str, ids))})
                for d in meta.get("instrumentDisplayDatas") or []:
                    instruments[int(d["instrumentID"])] = d
            except Exception as e:  # symboles manquants ≠ snapshot faux
                logger.warning(f"[etoro_sync] métadonnées instruments indisponibles: {e}")
    return normalize_public_api(pnl, instruments)


# ─────────────────────────────────────────────────────────────────────────────
# Persistance
# ─────────────────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"[etoro_sync] lecture {path.name} échouée: {e}")
        return default


def save_snapshot(snapshot: dict[str, Any]) -> None:
    """Écrit latest.json et met à jour la ligne du jour dans history.json."""
    ETORO_DIR.mkdir(parents=True, exist_ok=True)
    with FileLock(str(_LOCK_PATH), timeout=10):
        _atomic_write(LATEST_PATH, snapshot)
        history: list[dict[str, Any]] = _read_json(HISTORY_PATH, [])
        day = str(snapshot["as_of"])[:10]
        row = {
            "date": day,
            "as_of": snapshot["as_of"],
            "source": snapshot.get("source"),
            "total_value": snapshot["totals"]["total_value"],
            "cash": snapshot["split"]["cash"],
            "direct_value": snapshot["split"]["direct_value"],
            "copy_value": snapshot["split"]["copy_value"],
            "copy_by_trader": {c["username"]: c["value"] for c in snapshot["copy"]},
        }
        history = [h for h in history if h.get("date") != day] + [row]
        history.sort(key=lambda h: h["date"])
        _atomic_write(HISTORY_PATH, history[-HISTORY_MAX_DAYS:])


def load_latest() -> dict[str, Any] | None:
    data = _read_json(LATEST_PATH, None)
    return data if isinstance(data, dict) else None


def load_history() -> list[dict[str, Any]]:
    data = _read_json(HISTORY_PATH, [])
    return data if isinstance(data, list) else []


def snapshot_age_hours(snapshot: dict[str, Any], now: datetime | None = None) -> float | None:
    try:
        ts = datetime.fromisoformat(str(snapshot["as_of"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return round(((now or datetime.now(UTC)) - ts).total_seconds() / 3600, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Vue « argent réel » : réconciliation book + actions du jour
# ─────────────────────────────────────────────────────────────────────────────

def _book_symbol_map() -> dict[str, dict[str, Any]]:
    """Symbole eToro → position du book. eToro cote LNVGY sous 0992.HK, d'où
    la double clé (ticker d'affichage ET price_ticker)."""
    from modules.my_portfolio_data import POSITIONS

    out: dict[str, dict[str, Any]] = {}
    for p in POSITIONS:
        out[p["ticker"].upper()] = p
        if p.get("price_ticker"):
            out[p["price_ticker"].upper()] = p
    return out


def reconcile_with_book(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Écarts entre les lignes directes eToro et `my_portfolio_data.POSITIONS`."""
    book = _book_symbol_map()
    seen: set[str] = set()
    issues: list[dict[str, Any]] = []
    for r in snapshot.get("direct", []):
        sym = (r.get("symbol") or "").upper()
        p = book.get(sym)
        if p is None:
            if r["value"] >= 5:  # reliquats (CVR, fractions de spin-off) ignorés
                issues.append({"symbol": r["symbol"], "kind": "not_in_book",
                               "detail": f"détenu sur eToro ({r['value']:.0f} $) mais absent du book"})
            continue
        seen.add(p["ticker"])
        book_units, real_units = _f(p.get("shares")), _f(r.get("units"))
        if real_units > 0 and abs(book_units - real_units) / real_units > _UNITS_TOLERANCE:
            issues.append({"symbol": p["ticker"], "kind": "units_mismatch",
                           "detail": f"book {book_units:g} vs eToro {real_units:g} unités"})
    for t, p in {p["ticker"]: p for p in book.values()}.items():
        if t not in seen and _f(p.get("shares")) > 0:
            issues.append({"symbol": t, "kind": "missing_on_etoro",
                           "detail": "dans le book mais plus détenu sur eToro"})
    return issues


def actions_today(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Actions à décider sur les lignes réellement détenues, triées par gravité.

    Source : signaux de vente des thèses (statut éditorial — les auto_metric
    sont recalculés dans /api/my_portfolio, pas ici). Seul `declenche` crée une
    action ; `a_surveiller` est compté pour contexte.
    """
    from modules.my_portfolio_thesis import get_all_theses

    theses = get_all_theses()
    book = _book_symbol_map()
    out: list[dict[str, Any]] = []
    for r in snapshot.get("direct", []):
        p = book.get((r.get("symbol") or "").upper())
        ticker = p["ticker"] if p else r.get("symbol")
        signals = (theses.get(ticker) or {}).get("sell_signals") or []
        fired = [s for s in signals if s.get("statut") == "declenche"]
        watch = [s for s in signals if s.get("statut") == "a_surveiller"]
        if fired:
            out.append({
                "ticker": ticker, "severity": "high", "action": "Décider : vendre ou réviser la thèse",
                "reasons": [s["libelle"] for s in fired], "watch_count": len(watch),
                "value": r["value"], "pnl_pct": r.get("pnl_pct"),
            })
    for c in snapshot.get("copy", []):
        sl = c.get("stop_loss_pct")
        if c.get("paused"):
            out.append({"ticker": f"copy:{c['username']}", "severity": "medium",
                        "action": "Copie en pause", "reasons": [], "value": c["value"]})
        elif sl is not None and _f(sl) >= 40:
            out.append({"ticker": f"copy:{c['username']}", "severity": "medium",
                        "action": f"Stop de copie large ({_f(sl):.0f} %) — perte tolérée élevée",
                        "reasons": [], "value": c["value"]})
    rank = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda a: (rank.get(a["severity"], 9), -_f(a.get("value"))))
    return out


def build_overview() -> dict[str, Any]:
    snap = load_latest()
    if snap is None:
        return {"available": False, "api_configured": api_configured(),
                "reason": "aucun snapshot eToro — configurer ETORO_API_KEY/ETORO_USER_KEY "
                          "ou pousser un snapshot via POST /api/etoro/snapshot"}
    age = snapshot_age_hours(snap)
    return {
        "available": True,
        "api_configured": api_configured(),
        "age_hours": age,
        "stale": age is None or age > STALE_AFTER_HOURS,
        "snapshot": snap,
        "actions": actions_today(snap),
        "reconciliation": reconcile_with_book(snap),
        "history": load_history()[-90:],
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI (cron)
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Synchro lecture seule du compte eToro réel")
    ap.add_argument("--ingest", type=Path, help="fichier JSON « portfolio summary » à ingérer")
    args = ap.parse_args(argv)
    import config  # noqa: F401  — charge backend/.env (clés ETORO_*)

    if args.ingest:
        snap = normalize_summary(json.loads(args.ingest.read_text(encoding="utf-8")), source="file")
    else:
        if not api_configured():
            logger.info("[etoro_sync] clés API eToro absentes — synchro ignorée")
            return 0
        try:
            snap = fetch_snapshot_from_api()
        except Exception as e:
            logger.error(f"[etoro_sync] synchro API échouée: {e}")
            return 1
    save_snapshot(snap)
    print(f"[etoro_sync] OK total={snap['totals']['total_value']} "
          f"direct={snap['split']['direct_pct']}% copy={snap['split']['copy_pct']}% "
          f"cash={snap['split']['cash_pct']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
