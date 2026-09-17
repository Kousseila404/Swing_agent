"""Tests — améliorations post-audit (rebalance 1/N, stop de portefeuille,
shadow portfolios, attribution de l'écart, commandes Telegram)."""
from __future__ import annotations

import json

from modules import basket_rebalance, gap_attribution, portfolio_stop, shadow_portfolios, telegram_inbox

# ── rebalance ────────────────────────────────────────────────────

def test_compute_rebalance_targets_one_over_n_with_band_and_min_trade():
    positions = [
        {"ticker": "A", "qty": 100, "price": 10.0},   # 1000 $ = 40 %
        {"ticker": "B", "qty": 50, "price": 10.0},    # 500 $ = 20 %
        {"ticker": "C", "qty": 100, "price": 10.0},   # 1000 $ = 40 %
    ]
    orders = basket_rebalance.compute_rebalance(positions, band_pts=2.0, min_trade_usd=50)
    by = {o["ticker"]: o for o in orders}
    assert by["A"]["delta_qty"] == -17 and by["C"]["delta_qty"] == -17   # 2500/3 = 833 $ → 83 actions
    assert by["B"]["delta_qty"] == 33
    assert [o["delta_qty"] < 0 for o in orders] == [True, True, False]   # ventes d'abord


def test_compute_rebalance_skips_inside_band_and_small_trades():
    positions = [{"ticker": "A", "qty": 101, "price": 10.0}, {"ticker": "B", "qty": 99, "price": 10.0}]
    assert basket_rebalance.compute_rebalance(positions, band_pts=2.0) == []
    positions = [{"ticker": "A", "qty": 110, "price": 10.0}, {"ticker": "B", "qty": 90, "price": 10.0}]
    assert basket_rebalance.compute_rebalance(positions, band_pts=2.0, min_trade_usd=500) == []
    assert len(basket_rebalance.compute_rebalance(positions, band_pts=2.0, min_trade_usd=50)) == 2


# ── portfolio stop ───────────────────────────────────────────────

def test_portfolio_stop_evaluate():
    eq = [100_000, 104_000, 102_000, 90_000, 88_000, 87_000]
    r = portfolio_stop.evaluate(eq, window=60, dd_threshold_pct=-15.0)
    assert r["peak"] == 104_000 and r["dd_pct"] == round((87_000 / 104_000 - 1) * 100, 2)
    assert r["triggered"] is True
    assert portfolio_stop.evaluate(eq[:3], 60, -15.0)["triggered"] is False   # < 5 lectures


# ── shadow portfolios ────────────────────────────────────────────

def _scored(prices: dict[str, float], mom: dict[str, float] | None = None):
    mom = mom or {}
    return {t: {"current_price": p, "momentum_score": mom.get(t, 50.0), "quality_score": 50.0}
            for t, p in prices.items()}


def test_shadow_step_rebalances_weekly_and_marks_to_market(monkeypatch):
    monkeypatch.setattr(shadow_portfolios, "PROFILES", {"momentum_only": {"momentum_score": 1.0}, "universe_ew": {}})
    monkeypatch.setattr(shadow_portfolios, "TOP_N", 2)
    state: dict = {}
    scored = _scored({"A": 10.0, "B": 20.0, "C": 30.0}, {"A": 90, "B": 80, "C": 10})
    rep = shadow_portfolios.step(state, scored, "2026-09-01")
    p = state["profiles"]["momentum_only"]
    assert set(p["holdings"]) == {"A", "B"} and rep["momentum_only"]["rebalanced"] is True
    nav0 = p["nav"][-1][1]
    assert abs(nav0 - (100_000 - 100_000 * 0.001)) < 1.0        # 10 bps sur 100 % de turnover
    # J+1 : A +10 % → NAV monte, pas de rebalance
    rep = shadow_portfolios.step(state, _scored({"A": 11.0, "B": 20.0, "C": 30.0}), "2026-09-02")
    assert rep["momentum_only"]["rebalanced"] is False
    assert p["nav"][-1][1] > nav0
    assert len(state["profiles"]["universe_ew"]["holdings"]) == 3
    s = shadow_portfolios.summary(state)
    assert {r["profile"] for r in s["rows"]} == {"momentum_only", "universe_ew"}
    assert s["rows"][0]["days"] == 2


# ── gap attribution ──────────────────────────────────────────────

def test_gap_attribution_components_sum_to_gap():
    dates = ["2026-05-01", "2026-05-02", "2026-05-05", "2026-05-06"]
    acct = [100.0, 100.5, 101.0, 101.0]
    basket = [("2026-05-01", 100.0), ("2026-05-06", 104.0)]
    journal = [
        {"Date": "2026-05-01 10:00:00", "Exit_Date": "", "Ticker": "A", "Size": 100, "Entry": 100, "Status": "OPEN", "Slippage_Bps": "10"},
        {"Date": "2026-04-20 10:00:00", "Exit_Date": "2026-05-02 10:00", "Ticker": "B", "Size": 50, "Entry": 100,
         "Exit_Price": 90, "Status": "LOSS", "Close_Reason": "SL_HIT"},
    ]
    eq = {d: 100_000.0 for d in dates}
    res = gap_attribution.attribute(dates, acct, basket, journal, eq, lambda t, d: 100.0)
    assert res["available"]
    c = res["components"]
    assert abs(sum(c.values()) - res["gap_pct"]) < 1e-6
    assert c["cash_drag_pct"] < 0 and c["slippage_pct"] < 0 and c["stops_pct"] < 0
    assert res["details"]["n_stop_exits"] == 1


# ── telegram inbox ───────────────────────────────────────────────

def test_telegram_help_and_unknown():
    assert "/approve" in telegram_inbox.handle_command("/help")
    assert "inconnue" in telegram_inbox.handle_command("/foo")


def test_telegram_approve_requires_pending(monkeypatch):
    monkeypatch.setattr(telegram_inbox.proposals, "list_all", lambda status=None: [])
    assert "Aucune proposition" in telegram_inbox.handle_command("/approve MU")
    assert "Usage" in telegram_inbox.handle_command("/approve")


def test_telegram_approve_calls_api(monkeypatch):
    monkeypatch.setattr(telegram_inbox.proposals, "list_all", lambda status=None: [{"id": "P1", "ticker": "MU"}])
    calls = []
    monkeypatch.setattr(telegram_inbox, "_api", lambda m, p, **kw: calls.append((m, p, kw)) or {"results": [{"ok": True, "message": "exécuté"}]})
    out = telegram_inbox.handle_command("/approve mu")
    assert out.startswith("✅") and calls[0][1] == "/api/proposals/approve_batch"
    assert json.dumps(calls[0][2]["json"]).count("P1") == 1
