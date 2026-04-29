"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE WATCHLIST — Couche idées + notes par ticker              ║
║                                                                  ║
║  Storage : DuckDB (data/watchlist.duckdb), table par concept :   ║
║    - watchlist : tickers observés (sans engagement capital)      ║
║    - ticker_notes : notes markdown libres par ticker (multi)     ║
║                                                                  ║
║  Persistance simple, fail-open : une corruption ne casse pas     ║
║  l'API (l'endpoint renvoie 503 mais le reste continue).          ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_DB  = _PROJECT_ROOT / "data" / "watchlist.duckdb"

_LOCK = threading.Lock()


@contextmanager
def _conn(db_path: Path | str = WATCHLIST_DB, read_only: bool = False):
    path = Path(db_path)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect(str(path), read_only=read_only)
    try:
        yield c
    finally:
        c.close()


def ensure_schema(db_path: Path | str = WATCHLIST_DB) -> None:
    """Crée les 2 tables si absentes."""
    with _conn(db_path) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                ticker     VARCHAR PRIMARY KEY,
                added_at   VARCHAR NOT NULL,
                tag        VARCHAR,
                target_buy DOUBLE,
                comment    VARCHAR
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS ticker_notes (
                id         VARCHAR PRIMARY KEY,
                ticker     VARCHAR NOT NULL,
                created_at VARCHAR NOT NULL,
                updated_at VARCHAR NOT NULL,
                body       VARCHAR NOT NULL
            )
        """)
        c.execute('CREATE INDEX IF NOT EXISTS idx_notes_ticker ON ticker_notes("ticker")')


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─────────────────────────────────────────────────────────────────
# WATCHLIST
# ─────────────────────────────────────────────────────────────────

def list_watchlist(db_path: Path | str = WATCHLIST_DB) -> list[dict[str, Any]]:
    """Retourne tous les items watchlist triés par added_at desc."""
    with _LOCK, _conn(db_path, read_only=False) as c:
        ensure_schema(db_path)
        rows = c.execute("""
            SELECT ticker, added_at, tag, target_buy, comment
            FROM watchlist ORDER BY added_at DESC
        """).fetchall()
    return [
        {
            "ticker":     r[0],
            "added_at":   r[1],
            "tag":        r[2],
            "target_buy": r[3],
            "comment":    r[4],
        }
        for r in rows
    ]


def add_to_watchlist(
    ticker: str,
    tag: str | None = None,
    target_buy: float | None = None,
    comment: str | None = None,
    db_path: Path | str = WATCHLIST_DB,
) -> dict[str, Any]:
    """Ajoute (ou upsert) un ticker dans la watchlist."""
    t = (ticker or "").upper().strip()
    if not t:
        raise ValueError("Ticker vide")
    now = _now_iso()
    with _LOCK, _conn(db_path) as c:
        ensure_schema(db_path)
        # DuckDB INSERT OR REPLACE est dispo (ON CONFLICT REPLACE équivalent).
        c.execute("""
            INSERT INTO watchlist (ticker, added_at, tag, target_buy, comment)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (ticker) DO UPDATE SET
                tag        = EXCLUDED.tag,
                target_buy = EXCLUDED.target_buy,
                comment    = EXCLUDED.comment
        """, [t, now, tag, target_buy, comment])
    return {
        "ticker": t, "added_at": now,
        "tag": tag, "target_buy": target_buy, "comment": comment,
    }


def remove_from_watchlist(ticker: str, db_path: Path | str = WATCHLIST_DB) -> bool:
    """Retire un ticker. True si une ligne supprimée."""
    t = (ticker or "").upper().strip()
    if not t:
        return False
    with _LOCK, _conn(db_path) as c:
        ensure_schema(db_path)
        before = c.execute("SELECT COUNT(*) FROM watchlist WHERE ticker = ?", [t]).fetchone()[0]
        c.execute("DELETE FROM watchlist WHERE ticker = ?", [t])
    return before > 0


# ─────────────────────────────────────────────────────────────────
# NOTES (multi par ticker)
# ─────────────────────────────────────────────────────────────────

def list_notes(ticker: str, db_path: Path | str = WATCHLIST_DB) -> list[dict[str, Any]]:
    """Retourne toutes les notes d'un ticker, plus récentes d'abord."""
    t = (ticker or "").upper().strip()
    if not t:
        return []
    with _LOCK, _conn(db_path, read_only=False) as c:
        ensure_schema(db_path)
        rows = c.execute("""
            SELECT id, ticker, created_at, updated_at, body
            FROM ticker_notes WHERE ticker = ? ORDER BY updated_at DESC
        """, [t]).fetchall()
    return [
        {
            "id":         r[0],
            "ticker":     r[1],
            "created_at": r[2],
            "updated_at": r[3],
            "body":       r[4],
        }
        for r in rows
    ]


def add_note(ticker: str, body: str, db_path: Path | str = WATCHLIST_DB) -> dict[str, Any]:
    """Crée une note. Body non-vide requis."""
    t = (ticker or "").upper().strip()
    b = (body or "").strip()
    if not t:
        raise ValueError("Ticker vide")
    if not b:
        raise ValueError("Note vide")
    nid = uuid.uuid4().hex[:12]
    now = _now_iso()
    with _LOCK, _conn(db_path) as c:
        ensure_schema(db_path)
        c.execute("""
            INSERT INTO ticker_notes (id, ticker, created_at, updated_at, body)
            VALUES (?, ?, ?, ?, ?)
        """, [nid, t, now, now, b])
    return {
        "id": nid, "ticker": t,
        "created_at": now, "updated_at": now, "body": b,
    }


def update_note(note_id: str, body: str, db_path: Path | str = WATCHLIST_DB) -> dict[str, Any] | None:
    """Met à jour le body d'une note. Retourne None si introuvable."""
    nid = (note_id or "").strip()
    b = (body or "").strip()
    if not nid:
        raise ValueError("ID note vide")
    if not b:
        raise ValueError("Note vide")
    now = _now_iso()
    with _LOCK, _conn(db_path) as c:
        ensure_schema(db_path)
        existing = c.execute("SELECT ticker FROM ticker_notes WHERE id = ?", [nid]).fetchone()
        if existing is None:
            return None
        c.execute("UPDATE ticker_notes SET body = ?, updated_at = ? WHERE id = ?",
                  [b, now, nid])
        row = c.execute("""
            SELECT id, ticker, created_at, updated_at, body
            FROM ticker_notes WHERE id = ?
        """, [nid]).fetchone()
    return {
        "id":         row[0],
        "ticker":     row[1],
        "created_at": row[2],
        "updated_at": row[3],
        "body":       row[4],
    }


def delete_note(note_id: str, db_path: Path | str = WATCHLIST_DB) -> bool:
    """Supprime une note. True si supprimée."""
    nid = (note_id or "").strip()
    if not nid:
        return False
    with _LOCK, _conn(db_path) as c:
        ensure_schema(db_path)
        before = c.execute("SELECT COUNT(*) FROM ticker_notes WHERE id = ?", [nid]).fetchone()[0]
        c.execute("DELETE FROM ticker_notes WHERE id = ?", [nid])
    return before > 0


def count_notes_by_ticker(db_path: Path | str = WATCHLIST_DB) -> dict[str, int]:
    """Renvoie {ticker: n_notes} pour badging UI."""
    with _LOCK, _conn(db_path, read_only=False) as c:
        ensure_schema(db_path)
        rows = c.execute("""
            SELECT ticker, COUNT(*) FROM ticker_notes GROUP BY ticker
        """).fetchall()
    return {r[0]: int(r[1]) for r in rows}
