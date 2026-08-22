"""Tests unitaires — modules/my_portfolio_executions.py (Upgrade 4, incrément 2)

Couvre :
  - compute_reference : marché ouvert/fermé, résolution intraday/daily_close,
    FX manquant, prix manquant, signe BUY/SELL, cas limites de la spec.
  - log_execution / append_execution / load_executions : CRUD CSV sous
    FileLock, écriture atomique, indépendance vis-à-vis de POSITIONS.
  - summarize : agrégats (coût cumulé, % hors séance, pires exécutions).

Aucun vrai appel réseau : `get_historical_price`/`get_historical_fx_rate`
et `exchange_hours.is_open` sont monkeypatchés à chaque test.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from modules import my_portfolio_executions as mpe


@pytest.fixture(autouse=True)
def _isolate_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(mpe, "CSV_PATH", tmp_path / "my_portfolio_executions.csv")
    monkeypatch.setattr(mpe, "CSV_LOCK_PATH", tmp_path / "my_portfolio_executions.csv.lock")


# ─────────────────────────────────────────────────────────────────
# compute_reference
# ─────────────────────────────────────────────────────────────────

def test_compute_reference_buy_paid_more_than_reference_is_positive_bps(monkeypatch):
    """Cas CNC de la spec : BUY payé plus cher que la référence -> bps positif."""
    at = datetime(2026, 8, 19, 9, 50, tzinfo=UTC)
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: False)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (64.02, "daily_close"))

    ref = mpe.compute_reference(
        price_ticker="CNC", currency="USD", primary_exchange="US",
        direction="BUY", shares=3.27527, fill_price_native=67.17, executed_at=at,
    )

    assert ref["market_open_at_fill"] is False
    assert ref["reference_price_usd"] == pytest.approx(64.02)
    assert ref["reference_price_resolution"] == "daily_close"
    assert ref["slippage_bps"] == pytest.approx(492.03, abs=0.5)
    assert ref["slippage_bps"] > 0
    assert ref["slippage_usd"] == pytest.approx(10.32, abs=0.05)


def test_compute_reference_sell_below_reference_is_positive_bps(monkeypatch):
    """SELL vendu moins cher que la référence -> aussi compté positif (coût),
    même sémantique que le cas BUY (voir docstring compute_reference)."""
    at = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: True)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (100.0, "intraday"))

    ref = mpe.compute_reference(
        price_ticker="AAPL", currency="USD", primary_exchange="US",
        direction="SELL", shares=10.0, fill_price_native=98.0, executed_at=at,
    )

    assert ref["slippage_bps"] == pytest.approx(200.0)
    assert ref["slippage_bps"] > 0
    assert ref["slippage_usd"] == pytest.approx(20.0)


def test_compute_reference_sell_above_reference_is_negative_bps(monkeypatch):
    """SELL vendu plus cher que la référence -> exécution favorable, bps négatif."""
    at = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: True)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (100.0, "intraday"))

    ref = mpe.compute_reference(
        price_ticker="AAPL", currency="USD", primary_exchange="US",
        direction="SELL", shares=10.0, fill_price_native=102.0, executed_at=at,
    )

    assert ref["slippage_bps"] < 0
    assert ref["slippage_usd"] < 0


def test_compute_reference_non_usd_converts_fill_and_reference(monkeypatch):
    """Currency != USD : fill natif ET référence convertis avec le même taux
    FX historique (spec, item 5) — pas de double conversion, pas de taux live."""
    at = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: True)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (100.0, "intraday"))
    # EURUSD=X @ 1.10 (convention _FX_PAIR_FOR_CURRENCY : EUR -> multiplicateur direct)
    monkeypatch.setattr(
        mpe, "get_historical_fx_rate", lambda pair, _at: (1.10, "2026-08-19")
    )

    ref = mpe.compute_reference(
        price_ticker="BNP.PA", currency="EUR", primary_exchange="EURONEXT_PARIS",
        direction="BUY", shares=1.0, fill_price_native=101.0, executed_at=at,
    )

    assert ref["reference_price_usd"] == pytest.approx(100.0 * 1.10)
    # fill_usd = 101 * 1.10 = 111.1 ; ref_usd = 110.0
    assert ref["slippage_bps"] == pytest.approx((111.1 - 110.0) / 110.0 * 10000.0)


def test_compute_reference_hkd_inverse_quote(monkeypatch):
    """HKD : USDHKD=X cote en HKD par USD -> multiplicateur = 1/taux (inverse
    de la convention EUR), même logique que routers/my_portfolio.py::_usd_multiplier."""
    at = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: True)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (78.0, "daily_close"))
    monkeypatch.setattr(
        mpe, "get_historical_fx_rate", lambda pair, _at: (7.8, "2026-08-19")
    )

    ref = mpe.compute_reference(
        price_ticker="0992.HK", currency="HKD", primary_exchange="HKEX",
        direction="BUY", shares=37.094844, fill_price_native=78.0, executed_at=at,
    )

    assert ref["reference_price_usd"] == pytest.approx(78.0 / 7.8)
    assert ref["slippage_bps"] == pytest.approx(0.0, abs=1e-6)  # fill == ref natif


def test_compute_reference_missing_price_is_fail_open_none(monkeypatch):
    at = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: True)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (None, "daily_close"))

    ref = mpe.compute_reference(
        price_ticker="AAPL", currency="USD", primary_exchange="US",
        direction="BUY", shares=1.0, fill_price_native=100.0, executed_at=at,
    )

    assert ref["reference_price_usd"] is None
    assert ref["slippage_bps"] is None
    assert ref["slippage_usd"] is None
    assert ref["reference_price_resolution"] == "daily_close"  # toujours renseigné


def test_compute_reference_missing_fx_is_fail_open_none(monkeypatch):
    at = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: True)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (100.0, "intraday"))
    monkeypatch.setattr(mpe, "get_historical_fx_rate", lambda *_a, **_k: (None, None))

    ref = mpe.compute_reference(
        price_ticker="BNP.PA", currency="EUR", primary_exchange="EURONEXT_PARIS",
        direction="BUY", shares=1.0, fill_price_native=100.0, executed_at=at,
    )

    assert ref["reference_price_usd"] is None
    assert ref["slippage_bps"] is None
    assert ref["slippage_usd"] is None


def test_compute_reference_unconfigured_currency_is_fail_open_none(monkeypatch):
    at = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: True)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (100.0, "intraday"))

    ref = mpe.compute_reference(
        price_ticker="XYZ", currency="JPY", primary_exchange="US",
        direction="BUY", shares=1.0, fill_price_native=100.0, executed_at=at,
    )

    assert ref["reference_price_usd"] is None
    assert ref["slippage_bps"] is None


def test_compute_reference_invalid_direction_raises():
    at = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        mpe.compute_reference(
            price_ticker="AAPL", currency="USD", primary_exchange="US",
            direction="HOLD", shares=1.0, fill_price_native=100.0, executed_at=at,
        )


def test_compute_reference_hkex_lunch_break_market_closed(monkeypatch):
    """Pause déjeuner HKEX (cas limite explicite de la spec) : compute_reference
    délègue à exchange_hours.is_open, qui classe déjà 12h15 HKT fermé — ce
    test vérifie juste que le résultat réel remonte bien jusqu'à la row."""
    at = datetime(2026, 8, 19, 4, 15, tzinfo=UTC)  # 12h15 HKT
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (10.0, "intraday"))
    ref = mpe.compute_reference(
        # currency=USD (pas HKD) : ce test cible exchange_hours.is_open réel,
        # pas le FX historique (déjà couvert par test_compute_reference_hkd_inverse_quote).
        price_ticker="0992.HK", currency="USD", primary_exchange="HKEX",
        direction="BUY", shares=1.0, fill_price_native=10.0, executed_at=at,
    )
    assert ref["market_open_at_fill"] is False  # exchange_hours réel, pas mocké


# ─────────────────────────────────────────────────────────────────
# log_execution / append_execution / load_executions — CRUD CSV
# ─────────────────────────────────────────────────────────────────

def test_log_execution_rejects_naive_datetime():
    with pytest.raises(ValueError):
        mpe.log_execution(
            ticker="CNC", price_ticker="CNC", direction="BUY", shares=1.0,
            fill_price_native=67.17, currency="USD",
            executed_at=datetime(2026, 8, 19, 9, 50),  # naïf
            primary_exchange="US",
        )


def test_log_execution_writes_row_and_returns_it(monkeypatch):
    at = datetime(2026, 8, 19, 9, 50, tzinfo=UTC)
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: False)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (64.02, "daily_close"))

    row = mpe.log_execution(
        ticker="CNC", price_ticker="CNC", direction="BUY", shares=3.27527,
        fill_price_native=67.17, currency="USD", executed_at=at,
        primary_exchange="US", notes="hors séance US",
    )

    assert row["Ticker"] == "CNC"
    assert row["Market_Open_At_Fill"] is False
    assert row["Reference_Price_USD"] == pytest.approx(64.02)
    assert row["Notes"] == "hors séance US"

    df = mpe.load_executions()
    assert len(df) == 1
    assert df.iloc[0]["Ticker"] == "CNC"
    assert list(df.columns) == mpe.CSV_SCHEMA


def test_log_execution_appends_without_overwriting(monkeypatch):
    at = datetime(2026, 8, 19, 9, 50, tzinfo=UTC)
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: True)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (100.0, "intraday"))

    for ticker in ("CNC", "PSX", "MU"):
        mpe.log_execution(
            ticker=ticker, price_ticker=ticker, direction="BUY", shares=1.0,
            fill_price_native=100.0, currency="USD", executed_at=at,
            primary_exchange="US",
        )

    df = mpe.load_executions()
    assert len(df) == 3
    assert list(df["Ticker"]) == ["CNC", "PSX", "MU"]


def test_log_execution_independent_of_positions_data(monkeypatch):
    """Aucune contrainte de clé étrangère vers POSITIONS (cas limite spec) :
    un ticker absent de my_portfolio_data.POSITIONS reste journalisable."""
    at = datetime(2026, 8, 19, 9, 50, tzinfo=UTC)
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: True)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (50.0, "intraday"))

    row = mpe.log_execution(
        ticker="ZZZZ_NOT_IN_BOOK", price_ticker="ZZZZ_NOT_IN_BOOK", direction="BUY",
        shares=1.0, fill_price_native=50.0, currency="USD", executed_at=at,
        primary_exchange="US",
    )
    assert row["Ticker"] == "ZZZZ_NOT_IN_BOOK"


def test_load_executions_empty_when_no_file():
    df = mpe.load_executions()
    assert df.empty
    assert list(df.columns) == mpe.CSV_SCHEMA


# ─────────────────────────────────────────────────────────────────
# summarize
# ─────────────────────────────────────────────────────────────────

def test_summarize_empty_journal():
    summary = mpe.summarize(pd.DataFrame(columns=mpe.CSV_SCHEMA))
    assert summary == {
        "total_slippage_usd": 0.0,
        "fills_count": 0,
        "off_hours_fills_count": 0,
        "off_hours_fills_pct": 0.0,
        "worst_fills": [],
    }


def test_summarize_computes_aggregates(monkeypatch):
    at = datetime(2026, 8, 19, 9, 50, tzinfo=UTC)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (64.02, "daily_close"))

    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: False)
    mpe.log_execution(
        ticker="CNC", price_ticker="CNC", direction="BUY", shares=3.27527,
        fill_price_native=67.17, currency="USD", executed_at=at, primary_exchange="US",
    )
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: True)
    mpe.log_execution(
        ticker="PSX", price_ticker="PSX", direction="BUY", shares=1.0,
        fill_price_native=64.02, currency="USD", executed_at=at, primary_exchange="US",
    )

    df = mpe.load_executions()
    summary = mpe.summarize(df)

    assert summary["fills_count"] == 2
    assert summary["off_hours_fills_count"] == 1
    assert summary["off_hours_fills_pct"] == pytest.approx(50.0)
    assert summary["total_slippage_usd"] > 0  # CNC coûteux, PSX neutre
    assert summary["worst_fills"][0]["Ticker"] == "CNC"


def test_summarize_excludes_unresolvable_fills_from_slippage_but_counts_them(monkeypatch):
    at = datetime(2026, 8, 19, 9, 50, tzinfo=UTC)
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: True)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (None, "daily_close"))

    mpe.log_execution(
        ticker="UNKNOWN", price_ticker="UNKNOWN", direction="BUY", shares=1.0,
        fill_price_native=10.0, currency="USD", executed_at=at, primary_exchange="US",
    )

    df = mpe.load_executions()
    summary = mpe.summarize(df)
    assert summary["fills_count"] == 1
    assert summary["total_slippage_usd"] == 0.0
    assert summary["worst_fills"] == []


def test_summarize_worst_fills_top3_sorted_by_abs_bps(monkeypatch):
    at = datetime(2026, 8, 19, 9, 50, tzinfo=UTC)
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: True)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (100.0, "intraday"))

    fills = [
        ("A", 101.0),   # +100 bps
        ("B", 95.0),    # -500 bps
        ("C", 100.5),   # +50 bps
        ("D", 110.0),   # +1000 bps (pire)
    ]
    for ticker, fill_price in fills:
        mpe.log_execution(
            ticker=ticker, price_ticker=ticker, direction="BUY", shares=1.0,
            fill_price_native=fill_price, currency="USD", executed_at=at,
            primary_exchange="US",
        )

    summary = mpe.summarize(mpe.load_executions())
    worst_tickers = [w["Ticker"] for w in summary["worst_fills"]]
    assert worst_tickers == ["D", "B", "A"]
    assert len(summary["worst_fills"]) == 3


# ─────────────────────────────────────────────────────────────────
# vérification "live" — round-trip disque réel, sans mock du CSV I/O
# (les mocks portent uniquement sur les sources de données de marché,
# jamais sur la lecture/écriture disque elle-même)
# ─────────────────────────────────────────────────────────────────

def test_live_roundtrip_disk_write_then_read_back(monkeypatch):
    at = datetime.now(UTC) - timedelta(days=1)
    monkeypatch.setattr(mpe.exchange_hours, "is_open", lambda *_a, **_k: True)
    monkeypatch.setattr(mpe, "get_historical_price", lambda *_a, **_k: (100.0, "intraday"))

    mpe.log_execution(
        ticker="CNC", price_ticker="CNC", direction="BUY", shares=2.0,
        fill_price_native=105.0, currency="USD", executed_at=at, primary_exchange="US",
    )

    assert mpe.CSV_PATH.exists()
    df_disk = pd.read_csv(mpe.CSV_PATH)
    assert len(df_disk) == 1
    assert df_disk.iloc[0]["Ticker"] == "CNC"
    assert float(df_disk.iloc[0]["Slippage_Bps"]) == pytest.approx(500.0)
