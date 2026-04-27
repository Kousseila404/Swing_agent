"""Tests AlpacaBroker — market-hours gate.

Vérifie qu'un ordre soumis hors heures NYSE est refusé fail-closed
sans contacter Alpaca, pour éviter les brackets en queue avec SL/TP stale.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from modules.broker_gateway import AlpacaBroker, OrderResult


class _FakeClock:
    def __init__(self, is_open: bool, next_open: str = "2026-04-24T09:30:00-04:00"):
        self.is_open = is_open
        self.next_open = SimpleNamespace(isoformat=lambda: next_open)
        self.next_close = SimpleNamespace(isoformat=lambda: "2026-04-24T16:00:00-04:00")
        self.timestamp = SimpleNamespace(isoformat=lambda: "2026-04-24T08:00:00-04:00")


class _FakeClient:
    def __init__(self, is_open: bool):
        self._is_open = is_open
        self.submitted = []

    def get_clock(self):
        return _FakeClock(self._is_open)

    def submit_order(self, order_data):
        self.submitted.append(order_data)
        return SimpleNamespace(
            id="fake-order-id",
            status="accepted",
            filled_avg_price=None,
        )


@pytest.fixture
def alpaca_broker(monkeypatch, tmp_path):
    """AlpacaBroker avec credentials fake + CSV isolé."""
    csv = tmp_path / "trade_journal.csv"
    lock = tmp_path / "trade_journal.csv.lock"
    monkeypatch.setattr("modules.broker_gateway.CSV_PATH", csv)
    monkeypatch.setattr("modules.broker_gateway.CSV_LOCK_PATH", lock)
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    # Réinstalle la config (les getattr() de __init__ lisent config à l'import)
    import config
    monkeypatch.setattr(config, "ALPACA_API_KEY", "k")
    monkeypatch.setattr(config, "ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr(config, "ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    return AlpacaBroker()


def test_submit_refused_when_market_closed(alpaca_broker, sample_scan):
    """Gate horaire : NYSE fermée → OrderResult(success=False) sans submit."""
    fake = _FakeClient(is_open=False)
    alpaca_broker._client = fake

    result = alpaca_broker.submit_order(sample_scan)

    assert isinstance(result, OrderResult)
    assert result.success is False
    assert result.ticker == "AAPL"
    assert result.order_id == ""
    assert "fermée" in result.message.lower() or "closed" in result.message.lower()
    assert fake.submitted == []  # aucun ordre envoyé à Alpaca


def test_submit_allowed_when_market_open(alpaca_broker, sample_scan):
    """Gate horaire : NYSE ouverte → submit normal."""
    fake = _FakeClient(is_open=True)
    alpaca_broker._client = fake

    result = alpaca_broker.submit_order(sample_scan)

    assert result.success is True
    assert result.ticker == "AAPL"
    assert result.order_id == "fake-order-id"
    assert len(fake.submitted) == 1


def test_market_status_helper(alpaca_broker):
    """market_status() expose is_open + next_open en dict JSON-serializable."""
    alpaca_broker._client = _FakeClient(is_open=False)
    status = alpaca_broker.market_status()
    assert status["is_open"] is False
    assert status["next_open"] == "2026-04-24T09:30:00-04:00"
    assert status["next_close"] == "2026-04-24T16:00:00-04:00"
