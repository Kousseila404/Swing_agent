"""Tests modules.utils — atr_slippage, ensure_csv_schema, get_open_tickers."""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from modules.utils import (
    CSV_SCHEMA,
    atr_slippage,
    ensure_csv_schema,
    get_open_tickers,
    get_recently_lost_tickers,
)


class TestAtrSlippage:
    def test_high_volatility_uses_atr(self):
        # NVDA-like: ATR=3% du prix → slip = 0.03 × 0.04 = 0.12%
        slip = atr_slippage(atr_value=30.0, price=1000.0, base_pct=0.0005)
        assert slip == pytest.approx(0.0012, rel=1e-3)

    def test_low_volatility_floors_to_base(self):
        # JNJ-like: ATR très bas → floor au base_pct
        slip = atr_slippage(atr_value=0.1, price=150.0, base_pct=0.0005)
        assert slip == 0.0005

    def test_zero_price_returns_base(self):
        assert atr_slippage(5.0, 0.0, base_pct=0.0005) == 0.0005

    def test_nan_atr_returns_base(self):
        assert atr_slippage(math.nan, 100.0, base_pct=0.0005) == 0.0005

    def test_negative_atr_returns_base(self):
        assert atr_slippage(-5.0, 100.0, base_pct=0.0005) == 0.0005

    def test_uses_config_default_when_base_none(self):
        # Passe base_pct=None → fallback config.SLIPPAGE_PCT (0.0005)
        slip = atr_slippage(atr_value=0.0, price=100.0)
        assert slip > 0


class TestEnsureCsvSchema:
    def test_creates_file_when_absent(self, tmp_csv):
        assert not tmp_csv.exists()
        ensure_csv_schema(tmp_csv)
        assert tmp_csv.exists()
        df = pd.read_csv(tmp_csv)
        assert list(df.columns) == CSV_SCHEMA

    def test_initializes_empty_file(self, tmp_csv):
        tmp_csv.write_text("")
        ensure_csv_schema(tmp_csv)
        df = pd.read_csv(tmp_csv)
        assert list(df.columns) == CSV_SCHEMA

    def test_idempotent_on_complete_schema(self, tmp_csv):
        ensure_csv_schema(tmp_csv)
        first_content = tmp_csv.read_text()
        ensure_csv_schema(tmp_csv)
        assert tmp_csv.read_text() == first_content

    def test_migrates_missing_columns(self, tmp_csv):
        # Ancien CSV partiel
        legacy = pd.DataFrame({"Ticker": ["AAPL"], "Status": ["OPEN"]})
        legacy.to_csv(tmp_csv, index=False)
        ensure_csv_schema(tmp_csv)
        df = pd.read_csv(tmp_csv)
        for col in CSV_SCHEMA:
            assert col in df.columns
        assert df.loc[0, "Ticker"] == "AAPL"
        assert df.loc[0, "Status"] == "OPEN"

    def test_preserves_extra_columns(self, tmp_csv):
        df_in = pd.DataFrame({col: [""] for col in CSV_SCHEMA})
        df_in["Ticker"] = "NVDA"
        df_in["CustomField"] = "extra_value"
        df_in.to_csv(tmp_csv, index=False)
        ensure_csv_schema(tmp_csv)
        df = pd.read_csv(tmp_csv)
        assert "CustomField" in df.columns
        assert df.loc[0, "CustomField"] == "extra_value"

    def test_recreates_corrupted_with_backup(self, tmp_csv):
        # Simule un fichier binaire illisible par pd.read_csv
        tmp_csv.write_bytes(b"\x00\x01\x02\xff garbage not csv at all")
        ensure_csv_schema(tmp_csv)
        # Un backup horodaté doit exister
        backups = list(tmp_csv.parent.glob("*.bak_*.csv"))
        assert len(backups) >= 1
        df = pd.read_csv(tmp_csv)
        assert list(df.columns) == CSV_SCHEMA


class TestGetOpenTickers:
    def test_empty_when_csv_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("modules.utils.CSV_PATH", tmp_path / "nope.csv")
        assert get_open_tickers() == set()

    def test_returns_open_tickers(self, tmp_path, monkeypatch):
        csv = tmp_path / "j.csv"
        pd.DataFrame({
            "Ticker": ["AAPL", "NVDA", "MSFT"],
            "Status": ["OPEN", "WIN", "OPEN"],
        }).to_csv(csv, index=False)
        monkeypatch.setattr("modules.utils.CSV_PATH", csv)
        assert get_open_tickers() == {"AAPL", "MSFT"}

    def test_case_insensitive_upper(self, tmp_path, monkeypatch):
        csv = tmp_path / "j.csv"
        pd.DataFrame({"Ticker": ["aapl"], "Status": ["OPEN"]}).to_csv(csv, index=False)
        monkeypatch.setattr("modules.utils.CSV_PATH", csv)
        assert get_open_tickers() == {"AAPL"}


class TestRecentlyLost:
    def test_empty_when_no_losses(self, tmp_path, monkeypatch):
        csv = tmp_path / "j.csv"
        pd.DataFrame({
            "Ticker": ["AAPL"], "Status": ["WIN"], "Exit_Date": ["2026-04-01"],
        }).to_csv(csv, index=False)
        monkeypatch.setattr("modules.utils.CSV_PATH", csv)
        assert get_recently_lost_tickers(14) == set()

    def test_recent_loss_within_window(self, tmp_path, monkeypatch):
        csv = tmp_path / "j.csv"
        recent = (pd.Timestamp.now() - pd.Timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
        pd.DataFrame({
            "Ticker": ["AAPL"], "Status": ["LOSS"], "Exit_Date": [recent],
        }).to_csv(csv, index=False)
        monkeypatch.setattr("modules.utils.CSV_PATH", csv)
        assert get_recently_lost_tickers(14) == {"AAPL"}

    def test_old_loss_outside_window(self, tmp_path, monkeypatch):
        csv = tmp_path / "j.csv"
        old = (pd.Timestamp.now() - pd.Timedelta(days=30)).strftime("%Y-%m-%d %H:%M")
        pd.DataFrame({
            "Ticker": ["AAPL"], "Status": ["LOSS"], "Exit_Date": [old],
        }).to_csv(csv, index=False)
        monkeypatch.setattr("modules.utils.CSV_PATH", csv)
        assert get_recently_lost_tickers(14) == set()
