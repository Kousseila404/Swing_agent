"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE PERFORMANCE_ATTRIBUTION — Corrèle scores entry → outcome ║
║                                                                  ║
║  Objectif : tu as N trades clos, chacun avec ses scores TITAN à  ║
║  l'entrée (Lot 15). On regroupe par bucket de score pour voir si ║
║  les hauts scores produisent vraiment des wins (ie. notre moteur ║
║  est calibré).                                                   ║
║                                                                  ║
║  Sortie : par pilier (TITAN composite, Quality, Value, Risk,     ║
║  Momentum, Piotroski, Growth, F-Score), 4 buckets de scores avec ║
║  win rate + PnL moyen + count.                                   ║
║                                                                  ║
║  Mode "preview" même si <20 trades — utile pour voir le shape     ║
║  qui se construit. Marque les buckets sous-représentés.          ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from modules.duckdb_journal import read_journal_df
from modules.log import logger

# Buckets de score (0-100). 4 quartiles classiques.
SCORE_BUCKETS = [
    {"name": "<50",    "min":  0, "max":  50, "tone": "var(--danger)"},
    {"name": "50-70",  "min": 50, "max":  70, "tone": "var(--warning)"},
    {"name": "70-85",  "min": 70, "max":  85, "tone": "var(--accent-primary)"},
    {"name": "85+",    "min": 85, "max": 999, "tone": "var(--success)"},
]

# Piliers à attribuer. Champ CSV → libellé UI.
PILLARS = [
    {"col": "Titan_Score_Entry", "label": "TITAN composite"},
    {"col": "Quality_Entry",     "label": "Quality"},
    {"col": "Value_Entry",       "label": "Value"},
    {"col": "Risk_Entry",        "label": "Risk"},
    {"col": "Momentum_Entry",    "label": "Momentum"},
    {"col": "Piotroski_Entry",   "label": "Piotroski"},
    {"col": "Growth_Entry",      "label": "Growth"},
]

# Min sample par bucket pour considérer le signal "stable". En dessous,
# le frontend affiche un avertissement.
MIN_SAMPLE_STABLE = 5
MIN_TOTAL_TRADES_STABLE = 20


def _safe_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        if pd.isna(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _trade_pnl(row: dict[str, Any]) -> float | None:
    entry = _safe_float(row.get("Entry"))
    exit_p = _safe_float(row.get("Exit_Price"))
    size = _safe_float(row.get("Size"))
    if entry is None or exit_p is None or size is None:
        return None
    direction = str(row.get("Direction") or "LONG").upper()
    if direction == "LONG":
        return (exit_p - entry) * size
    return (entry - exit_p) * size


def _trade_pnl_pct(row: dict[str, Any]) -> float | None:
    entry = _safe_float(row.get("Entry"))
    exit_p = _safe_float(row.get("Exit_Price"))
    if entry is None or exit_p is None or entry <= 0:
        return None
    direction = str(row.get("Direction") or "LONG").upper()
    if direction == "LONG":
        return (exit_p / entry - 1.0) * 100.0
    return (1.0 - exit_p / entry) * 100.0


def _is_winner(row: dict[str, Any]) -> bool | None:
    status = str(row.get("Status") or "").upper()
    if status in {"WIN", "TP"}:
        return True
    if status in {"LOSS", "SL"}:
        return False
    return None


def _bucket_for(score: float | None) -> str | None:
    if score is None:
        return None
    for b in SCORE_BUCKETS:
        if b["min"] <= score < b["max"]:
            return b["name"]
    return None


def _closed_trades_df() -> pd.DataFrame:
    df = read_journal_df()
    if df is None or df.empty:
        return pd.DataFrame()
    if "Status" not in df.columns:
        return pd.DataFrame()
    closed_mask = df["Status"].astype(str).str.upper().isin(
        ["WIN", "LOSS", "TP", "SL"]
    )
    return df[closed_mask].copy()


def _attribute_pillar(df: pd.DataFrame, col: str, label: str) -> dict[str, Any]:
    """Pour un pilier, retourne agrégat par bucket : {n, n_wins, win_rate, pnl_avg_pct}."""
    if col not in df.columns or df.empty:
        return {"label": label, "col": col, "buckets": [], "n_total": 0,
                "covered": 0}

    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        score = _safe_float(r.get(col))
        is_win = _is_winner(r)
        if score is None or is_win is None:
            continue
        pnl_pct = _trade_pnl_pct(r)
        rows.append({
            "score":   score,
            "win":     bool(is_win),
            "pnl_pct": pnl_pct,
            "ticker":  r.get("Ticker"),
        })

    by_bucket: dict[str, list[dict[str, Any]]] = {b["name"]: [] for b in SCORE_BUCKETS}
    for row in rows:
        b = _bucket_for(row["score"])
        if b is not None:
            by_bucket[b].append(row)

    buckets_out: list[dict[str, Any]] = []
    for b in SCORE_BUCKETS:
        bucket_rows = by_bucket[b["name"]]
        n = len(bucket_rows)
        n_wins = sum(1 for r in bucket_rows if r["win"])
        pnl_pcts = [r["pnl_pct"] for r in bucket_rows if r["pnl_pct"] is not None]
        avg_pnl = (sum(pnl_pcts) / len(pnl_pcts)) if pnl_pcts else None
        buckets_out.append({
            "name":        b["name"],
            "min":         b["min"],
            "max":         b["max"] if b["max"] < 999 else 100,
            "n":           n,
            "n_wins":      n_wins,
            "win_rate":    (n_wins / n * 100.0) if n else None,
            "pnl_avg_pct": avg_pnl,
            "stable":      n >= MIN_SAMPLE_STABLE,
        })

    return {
        "label":   label,
        "col":     col,
        "buckets": buckets_out,
        "n_total": len(rows),
        "covered": len(rows),
    }


def _tilt_flags_attribution(df: pd.DataFrame) -> dict[str, Any]:
    """Attribution par tilt flag (qarp/garp/consistent/cheap_junk/falling_knife).

    Comme un trade peut avoir plusieurs flags, on additionne les comptes.
    """
    flag_stats: dict[str, dict[str, Any]] = {}
    if "Tilt_Flags_Entry" not in df.columns:
        return {"flags": [], "n_total": 0}

    n_total = 0
    for _, r in df.iterrows():
        is_win = _is_winner(r)
        if is_win is None:
            continue
        n_total += 1
        flags_csv = str(r.get("Tilt_Flags_Entry") or "").strip()
        if not flags_csv:
            continue
        for flag in flags_csv.split(","):
            f = flag.strip()
            if not f:
                continue
            if f not in flag_stats:
                flag_stats[f] = {"n": 0, "n_wins": 0, "pnls": []}
            flag_stats[f]["n"] += 1
            if is_win:
                flag_stats[f]["n_wins"] += 1
            pnl_pct = _trade_pnl_pct(r)
            if pnl_pct is not None:
                flag_stats[f]["pnls"].append(pnl_pct)

    out_flags = []
    for flag, stats in flag_stats.items():
        n = stats["n"]
        avg = (sum(stats["pnls"]) / len(stats["pnls"])) if stats["pnls"] else None
        out_flags.append({
            "flag":         flag,
            "n":            n,
            "n_wins":       stats["n_wins"],
            "win_rate":     (stats["n_wins"] / n * 100.0) if n else None,
            "pnl_avg_pct":  avg,
            "stable":       n >= MIN_SAMPLE_STABLE,
        })
    out_flags.sort(key=lambda f: -f["n"])
    return {"flags": out_flags, "n_total": n_total}


def compute_attribution() -> dict[str, Any]:
    """Point d'entrée principal. Retourne dict structuré pour l'UI."""
    try:
        df = _closed_trades_df()
    except Exception as exc:
        logger.error(f"[performance_attribution] load fail: {exc}")
        return {"ok": False, "error": str(exc)}

    n_closed = len(df)
    if n_closed == 0:
        return {
            "ok": True, "n_closed": 0,
            "stable": False,
            "pillars": [],
            "tilts":   {"flags": [], "n_total": 0},
        }

    pillars_out = [_attribute_pillar(df, p["col"], p["label"]) for p in PILLARS]

    # Couverture globale (% trades avec scores capturés).
    composite = next((p for p in pillars_out if p["col"] == "Titan_Score_Entry"), None)
    coverage_pct = (composite["covered"] / n_closed * 100.0) if composite and n_closed else 0.0

    # F-Score (champ "X/9" string, parser).
    f_score_buckets = _f_score_attribution(df)

    return {
        "ok":            True,
        "n_closed":      n_closed,
        "stable":        n_closed >= MIN_TOTAL_TRADES_STABLE,
        "min_for_stable": MIN_TOTAL_TRADES_STABLE,
        "coverage_pct":  coverage_pct,
        "pillars":       pillars_out,
        "f_score":       f_score_buckets,
        "tilts":         _tilt_flags_attribution(df),
    }


def _f_score_attribution(df: pd.DataFrame) -> dict[str, Any]:
    """F-Score Piotroski : champ "8/9" string. Bucketing 0-3 / 4-6 / 7-9."""
    buckets_def = [
        {"name": "0-3",  "min": 0, "max": 4, "tone": "var(--danger)"},
        {"name": "4-6",  "min": 4, "max": 7, "tone": "var(--warning)"},
        {"name": "7-9",  "min": 7, "max": 10, "tone": "var(--success)"},
    ]
    rows = []
    if "F_Score_Entry" not in df.columns:
        return {"label": "F-Score", "buckets": [], "n_total": 0}
    for _, r in df.iterrows():
        is_win = _is_winner(r)
        if is_win is None:
            continue
        raw = str(r.get("F_Score_Entry") or "").strip()
        if not raw or "/" not in raw:
            continue
        try:
            score = int(raw.split("/")[0])
        except (TypeError, ValueError):
            continue
        rows.append({
            "score":   score,
            "win":     bool(is_win),
            "pnl_pct": _trade_pnl_pct(r),
        })

    by_bucket: dict[str, list[dict[str, Any]]] = {b["name"]: [] for b in buckets_def}
    for row in rows:
        for b in buckets_def:
            if b["min"] <= row["score"] < b["max"]:
                by_bucket[b["name"]].append(row)
                break

    out = []
    for b in buckets_def:
        bucket_rows = by_bucket[b["name"]]
        n = len(bucket_rows)
        n_wins = sum(1 for r in bucket_rows if r["win"])
        pnls = [r["pnl_pct"] for r in bucket_rows if r["pnl_pct"] is not None]
        avg = (sum(pnls) / len(pnls)) if pnls else None
        out.append({
            "name": b["name"], "min": b["min"], "max": b["max"] - 1,
            "n": n, "n_wins": n_wins,
            "win_rate":    (n_wins / n * 100.0) if n else None,
            "pnl_avg_pct": avg,
            "stable":      n >= MIN_SAMPLE_STABLE,
        })
    return {"label": "F-Score", "buckets": out, "n_total": len(rows)}
