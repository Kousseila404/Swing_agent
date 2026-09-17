"""Tests de non-régression — audit 2026-09-17 (P0-1 / P0-2 / P0-3).

  P0-1  bracket GTC + ré-armement des stops manquants (ensure_protective_stops,
        update_stop_loss crée un stop si aucune jambe n'existe).
  P0-2  save_journal fusionne avec le disque (un append concurrent survit).
  P0-3  sync_fills_from_alpaca : fenêtre de grâce + refus des vieux fills.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from modules.broker_gateway import AlpacaBroker
from modules.tracker import evaluation
from modules.utils import CSV_SCHEMA

# ─────────────────────────────────────────────────────────────────
# Fakes Alpaca
# ─────────────────────────────────────────────────────────────────

def _order(**kw):
    base = dict(
        id="o-1", symbol="AAPL", side="sell", type="stop", status="new",
        filled_qty=0, filled_avg_price=None, filled_at=None, stop_price=None,
        limit_price=None, qty=10, submitted_at=datetime.now(UTC), legs=None,
        order_class="simple",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeClient:
    def __init__(self, positions=None, open_orders=None, closed_orders=None, orders_by_id=None):
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.closed_orders = closed_orders or []
        self.orders_by_id = orders_by_id or {}
        self.submitted = []
        self.canceled = []
        self.replaced = []

    def get_clock(self):
        return SimpleNamespace(is_open=True, next_open=None, next_close=None, timestamp=None)

    def get_all_positions(self):
        return self.positions

    def get_open_position(self, symbol):
        for p in self.positions:
            if p.symbol == symbol:
                return p
        raise RuntimeError("position does not exist")

    def get_orders(self, filter=None):
        status = str(getattr(filter, "status", "")).lower()
        syms = getattr(filter, "symbols", None)
        pool = self.open_orders if "open" in status else self.closed_orders
        after = getattr(filter, "after", None)
        out = []
        for o in pool:
            if syms and o.symbol not in syms:
                continue
            if after is not None and o.submitted_at is not None and o.submitted_at < after:
                continue
            out.append(o)
        return out

    def get_order_by_id(self, oid):
        if oid in self.orders_by_id:
            return self.orders_by_id[oid]
        raise RuntimeError("404 order not found")

    def submit_order(self, order_data):
        self.submitted.append(order_data)
        return SimpleNamespace(id=f"new-{len(self.submitted)}", status="accepted",
                               filled_avg_price=None, legs=[])

    def cancel_order_by_id(self, oid):
        self.canceled.append(oid)

    def replace_order_by_id(self, oid, req):
        self.replaced.append((oid, req))


@pytest.fixture
def broker(monkeypatch, tmp_path):
    csv = tmp_path / "trade_journal.csv"
    lock = tmp_path / "trade_journal.csv.lock"
    monkeypatch.setattr("modules.broker_gateway.CSV_PATH", csv)
    monkeypatch.setattr("modules.broker_gateway.CSV_LOCK_PATH", lock)
    import config
    monkeypatch.setattr(config, "ALPACA_API_KEY", "k")
    monkeypatch.setattr(config, "ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr(config, "ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    b = AlpacaBroker()
    b._csv = csv
    return b


def _write_csv(path, rows):
    df = pd.DataFrame([{**{c: "" for c in CSV_SCHEMA}, **r} for r in rows], columns=CSV_SCHEMA)
    df.to_csv(path, index=False)


# ─────────────────────────────────────────────────────────────────
# P0-1 — bracket GTC
# ─────────────────────────────────────────────────────────────────

def test_submit_order_uses_gtc_bracket_with_client_order_id(broker, sample_scan):
    fake = _FakeClient()
    broker._client = fake
    res = broker.submit_order(sample_scan)
    assert res.success is True
    req = fake.submitted[0]
    assert str(req.time_in_force).lower().endswith("gtc")
    assert str(req.order_class).lower().endswith("bracket")
    assert req.client_order_id.startswith("SQ-AAPL-")
    assert req.stop_loss.stop_price == 147.0
    assert req.take_profit.limit_price == 156.0


def test_ensure_protective_stops_arms_missing_stop_with_journal_levels(broker):
    """Position ouverte, aucun ordre stop → OCO GTC (SL journal + TP journal)."""
    fake = _FakeClient(positions=[SimpleNamespace(symbol="CF", qty="50", avg_entry_price="118.9")])
    broker._client = fake
    actions = broker.ensure_protective_stops([
        {"Ticker": "CF", "Direction": "LONG", "Entry": "118.9", "Stop_Loss": "76.46", "Take_Profit": "243.75"},
    ])
    assert len(actions) == 1 and actions[0]["ok"] and actions[0]["fallback"] is False
    req = fake.submitted[0]
    assert str(req.order_class).lower().endswith("oco")
    assert str(req.time_in_force).lower().endswith("gtc")
    assert req.limit_price == 243.75
    assert req.stop_loss.stop_price == 76.46
    assert req.qty == 50


def test_ensure_protective_stops_falls_back_to_catastrophe_floor(broker):
    """Ligne importée sans SL → stop simple GTC à −35 % de l'entrée + flag fallback."""
    fake = _FakeClient(positions=[SimpleNamespace(symbol="HAS", qty="39", avg_entry_price="93.94")])
    broker._client = fake
    actions = broker.ensure_protective_stops([
        {"Ticker": "HAS", "Direction": "LONG", "Entry": "93.94", "Stop_Loss": "", "Take_Profit": ""},
    ])
    assert actions[0]["fallback"] is True and actions[0]["ok"]
    req = fake.submitted[0]
    assert req.stop_price == pytest.approx(round(93.94 * 0.65, 2))
    assert not hasattr(req, "limit_price") or req.limit_price is None


def test_ensure_protective_stops_noop_when_stop_exists(broker):
    fake = _FakeClient(
        positions=[SimpleNamespace(symbol="MU", qty="1", avg_entry_price="1007")],
        open_orders=[_order(symbol="MU", side="sell", type="stop", stop_price=652.0)],
    )
    broker._client = fake
    actions = broker.ensure_protective_stops([
        {"Ticker": "MU", "Direction": "LONG", "Entry": "1007", "Stop_Loss": "652", "Take_Profit": "2986"},
    ])
    assert actions == [] and fake.submitted == []


def test_ensure_protective_stops_cancels_orphan_limit_before_oco(broker):
    """Une jambe LIMIT orpheline bloque la qty → annulée puis remplacée par un OCO."""
    fake = _FakeClient(
        positions=[SimpleNamespace(symbol="EOG", qty="10", avg_entry_price="148.5")],
        open_orders=[_order(id="lim-1", symbol="EOG", side="sell", type="limit", limit_price=245.39)],
    )
    broker._client = fake
    actions = broker.ensure_protective_stops([
        {"Ticker": "EOG", "Direction": "LONG", "Entry": "148.5", "Stop_Loss": "95.94", "Take_Profit": ""},
    ])
    assert actions[0]["ok"]
    assert fake.canceled == ["lim-1"]
    req = fake.submitted[0]
    assert req.limit_price == 245.39 and req.stop_loss.stop_price == 95.94


def test_update_stop_loss_creates_stop_when_no_leg(broker):
    fake = _FakeClient(positions=[SimpleNamespace(symbol="NEM", qty="22", avg_entry_price="123")])
    broker._client = fake
    _write_csv(broker._csv, [{"Ticker": "NEM", "Status": "OPEN", "Stop_Loss": "80"}])
    assert broker.update_stop_loss("NEM", 110.0, "LONG") is True
    assert fake.replaced == [] and len(fake.submitted) == 1
    assert fake.submitted[0].stop_price == 110.0
    df = pd.read_csv(broker._csv, dtype=str)
    assert df.loc[0, "Stop_Loss"] == "110.0"


# ─────────────────────────────────────────────────────────────────
# P0-3 — sync_fills_from_alpaca
# ─────────────────────────────────────────────────────────────────

def test_sync_skips_rows_inside_grace_window(broker):
    """Ligne écrite il y a 2 min, position pas encore visible → PAS de clôture,
    même si un vieux fill traîne (cas CF 17/08)."""
    old_fill = _order(symbol="CF", side="sell", type="market", filled_qty=28,
                      filled_avg_price=120.0475,
                      filled_at=datetime.now(UTC) - timedelta(days=21),
                      submitted_at=datetime.now(UTC) - timedelta(days=21), status="filled")
    fake = _FakeClient(positions=[], closed_orders=[old_fill])
    broker._client = fake
    _write_csv(broker._csv, [{
        "Ticker": "CF", "Status": "OPEN", "Entry": "118.9", "Size": "50",
        "Date": (datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S"),
        "Order_ID": "b841ac15-8fe2-4085-8515-5e773846bc54",
    }])
    assert broker.sync_fills_from_alpaca() == 0
    df = pd.read_csv(broker._csv, dtype=str)
    assert df.loc[0, "Status"] == "OPEN"


def test_sync_ignores_fills_older_than_entry(broker):
    """Ligne de 2 h, position absente, seul fill = antérieur à l'entrée → reste OPEN + warning."""
    old_fill = _order(symbol="CF", side="sell", type="market", filled_qty=28,
                      filled_avg_price=120.0475,
                      filled_at=datetime.now(UTC) - timedelta(days=21),
                      submitted_at=datetime.now(UTC) - timedelta(days=21), status="filled")
    fake = _FakeClient(positions=[], closed_orders=[old_fill])
    broker._client = fake
    _write_csv(broker._csv, [{
        "Ticker": "CF", "Status": "OPEN", "Entry": "118.9", "Size": "50",
        "Date": (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
        "Order_ID": "not-a-uuid",
    }])
    assert broker.sync_fills_from_alpaca() == 0
    assert pd.read_csv(broker._csv, dtype=str).loc[0, "Status"] == "OPEN"


def test_sync_closes_with_exit_fill_after_entry(broker):
    entry_dt = datetime.now() - timedelta(days=3)
    exit_fill = _order(symbol="CF", side="sell", type="stop", filled_qty=50,
                       filled_avg_price=76.5,
                       filled_at=datetime.now(UTC) - timedelta(hours=1),
                       submitted_at=datetime.now(UTC) - timedelta(days=3), status="filled")
    fake = _FakeClient(positions=[], closed_orders=[exit_fill])
    broker._client = fake
    _write_csv(broker._csv, [{
        "Ticker": "CF", "Status": "OPEN", "Entry": "118.9", "Size": "50",
        "Date": entry_dt.strftime("%Y-%m-%d %H:%M:%S"), "Order_ID": "x",
    }])
    assert broker.sync_fills_from_alpaca() == 1
    df = pd.read_csv(broker._csv, dtype=str)
    assert df.loc[0, "Status"] == "LOSS"
    assert df.loc[0, "Close_Reason"] == "SL_HIT"
    assert float(df.loc[0, "Exit_Price"]) == 76.5


def test_sync_marks_canceled_when_parent_never_filled(broker):
    pid = "5684faf2-126e-4676-b310-17ea1ae58810"
    parent = _order(id=pid, symbol="NEM", side="buy", type="market", status="canceled", filled_qty=0)
    fake = _FakeClient(positions=[], orders_by_id={pid: parent})
    broker._client = fake
    _write_csv(broker._csv, [{
        "Ticker": "NEM", "Status": "OPEN", "Entry": "110.6", "Size": "16",
        "Date": (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"), "Order_ID": pid,
    }])
    assert broker.sync_fills_from_alpaca() == 1
    df = pd.read_csv(broker._csv, dtype=str)
    assert df.loc[0, "Status"] == "CANCELED" and df.loc[0, "Close_Reason"] == "ENTRY_EXPIRED"


# ─────────────────────────────────────────────────────────────────
# P0-2 — save_journal fusionne avec le disque
# ─────────────────────────────────────────────────────────────────

def test_merge_journal_frames_keeps_concurrent_append():
    disk = pd.DataFrame([
        {"Date": "2026-09-01 13:30:05", "Ticker": "HAS", "Status": "OPEN", "Order_ID": "a24", "Stop_Loss": "61.1"},
        {"Date": "2026-08-17 13:30:06", "Ticker": "MU", "Status": "OPEN", "Order_ID": "ce6", "Stop_Loss": "652"},
    ], dtype=str)
    updated = pd.DataFrame([  # vue tracker chargée AVANT l'append de HAS
        {"Date": "2026-08-17 13:30:06", "Ticker": "MU", "Status": "OPEN", "Order_ID": "ce6", "Stop_Loss": "700"},
    ], dtype=str)
    merged = evaluation.merge_journal_frames(disk, updated)
    assert len(merged) == 2
    assert merged.loc[merged["Ticker"] == "MU", "Stop_Loss"].iloc[0] == "700"
    assert merged.loc[merged["Ticker"] == "HAS", "Stop_Loss"].iloc[0] == "61.1"


def test_save_journal_does_not_lose_api_append(monkeypatch, tmp_path):
    csv = tmp_path / "trade_journal.csv"
    monkeypatch.setattr(evaluation, "CSV_PATH", csv)
    monkeypatch.setattr(evaluation, "CSV_LOCK_PATH", tmp_path / "j.lock")
    _write_csv(csv, [{"Ticker": "MU", "Status": "OPEN", "Order_ID": "ce6", "Stop_Loss": "652", "Date": "2026-08-17 13:30:06"}])
    df = evaluation.load_journal()               # vue du cycle
    # L'API ajoute HAS pendant l'évaluation
    disk = pd.read_csv(csv, dtype=str)
    disk = pd.concat([disk, pd.DataFrame([{**{c: "" for c in CSV_SCHEMA}, "Ticker": "HAS", "Status": "OPEN",
                                            "Order_ID": "a24", "Stop_Loss": "61.1", "Date": "2026-09-01 13:30:05"}])])
    disk.to_csv(csv, index=False)
    # Le tracker remonte le SL de MU et sauvegarde sa vue (sans HAS)
    df.loc[df["Ticker"] == "MU", "Stop_Loss"] = 700
    evaluation.save_journal(df)
    out = pd.read_csv(csv, dtype=str)
    assert set(out["Ticker"]) == {"MU", "HAS"}
    assert out.loc[out["Ticker"] == "MU", "Stop_Loss"].iloc[0] in ("700", "700.0")
    assert out.loc[out["Ticker"] == "HAS", "Stop_Loss"].iloc[0] == "61.1"
    # miroir DuckDB co-localisé avec le CSV (jamais la DB prod)
    assert (tmp_path / "trade_journal.duckdb").exists()
