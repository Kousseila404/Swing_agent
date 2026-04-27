#!/usr/bin/env python3
"""Outil de réconciliation des doublons OPEN dans trade_journal.csv.

Scénario cible (observé 2026-04-23) : bulk-click /execute → même ticker ouvert
N fois en quelques secondes. Le CSV a plusieurs lignes OPEN pour un seul ticker,
mais Alpaca (en réalité) n'a probablement exécuté qu'UNE fois avec la quantité
cumulée, ou a filled partiellement.

Cet outil :
  1. Liste les tickers avec > 1 ligne OPEN
  2. Pour chaque doublon, propose les lignes à garder/marquer CANCELED
  3. ECRIT RIEN sans --apply (dry-run par default)

Usage :
    python scripts/reconcile_duplicates.py            # dry-run (diagnostic)
    python scripts/reconcile_duplicates.py --apply    # applique la résolution
    python scripts/reconcile_duplicates.py --keep first  # garde la 1ère ligne
    python scripts/reconcile_duplicates.py --keep last   # garde la dernière

Convention de résolution par défaut (`--keep first`) :
  • On garde la PREMIÈRE ligne OPEN (plus ancienne, celle qui est probablement
    réellement fillée côté broker).
  • Les autres lignes OPEN pour ce ticker → Status='CANCELED' + Exit_Date=now.
  • PnL = 0 sur les CANCELED (elles ne sont pas comptées dans les WIN/LOSS stats).

Après exécution : VÉRIFIER Alpaca manuellement pour s'assurer que le broker
et le journal sont synchro (le broker peut avoir fillé la somme des sizes).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from filelock import FileLock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modules.api_core import CSV_LOCK_PATH, CSV_PATH   # noqa: E402


def find_duplicates(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Retourne {ticker: DataFrame} pour tickers avec > 1 ligne OPEN."""
    opens = df[df["Status"] == "OPEN"].copy()
    counts = opens["Ticker"].value_counts()
    dupes = counts[counts > 1]
    return {
        str(tk): opens[opens["Ticker"] == tk].sort_values("Date")
        for tk in dupes.index
    }


def resolve(df: pd.DataFrame, keep: str = "first") -> tuple[pd.DataFrame, list[dict]]:
    """Modifie df in-place : marque CANCELED toutes les lignes OPEN dupliquées
    sauf celle à garder (first|last). Retourne (df, actions_report).
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    dupes = find_duplicates(df)
    actions: list[dict] = []
    for ticker, rows in dupes.items():
        if keep == "first":
            keep_idx = rows.index[0]
        elif keep == "last":
            keep_idx = rows.index[-1]
        else:
            raise ValueError(f"keep invalide: {keep!r}")
        cancel_indices = [i for i in rows.index if i != keep_idx]
        for ci in cancel_indices:
            row = df.loc[ci]
            df.at[ci, "Status"] = "CANCELED"
            df.at[ci, "Exit_Date"] = now
            df.at[ci, "Exit_Price"] = row.get("Entry")  # PnL=0
            actions.append({
                "ticker": ticker,
                "date":   row.get("Date"),
                "size":   row.get("Size"),
                "entry":  row.get("Entry"),
                "order_id": row.get("Order_ID"),
                "action": "CANCELED",
            })
    return df, actions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="Applique les changements (sinon dry-run)")
    parser.add_argument("--keep", choices=("first", "last"), default="first",
                        help="Laquelle des lignes dupliquées garder (défaut: first)")
    args = parser.parse_args()

    if not CSV_PATH.exists():
        print(f"[!] Journal introuvable : {CSV_PATH}")
        return 2

    with FileLock(str(CSV_LOCK_PATH), timeout=10):
        df = pd.read_csv(CSV_PATH, dtype=str)

    dupes = find_duplicates(df)
    if not dupes:
        print("[OK] Aucun doublon OPEN détecté.")
        return 0

    print(f"[!] {len(dupes)} ticker(s) avec > 1 ligne OPEN :")
    for ticker, rows in dupes.items():
        print(f"\n  {ticker}: {len(rows)} lignes OPEN")
        for _, r in rows.iterrows():
            print(f"    • {r.get('Date')}  size={r.get('Size')}  "
                  f"entry={r.get('Entry')}  order_id={r.get('Order_ID')}")

    df_resolved, actions = resolve(df.copy(), keep=args.keep)
    print(f"\n[Plan] Garder la ligne {args.upper if hasattr(args, 'upper') else args.keep.upper()}, "
          f"marquer CANCELED les {len(actions)} autre(s) :")
    for a in actions:
        print(f"  → CANCEL {a['ticker']}  {a['date']}  size={a['size']}  entry={a['entry']}")

    if not args.apply:
        print("\n[DRY-RUN] Ajouter --apply pour effectivement modifier le journal.")
        print("[NOTE] Vérifier Alpaca AVANT d'appliquer : le broker a probablement")
        print("       fillé la somme des sizes en une seule position (ex: NEM 36 sh).")
        return 0

    # Apply
    with FileLock(str(CSV_LOCK_PATH), timeout=10):
        tmp_path = CSV_PATH.with_suffix(".tmp")
        df_resolved.to_csv(tmp_path, index=False)
        tmp_path.replace(CSV_PATH)
    print(f"\n[OK] {len(actions)} ligne(s) marquée(s) CANCELED. Journal : {CSV_PATH}")
    print("[NEXT] Vérifier Alpaca : `python main.py --alpaca-sync` pour resync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
