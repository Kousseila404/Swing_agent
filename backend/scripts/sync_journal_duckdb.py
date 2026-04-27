"""
Synchronise data/trade_journal.csv → data/trade_journal.duckdb.

Usage :
    python scripts/sync_journal_duckdb.py

Idempotent : vide puis réimporte toute la table. Sûr à lancer en cron.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from modules.duckdb_journal import DUCKDB_PATH, sync_from_csv  # noqa: E402


def main() -> None:
    stats = sync_from_csv()
    print(
        f"[duckdb_sync] {stats['rows_imported']} lignes importées "
        f"({stats['columns_matched']}/{17} colonnes matchées) → {DUCKDB_PATH}"
    )


if __name__ == "__main__":
    main()
