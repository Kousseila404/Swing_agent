"""Tests read_open_positions + compute_current_exposure.

Utilise monkeypatch sur api_core.load_journal / load_equity — pas de
dépendance disque, purement fonctionnel.
"""
from __future__ import annotations

from modules import api_core
from modules.portfolio import compute_current_exposure, read_open_positions


def _row(ticker, status="OPEN", entry="100", size="10", sector="Tech",
         direction="LONG", order_id="ORD1"):
    return {
        "Ticker": ticker, "Status": status, "Entry": entry, "Size": size,
        "Sector": sector, "Direction": direction, "Order_ID": order_id,
    }


def test_read_open_positions_normalizes(monkeypatch):
    journal = [
        _row("AAPL", entry="150.5", size="10", sector="Tech"),
        _row("MSFT", status="WIN",   entry="300",   size="5", sector="Tech"),  # exclu
        _row("TSLA", entry="200",    size="4",     sector="Autos"),
        _row("",     entry="50",     size="1"),                                # exclu (ticker vide)
        _row("BADP", entry="0",      size="3"),                                # exclu (entry ≤ 0)
        _row("BADS", entry="10",     size="0"),                                # exclu (size ≤ 0)
    ]
    equity = {
        "open_positions": [
            {"ticker": "AAPL", "current_price": 160.0, "unrealized_pnl": 95.0},
        ],
    }
    monkeypatch.setattr(api_core, "load_journal", lambda: journal)
    monkeypatch.setattr(api_core, "load_equity",  lambda: equity)

    out = read_open_positions()
    tickers = [p["ticker"] for p in out]
    assert tickers == ["AAPL", "TSLA"]

    aapl = next(p for p in out if p["ticker"] == "AAPL")
    assert aapl["notional_usd"] == 1505.0
    assert aapl["current_price"] == 160.0
    assert aapl["unrealized_pnl"] == 95.0
    assert aapl["sector"] == "Tech"

    tsla = next(p for p in out if p["ticker"] == "TSLA")
    assert tsla["notional_usd"] == 800.0
    assert tsla["current_price"] is None  # pas dans live_map


def test_compute_current_exposure_aggregates():
    positions = [
        {"ticker": "AAPL", "notional_usd": 1500, "sector": "Tech"},
        {"ticker": "MSFT", "notional_usd":  500, "sector": "Tech"},
        {"ticker": "XOM",  "notional_usd":  800, "sector": "Energy"},
    ]
    exp = compute_current_exposure(positions)
    assert exp["total_invested_usd"] == 2800
    assert exp["n_open_positions"] == 3
    assert exp["sector_usd"] == {"Tech": 2000, "Energy": 800}
    assert set(exp["sector_tickers"]["Tech"]) == {"AAPL", "MSFT"}
    assert exp["sector_tickers"]["Energy"] == ["XOM"]
    assert sorted(exp["tickers_open"]) == ["AAPL", "MSFT", "XOM"]


def test_compute_current_exposure_empty():
    exp = compute_current_exposure([])
    assert exp == {
        "total_invested_usd": 0.0,
        "n_open_positions": 0,
        "n_open_rows": 0,
        "sector_usd": {},
        "sector_tickers": {},
        "tickers_open": [],
    }


def test_compute_current_exposure_deduplicates_same_ticker_open_twice():
    """Bug trade_journal : même ticker 2× en OPEN. Le count doit être UNIQUE
    pour ne pas bloquer artificiellement la gate free_slots de l'auto-proposer.
    """
    positions = [
        {"ticker": "NEM", "notional_usd": 1000, "sector": "Materials"},
        {"ticker": "NEM", "notional_usd": 2000, "sector": "Materials"},  # doublon
        {"ticker": "AAPL", "notional_usd": 500, "sector": "Tech"},
    ]
    exp = compute_current_exposure(positions)
    assert exp["n_open_positions"] == 2   # NEM + AAPL unique
    assert exp["n_open_rows"] == 3        # 3 rows CSV
    assert sorted(exp["tickers_open"]) == ["AAPL", "NEM"]
    assert "NEM" in exp["duplicated_open_tickers"]
    # Notionals cumulés côté capital engagé (on pénalise quand même)
    assert exp["total_invested_usd"] == 3500


def test_compute_current_exposure_missing_sector_defaults_unknown():
    exp = compute_current_exposure([
        {"ticker": "XXX", "notional_usd": 100, "sector": None},
    ])
    assert "Unknown" in exp["sector_usd"]
    assert exp["sector_usd"]["Unknown"] == 100


def test_read_open_positions_handles_missing_equity(monkeypatch):
    """equity_state sans open_positions ne doit pas crasher."""
    monkeypatch.setattr(api_core, "load_journal",
                        lambda: [_row("AAPL", entry="100", size="5")])
    monkeypatch.setattr(api_core, "load_equity", lambda: {})
    out = read_open_positions()
    assert len(out) == 1
    assert out[0]["current_price"] is None
