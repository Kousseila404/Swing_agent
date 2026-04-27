"""Tests pour modules.duckdb_journal (dual-write + sync)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from modules.duckdb_journal import (
    _LAST_SYNC_MTIME,
    ensure_schema,
    query,
    read_journal_df,
    shadow_insert,
    shadow_update_status,
    sync_from_csv,
)
from modules.utils import CSV_SCHEMA


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "journal.duckdb"


@pytest.fixture
def tmp_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "trade_journal.csv"
    pd.DataFrame(columns=CSV_SCHEMA).to_csv(csv, index=False)
    return csv


class TestEnsureSchema:
    def test_creates_table_if_absent(self, tmp_db: Path):
        ensure_schema(tmp_db)
        rows = query("SELECT * FROM trade_journal", tmp_db)
        assert rows == []

    def test_idempotent(self, tmp_db: Path):
        ensure_schema(tmp_db)
        ensure_schema(tmp_db)
        ensure_schema(tmp_db)
        rows = query("SELECT * FROM trade_journal", tmp_db)
        assert rows == []


class TestShadowInsert:
    def test_insert_single_trade(self, tmp_db: Path):
        row = {
            "Date": "2026-04-17 10:00:00", "Ticker": "NVDA", "Direction": "LONG",
            "Entry": "900.00", "Stop_Loss": "870.00", "Initial_SL": "870.00",
            "Take_Profit": "960.00", "Size": "10", "RR": "2.0", "Status": "OPEN",
            "Exit_Price": "", "Exit_Date": "", "Last_Alert_Pct": "",
            "Last_TS_Update": "", "Order_ID": "TEST_1", "Signal": "MOMENTUM",
            "Sector": "SOXX",
        }
        assert shadow_insert(row, tmp_db) is True
        rows = query("SELECT * FROM trade_journal", tmp_db)
        assert len(rows) == 1
        assert rows[0]["Ticker"] == "NVDA"
        assert rows[0]["Entry"] == 900.0
        assert rows[0]["Size"] == 10
        assert rows[0]["Exit_Price"] is None  # "" → NULL

    def test_insert_coerces_numeric_strings(self, tmp_db: Path):
        row = {c: "" for c in CSV_SCHEMA}
        row.update({
            "Ticker": "AAPL", "Entry": "150.5", "Size": "5",
            "Direction": "LONG", "Status": "OPEN", "Order_ID": "X",
            "Stop_Loss": "145", "Initial_SL": "145", "Take_Profit": "160",
            "Date": "2026-04-17", "RR": "2.0",
        })
        shadow_insert(row, tmp_db)
        r = query('SELECT "Entry", "Size" FROM trade_journal WHERE "Ticker" = \'AAPL\'', tmp_db)
        assert r[0]["Entry"] == 150.5
        assert r[0]["Size"] == 5

    def test_fail_open_on_bad_path(self):
        # Chemin non-writable → False, pas d'exception
        result = shadow_insert({"Ticker": "X"}, Path("/nonexistent/ro/fake.duckdb"))
        assert result is False


class TestShadowUpdate:
    def test_update_last_open(self, tmp_db: Path):
        base = {c: "" for c in CSV_SCHEMA}
        base.update({
            "Ticker": "NVDA", "Direction": "LONG", "Status": "OPEN",
            "Entry": "900", "Size": "10", "Order_ID": "A",
            "Stop_Loss": "870", "Initial_SL": "870", "Take_Profit": "960",
            "RR": "2.0",
        })
        shadow_insert({**base, "Date": "2026-04-15 09:00:00", "Order_ID": "OLD"}, tmp_db)
        shadow_insert({**base, "Date": "2026-04-17 09:00:00", "Order_ID": "NEW"}, tmp_db)

        assert shadow_update_status("NVDA", "WIN", 950.0, "2026-04-17 15:00", tmp_db) is True

        rows = query(
            'SELECT "Order_ID", "Status", "Exit_Price" FROM trade_journal ORDER BY "Date"',
            tmp_db,
        )
        # La plus récente doit être WIN, l'ancienne reste OPEN
        assert rows[0]["Status"] == "OPEN"
        assert rows[0]["Order_ID"] == "OLD"
        assert rows[1]["Status"] == "WIN"
        assert rows[1]["Exit_Price"] == 950.0

    def test_update_no_open_position(self, tmp_db: Path):
        ensure_schema(tmp_db)
        # Pas de position → no-op, mais pas d'erreur
        assert shadow_update_status("ZZZ", "WIN", 100.0, "2026-04-17", tmp_db) is True


class TestSyncFromCsv:
    def test_sync_empty_csv(self, tmp_csv: Path, tmp_db: Path):
        stats = sync_from_csv(tmp_csv, tmp_db)
        assert stats["rows_imported"] == 0

    def test_sync_populates_db(self, tmp_csv: Path, tmp_db: Path):
        df = pd.DataFrame([
            {
                "Date": "2026-04-01", "Ticker": "NVDA", "Direction": "LONG",
                "Entry": 900.0, "Stop_Loss": 870.0, "Initial_SL": 870.0,
                "Take_Profit": 960.0, "Size": 10, "RR": 2.0, "Status": "WIN",
                "Exit_Price": 950.0, "Exit_Date": "2026-04-05",
                "Last_Alert_Pct": "", "Last_TS_Update": "",
                "Order_ID": "OK1", "Signal": "MOMENTUM", "Sector": "SOXX",
                "Reco_Entry": "", "Slippage_Bps": "",
            },
            {
                "Date": "2026-04-10", "Ticker": "AAPL", "Direction": "LONG",
                "Entry": 180.0, "Stop_Loss": 175.0, "Initial_SL": 175.0,
                "Take_Profit": 190.0, "Size": 20, "RR": 2.0, "Status": "OPEN",
                "Exit_Price": "", "Exit_Date": "", "Last_Alert_Pct": "",
                "Last_TS_Update": "", "Order_ID": "OK2", "Signal": "MEAN_REV",
                "Sector": "XLK",
                "Reco_Entry": "", "Slippage_Bps": "",
            },
        ])
        df.to_csv(tmp_csv, index=False)

        stats = sync_from_csv(tmp_csv, tmp_db)
        assert stats["rows_imported"] == 2
        # columns_matched = cols présentes dans CSV ∩ CSV_SCHEMA. Le DataFrame
        # de test ne contient pas les nouvelles colonnes Titan_*_Entry (Lot 15)
        # pour rester concis — on vérifie juste qu'au moins les cols de base
        # du schéma legacy matchent (> 15).
        assert stats["columns_matched"] >= 15

        rows = query('SELECT "Ticker", "Status" FROM trade_journal ORDER BY "Ticker"', tmp_db)
        assert rows == [
            {"Ticker": "AAPL", "Status": "OPEN"},
            {"Ticker": "NVDA", "Status": "WIN"},
        ]

    def test_sync_wipes_previous_rows(self, tmp_csv: Path, tmp_db: Path):
        # Injecte un trade fantôme dans la DB
        shadow_insert({
            **{c: "" for c in CSV_SCHEMA},
            "Ticker": "GHOST", "Direction": "LONG", "Status": "OPEN",
            "Entry": "1", "Size": "1", "Date": "2000-01-01",
            "Stop_Loss": "0.9", "Initial_SL": "0.9", "Take_Profit": "1.1",
            "RR": "1.0", "Order_ID": "Z",
        }, tmp_db)
        # CSV vide → DB doit être vidée
        sync_from_csv(tmp_csv, tmp_db)
        rows = query("SELECT * FROM trade_journal", tmp_db)
        assert rows == []


class TestReadJournalDf:
    def test_empty_csv_returns_schema_df(self, tmp_csv: Path, tmp_db: Path):
        _LAST_SYNC_MTIME.clear()
        df = read_journal_df(tmp_csv, tmp_db)
        assert list(df.columns) == CSV_SCHEMA
        assert df.empty

    def test_returns_strings_matching_csv_shape(
        self, tmp_csv: Path, tmp_db: Path
    ):
        _LAST_SYNC_MTIME.clear()
        pd.DataFrame([
            {
                "Date": "2026-04-01", "Ticker": "NVDA", "Direction": "LONG",
                "Entry": 900.0, "Stop_Loss": 870.0, "Initial_SL": 870.0,
                "Take_Profit": 960.0, "Size": 10, "RR": 2.0, "Status": "WIN",
                "Exit_Price": 950.0, "Exit_Date": "2026-04-05",
                "Last_Alert_Pct": "", "Last_TS_Update": "",
                "Order_ID": "OK1", "Signal": "MOMENTUM", "Sector": "SOXX",
            },
        ]).to_csv(tmp_csv, index=False)

        df = read_journal_df(tmp_csv, tmp_db)
        assert len(df) == 1
        # Toutes les cellules doivent être des strings (drop-in dtype=str)
        for col in df.columns:
            for v in df[col]:
                assert isinstance(v, str), f"col={col} type={type(v)}"
        assert df.iloc[0]["Ticker"] == "NVDA"
        assert df.iloc[0]["Entry"] == "900.0"
        assert df.iloc[0]["Last_Alert_Pct"] == ""  # NaN/None → ""

    def test_csv_updates_trigger_resync(self, tmp_csv: Path, tmp_db: Path):
        _LAST_SYNC_MTIME.clear()
        # 1ère écriture + lecture
        pd.DataFrame([{
            **{c: "" for c in CSV_SCHEMA},
            "Date": "2026-04-01", "Ticker": "NVDA", "Status": "OPEN",
            "Direction": "LONG", "Entry": "900", "Size": "10",
            "Stop_Loss": "870", "Initial_SL": "870", "Take_Profit": "960",
            "RR": "2.0", "Order_ID": "A",
        }]).to_csv(tmp_csv, index=False)
        df1 = read_journal_df(tmp_csv, tmp_db)
        assert len(df1) == 1

        # Ajoute une ligne dans la CSV → read_journal_df doit la voir
        import time
        time.sleep(1.1)  # mtime resolution 1s sur certains FS
        pd.DataFrame([
            {
                **{c: "" for c in CSV_SCHEMA},
                "Date": "2026-04-01", "Ticker": "NVDA", "Status": "OPEN",
                "Direction": "LONG", "Entry": "900", "Size": "10",
                "Stop_Loss": "870", "Initial_SL": "870", "Take_Profit": "960",
                "RR": "2.0", "Order_ID": "A",
            },
            {
                **{c: "" for c in CSV_SCHEMA},
                "Date": "2026-04-15", "Ticker": "AAPL", "Status": "OPEN",
                "Direction": "LONG", "Entry": "180", "Size": "20",
                "Stop_Loss": "175", "Initial_SL": "175", "Take_Profit": "190",
                "RR": "2.0", "Order_ID": "B",
            },
        ]).to_csv(tmp_csv, index=False)

        df2 = read_journal_df(tmp_csv, tmp_db)
        assert len(df2) == 2
        assert set(df2["Ticker"]) == {"NVDA", "AAPL"}

    def test_backend_env_forces_csv_path(
        self, tmp_csv: Path, tmp_db: Path, monkeypatch
    ):
        _LAST_SYNC_MTIME.clear()
        # DB vide, CSV avec 1 ligne → backend=csv doit lire direct la CSV
        pd.DataFrame([{
            **{c: "" for c in CSV_SCHEMA},
            "Date": "2026-04-01", "Ticker": "CSVONLY", "Status": "OPEN",
            "Direction": "LONG", "Entry": "1", "Size": "1",
            "Stop_Loss": "0.9", "Initial_SL": "0.9", "Take_Profit": "1.1",
            "RR": "1.0", "Order_ID": "X",
        }]).to_csv(tmp_csv, index=False)
        monkeypatch.setenv("JOURNAL_READ_BACKEND", "csv")
        df = read_journal_df(tmp_csv, tmp_db)
        assert len(df) == 1
        assert df.iloc[0]["Ticker"] == "CSVONLY"
        # La DB ne doit pas avoir été touchée (path CSV direct)
        assert not tmp_db.exists()

    def test_fallback_csv_on_duckdb_error(
        self, tmp_csv: Path, tmp_path: Path
    ):
        _LAST_SYNC_MTIME.clear()
        pd.DataFrame([{
            **{c: "" for c in CSV_SCHEMA},
            "Date": "2026-04-01", "Ticker": "FALL", "Status": "OPEN",
            "Direction": "LONG", "Entry": "1", "Size": "1",
            "Stop_Loss": "0.9", "Initial_SL": "0.9", "Take_Profit": "1.1",
            "RR": "1.0", "Order_ID": "F",
        }]).to_csv(tmp_csv, index=False)
        # Chemin DB non-writable → fallback CSV
        bad_db = Path("/nonexistent/ro/fake.duckdb")
        df = read_journal_df(tmp_csv, bad_db)
        assert len(df) == 1
        assert df.iloc[0]["Ticker"] == "FALL"
