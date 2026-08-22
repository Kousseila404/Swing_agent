"""Tests unitaires — journal d'exécution `my_portfolio` (Upgrade 4, incrément 3).

Cas limites couverts (voir docs/UPGRADES_MY_PORTFOLIO.md, Upgrade 4) :
datetime naïf rejeté, pause déjeuner HKEX, résolution intraday vs
daily_close surfacée, FX historique manquant, cash/watchlist/ticker
inconnu, round-trip CSV, agrégats.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from modules import my_portfolio_executions as mpe


def _at(y, mo, d, h, mi, tz_hours):
    return datetime(y, mo, d, h, mi, tzinfo=timezone(timedelta(hours=tz_hours)))


class TestComputeExecutionValidation:
    def test_rejects_naive_datetime(self):
        with pytest.raises(ValueError):
            mpe.compute_execution("CNC", "BUY", 1.0, 67.17, datetime(2026, 8, 19, 9, 50))

    def test_rejects_invalid_direction(self):
        with pytest.raises(ValueError):
            mpe.compute_execution("CNC", "HOLD", 1.0, 67.17, _at(2026, 8, 19, 9, 50, 2))


class TestComputeExecutionUsdTicker:
    def test_buy_paid_more_than_reference_is_positive_slippage(self, monkeypatch):
        # CNC : cas réel de la spec — acheté 67.17 hors séance US, ref ~64.02.
        monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (64.02, "daily_close"))
        monkeypatch.setattr(mpe, "get_historical_fx_rate", lambda pair, at: (None, False))
        at = _at(2026, 8, 19, 9, 50, 2)  # 09h50 Paris = hors séance US
        row = mpe.compute_execution("CNC", "BUY", 3.27527, 67.17, at, notes="hors séance US")

        assert row["Primary_Exchange"] == "US"
        assert row["Market_Open_At_Fill"] is False
        assert row["Reference_Price_USD"] == pytest.approx(64.02)
        assert row["Reference_Price_Resolution"] == "daily_close"
        assert row["Slippage_Bps"] == pytest.approx(492.03, abs=0.5)
        assert row["Slippage_Usd"] == pytest.approx(10.29, abs=0.1)
        assert row["Notes"] == "hors séance US"

    def test_sell_received_less_than_reference_is_positive_slippage(self, monkeypatch):
        monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (100.0, "intraday"))
        monkeypatch.setattr(mpe, "get_historical_fx_rate", lambda pair, at: (None, False))
        at = _at(2026, 3, 10, 15, 0, 0)  # UTC, marché US ouvert (~11h EST)
        row = mpe.compute_execution("PSX", "SELL", 1.0, 98.0, at)

        # Vendu moins cher que la référence → coût positif (mauvais fill).
        assert row["Slippage_Bps"] == pytest.approx(200.0, abs=0.5)
        assert row["Slippage_Usd"] == pytest.approx(2.0, abs=0.05)

    def test_favorable_fill_is_negative_slippage(self, monkeypatch):
        monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (100.0, "intraday"))
        monkeypatch.setattr(mpe, "get_historical_fx_rate", lambda pair, at: (None, False))
        at = _at(2026, 3, 10, 15, 0, 0)
        row = mpe.compute_execution("PSX", "BUY", 1.0, 98.0, at)
        assert row["Slippage_Bps"] < 0
        assert row["Slippage_Usd"] < 0

    def test_reference_unavailable_leaves_slippage_null(self, monkeypatch):
        monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (None, "daily_close"))
        monkeypatch.setattr(mpe, "get_historical_fx_rate", lambda pair, at: (None, False))
        at = _at(2026, 3, 10, 15, 0, 0)
        row = mpe.compute_execution("PSX", "BUY", 1.0, 98.0, at)
        assert row["Reference_Price_USD"] is None
        assert row["Slippage_Bps"] is None
        assert row["Slippage_Usd"] is None


class TestComputeExecutionFxCurrencies:
    def test_bnp_pa_eur_direct_quote(self, monkeypatch):
        monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (110.0, "daily_close"))
        monkeypatch.setattr(mpe, "get_historical_fx_rate", lambda pair, at: (1.10, False))
        at = _at(2026, 8, 19, 12, 0, 2)  # Euronext Paris ouvert
        row = mpe.compute_execution("BNP.PA", "BUY", 1.0, 110.0, at)

        assert row["Primary_Exchange"] == "EURONEXT_PARIS"
        assert row["Currency"] == "EUR"
        assert row["Reference_Price_USD"] == pytest.approx(110.0 * 1.10)
        assert row["Slippage_Bps"] == pytest.approx(0.0, abs=1e-6)

    def test_lnvgy_hkd_inverted_quote_and_hkex_lunch_pause(self, monkeypatch):
        monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (140.0, "daily_close"))
        monkeypatch.setattr(mpe, "get_historical_fx_rate", lambda pair, at: (7.8, False))
        at = _at(2026, 8, 19, 12, 15, 8)  # 12h15 HKT = pause déjeuner HKEX
        row = mpe.compute_execution("LNVGY", "BUY", 1.0, 140.0, at)

        assert row["Primary_Exchange"] == "HKEX"
        assert row["Market_Open_At_Fill"] is False  # piège pause déjeuner, pas "ouvert" juste car diurne
        assert row["Reference_Price_USD"] == pytest.approx(140.0 / 7.8)

    def test_fx_unavailable_leaves_reference_null(self, monkeypatch):
        monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (110.0, "daily_close"))
        monkeypatch.setattr(mpe, "get_historical_fx_rate", lambda pair, at: (None, False))
        at = _at(2026, 8, 19, 12, 0, 2)
        row = mpe.compute_execution("BNP.PA", "BUY", 1.0, 110.0, at)
        assert row["Reference_Price_USD"] is None
        assert row["Slippage_Bps"] is None

    def test_fx_approximate_annotates_notes(self, monkeypatch):
        monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (110.0, "daily_close"))
        monkeypatch.setattr(mpe, "get_historical_fx_rate", lambda pair, at: (1.10, True))
        at = _at(2026, 8, 19, 12, 0, 2)
        row = mpe.compute_execution("BNP.PA", "BUY", 1.0, 110.0, at, notes="test")
        assert "FX approximatif" in row["Notes"]


class TestComputeExecutionUnknownTicker:
    def test_ticker_not_in_book_falls_back_to_usd_us(self, monkeypatch, caplog):
        monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (50.0, "daily_close"))
        monkeypatch.setattr(mpe, "get_historical_fx_rate", lambda pair, at: (None, False))
        at = _at(2026, 3, 10, 15, 0, 0)
        row = mpe.compute_execution("UNKNOWNTICKER", "BUY", 1.0, 50.0, at)
        assert row["Currency"] == "USD"
        assert row["Primary_Exchange"] == "US"


class TestJournalRoundTrip:
    def test_log_then_load_round_trip(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mpe, "CSV_PATH", tmp_path / "execs.csv")
        monkeypatch.setattr(mpe, "CSV_LOCK_PATH", tmp_path / "execs.csv.lock")
        monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (64.02, "daily_close"))
        monkeypatch.setattr(mpe, "get_historical_fx_rate", lambda pair, at: (None, False))

        at = _at(2026, 8, 19, 9, 50, 2)
        mpe.log_execution("CNC", "BUY", 3.27527, 67.17, at, notes="hors séance US")

        df = mpe.load_executions()
        assert len(df) == 1
        assert df.iloc[0]["Ticker"] == "CNC"
        assert list(df.columns) == mpe.CSV_SCHEMA

    def test_load_missing_file_returns_empty_schema(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mpe, "CSV_PATH", tmp_path / "execs.csv")
        monkeypatch.setattr(mpe, "CSV_LOCK_PATH", tmp_path / "execs.csv.lock")
        df = mpe.load_executions()
        assert df.empty
        assert list(df.columns) == mpe.CSV_SCHEMA


class TestSummary:
    def test_empty_journal_summary(self):
        df = mpe._empty_df()
        summary = mpe.get_summary(df)
        assert summary["n_fills"] == 0
        assert summary["total_slippage_usd"] is None
        assert summary["worst_executions"] == []

    def test_summary_aggregates_and_ranks_worst(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mpe, "CSV_PATH", tmp_path / "execs.csv")
        monkeypatch.setattr(mpe, "CSV_LOCK_PATH", tmp_path / "execs.csv.lock")
        monkeypatch.setattr(mpe, "get_historical_fx_rate", lambda pair, at: (None, False))

        at = _at(2026, 3, 10, 15, 0, 0)
        monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (100.0, "intraday"))
        mpe.log_execution("PSX", "BUY", 1.0, 103.0, at)  # +300bps
        monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (100.0, "intraday"))
        mpe.log_execution("CNC", "BUY", 1.0, 100.5, at)  # +50bps
        monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (None, "daily_close"))
        mpe.log_execution("DRH", "BUY", 1.0, 12.0, at)  # référence indisponible, exclu des agrégats

        df = mpe.load_executions()
        summary = mpe.get_summary(df)

        assert summary["n_fills"] == 3
        assert summary["total_slippage_usd"] == pytest.approx(3.0 + 0.5, abs=0.05)
        assert summary["worst_executions"][0]["ticker"] == "PSX"
        assert len(summary["worst_executions"]) == 2  # DRH exclu (slippage null)

    def test_pct_outside_market_computed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mpe, "CSV_PATH", tmp_path / "execs.csv")
        monkeypatch.setattr(mpe, "CSV_LOCK_PATH", tmp_path / "execs.csv.lock")
        monkeypatch.setattr(mpe, "get_historical_price", lambda ticker, at: (100.0, "intraday"))
        monkeypatch.setattr(mpe, "get_historical_fx_rate", lambda pair, at: (None, False))

        outside = _at(2026, 8, 19, 9, 50, 2)  # hors séance US
        inside = _at(2026, 3, 10, 15, 0, 0)  # en séance US
        mpe.log_execution("CNC", "BUY", 1.0, 100.0, outside)
        mpe.log_execution("PSX", "BUY", 1.0, 100.0, inside)

        df = mpe.load_executions()
        summary = mpe.get_summary(df)
        assert summary["n_fills_outside_market"] == 1
        assert summary["pct_fills_outside_market"] == pytest.approx(50.0)
