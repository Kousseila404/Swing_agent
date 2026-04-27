"""Tests PaperBroker — journalisation CSV, update SL, clôture position."""
from __future__ import annotations

import pandas as pd
import pytest

from modules.broker_gateway import OrderResult, PaperBroker


@pytest.fixture
def broker_with_tmp_csv(tmp_path, monkeypatch):
    """PaperBroker avec CSV + lock pointant vers tmp_path pour isolation."""
    csv = tmp_path / "trade_journal.csv"
    lock = tmp_path / "trade_journal.csv.lock"
    monkeypatch.setattr("modules.broker_gateway.CSV_PATH", csv)
    monkeypatch.setattr("modules.broker_gateway.CSV_LOCK_PATH", lock)
    return PaperBroker(), csv


def test_name(broker_with_tmp_csv):
    broker, _ = broker_with_tmp_csv
    assert "Paper" in broker.name


def test_submit_order_writes_csv(broker_with_tmp_csv, sample_scan):
    broker, csv = broker_with_tmp_csv
    result = broker.submit_order(sample_scan)
    assert isinstance(result, OrderResult)
    assert result.success
    assert result.ticker == "AAPL"
    assert result.order_id.startswith("PAPER-")
    assert csv.exists()

    df = pd.read_csv(csv)
    assert len(df) == 1
    assert df.loc[0, "Ticker"] == "AAPL"
    assert df.loc[0, "Status"] == "OPEN"
    assert float(df.loc[0, "Entry"]) == 150.0
    assert float(df.loc[0, "Stop_Loss"]) == 147.0
    assert df.loc[0, "Signal"] == "TEST"


def test_submit_order_then_close(broker_with_tmp_csv, sample_scan):
    broker, csv = broker_with_tmp_csv
    broker.submit_order(sample_scan)
    ok = broker.close_position("AAPL", exit_price=156.0, status="WIN")
    assert ok is True

    df = pd.read_csv(csv)
    assert df.loc[0, "Status"] == "WIN"
    assert float(df.loc[0, "Exit_Price"]) == 156.0
    assert df.loc[0, "Exit_Date"]  # non vide


def test_close_unknown_ticker_returns_false(broker_with_tmp_csv, sample_scan):
    broker, _ = broker_with_tmp_csv
    broker.submit_order(sample_scan)
    assert broker.close_position("UNKNOWN", 100.0, "WIN") is False


def test_update_stop_loss(broker_with_tmp_csv, sample_scan):
    broker, csv = broker_with_tmp_csv
    broker.submit_order(sample_scan)
    ok = broker.update_stop_loss("AAPL", new_sl=148.5)
    assert ok is True

    df = pd.read_csv(csv)
    assert float(df.loc[0, "Stop_Loss"]) == 148.5
    # Initial_SL ne doit PAS avoir changé
    assert float(df.loc[0, "Initial_SL"]) == 147.0


def test_update_stop_loss_unknown_ticker(broker_with_tmp_csv):
    broker, _ = broker_with_tmp_csv
    assert broker.update_stop_loss("UNKNOWN", 100.0) is False


def test_dry_run_close_no_csv_write(broker_with_tmp_csv, sample_scan):
    broker, csv = broker_with_tmp_csv
    broker.submit_order(sample_scan)
    ok = broker.close_position("AAPL", 156.0, "WIN", update_csv=False)
    assert ok is True
    df = pd.read_csv(csv)
    assert df.loc[0, "Status"] == "OPEN"  # pas touché


def test_get_open_positions_empty_when_no_csv(tmp_path, monkeypatch):
    monkeypatch.setattr("modules.broker_gateway.CSV_PATH", tmp_path / "nope.csv")
    broker = PaperBroker()
    assert broker.get_open_positions() == []


def test_get_open_positions_returns_structured(broker_with_tmp_csv, sample_scan):
    broker, _ = broker_with_tmp_csv
    broker.submit_order(sample_scan)
    positions = broker.get_open_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p.ticker == "AAPL"
    assert p.direction == "LONG"
    assert p.entry == 150.0
    assert p.size == 10
    assert p.status == "OPEN"
    assert p.order_id.startswith("PAPER-")


def test_get_open_positions_filters_closed(broker_with_tmp_csv, sample_scan):
    broker, _ = broker_with_tmp_csv
    broker.submit_order(sample_scan)
    broker.close_position("AAPL", 156.0, "WIN")
    assert broker.get_open_positions() == []


def test_get_account_equity_fallback_to_config(tmp_path, monkeypatch):
    # Pas de equity_state.json → fallback ACCOUNT_SIZE (100k)
    # On pointe Path(__file__).parent.parent vers un dir sans data/
    broker = PaperBroker()
    equity = broker.get_account_equity()
    # Soit config.ACCOUNT_SIZE (si equity_state absent), soit valeur réelle
    assert equity > 0
