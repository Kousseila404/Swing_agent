"""Stratégie cœur-satellite (ADR-022) — logique pure + intégration."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import config
from modules import core_satellite as cs


def _series(n: int, start: float, end: float) -> tuple[list[str], list[float]]:
    dates = [d.date().isoformat() for d in pd.bdate_range("2025-01-01", periods=n)]
    step = (end - start) / (n - 1)
    return dates, [start + i * step for i in range(n)]


def test_momentum_12_1_skips_last_month_and_requires_history():
    d, v = _series(300, 100.0, 400.0)
    v[-1] = 1.0  # krach du dernier jour : ignoré (fenêtre s'arrête à t−21)
    mom = cs.momentum_12_1({"A": (d, v), "SHORT": _series(200, 1, 2)}, asof=d[-1])
    need = cs.MOM_LOOKBACK + cs.MOM_SKIP + 1
    assert set(mom) == {"A"}
    assert abs(mom["A"] - (v[-1 - cs.MOM_SKIP] / v[-need] - 1)) < 1e-12


def test_momentum_ignores_stale_series():
    d, v = _series(300, 100, 200)
    assert cs.momentum_12_1({"A": (d, v)}, asof="2030-01-01") == {}


def test_sector_zscores_neutralise_sector_level():
    vals = {"E1": 0.50, "E2": 0.40, "E3": 0.30, "T1": 0.05, "T2": 0.00, "T3": -0.05}
    sec = {t: ("Energy" if t[0] == "E" else "Tech") for t in vals}
    z = cs.sector_zscores(vals, sec)
    assert abs(z["E1"] - z["T1"]) < 1e-9 and z["E3"] < 0


def test_rank_with_cap_and_buffer_selection():
    scores = {f"E{i}": 10 - i for i in range(6)} | {f"T{i}": 5 - i for i in range(6)}
    sec = {t: ("Energy" if t[0] == "E" else "Tech") for t in scores}
    ranks = cs.rank_with_cap(scores, sec, cap=2)
    assert [t for t in sorted(ranks, key=ranks.get)][:4] == ["E0", "E1", "T0", "T1"]
    # Titre détenu de rang ≤ exit_rank conservé même hors top-n ; plafond respecté.
    sel = cs.select_satellite(ranks, sec, held={"T3"}, n=4, exit_rank=8, cap=2)
    assert "T3" in sel and len(sel) == 4
    assert sum(1 for t in sel if t[0] == "T") <= 2
    # Détenu au-delà du tampon → remplacé.
    assert "T5" not in cs.select_satellite(ranks, sec, held={"T5"}, n=4, exit_rank=6, cap=2)


def test_regime_and_target_weights():
    assert cs.regime_is_bull([1.0] * 150) is None
    assert cs.regime_is_bull([1.0] * 199 + [2.0]) is True
    assert cs.regime_is_bull([2.0] * 199 + [1.0]) is False
    w = cs.target_weights(["A", "B"], bull=True)
    assert w == {"A": pytest.approx(0.15), "B": pytest.approx(0.15), "RSP": pytest.approx(0.70)}
    assert cs.target_weights(["A", "B"], bull=False) == {"RSP": 1.0}
    assert cs.target_weights(["A"], bull=None) == {"RSP": 1.0}  # régime inconnu = prudence


def test_plan_orders_sells_first_and_ignores_small_adjustments():
    orders = cs.plan_orders(
        targets={"RSP": 0.7, "NEW": 0.3}, equity=10_000,
        positions={"RSP": 30.0, "OLD": 5.0}, prices={"RSP": 200.0, "NEW": 100.0, "OLD": 50.0},
        min_trade_usd=250,
    )
    kinds = [(o["ticker"], o["kind"], o["delta_qty"]) for o in orders]
    assert kinds[0] == ("OLD", "close", -5.0)
    assert ("NEW", "open", 30) in kinds and ("RSP", "buy", 5.0) in kinds
    small = cs.plan_orders({"RSP": 1.0}, 10_000, {"RSP": 49.0}, {"RSP": 200.0}, min_trade_usd=250)
    assert small == []  # +1 part = 200 $ < 250 $


class _Broker:
    def __init__(self):
        self.calls: list[tuple] = []

    def market_status(self):
        return {"is_open": True}

    def get_account_equity(self):
        return 10_000.0

    def get_open_positions(self):
        return [SimpleNamespace(ticker="OLD", size=5, entry=40.0)]

    def close_position(self, t, px, status, direction, update_csv=True):
        self.calls.append(("close", t, status))
        return True

    def submit_order(self, scan):
        self.calls.append(("open", scan.ticker, scan.position_size, scan.signal, round(scan.stop_loss / scan.price, 2)))
        return SimpleNamespace(success=True, message="ok")

    def adjust_position(self, t, delta):
        self.calls.append(("adjust", t, delta))
        return {"ok": True}


def _wire(monkeypatch, tmp_path, allowed=True):
    import modules.basket_rotation as br
    import modules.broker_gateway as bg
    import modules.tracker.killswitch as ks
    monkeypatch.setattr(config, "STRATEGY_MODE", "core_satellite", raising=False)
    monkeypatch.setattr(cs, "STATE_PATH", tmp_path / "cs.json")
    broker = _Broker()
    monkeypatch.setattr(bg, "get_broker", lambda: broker)
    monkeypatch.setattr(ks, "is_trading_allowed", lambda: allowed)
    monkeypatch.setattr(br, "_tag_close_reason", lambda *a, **k: None)
    monkeypatch.setattr(cs, "_retag_open_row", lambda t: None)
    monkeypatch.setattr(cs, "_sectors", lambda: {"NEW": "Tech", "OLD": "Energy"})
    monkeypatch.setattr("modules.alerter._send_telegram_message", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(cs, "build_plan", lambda b=None, asof=None: {
        "regime_bull": True, "satellite": ["NEW"], "targets": {"RSP": 0.7, "NEW": 0.3},
        "entries": {"OLD": 40.0},
        "orders": cs.plan_orders({"RSP": 0.7, "NEW": 0.3}, 10_000, {"OLD": 5.0},
                                 {"RSP": 200.0, "NEW": 100.0, "OLD": 50.0}),
    })
    return broker


def test_run_monthly_executes_once_per_month(monkeypatch, tmp_path):
    broker = _wire(monkeypatch, tmp_path)
    r = cs.run_monthly()
    assert r["month_done"] is True
    assert ("close", "OLD", "WIN") in broker.calls  # 50 > entrée 40
    opens = [c for c in broker.calls if c[0] == "open"]
    assert {c[1] for c in opens} == {"RSP", "NEW"}
    assert all(c[3] == "CORE_SATELLITE" and c[4] == 0.65 for c in opens)
    assert cs.run_monthly()["skipped"] == "déjà exécuté ce mois"


def test_run_monthly_killswitch_allows_only_derisking(monkeypatch, tmp_path):
    broker = _wire(monkeypatch, tmp_path, allowed=False)
    r = cs.run_monthly()
    assert {c[1] for c in broker.calls if c[0] == "open"} == {"RSP"}
    assert [s["ticker"] for s in r["skipped"]] == ["NEW"]


def test_run_monthly_market_closed_and_wrong_mode(monkeypatch, tmp_path):
    broker = _wire(monkeypatch, tmp_path)
    broker.market_status = lambda: {"is_open": False}
    assert cs.run_monthly()["skipped"] == "marché fermé" and broker.calls == []
    monkeypatch.setattr(config, "STRATEGY_MODE", "basket", raising=False)
    assert cs.run_monthly()["skipped"] == "STRATEGY_MODE != core_satellite"


def test_tracker_ignores_core_satellite_rows(monkeypatch):
    from modules.tracker import evaluation as ev
    df = pd.DataFrame([{"Ticker": "RSP", "Status": "OPEN", "Signal": "CORE_SATELLITE", "Entry": "200"}])
    out, closed, modified = ev.evaluate_trades(df)
    assert (closed, modified) == (0, 0) and out.loc[0, "Status"] == "OPEN"


def test_auto_approve_routes_to_core_satellite(monkeypatch):
    from modules import auto_approve as aa
    monkeypatch.setattr(aa, "STRATEGY_MODE", "core_satellite")
    monkeypatch.setattr(cs, "run_monthly", lambda: {"skipped": "marché fermé"})
    r = aa.run_auto_approve()
    assert r["mode"] == "core_satellite" and r["approved"] == 0
    assert r["core_satellite"]["skipped"] == "marché fermé"


def test_shadow_selector_profiles_rebalance_monthly():
    from modules import shadow_portfolios as sp
    state: dict = {}
    prices = {"RSP": 100.0, "A": 10.0, "B": 20.0}
    sel = {"rsp": lambda held: ["RSP"], "cs_momentum_12_1": lambda held: ["A", "B"]}
    rep = sp.step(state, {}, "2026-11-21", fresh_prices=prices, selectors=sel)
    assert rep["rsp"]["rebalanced"] and rep["cs_momentum_12_1"]["rebalanced"]
    assert set(state["profiles"]["cs_momentum_12_1"]["holdings"]) == {"A", "B"}
    rep2 = sp.step(state, {}, "2026-11-28", fresh_prices=prices, selectors=sel)
    assert rep2["rsp"]["rebalanced"] is False  # mensuel, pas hebdo
