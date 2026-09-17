"""Reconstruit `data/trade_journal.csv` depuis l'historique d'ordres Alpaca.

Audit 2026-09-17 (P0-4) : le journal contenait des doublons fantômes (lignes
clôturées avec de vieux fills puis ré-importées), des lignes `ALPACA-IMPORT-*`
sans SL/TP/scores, et des lignes d'entrée perdues (race CSV). Alpaca est la
source de vérité des fills ; `proposals.json` (status=executed, order_id)
fournit SL/TP/scores/secteur capturés à l'approbation ; l'ancien CSV fournit
les métadonnées tracker (Close_Reason, Last_TS_*, Last_LT_*) par Order_ID.

Usage :
    python -m scripts.rebuild_journal_from_alpaca            # dry-run (affiche)
    python -m scripts.rebuild_journal_from_alpaca --apply    # backup + écrit + DuckDB

Règles :
  • Parent = ordre BUY (bracket ou simple) avec filled_qty > 0 → une ligne.
    Parent BUY jamais fillé (canceled/expired) → ligne CANCELED (PnL 0).
  • Sortie = ordre SELL fillé, apparié FIFO au parent OPEN le plus ancien du
    même symbole (quantité ≥). Status WIN/LOSS par PnL ; Close_Reason par type
    d'ordre (stop → SL_HIT, limit → TP_HIT, market → valeur de l'ancien CSV si
    connue, sinon BROKER_SYNC).
  • Les positions Alpaca ouvertes doivent correspondre exactement aux lignes
    OPEN produites — sinon le script refuse d'appliquer.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import config  # noqa: E402
from modules.utils import CSV_PATH, CSV_SCHEMA  # noqa: E402

PROPOSALS_PATH = _BACKEND / "data" / "proposals.json"
# Premier trade TITAN réel : NEM 2026-04-24. Avant : ordres de test (GOOGL/AAPL).
SINCE = datetime(2026, 4, 24, tzinfo=UTC)

_TRACKER_META_COLS = [
    "Last_Alert_Pct", "Last_TS_Update", "Last_TS_Mode", "Signal", "Sector",
    "Reco_Entry", "Slippage_Bps", "Last_LT_Action", "Last_LT_Date",
    "Last_LT_Severity",
    # Scores d'entrée : les propositions d'avant août ont été purgées de
    # proposals.json — l'ancien CSV (backfill_entry_scores) est la seule source.
    "Titan_Score_Entry", "Quality_Entry", "Value_Entry", "Risk_Entry",
    "Momentum_Entry", "Piotroski_Entry", "Growth_Entry", "F_Score_Entry",
    "Tilt_Flags_Entry", "Confidence_Entry",
]
_SCORE_COLS = {
    "Titan_Score_Entry": "titan_score", "Quality_Entry": "quality_score",
    "Value_Entry": "value_score", "Risk_Entry": "risk_score",
    "Momentum_Entry": "momentum_score", "Piotroski_Entry": "piotroski_score",
    "Growth_Entry": "growth_score",
}


def _local(dt) -> str:
    """Horodatage Alpaca (UTC) → format journal (heure locale du process)."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _fmt(v, nd=4) -> str:
    if v is None:
        return ""
    try:
        return f"{round(float(v), nd):g}" if nd else str(v)
    except (TypeError, ValueError):
        return ""


def _load_proposals_by_order() -> dict[str, dict[str, Any]]:
    if not PROPOSALS_PATH.exists():
        return {}
    raw = json.loads(PROPOSALS_PATH.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("items") or raw.get("proposals") or []
    out = {}
    for p in items:
        oid = str(p.get("order_id") or "")
        if oid and p.get("status") == "executed":
            out[oid] = p
    return out


def _load_old_by_order() -> dict[str, dict[str, Any]]:
    if not CSV_PATH.exists():
        return {}
    df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
    out = {}
    for _, r in df.iterrows():
        oid = str(r.get("Order_ID") or "")
        if oid:
            out[oid] = r.to_dict()
    return out


def fetch_orders(client) -> list:
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    orders: list = []
    until = None
    for _ in range(20):
        req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500, after=SINCE, until=until, nested=False)
        batch = list(client.get_orders(filter=req) or [])
        if not batch:
            break
        orders.extend(batch)
        if len(batch) < 500:
            break
        until = min(o.submitted_at for o in batch)
    seen = set()
    uniq = []
    for o in orders:
        if str(o.id) in seen:
            continue
        seen.add(str(o.id))
        uniq.append(o)
    return sorted(uniq, key=lambda o: o.submitted_at)


def build_rows(orders: list, positions: dict[str, float]) -> tuple[list[dict], list[str]]:
    props = _load_proposals_by_order()
    old = _load_old_by_order()
    notes: list[str] = []

    def side(o):
        return str(o.side.value if hasattr(o.side, "value") else o.side).lower()

    def otype(o):
        return str(o.type.value if hasattr(o.type, "value") else o.type).lower()

    def status(o):
        return str(o.status.value if hasattr(o.status, "value") else o.status).lower()

    rows: list[dict] = []
    open_by_sym: dict[str, list[dict]] = {}

    # Jambes enfants indexées par parent (nested=False → legs absents ; on
    # retrouve les enfants bracket via leur symbole + submitted_at proche).
    for o in orders:
        sym = str(o.symbol).upper()
        fq = float(o.filled_qty or 0)
        if side(o) == "buy":
            if fq <= 0:
                if any(s in status(o) for s in ("canceled", "expired", "rejected")) and str(o.order_class.value if hasattr(o.order_class, "value") else o.order_class).lower() == "bracket":
                    row = {c: "" for c in CSV_SCHEMA}
                    row.update({
                        "Date": _local(o.submitted_at), "Ticker": sym, "Direction": "LONG",
                        "Entry": _fmt(o.limit_price or 0), "Size": "0", "Status": "CANCELED",
                        "Exit_Date": _local(o.canceled_at or o.expired_at or o.updated_at),
                        "Order_ID": str(o.id), "Close_Reason": "ENTRY_EXPIRED",
                    })
                    rows.append(row)
                continue
            row = {c: "" for c in CSV_SCHEMA}
            row.update({
                "Date": _local(o.filled_at or o.submitted_at), "Ticker": sym, "Direction": "LONG",
                "Entry": _fmt(o.filled_avg_price), "Size": str(int(fq)), "Status": "OPEN",
                "Order_ID": str(o.id), "Signal": "AUTO_PROPOSAL",
            })
            # SL/TP : jambes bracket (même symbole, soumises dans la minute, sell)
            legs = [x for x in orders if str(x.symbol).upper() == sym and side(x) == "sell"
                    and abs((x.submitted_at - o.submitted_at).total_seconds()) < 90]
            for leg in legs:
                if "stop" in otype(leg) and leg.stop_price:
                    row["Stop_Loss"] = _fmt(leg.stop_price)
                    row["Initial_SL"] = _fmt(leg.stop_price)
                elif otype(leg) == "limit" and leg.limit_price:
                    row["Take_Profit"] = _fmt(leg.limit_price)
            # Proposition exécutée → SL/TP précis + scores + secteur
            p = props.get(str(o.id))
            if p:
                ctx = p.get("context") or {}
                row["Stop_Loss"] = row["Stop_Loss"] or _fmt(p.get("stop_loss"))
                row["Initial_SL"] = row["Initial_SL"] or _fmt(p.get("stop_loss"))
                row["Take_Profit"] = row["Take_Profit"] or _fmt(p.get("take_profit"))
                row["Sector"] = str(p.get("sector") or "")
                row["Reco_Entry"] = _fmt(p.get("entry"))
                try:
                    reco = float(p.get("entry") or 0)
                    if reco > 0:
                        row["Slippage_Bps"] = _fmt((float(o.filled_avg_price) - reco) / reco * 10000, 1)
                except (TypeError, ValueError):
                    pass
                for col, key in _SCORE_COLS.items():
                    v = ctx.get(key)
                    row[col] = _fmt(v, 2) if v is not None else ""
                fs, fsm = ctx.get("f_score"), ctx.get("f_score_max")
                row["F_Score_Entry"] = f"{int(fs)}/{int(fsm)}" if fs is not None and fsm is not None else ""
                row["Tilt_Flags_Entry"] = ",".join(ctx.get("titan_tilt_flags") or [])
                conf = ctx.get("confidence")
                row["Confidence_Entry"] = str(int(conf)) if isinstance(conf, (int, float)) else ""
            else:
                notes.append(f"{sym} {o.id}: pas de proposition exécutée (ordre manuel ?)")
            try:
                if row["Stop_Loss"] and row["Take_Profit"] and float(row["Entry"]) > float(row["Stop_Loss"]):
                    e, s_, t = float(row["Entry"]), float(row["Stop_Loss"]), float(row["Take_Profit"])
                    row["RR"] = _fmt((t - e) / (e - s_), 2)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
            # Métadonnées tracker préservées depuis l'ancien CSV (par Order_ID).
            # Le SL courant n'est PAS repris : le trailing stop est recalculé
            # par le tracker avec les paramètres LT en vigueur (le SL bracket
            # d'origine = plancher catastrophe).
            o_row = old.get(str(o.id))
            if o_row:
                for c in _TRACKER_META_COLS:
                    if o_row.get(c) and not row.get(c):
                        row[c] = o_row[c]
                row["_old_close_reason"] = o_row.get("Close_Reason") or ""
            rows.append(row)
            open_by_sym.setdefault(sym, []).append(row)
        else:  # sell fillé → clôture FIFO
            if fq <= 0:
                continue
            queue = open_by_sym.get(sym) or []
            if not queue:
                notes.append(f"{sym} sell {o.id} ({fq} @ {o.filled_avg_price}) sans parent OPEN — ignoré")
                continue
            row = queue.pop(0)
            exit_px = float(o.filled_avg_price)
            entry = float(row["Entry"])
            row["Status"] = "WIN" if exit_px > entry else "LOSS"
            row["Exit_Price"] = _fmt(exit_px, 6)
            row["Exit_Date"] = _local(o.filled_at)[:16]
            t = otype(o)
            if "stop" in t:
                reason = "SL_HIT"
            elif t == "limit":
                reason = "TP_HIT"
            else:
                reason = row.get("_old_close_reason") or "BROKER_SYNC"
                if reason in ("", "BROKER_SYNC") and row.get("Last_TS_Mode"):
                    reason = "TRAILING_STOP"
            row["Close_Reason"] = reason
            if abs(fq - float(row["Size"])) >= 1:
                notes.append(f"{sym}: sell qty {fq} ≠ parent {row['Size']}")

    # Validation vs positions ouvertes chez Alpaca
    open_rows = {r["Ticker"]: float(r["Size"]) for r in rows if r["Status"] == "OPEN"}
    for sym, q in positions.items():
        if sym not in open_rows:
            notes.append(f"MISMATCH: {sym} ouvert chez Alpaca ({q}) mais aucune ligne OPEN reconstruite")
        elif abs(open_rows[sym] - q) >= 1:
            notes.append(f"MISMATCH: {sym} qty Alpaca {q} ≠ journal {open_rows[sym]}")
    for sym in open_rows:
        if sym not in positions:
            notes.append(f"MISMATCH: {sym} OPEN dans le journal mais absent chez Alpaca")
    for r in rows:
        r.pop("_old_close_reason", None)
    rows.sort(key=lambda r: r["Date"])
    return rows, notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from alpaca.trading.client import TradingClient
    client = TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY,
                           paper="paper" in str(config.ALPACA_BASE_URL))
    positions = {str(p.symbol).upper(): abs(float(p.qty)) for p in client.get_all_positions()}
    orders = fetch_orders(client)
    rows, notes = build_rows(orders, positions)

    df = pd.DataFrame(rows, columns=CSV_SCHEMA).fillna("")
    cols = ["Date", "Ticker", "Entry", "Stop_Loss", "Take_Profit", "Size", "Status",
            "Exit_Price", "Exit_Date", "Close_Reason", "Titan_Score_Entry", "Order_ID"]
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(df[cols].to_string(index=False))
    realized = 0.0
    for _, r in df[df["Status"].isin(["WIN", "LOSS"])].iterrows():
        realized += (float(r["Exit_Price"]) - float(r["Entry"])) * float(r["Size"])
    print(f"\n{len(df)} lignes | OPEN={int((df['Status']=='OPEN').sum())} "
          f"WIN={int((df['Status']=='WIN').sum())} LOSS={int((df['Status']=='LOSS').sum())} "
          f"CANCELED={int((df['Status']=='CANCELED').sum())} | PnL réalisé = {realized:+.2f} $")
    for n in notes:
        print("  •", n)
    mismatches = [n for n in notes if n.startswith("MISMATCH")]

    if not args.apply:
        print("\n(dry-run — relancer avec --apply pour écrire)")
        return 0
    if mismatches:
        print("\nREFUS : incohérences avec les positions Alpaca, rien n'est écrit.")
        return 2

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_dir = _BACKEND.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    if CSV_PATH.exists():
        shutil.copy2(CSV_PATH, backup_dir / f"trade_journal.csv.pre_rebuild_{stamp}")
    tmp = CSV_PATH.with_suffix(".tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(CSV_PATH)
    from modules.duckdb_journal import sync_from_csv
    print("DuckDB :", sync_from_csv())
    print(f"Écrit → {CSV_PATH} (backup dans {backup_dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
