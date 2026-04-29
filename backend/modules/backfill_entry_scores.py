"""Backfill rétroactif des entry scores TITAN/F-Score/etc dans trade_journal.

Contexte : la capture systématique des `*_Entry` (Titan_Score_Entry,
Quality_Entry, F_Score_Entry, Tilt_Flags_Entry, …) est active depuis
2026-04-24 via /proposals/approve_batch (cf. project_titan_entry_scores_capture).
Les positions ouvertes AVANT cette date n'ont pas ces champs → thesis_status
retourne NO_DATA pour elles.

Ce module utilise `universe_history` (Lot 6) pour récupérer les scores TITAN à
la date d'entrée et populer rétroactivement le CSV trade_journal.

Stratégie :
  1. Lit trade_journal.csv
  2. Pour chaque ligne OPEN sans Titan_Score_Entry, fenêtre ±3 jours autour de
     Date d'entrée → ticker_history sur les champs nécessaires.
  3. Prend la snapshot la plus PROCHE de la date d'entrée (idéalement même jour,
     sinon +/- 1, etc.).
  4. Update les colonnes *_Entry sur place (écriture atomique tmp + rename).
  5. Optionnel : resync DuckDB via duckdb_journal.sync_from_csv.

Idempotent — peut être ré-exécuté sans risque (skip les lignes qui ont déjà
Titan_Score_Entry rempli). Backup CSV créé en .archive/ avant écriture.
"""
from __future__ import annotations

import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from modules import universe_history
from modules.duckdb_journal import CSV_PATH, sync_from_csv
from modules.log import logger

_BACKFILL_FIELDS = [
    "titan_composite_score",
    "quality_score",
    "value_score",
    "risk_score",
    "momentum_score",
    "piotroski_score",
    "growth_score",
    "f_score",
    "f_score_max",
    "titan_tilt_flags",
]

# Map champ universe_history → colonne CSV.
_FIELD_TO_COL = {
    "titan_composite_score": "Titan_Score_Entry",
    "quality_score":         "Quality_Entry",
    "value_score":           "Value_Entry",
    "risk_score":            "Risk_Entry",
    "momentum_score":        "Momentum_Entry",
    "piotroski_score":       "Piotroski_Entry",
    "growth_score":          "Growth_Entry",
    # f_score format "X/Y" (ex: "8/9")
    # tilt_flags format CSV "qarp,consistent"
}

# Fenêtre autour de la date d'entrée pour trouver la snapshot la plus proche.
_LOOKUP_WINDOW_DAYS = 3


def _parse_entry_date(s: Any) -> date | None:
    if s is None:
        return None
    txt = str(s).strip()
    if not txt:
        return None
    # Format CSV typique : "2026-04-24 17:53:10" ou "2026-04-24"
    txt = txt[:10]
    try:
        return datetime.strptime(txt, "%Y-%m-%d").date()
    except ValueError:
        return None


def _is_blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def _pick_closest_snapshot(
    entry_date: date,
    ticker: str,
) -> dict[str, Any] | None:
    """Fenêtre ±_LOOKUP_WINDOW_DAYS autour de entry_date, retourne la snapshot
    la plus proche (par |delta_days|, puis date desc en cas d'égalité).
    """
    start = entry_date - timedelta(days=_LOOKUP_WINDOW_DAYS)
    end = entry_date + timedelta(days=_LOOKUP_WINDOW_DAYS)
    try:
        rows = universe_history.ticker_history(
            ticker, start=start, end=end, fields=_BACKFILL_FIELDS,
        )
    except Exception as exc:
        logger.warning(f"[backfill] hist {ticker} fail: {exc}")
        return None
    if not rows:
        return None

    def _key(row: dict[str, Any]) -> tuple[int, str]:
        try:
            d = datetime.strptime(row.get("date") or "", "%Y-%m-%d").date()
        except ValueError:
            return (999, "")
        return (abs((d - entry_date).days), row.get("date") or "")

    rows_sorted = sorted(rows, key=_key)
    return rows_sorted[0]


def _row_to_entry_cols(snap: dict[str, Any]) -> dict[str, Any]:
    """Transforme une snapshot universe_history en dict {col_csv: valeur}."""
    out: dict[str, Any] = {}
    for src, dst in _FIELD_TO_COL.items():
        v = snap.get(src)
        if v is None:
            continue
        try:
            out[dst] = round(float(v), 2)
        except (TypeError, ValueError):
            continue

    # F_Score_Entry : format "X/Y"
    f_score = snap.get("f_score")
    f_max = snap.get("f_score_max") or 9
    if f_score is not None:
        try:
            out["F_Score_Entry"] = f"{int(f_score)}/{int(f_max)}"
        except (TypeError, ValueError):
            pass

    # Tilt_Flags_Entry : CSV
    tilts = snap.get("titan_tilt_flags")
    if isinstance(tilts, list) and tilts:
        out["Tilt_Flags_Entry"] = ",".join(str(t) for t in tilts)
    elif isinstance(tilts, str) and tilts.strip():
        out["Tilt_Flags_Entry"] = tilts.strip()

    return out


def backfill_journal(
    *,
    csv_path: Path | str = CSV_PATH,
    dry_run: bool = False,
    sync_duckdb: bool = True,
) -> dict[str, Any]:
    """Backfille les *_Entry pour toutes les lignes OPEN sans Titan_Score_Entry.

    Args:
        csv_path: chemin vers trade_journal.csv
        dry_run: si True, ne réécrit pas le CSV (juste retourne le diff)
        sync_duckdb: si True et !dry_run, appelle sync_from_csv après écriture

    Returns:
        {
          "n_total": int,
          "n_eligible": int,
          "n_filled": int,
          "n_skipped_no_history": int,
          "details": [{ticker, entry_date, fields_filled, snapshot_date}],
          "dry_run": bool,
        }
    """
    csv = Path(csv_path)
    if not csv.exists():
        return {
            "n_total": 0, "n_eligible": 0, "n_filled": 0,
            "n_skipped_no_history": 0, "details": [],
            "dry_run": dry_run, "error": "CSV introuvable",
        }

    df = pd.read_csv(csv, dtype={"Ticker": str})
    n_total = len(df)
    if n_total == 0:
        return {
            "n_total": 0, "n_eligible": 0, "n_filled": 0,
            "n_skipped_no_history": 0, "details": [],
            "dry_run": dry_run,
        }

    # Critère d'éligibilité : Status=OPEN ET Titan_Score_Entry vide.
    if "Titan_Score_Entry" not in df.columns:
        df["Titan_Score_Entry"] = None
    eligible_mask = (df.get("Status") == "OPEN") & df["Titan_Score_Entry"].apply(_is_blank)
    eligible_idx = df.index[eligible_mask].tolist()
    n_eligible = len(eligible_idx)

    n_filled = 0
    n_skipped = 0
    details: list[dict[str, Any]] = []

    for idx in eligible_idx:
        ticker = str(df.at[idx, "Ticker"] or "").upper().strip()
        entry_date = _parse_entry_date(df.at[idx, "Date"])
        if not ticker or entry_date is None:
            n_skipped += 1
            continue
        snap = _pick_closest_snapshot(entry_date, ticker)
        if snap is None:
            n_skipped += 1
            details.append({
                "ticker": ticker, "entry_date": entry_date.isoformat(),
                "snapshot_date": None, "fields_filled": [],
            })
            continue
        new_cols = _row_to_entry_cols(snap)
        if not new_cols:
            n_skipped += 1
            continue
        for col, val in new_cols.items():
            if col not in df.columns:
                df[col] = None
            df.at[idx, col] = val
        n_filled += 1
        details.append({
            "ticker": ticker,
            "entry_date": entry_date.isoformat(),
            "snapshot_date": snap.get("date"),
            "fields_filled": list(new_cols.keys()),
        })

    if dry_run:
        return {
            "n_total": n_total, "n_eligible": n_eligible,
            "n_filled": n_filled, "n_skipped_no_history": n_skipped,
            "details": details, "dry_run": True,
        }

    # Backup avant écriture (immutable trail).
    if n_filled > 0:
        archive_dir = csv.parent / ".archive" / "backfill_entry_scores"
        archive_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = archive_dir / f"trade_journal_{ts}.csv"
        shutil.copy2(csv, backup)
        # tmp + rename atomique.
        tmp = csv.with_suffix(csv.suffix + ".tmp")
        df.to_csv(tmp, index=False)
        tmp.replace(csv)
        if sync_duckdb:
            try:
                sync_from_csv(csv)
            except Exception as exc:
                logger.warning(f"[backfill] sync_duckdb fail: {exc}")

    return {
        "n_total": n_total, "n_eligible": n_eligible,
        "n_filled": n_filled, "n_skipped_no_history": n_skipped,
        "details": details, "dry_run": False,
    }
