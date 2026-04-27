"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE MARKET_DB — Cache OHLCV en DuckDB (V2)                  ║
║  Remplace les 503 fichiers CSV par une base embarquée unique.   ║
║                                                                  ║
║  API publique :                                                  ║
║    init_db()             — crée la DB et le schéma              ║
║    upsert_ohlcv()        — insère/met à jour les barres OHLCV   ║
║    read_ohlcv()          — lit les données d'un ticker          ║
║    get_cache_age_days()  — âge en jours de la dernière barre    ║
║    migrate_csv_to_duckdb() — migration one-shot des CSV V1      ║
╚══════════════════════════════════════════════════════════════════╝

Avantages vs 503 CSV :
  - Requêtes SQL instantanées avec index temporel
  - Upsert atomique — aucun fichier corrompu possible
  - Thread-safe : DuckDB gère la concurrence en lecture
  - Fichier unique → sauvegarde triviale (cp data/market.duckdb)
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from modules.log import logger

# ─────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH       = _PROJECT_ROOT / "data" / "market.duckdb"

# Verrou réentrant — permet à upsert_ohlcv d'appeler init_db sans deadlock
_write_lock = threading.RLock()


# ─────────────────────────────────────────────────────────────────
# INITIALISATION
# ─────────────────────────────────────────────────────────────────

def init_db(db_path: Path = DB_PATH) -> None:
    """
    Crée la base DuckDB et la table `ohlcv` si elles n'existent pas.

    Schéma :
        ohlcv(ticker TEXT, date TIMESTAMP WITH TIME ZONE,
              open DOUBLE, high DOUBLE, low DOUBLE,
              close DOUBLE, volume BIGINT,
              PRIMARY KEY (ticker, date))

    Idempotent : peut être appelée plusieurs fois sans effet de bord.

    Args:
        db_path: Chemin vers le fichier .duckdb (défaut : data/market.duckdb).
    """
    import duckdb

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        con = duckdb.connect(str(db_path))
        try:
            con.execute("""
                CREATE TABLE IF NOT EXISTS ohlcv (
                    ticker  TEXT      NOT NULL,
                    date    TIMESTAMP NOT NULL,
                    open    DOUBLE,
                    high    DOUBLE,
                    low     DOUBLE,
                    close   DOUBLE    NOT NULL,
                    volume  BIGINT,
                    PRIMARY KEY (ticker, date)
                )
            """)
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_date "
                "ON ohlcv (ticker, date)"
            )
            logger.debug(f"[MarketDB] Base initialisée → {db_path}")
        finally:
            con.close()


# ─────────────────────────────────────────────────────────────────
# ÉCRITURE — UPSERT ATOMIQUE
# ─────────────────────────────────────────────────────────────────

def upsert_ohlcv(ticker: str, df: pd.DataFrame, db_path: Path = DB_PATH) -> int:
    """
    Insère ou met à jour les barres OHLCV pour un ticker dans DuckDB.

    Utilise INSERT OR REPLACE (idempotent — peut être rappelé sans doublon).
    Thread-safe via _write_lock (DuckDB ne supporte qu'un writer simultané).

    Args:
        ticker:  Symbole boursier (ex : "NVDA", "^GSPC").
        df:      DataFrame avec DatetimeIndex et colonnes Open/High/Low/Close/Volume.
                 Les colonnes sont insensibles à la casse.
        db_path: Chemin vers la base DuckDB.

    Returns:
        Nombre de lignes insérées/mises à jour, ou 0 en cas d'erreur.
    """
    import duckdb

    if df is None or df.empty:
        logger.debug(f"[MarketDB] upsert_ohlcv({ticker}) — DataFrame vide, ignoré.")
        return 0

    # ── Normalisation du DataFrame ────────────────────────────────
    df = df.copy()

    # Normalise les noms de colonnes en minuscules
    df.columns = [c.lower() for c in df.columns]

    # Assure un index DatetimeIndex avec timezone UTC
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index, utc=True)
        except Exception as exc:
            logger.warning(f"[MarketDB] {ticker} — impossible de parser l'index : {exc}")
            return 0
    elif df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    # Colonnes requises
    required = {"close"}
    present  = set(df.columns)

    if not required.issubset(present):
        logger.warning(f"[MarketDB] {ticker} — colonne 'close' manquante.")
        return 0

    # Construit un DataFrame normalisé avec toutes les colonnes
    normalized = pd.DataFrame({
        "ticker": ticker,
        "date": df.index,
        "open": df["open"] if "open" in present else float("nan"),
        "high": df["high"] if "high" in present else float("nan"),
        "low": df["low"] if "low" in present else float("nan"),
        "close": df["close"],
        "volume": df["volume"].astype("Int64") if "volume" in present else 0,
    })
    normalized = normalized.dropna(subset=["close"])

    if normalized.empty:
        logger.debug(f"[MarketDB] {ticker} — aucune barre valide après nettoyage.")
        return 0

    try:
        with _write_lock:
            con = duckdb.connect(str(db_path))
            try:
                init_db(db_path)   # Idempotent — garantit le schéma
                # Enregistre le DataFrame comme table temporaire pour l'upsert
                con.register("_staging", normalized)
                con.execute("""
                    INSERT OR REPLACE INTO ohlcv
                        (ticker, date, open, high, low, close, volume)
                    SELECT ticker, date, open, high, low, close, volume
                    FROM _staging
                """)
                rows = len(normalized)
                logger.debug(
                    f"[MarketDB] upsert_ohlcv({ticker}) — {rows} barre(s) sauvegardées."
                )
                return rows
            finally:
                con.close()

    except Exception as exc:
        logger.error(f"[MarketDB] Erreur upsert {ticker} : {exc}")
        return 0


# ─────────────────────────────────────────────────────────────────
# LECTURE
# ─────────────────────────────────────────────────────────────────

def read_ohlcv(
    ticker: str,
    days: int = 730,
    db_path: Path = DB_PATH,
) -> pd.DataFrame | None:
    """
    Lit les données OHLCV d'un ticker depuis DuckDB.

    Retourne les `days` derniers jours de données triés par date croissante,
    avec un DatetimeIndex UTC.

    Args:
        ticker:  Symbole boursier (ex : "NVDA").
        days:    Nombre de jours d'historique à retourner (défaut : 730 = 2 ans).
        db_path: Chemin vers la base DuckDB.

    Returns:
        DataFrame avec DatetimeIndex et colonnes Open/High/Low/Close/Volume,
        ou None si le ticker est absent ou les données insuffisantes.
    """
    import duckdb

    if not db_path.exists():
        return None

    try:
        from datetime import timedelta
        cutoff = datetime.now(tz=UTC) - timedelta(days=days)

        con = duckdb.connect(str(db_path), read_only=True)
        try:
            result = con.execute("""
                SELECT date, open, high, low, close, volume
                FROM   ohlcv
                WHERE  ticker = ?
                  AND  date >= ?
                ORDER  BY date ASC
            """, [ticker, cutoff]).fetchdf()
        finally:
            con.close()

        if result.empty or len(result) < 10:
            return None

        result.index = pd.DatetimeIndex(
            pd.to_datetime(result["date"], utc=True)
        )
        result = result.drop(columns=["date"])
        result.columns = ["Open", "High", "Low", "Close", "Volume"]
        result.index.name = "Date"

        return result

    except Exception as exc:
        logger.debug(f"[MarketDB] read_ohlcv({ticker}) — erreur : {exc}")
        return None


# ─────────────────────────────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────────────────────────────

def get_cache_age_days(ticker: str, db_path: Path = DB_PATH) -> float | None:
    """
    Retourne l'âge en jours de la dernière barre OHLCV pour un ticker.

    Utilisé pour reproduire la logique TTL (CACHE_TTL_DAYS = 6) de l'ancien
    système CSV dans update_market_cache().

    Returns:
        Age en jours (float), ou None si le ticker est absent.
    """
    import duckdb

    if not db_path.exists():
        return None

    try:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            row = con.execute("""
                SELECT MAX(date) FROM ohlcv WHERE ticker = ?
            """, [ticker]).fetchone()
        finally:
            con.close()

        if row is None or row[0] is None:
            return None

        last_date = pd.to_datetime(row[0], utc=True)
        now       = datetime.now(tz=UTC)
        return (now - last_date).total_seconds() / 86400.0

    except Exception as exc:
        logger.debug(f"[MarketDB] get_cache_age_days({ticker}) : {exc}")
        return None


def list_tickers(db_path: Path = DB_PATH) -> list[str]:
    """Retourne la liste de tous les tickers présents dans la base."""
    import duckdb

    if not db_path.exists():
        return []

    try:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            rows = con.execute(
                "SELECT DISTINCT ticker FROM ohlcv ORDER BY ticker"
            ).fetchall()
        finally:
            con.close()
        return [r[0] for r in rows]
    except Exception as exc:
        logger.debug(f"[MarketDB] list_tickers() : {exc}")
        return []


# ─────────────────────────────────────────────────────────────────
# MIGRATION ONE-SHOT : CSV → DuckDB
# ─────────────────────────────────────────────────────────────────

def migrate_csv_to_duckdb(
    csv_dir: Path | None = None,
    db_path: Path = DB_PATH,
) -> dict[str, int]:
    """
    Migration one-shot : importe tous les fichiers CSV de data/market_cache/
    vers DuckDB.

    Cette fonction est idempotente : si un ticker est déjà dans la base,
    les données existantes sont écrasées par les données du CSV (INSERT OR REPLACE).

    À lancer une seule fois après déploiement de la V2 :
        python -c "from modules.market_db import migrate_csv_to_duckdb; migrate_csv_to_duckdb()"

    Args:
        csv_dir:  Répertoire source (défaut : data/market_cache/).
        db_path:  Chemin vers la base DuckDB cible.

    Returns:
        Dict {ticker: nb_barres} pour les migrations réussies.
    """
    if csv_dir is None:
        csv_dir = _PROJECT_ROOT / "data" / "market_cache"

    init_db(db_path)
    csv_files = sorted(csv_dir.glob("*.csv"))

    if not csv_files:
        logger.warning(f"[MarketDB] Aucun fichier CSV dans {csv_dir}")
        return {}

    logger.info(
        f"[MarketDB] Migration CSV → DuckDB : {len(csv_files)} fichiers "
        f"depuis {csv_dir}"
    )

    summary: dict[str, int] = {}
    errors  = 0

    for i, fp in enumerate(csv_files, 1):
        # Reconstitue le ticker depuis le nom de fichier (inverse de _cache_path)
        ticker = fp.stem.replace("_", "/")
        if ticker == "GSPC":
            ticker = "^GSPC"

        try:
            df = pd.read_csv(fp, index_col=0, parse_dates=True)
            if df.empty or len(df) < 10:
                errors += 1
                continue

            rows = upsert_ohlcv(ticker, df, db_path)
            if rows > 0:
                summary[ticker] = rows
            else:
                errors += 1

        except Exception as exc:
            logger.debug(f"[MarketDB] Migration {fp.name} échouée : {exc}")
            errors += 1

        if i % 50 == 0 or i == len(csv_files):
            logger.info(
                f"[MarketDB] Migration : {i}/{len(csv_files)} fichiers traités "
                f"({len(summary)} OK, {errors} erreurs)"
            )

    logger.info(
        f"[MarketDB] Migration terminée — "
        f"{len(summary)}/{len(csv_files)} tickers migrés avec succès."
    )
    return summary


# ─────────────────────────────────────────────────────────────────
# CLASSE WRAPPER — compatibilité avec les imports MarketDB()
# ─────────────────────────────────────────────────────────────────

class MarketDB:
    """Wrapper orienté-objet autour des fonctions standalone de market_db."""

    def __init__(self, db_path: Path = DB_PATH, read_only: bool = False) -> None:
        self.db_path = db_path
        if not read_only:
            try:
                init_db(db_path)
            except Exception:
                pass

    def get_ohlcv(self, ticker: str) -> pd.DataFrame | None:
        return read_ohlcv(ticker, db_path=self.db_path)

    def upsert_ohlcv(self, ticker: str, df: pd.DataFrame) -> int:
        return upsert_ohlcv(ticker, df, db_path=self.db_path)

    def get_cache_age_days(self, ticker: str) -> float | None:
        return get_cache_age_days(ticker, db_path=self.db_path)

    def list_tickers(self) -> list[str]:
        return list_tickers(db_path=self.db_path)
