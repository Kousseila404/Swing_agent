"""AlpacaBroker.adjust_position — rebalance dans les deux sens (audit 2026-11-18).

Le faux client reproduit les deux rejets Alpaca réels :
  - achat avec une jambe de vente ouverte → « potential wash trade detected » ;
  - vente pendant que la quantité est bloquée par le stop → « insufficient qty ».
L'annulation n'est effective qu'au poll suivant (comme chez Alpaca).
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from modules.broker_gateway import AlpacaBroker
from modules.utils import CSV_SCHEMA


class _FakeAlpaca:
    def __init__(self, fill_px=100.0, fills=True):
        self.orders = {
            "stop1": SimpleNamespace(id="stop1", side="sell", type="stop", stop_price=90.0,
                                     limit_price=None, status="new", legs=[]),
        }
        self.pending_cancel: set[str] = set()
        self.submitted = []
        self.fill_px, self.fills = fill_px, fills

    def get_orders(self, filter=None):
        for oid in list(self.pending_cancel):  # annulation effective au poll suivant
            self.orders.pop(oid, None)
        self.pending_cancel.clear()
        return list(self.orders.values())

    def cancel_order_by_id(self, oid):
        if oid in self.orders:
            self.pending_cancel.add(oid)

    def submit_order(self, order_data):
        side = str(order_data.side).lower()
        if self.orders and "buy" in side:
            raise RuntimeError("potential wash trade detected")
        if self.orders and "sell" in side:
            raise RuntimeError("insufficient qty available for order")
        o = SimpleNamespace(id=f"ord{len(self.submitted)}", qty=order_data.qty)
        self.submitted.append(order_data)
        return o

    def get_order_by_id(self, oid):
        qty = self.submitted[-1].qty if self.fills else 0
        return SimpleNamespace(filled_avg_price=self.fill_px if self.fills else None, filled_qty=qty)


@pytest.fixture
def broker(monkeypatch, tmp_path):
    csv = tmp_path / "trade_journal.csv"
    monkeypatch.setattr("modules.broker_gateway.CSV_PATH", csv)
    monkeypatch.setattr("modules.broker_gateway.CSV_LOCK_PATH", tmp_path / "j.lock")
    row = {c: "" for c in CSV_SCHEMA}
    row.update({"Ticker": "AAA", "Status": "OPEN", "Size": "10", "Entry": "80", "Stop_Loss": "90",
                "Direction": "LONG"})
    pd.DataFrame([row], columns=CSV_SCHEMA).to_csv(csv, index=False)
    import config
    monkeypatch.setattr(config, "ALPACA_API_KEY", "k")
    monkeypatch.setattr(config, "ALPACA_SECRET_KEY", "s")
    b = AlpacaBroker()
    monkeypatch.setattr(AlpacaBroker, "_CLOSE_CONFIRM_DELAY_SEC", 0)
    b.rearmed = []
    monkeypatch.setattr(b, "_arm_stop", lambda client, t, px, d, take_profit=None, qty=None: b.rearmed.append((t, px)) or "new-stop")
    return b, csv


@pytest.mark.parametrize("delta", [+5, -4])
def test_rebalance_both_directions_cancel_confirmed_then_rearm(broker, delta):
    b, csv = broker
    b._client = _FakeAlpaca()
    out = b.adjust_position("AAA", delta)
    assert out["ok"], out
    df = pd.read_csv(csv, dtype=str)
    assert int(df.loc[0, "Size"]) == 10 + delta
    assert b.rearmed == [("AAA", 90.0)]


def test_unfilled_order_does_not_touch_journal(broker):
    b, csv = broker
    b._client = _FakeAlpaca(fills=False)
    out = b.adjust_position("AAA", +5)
    assert not out["ok"]
    assert pd.read_csv(csv, dtype=str).loc[0, "Size"] == "10"
    assert b.rearmed == [("AAA", 90.0)]  # protection rétablie même sans fill
