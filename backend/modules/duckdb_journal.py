"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE DUCKDB_JOURNAL — Miroir analytique du trade_journal      ║
║                                                                  ║
║  Objectif : doter SwingQuant d'une couche DuckDB pour requêtes   ║
║  SQL analytiques (PnL par secteur, drawdown, Sharpe, etc.) sans  ║
║  toucher au pipeline CSV qui reste source de vérité pour l'écrit.║
║                                                                  ║
║  Stratégie "dual-write additive" :                               ║
║    • CSV (trade_journal.csv) = source de vérité (alerter/tracker)║
║    • DuckDB (trade_journal.duckdb) = miroir append-only          ║
║    • `shadow_insert` / `shadow_update` appelés depuis les sites  ║
║      d'écriture — aucun impact si DuckDB échoue (fail-open).     ║
║    • `sync_from_csv()` permet un re-seed complet à tout moment.  ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from modules.utils import CSV_PATH, CSV_SCHEMA

if TYPE_CHECKING:
    import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DUCKDB_PATH   = _PROJECT_ROOT / "data" / "trade_journal.duckdb"

# Mapping colonnes CSV → types SQL. Colonnes numériques typées pour analytics
# (fwd P/E, Sharpe etc.). Les textes restent VARCHAR pour tolérer les "".
_COLUMN_TYPES: dict[str, str] = {
    "Date":           "VARCHAR",
    "Ticker":         "VARCHAR",
    "Direction":      "VARCHAR",
    "Entry":          "DOUBLE",
    "Stop_Loss":      "DOUBLE",
    "Initial_SL":     "DOUBLE",
    "Take_Profit":    "DOUBLE",
    "Size":           "INTEGER",
    "RR":             "DOUBLE",
    "Status":         "VARCHAR",
    "Exit_Price":     "DOUBLE",
    "Exit_Date":      "VARCHAR",
    "Last_Alert_Pct": "DOUBLE",
    "Last_TS_Update": "VARCHAR",
    "Last_TS_Mode":   "VARCHAR",
    "Order_ID":       "VARCHAR",
    "Signal":         "VARCHAR",
    "Sector":         "VARCHAR",
    # Lot 13 — execution quality tracking
    "Reco_Entry":     "DOUBLE",
    "Slippage_Bps":   "DOUBLE",
    # Lot 15 — capture scores TITAN à l'entrée (corrélation score/outcome).
    "Titan_Score_Entry": "DOUBLE",
    "Quality_Entry":     "DOUBLE",
    "Value_Entry":       "DOUBLE",
    "Risk_Entry":        "DOUBLE",
    "Momentum_Entry":    "DOUBLE",
    "Piotroski_Entry":   "DOUBLE",
    "Growth_Entry":      "DOUBLE",
    "F_Score_Entry":     "VARCHAR",  # "8/9" format
    "Tilt_Flags_Entry":  "VARCHAR",  # CSV ex "qarp,consistent"
    "Confidence_Entry":  "DOUBLE",   # 0-100 (data_confidence à l'entrée)
    # Phase 7 audit (2026-05-06) — granularité de la raison de clôture.
    "Close_Reason":      "VARCHAR",
    # Audit 2026-05-12 — décisions lt_exit_policy persistées pour mesurer
    # l'edge de la policy après coup (corrélation action↔outcome).
    "Last_LT_Action":    "VARCHAR",  # HOLD|ADD_ON|TRIM|EXIT_THESIS|...
    "Last_LT_Date":      "VARCHAR",  # YYYY-MM-DD du dernier check
    "Last_LT_Severity":  "INTEGER",  # 0..4 (filtre les rows actionnables)
}

# Sécurise l'accès multi-thread (FastAPI peut appeler depuis plusieurs workers).
_LOCK = threading.Lock()


# ─────────────────────────────────────────────────────────────────
# CONNECTION
# ─────────────────────────────────────────────────────────────────

@contextmanager
def get_conn(db_path: Path | str = DUCKDB_PATH, read_only: bool = False):
    """Context manager sur connexion DuckDB. Crée le parent dir si besoin."""
    path = Path(db_path)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path), read_only=read_only)
    try:
        yield conn
    finally:
        conn.close()


def ensure_schema(db_path: Path | str = DUCKDB_PATH) -> None:
    """Crée la table trade_journal si absente, avec schéma canonique."""
    cols_def = ", ".join(f'"{col}" {_COLUMN_TYPES[col]}' for col in CSV_SCHEMA)
    with get_conn(db_path) as conn:
        conn.execute(f'CREATE TABLE IF NOT EXISTS trade_journal ({cols_def})')
        # Index sur Ticker+Status pour les queries fréquentes
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ticker ON trade_journal("Ticker")')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON trade_journal("Status")')


# ─────────────────────────────────────────────────────────────────
# COERCIONS — CSV-safe (strings "") → SQL types
# ─────────────────────────────────────────────────────────────────

def _coerce(col: str, raw: Any) -> Any:
    """Convertit la valeur brute (CSV ou dict) vers son type SQL cible.
    Chaines vides / NaN / None → NULL.
    """
    if raw is None:
        return None
    if isinstance(raw, float):
        # NaN pandas → None
        try:
            import math
            if math.isnan(raw):
                return None
        except Exception:
            pass
    s = str(raw).strip() if not isinstance(raw, (int, float)) else raw
    if s == "" or s == "nan" or s == "NaN":
        return None
    sql_type = _COLUMN_TYPES.get(col, "VARCHAR")
    try:
        if sql_type == "DOUBLE":
            return float(s)
        if sql_type == "INTEGER":
            return int(float(s))
    except (TypeError, ValueError):
        return None
    return s


# ─────────────────────────────────────────────────────────────────
# DUAL-WRITE HELPERS (fail-open — une erreur DuckDB ne doit pas
# casser le pipeline de trading qui repose sur le CSV)
# ─────────────────────────────────────────────────────────────────

def shadow_insert(row: dict[str, Any], db_path: Path | str = DUCKDB_PATH) -> bool:
    """Insère un trade dans DuckDB. Retourne True si OK, False sinon (fail-open).

    Phase 7 audit (2026-05-06) — BEGIN/COMMIT explicites pour garantir
    l'atomicité même si un autre thread/process accède au fichier entre
    l'INSERT et le flush WAL.
    """
    try:
        ensure_schema(db_path)
        values = [_coerce(col, row.get(col)) for col in CSV_SCHEMA]
        placeholders = ", ".join(["?"] * len(CSV_SCHEMA))
        cols = ", ".join(f'"{c}"' for c in CSV_SCHEMA)
        with _LOCK, get_conn(db_path) as conn:
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute(
                    f'INSERT INTO trade_journal ({cols}) VALUES ({placeholders})',
                    values,
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return True
    except Exception:
        return False


def shadow_update_status(
    ticker: str,
    status: str,
    exit_price: float,
    exit_date: str,
    db_path: Path | str = DUCKDB_PATH,
) -> bool:
    """Met à jour la dernière position OPEN d'un ticker. Fail-open.

    Phase 7 audit — BEGIN/COMMIT explicites (cf. shadow_insert).
    """
    try:
        ensure_schema(db_path)
        with _LOCK, get_conn(db_path) as conn:
            conn.execute("BEGIN TRANSACTION")
            try:
                # Cible la ligne OPEN la plus récente (ROW_NUMBER sur Date desc)
                conn.execute(
                    '''
                    UPDATE trade_journal
                    SET "Status" = ?, "Exit_Price" = ?, "Exit_Date" = ?
                    WHERE rowid = (
                        SELECT rowid FROM trade_journal
                        WHERE "Ticker" = ? AND "Status" = 'OPEN'
                        ORDER BY "Date" DESC LIMIT 1
                    )
                    ''',
                    [status, float(exit_price), exit_date, ticker.upper()],
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────
# BULK SYNC — reseed complet depuis le CSV source de vérité
# ─────────────────────────────────────────────────────────────────

def sync_from_csv(
    csv_path: Path | str = CSV_PATH,
    db_path: Path | str = DUCKDB_PATH,
) -> dict[str, int]:
    """Reconstruit entièrement trade_journal.duckdb depuis la CSV source de vérité.

    Stratégie : écrire dans un fichier `.tmp` puis `rename()` atomique.
    L'ancien pattern `DELETE + executemany INSERT` laissait des pages mortes
    (VACUUM est no-op sur DuckDB) → bloat observé 20× (10 MB pour 48 lignes).
    Le tmp+rename garantit un fichier toujours compact.

    Utilise `read_csv_auto` (parser DuckDB natif, 10-50× plus rapide que
    pandas→executemany). Les colonnes sont castées vers les types canoniques
    via TRY_CAST(NULLIF(..., '')) — une valeur non-parseable devient NULL
    au lieu de lever, cohérent avec l'ancien `_coerce`.

    Retourne {rows_imported, columns_matched}.
    """
    csv    = Path(csv_path)
    target = Path(db_path)

    if not csv.exists() or csv.stat().st_size == 0:
        ensure_schema(target)
        return {"rows_imported": 0, "columns_matched": 0}

    # Écriture dans un .tmp à côté, puis rename atomique. Si un autre writer
    # a la DB ouverte au moment du rename, Linux garde son FD sur l'ancien
    # inode — pas de corruption, juste une vue figée jusqu'à sa prochaine open().
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()

    cols_def = ", ".join(f'"{col}" {_COLUMN_TYPES[col]}' for col in CSV_SCHEMA)

    with _LOCK:
        with get_conn(tmp) as conn:
            conn.execute(f'CREATE TABLE trade_journal ({cols_def})')
            # Introspection des colonnes CSV réellement présentes via DESCRIBE
            # — supporte les CSV historiques plus courts que le schéma canonique.
            desc = conn.execute(
                "DESCRIBE SELECT * FROM "
                "read_csv_auto(?, all_varchar=true, header=true) LIMIT 0",
                [str(csv)],
            ).fetchall()
            present = {row[0] for row in desc}
            matched = [c for c in CSV_SCHEMA if c in present]

            if matched:
                select_expr = ", ".join(
                    f'TRY_CAST(NULLIF("{col}", \'\') AS {_COLUMN_TYPES[col]}) AS "{col}"'
                    if col in present
                    else f'CAST(NULL AS {_COLUMN_TYPES[col]}) AS "{col}"'
                    for col in CSV_SCHEMA
                )
                conn.execute(
                    f"INSERT INTO trade_journal SELECT {select_expr} "
                    f"FROM read_csv_auto(?, all_varchar=true, header=true)",
                    [str(csv)],
                )
            conn.execute('CREATE INDEX IF NOT EXISTS idx_ticker ON trade_journal("Ticker")')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON trade_journal("Status")')
            count = conn.execute("SELECT COUNT(*) FROM trade_journal").fetchone()[0]

        # Rename atomique (os.replace est atomique sur POSIX et Windows).
        tmp.replace(target)

    return {"rows_imported": int(count or 0), "columns_matched": len(matched)}


# ─────────────────────────────────────────────────────────────────
# READ-ONLY ANALYTICS HELPER
# ─────────────────────────────────────────────────────────────────

def query(sql: str, db_path: Path | str = DUCKDB_PATH) -> list[dict]:
    """Exécute une requête SQL en lecture seule, retourne list[dict]."""
    ensure_schema(db_path)
    with get_conn(db_path, read_only=True) as conn:
        rel = conn.execute(sql)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row, strict=False)) for row in rel.fetchall()]


# ─────────────────────────────────────────────────────────────────
# DROP-IN DATAFRAME READER (DuckDB-backed, CSV-fallback)
# ─────────────────────────────────────────────────────────────────

# Mémorise la dernière mtime CSV observée pour éviter de resynchroniser
# DuckDB à chaque lecture quand la CSV n'a pas bougé.
_LAST_SYNC_MTIME: dict[str, float] = {}

# Cache en RAM du DataFrame lu — évite de rouvrir DuckDB + reconvertir object
# dtypes pour chaque appel /api/portfolio + /api/equity_curve +
# /api/performance_metrics dans un même dashboard load. Invalidé sur mtime CSV.
_DF_CACHE: dict[str, Any] = {"mtime": None, "csv_key": None, "df": None}
_DF_CACHE_LOCK = threading.Lock()


def journal_mtime(csv_path: Path | str = CSV_PATH) -> float | None:
    """Renvoie la mtime de la CSV source — utilisée comme ETag côté API.
    None si le fichier n'existe pas."""
    p = Path(csv_path)
    try:
        return p.stat().st_mtime if p.exists() else None
    except OSError:
        return None


def _ensure_fresh(
    csv_path: Path | str = CSV_PATH,
    db_path: Path | str = DUCKDB_PATH,
) -> None:
    """Synchronise DuckDB depuis la CSV si celle-ci est plus récente.

    Fail-open : toute exception est silencieuse — le caller fallbackera
    sur la CSV directe via read_journal_df().
    """
    csv = Path(csv_path)
    db  = Path(db_path)
    if not csv.exists():
        return
    csv_mtime = csv.stat().st_mtime
    key = str(db)
    last = _LAST_SYNC_MTIME.get(key, -1.0)
    if not db.exists() or csv_mtime > last + 0.5:
        try:
            sync_from_csv(csv, db)
            _LAST_SYNC_MTIME[key] = csv_mtime
        except Exception:
            pass


def read_journal_df(
    csv_path: Path | str = CSV_PATH,
    db_path: Path | str = DUCKDB_PATH,
) -> pd.DataFrame:
    """Retourne le trade_journal complet en DataFrame de strings.

    Drop-in replacement pour `pd.read_csv(CSV_PATH, dtype=str)`.
    Par défaut lit depuis DuckDB (resynchronisé paresseusement depuis la CSV
    si mtime diverge). Fallback direct CSV sur toute erreur DuckDB.

    Cache RAM mtime-keyed : un dashboard load qui tape /portfolio +
    /equity_curve + /performance_metrics ne déclenche qu'UNE lecture DuckDB.

    Env var `JOURNAL_READ_BACKEND=csv` force le path CSV legacy (escape).
    """
    import os

    import pandas as pd

    csv = Path(csv_path)
    force_csv = os.environ.get("JOURNAL_READ_BACKEND", "duckdb").lower() == "csv"

    def _read_csv_fallback() -> pd.DataFrame:
        if csv.exists() and csv.stat().st_size > 0:
            try:
                return pd.read_csv(csv, dtype=str).fillna("")
            except Exception:
                return pd.DataFrame(columns=CSV_SCHEMA)
        return pd.DataFrame(columns=CSV_SCHEMA)

    # Cache hit : même CSV, même mtime → on renvoie une copie défensive.
    # La copie n'est pas gratuite mais ~100 µs sur 30-50 lignes, vs ~5-10 ms
    # pour rouvrir DuckDB + convertir les dtypes object.
    cache_key = (str(csv), str(db_path), force_csv)
    csv_mtime = csv.stat().st_mtime if csv.exists() else None
    with _DF_CACHE_LOCK:
        if (
            _DF_CACHE["csv_key"] == cache_key
            and _DF_CACHE["mtime"] == csv_mtime
            and _DF_CACHE["df"] is not None
        ):
            return _DF_CACHE["df"].copy()

    if force_csv:
        df = _read_csv_fallback()
        with _DF_CACHE_LOCK:
            _DF_CACHE["csv_key"] = cache_key
            _DF_CACHE["mtime"] = csv_mtime
            _DF_CACHE["df"] = df
        return df.copy()

    try:
        _ensure_fresh(csv, db_path)
        with get_conn(db_path, read_only=True) as conn:
            df = conn.execute(
                'SELECT * FROM trade_journal ORDER BY "Date"'
            ).fetch_df()
        if df.empty:
            df = pd.DataFrame(columns=CSV_SCHEMA)
        else:
            # Convertit tout en string et normalise NaN/None → "" pour matcher
            # le comportement historique (dtype=str sur pd.read_csv).
            df = df.astype(object).where(pd.notnull(df), "")
            for col in df.columns:
                df[col] = df[col].astype(str).replace(
                    {"None": "", "nan": "", "NaT": ""}
                )
            # Garantit toutes les colonnes du schéma dans l'ordre canonique.
            for col in CSV_SCHEMA:
                if col not in df.columns:
                    df[col] = ""
            df = df[CSV_SCHEMA]
    except Exception:
        df = _read_csv_fallback()

    with _DF_CACHE_LOCK:
        _DF_CACHE["csv_key"] = cache_key
        _DF_CACHE["mtime"] = csv_mtime
        _DF_CACHE["df"] = df
    return df.copy()


def _clear_df_cache() -> None:
    """Usage tests — force un reload au prochain appel."""
    with _DF_CACHE_LOCK:
        _DF_CACHE["csv_key"] = None
        _DF_CACHE["mtime"] = None
        _DF_CACHE["df"] = None
