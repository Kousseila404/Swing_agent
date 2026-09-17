"""Tests — routers/performance.py (cockpit) + modules/perf_benchmark.py."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from modules import api_core, perf_benchmark
from routers.performance import compute_protection

_TOKEN = "test-token-cockpit"


def _order(**kw):
    base = dict(id="o", symbol="X", side="sell", type="stop", status="new", stop_price=None,
                limit_price=None, order_class="simple", legs=None)
    base.update(kw)
    return SimpleNamespace(**base)


class _Broker:
    name = "AlpacaBroker (Paper)"

    def __init__(self, positions, orders_by_symbol):
        self._positions = positions
        self._orders = orders_by_symbol

    def _get_client(self):
        broker = self

        class _C:
            def get_all_positions(self):
                return [SimpleNamespace(symbol=s) for s in broker._positions]
        return _C()

    def _open_orders_for(self, client, ticker):
        return self._orders.get(ticker, [])

    def _find_open_stop(self, orders, direction="LONG"):
        return next((o for o in orders if "stop" in o.type), None)

    def _find_open_limit(self, orders, direction="LONG"):
        return next((o for o in orders if o.type == "limit"), None)


def test_compute_protection_flags_unprotected_and_drift():
    broker = _Broker(
        positions=["CF", "MU", "HAS"],
        orders_by_symbol={
            "CF": [_order(symbol="CF", stop_price=76.46)],
            "MU": [_order(symbol="MU", stop_price=600.0, order_class="oco"), _order(symbol="MU", type="limit", limit_price=2891.0)],
            "HAS": [],
        },
    )
    rows = [
        {"Ticker": "CF", "Entry": "118.9", "Stop_Loss": "76.46", "Take_Profit": "245", "current_price": 133.0},
        {"Ticker": "MU", "Entry": "1007", "Stop_Loss": "652.33", "Take_Profit": "2891", "current_price": 975.0},
        {"Ticker": "HAS", "Entry": "93.9", "Stop_Loss": "60.85", "current_price": 89.0},
        {"Ticker": "GONE", "Entry": "10", "Stop_Loss": "8", "current_price": 9.0},
    ]
    out = compute_protection(rows, broker)
    by = {i["ticker"]: i for i in out["items"]}
    assert by["CF"]["status"] == "protected" and by["CF"]["protected"] is True
    assert by["CF"]["pct_to_stop"] > 40
    assert by["MU"]["status"] == "drift" and by["MU"]["broker_order_class"] == "oco" and by["MU"]["broker_tp"] == 2891.0
    assert by["HAS"]["status"] == "unprotected" and by["HAS"]["protected"] is False
    assert by["GONE"]["status"] == "no_broker_position"
    assert out["n_unprotected"] == 2 and out["all_protected"] is False


def test_compute_protection_paper_mode_uses_journal():
    out = compute_protection([{"Ticker": "AAPL", "Entry": "100", "Stop_Loss": "90", "current_price": 110.0}], None)
    assert out["items"][0]["status"] == "journal_only" and out["all_protected"] is True


def test_build_benchmark_aligns_and_rebases(monkeypatch):
    monkeypatch.setattr(perf_benchmark, "account_equity_series",
                        lambda months=6: [("2026-05-01", 100_000.0), ("2026-05-02", 101_000.0), ("2026-05-05", 99_000.0)])
    monkeypatch.setattr(perf_benchmark, "spy_series",
                        lambda start, end=None, ticker="SPY": [("2026-05-01", 500.0), ("2026-05-02", 505.0), ("2026-05-05", 510.0)])
    monkeypatch.setattr(perf_benchmark, "titan_basket_series",
                        lambda top_n=20, force=False: {"points": [("2026-05-01", 100.0), ("2026-05-05", 104.0)],
                                                       "periods": 1, "hit_rate": 1.0, "top_n": top_n})
    out = perf_benchmark.build_benchmark(months=6, top_n=20)
    assert out["dates"] == ["2026-05-01", "2026-05-02", "2026-05-05"]
    assert out["series"]["account"] == [100.0, 101.0, 99.0]
    assert out["series"]["spy"] == [100.0, 101.0, 102.0]
    assert out["series"]["basket"] == [100.0, 100.0, 104.0]   # série éparse → step
    s = out["summary"]
    assert s["account_return_pct"] == -1.0 and s["spy_return_pct"] == 2.0 and s["basket_return_pct"] == 4.0
    assert s["alpha_vs_spy_pct"] == -3.0 and s["alpha_vs_basket_pct"] == -5.0
    assert s["account_max_drawdown_pct"] == round((99_000 / 101_000 - 1) * 100, 2)


def test_system_health_endpoint(monkeypatch, tmp_path):
    import api
    monkeypatch.setattr(api_core, "API_TOKEN", _TOKEN)
    monkeypatch.setattr(api_core, "TRACKER_HEARTBEAT_PATH", tmp_path / "hb.json")
    monkeypatch.setattr(api_core, "TRADING_PATH", tmp_path / "trading.json")
    client = TestClient(api.app)
    r = client.get("/api/system/health", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert r.status_code == 200
    body = r.json()
    assert body["tracker"]["alive"] is False
    assert any("Tracker" in c["msg"] for c in body["checks"])
    assert "killswitch" in body and "crons" in body


def test_protection_endpoint_requires_auth(monkeypatch):
    import api
    monkeypatch.setattr(api_core, "API_TOKEN", _TOKEN)
    client = TestClient(api.app)
    assert client.get("/api/portfolio/protection").status_code in (401, 403)
    assert client.get("/api/performance/benchmark").status_code in (401, 403)
