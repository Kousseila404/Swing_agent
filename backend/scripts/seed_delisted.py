"""Seed `data/delisted.json` à partir de l'historique disponible.

Audit S1.1 (2026-04-27) — bootstrap initial du registry delisted.

Sources scannées (de la plus ancienne à la plus récente) :
  1. Snapshots `data/.universe_history/snapshot_*.json.gz` (Lot 6).
  2. Backups `data/.universe_backups_quantamental/universe_*.json`
     (rotation 10 conservée par universe_engine).
  3. Universe courant `data/universe.json` (snapshot final).

Pour chaque source, on extrait la composition (set de tickers) et la date.
On parcourt en ordre chronologique et on appelle `delisted.record_diff(prev,
new)` à chaque transition — ce qui est exactement ce que ferait
`save_universe` s'il avait existé au moment du build.

Idempotent : ré-exécuter ne dégrade pas le registry (on relit l'état avant
chaque diff). Si le registry contient déjà des entrées plus récentes que la
source scannée, elles sont préservées.

Usage :
    python -m scripts.seed_delisted              # seed à partir de tout
    python -m scripts.seed_delisted --dry-run    # affiche les diffs sans écrire
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

# Permet `python -m scripts.seed_delisted` même hors de venv (path injection).
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from modules import delisted  # noqa: E402

_DATA_DIR = _BACKEND_ROOT / "data"
_HISTORY_DIR = _DATA_DIR / ".universe_history"
_BACKUPS_DIR = _DATA_DIR / ".universe_backups_quantamental"
_UNIVERSE_PATH = _DATA_DIR / "universe.json"


def _read_snapshot(p: Path) -> dict[str, Any] | None:
    """Lit un fichier snapshot (.json ou .json.gz). Retourne le payload ou None."""
    try:
        if p.suffix == ".gz":
            raw = gzip.decompress(p.read_bytes())
            return json.loads(raw)
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, EOFError) as e:
        print(f"  ⚠️  skip {p.name} : {e}")
        return None


def _extract_date(p: Path, payload: dict[str, Any]) -> date | None:
    """Détermine la date d'effet du payload — préfère `as_of_date` ou
    `snapshot_date` si présent ; fallback sur le nom du fichier.
    """
    for k in ("as_of_date", "snapshot_date"):
        v = payload.get(k)
        if isinstance(v, str):
            try:
                return date.fromisoformat(v[:10])
            except ValueError:
                pass
    # Fallback : parser le nom (universe_YYYYMMDD_HHMMSS.json ou snapshot_YYYYMMDD.json.gz)
    stem = p.stem.replace(".json", "")
    parts = stem.split("_")
    for token in parts[1:]:
        if len(token) == 8 and token.isdigit():
            try:
                return datetime.strptime(token, "%Y%m%d").date()
            except ValueError:
                pass
    return None


def _gather_sources() -> list[tuple[date, Path, dict[str, Any]]]:
    """Construit la liste chronologique (date, path, tickers_dict) à partir
    des trois sources. Dédup par date : on garde la version la plus
    informative (le plus de tickers) en cas de plusieurs builds le même jour.
    """
    candidates: list[tuple[date, Path, dict[str, Any]]] = []

    if _HISTORY_DIR.exists():
        for p in sorted(_HISTORY_DIR.glob("snapshot_*.json.gz")):
            payload = _read_snapshot(p)
            if not payload:
                continue
            d = _extract_date(p, payload)
            tickers = payload.get("tickers") or {}
            if d and isinstance(tickers, dict) and tickers:
                candidates.append((d, p, tickers))

    if _BACKUPS_DIR.exists():
        for p in sorted(_BACKUPS_DIR.glob("universe_*.json")):
            payload = _read_snapshot(p)
            if not payload:
                continue
            d = _extract_date(p, payload)
            tickers = payload.get("tickers") or {}
            if d and isinstance(tickers, dict) and tickers:
                candidates.append((d, p, tickers))

    if _UNIVERSE_PATH.exists():
        payload = _read_snapshot(_UNIVERSE_PATH)
        if payload:
            d = _extract_date(_UNIVERSE_PATH, payload)
            tickers = payload.get("tickers") or {}
            if d and isinstance(tickers, dict) and tickers:
                candidates.append((d, _UNIVERSE_PATH, tickers))

    # Dédup par date — garde l'entry avec le plus gros |tickers|.
    by_date: dict[date, tuple[Path, dict[str, Any]]] = {}
    for d, p, tk in candidates:
        cur = by_date.get(d)
        if cur is None or len(tk) > len(cur[1]):
            by_date[d] = (p, tk)

    return [(d, p, tk) for d, (p, tk) in sorted(by_date.items())]


def main() -> int:
    parser = argparse.ArgumentParser(prog="seed_delisted",
        description="Bootstrap data/delisted.json depuis l'historique local.")
    parser.add_argument("--dry-run", action="store_true",
                        help="N'écrit pas, affiche juste les diffs détectés.")
    args = parser.parse_args()

    sources = _gather_sources()
    if not sources:
        print("Aucune source détectée — rien à seed.")
        return 1

    print(f"Sources chronologiques détectées : {len(sources)}")
    for d, p, tk in sources:
        # `relative_to` lève si p n'est pas sous _BACKEND_ROOT (cas tests
        # avec tmp_path) — fallback sur le path absolu pour rester robuste.
        try:
            label = p.relative_to(_BACKEND_ROOT)
        except ValueError:
            label = p
        print(f"  • {d.isoformat()}  {label}  ({len(tk)} tickers)")
    print()

    prev_tickers: dict[str, Any] | None = None
    total_added = 0
    total_removed = 0
    for d, _p, new_tickers in sources:
        if args.dry_run:
            prev_set = set((prev_tickers or {}).keys())
            new_set = set(new_tickers.keys())
            added = sorted(new_set - prev_set)
            removed = sorted(prev_set - new_set)
            if added or removed:
                print(f"@ {d.isoformat()}  +{len(added)} added  -{len(removed)} removed")
                if removed[:5]:
                    print(f"    removed sample : {removed[:5]}")
            total_added += len(added)
            total_removed += len(removed)
        else:
            # alert=False : le seed historique reproduit potentiellement
            # plusieurs gros diffs ; on ne spamme pas Telegram en bootstrap.
            summary = delisted.record_diff(
                prev_tickers, new_tickers, today=d.isoformat(), alert=False,
            )
            if summary["added"] or summary["removed"]:
                print(f"@ {d.isoformat()}  +{len(summary['added'])} added  "
                      f"-{len(summary['removed'])} removed")
                if summary["removed"][:5]:
                    print(f"    removed sample : {summary['removed'][:5]}")
            total_added += len(summary["added"])
            total_removed += len(summary["removed"])
        prev_tickers = new_tickers

    print()
    print(f"Total : +{total_added} arrivals  -{total_removed} delistings")
    if args.dry_run:
        print("(dry-run — rien n'a été écrit)")
    else:
        try:
            label = delisted.DELISTED_PATH.relative_to(_BACKEND_ROOT)
        except ValueError:
            label = delisted.DELISTED_PATH
        print(f"Registry persisté : {label}")
        active_today = delisted.list_delisted()
        print(f"Tickers actuellement marqués delisted : {len(active_today)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
