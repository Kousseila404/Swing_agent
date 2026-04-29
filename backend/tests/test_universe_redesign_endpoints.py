"""Tests des 5 nouveaux endpoints livrés par la refonte page Univers (2026-04-29).

Cible :
  - POST /api/proposals/manual    (Tier S #3 — push hors cron)
  - POST /api/backtest/quick      (Tier B #1 — backtest sur whitelist)
  - GET  /api/titan_alerts        (Tier B #3 — list)
  - POST /api/titan_alerts        (Tier B #3 — create + dédup)
  - DELETE /api/titan_alerts/{id} (Tier B #3 — remove + 404)

Auth : tous les POST/DELETE doivent renvoyer 401/403 sans token.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api
from modules import api_core, proposals, titan_alerts


# ─────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.fixture
def auth_token(monkeypatch):
    monkeypatch.setattr(api_core, "API_TOKEN", "test-token")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", False)
    return "test-token"


@pytest.fixture
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def isolated_alerts(tmp_path, monkeypatch):
    """Redirige le storage titan_alerts.json vers un tmp_path."""
    monkeypatch.setattr(titan_alerts, "ALERTS_PATH", tmp_path / "titan_alerts.json")
    monkeypatch.setattr(titan_alerts, "ALERTS_LOCK_PATH", tmp_path / "titan_alerts.json.lock")
    return tmp_path


@pytest.fixture
def isolated_proposals(tmp_path, monkeypatch):
    """Redirige proposals storage pour ne pas polluer le fichier prod."""
    monkeypatch.setattr(proposals, "PROPOSALS_PATH", tmp_path / "proposals.json")
    monkeypatch.setattr(proposals, "PROPOSALS_LOCK_PATH", tmp_path / "proposals.json.lock")
    monkeypatch.setattr(proposals, "PROPOSALS_AUDIT_PATH", tmp_path / "proposals_audit.jsonl")
    return tmp_path


# ─────────────────────────────────────────────────────────────────
# /api/proposals/manual
# ─────────────────────────────────────────────────────────────────

class TestProposalsManual:

    def test_requires_auth(self, client):
        r = client.post("/api/proposals/manual", json={"ticker": "AAPL"})
        assert r.status_code in (401, 403)

    def test_404_when_ticker_absent_from_universe(
        self, client, auth_headers, isolated_proposals, monkeypatch,
    ):
        # Mock un univers scoré vide → 404 sur tout ticker.
        from modules import sector_metrics
        monkeypatch.setattr(sector_metrics, "get_scored_universe", lambda: {})
        r = client.post(
            "/api/proposals/manual",
            json={"ticker": "ZZZZ"},
            headers=auth_headers,
        )
        assert r.status_code == 404
        assert "absent" in r.json()["detail"].lower()

    def test_happy_path_creates_proposal(
        self, client, auth_headers, isolated_proposals, monkeypatch,
    ):
        # Mock un univers scoré avec un ticker complet.
        from modules import sector_metrics
        monkeypatch.setattr(sector_metrics, "get_scored_universe", lambda: {
            "AAPL": {
                "current_price": 180.0,
                "volatility_pct": 25.0,
                "sector": "Technology",
                "titan_composite_score": 82.5,
                "quality_score": 85,
                "value_score": 60,
                "f_score": 8,
                "f_score_max": 9,
            },
        })
        # Désactive cooldowns pour permettre l'enqueue.
        monkeypatch.setenv("VETO_COOLDOWN_DAYS", "0")
        monkeypatch.setenv("WIN_COOLDOWN_DAYS", "0")
        r = client.post(
            "/api/proposals/manual",
            json={"ticker": "aapl"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["proposal"]["ticker"] == "AAPL"
        assert body["proposal"]["sector"] == "Technology"
        assert body["proposal"]["context"]["titan_score"] == 82.5
        assert body["proposal"]["context"]["manual"] is True
        # SL < entry < TP (sanity garantie par suggest_trade_levels).
        assert body["proposal"]["stop_loss"] < body["proposal"]["entry"] < body["proposal"]["take_profit"]

    def test_dedup_returns_ok_false_on_second_push(
        self, client, auth_headers, isolated_proposals, monkeypatch,
    ):
        from modules import sector_metrics
        monkeypatch.setattr(sector_metrics, "get_scored_universe", lambda: {
            "MSFT": {"current_price": 400.0, "volatility_pct": 20.0, "sector": "Tech",
                     "titan_composite_score": 75.0},
        })
        monkeypatch.setenv("VETO_COOLDOWN_DAYS", "0")
        monkeypatch.setenv("WIN_COOLDOWN_DAYS", "0")
        r1 = client.post("/api/proposals/manual", json={"ticker": "MSFT"}, headers=auth_headers)
        assert r1.status_code == 200 and r1.json()["ok"] is True
        # 2e push immédiat → pending existe déjà, dédup silencieux.
        r2 = client.post("/api/proposals/manual", json={"ticker": "MSFT"}, headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["ok"] is False
        assert r2.json()["reason"] == "dedup_or_cooldown"

    def test_invalid_ticker_400(self, client, auth_headers, isolated_proposals):
        r = client.post(
            "/api/proposals/manual",
            json={"ticker": "TOOLONGTICKER123"},
            headers=auth_headers,
        )
        assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────
# /api/backtest/quick
# ─────────────────────────────────────────────────────────────────

class TestBacktestQuick:

    def test_requires_auth(self, client):
        r = client.post("/api/backtest/quick", json={"tickers": ["A", "B", "C"]})
        assert r.status_code in (401, 403)

    def test_422_when_whitelist_too_small(self, client, auth_headers):
        r = client.post(
            "/api/backtest/quick",
            json={"tickers": ["AAPL", "MSFT"], "top_n": 10},
            headers=auth_headers,
        )
        assert r.status_code == 422
        assert "trop petite" in r.json()["detail"].lower()

    def test_top_n_validation_via_pydantic(self, client, auth_headers):
        # top_n hors bornes (max=30) → 422 Pydantic.
        r = client.post(
            "/api/backtest/quick",
            json={"tickers": ["A"] * 50, "top_n": 999},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_happy_path_returns_stats(self, client, auth_headers, monkeypatch):
        # Mock backtest.run_titan_top_n pour ne pas dépendre de l'historique réel.
        from modules import backtest as bt_mod

        captured = {}

        def fake_run(top_n, benchmark, *, weighting, restrict_to, **_kwargs):
            captured["restrict_to"] = restrict_to
            captured["top_n"] = top_n
            # Vrai BacktestResult — asdict() requiert un dataclass.
            return bt_mod.BacktestResult(
                strategy="titan_top_n",
                top_n=top_n,
                periods=[],
                equity_curve=[("2026-01-01", 1.0), ("2026-04-01", 1.05)],
                total_return=0.05,
                avg_daily_return=0.001,
                sharpe_daily=0.15,
                sharpe_annual=1.2,
                max_drawdown=0.03,
                hit_rate=0.55,
                benchmark_return=0.02,
                alpha=0.03,
            )

        monkeypatch.setattr(bt_mod, "run_titan_top_n", fake_run)

        r = client.post(
            "/api/backtest/quick",
            json={"tickers": ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA"], "top_n": 3},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # restrict_to doit avoir été passé en upper-case strip.
        assert set(captured["restrict_to"]) == {"AAPL", "MSFT", "GOOGL", "NVDA", "TSLA"}
        assert captured["top_n"] == 3
        assert body["stats"]["total_return"] == 0.05
        assert body["meta"]["n_tickers_input"] == 5
        assert body["meta"]["top_n"] == 3


# ─────────────────────────────────────────────────────────────────
# /api/titan_alerts
# ─────────────────────────────────────────────────────────────────

class TestTitanAlerts:

    def test_get_empty_initially(self, client, isolated_alerts):
        r = client.get("/api/titan_alerts")
        assert r.status_code == 200
        assert r.json() == {"items": [], "count": 0}

    def test_post_requires_auth(self, client, isolated_alerts):
        r = client.post(
            "/api/titan_alerts",
            json={"ticker": "AAPL", "direction": "above", "threshold": 80},
        )
        assert r.status_code in (401, 403)

    def test_post_creates_alert(self, client, auth_headers, isolated_alerts):
        r = client.post(
            "/api/titan_alerts",
            json={"ticker": "aapl", "direction": "above", "threshold": 75.5},
            headers=auth_headers,
        )
        assert r.status_code == 200
        a = r.json()["alert"]
        assert a["ticker"] == "AAPL"
        assert a["direction"] == "above"
        assert a["threshold"] == 75.5
        assert a["fire_count"] == 0
        assert a["id"].startswith("ALT_")
        # GET doit la retourner.
        r2 = client.get("/api/titan_alerts")
        assert r2.json()["count"] == 1

    def test_post_idempotent_on_duplicate(self, client, auth_headers, isolated_alerts):
        body = {"ticker": "MSFT", "direction": "below", "threshold": 70}
        r1 = client.post("/api/titan_alerts", json=body, headers=auth_headers)
        r2 = client.post("/api/titan_alerts", json=body, headers=auth_headers)
        assert r1.json()["alert"]["id"] == r2.json()["alert"]["id"]
        assert client.get("/api/titan_alerts").json()["count"] == 1

    def test_post_400_on_invalid_threshold(self, client, auth_headers, isolated_alerts):
        # Pydantic ge=0 le=100 → 422 (validation FastAPI native).
        r = client.post(
            "/api/titan_alerts",
            json={"ticker": "A", "direction": "above", "threshold": 999},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_post_400_on_invalid_direction(self, client, auth_headers, isolated_alerts):
        r = client.post(
            "/api/titan_alerts",
            json={"ticker": "A", "direction": "diagonal", "threshold": 80},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_delete_existing_alert(self, client, auth_headers, isolated_alerts):
        r1 = client.post(
            "/api/titan_alerts",
            json={"ticker": "TSLA", "direction": "above", "threshold": 60},
            headers=auth_headers,
        )
        alert_id = r1.json()["alert"]["id"]
        r2 = client.delete(f"/api/titan_alerts/{alert_id}", headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["removed"] is True
        # Liste vide ensuite.
        assert client.get("/api/titan_alerts").json()["count"] == 0

    def test_delete_404_when_unknown(self, client, auth_headers, isolated_alerts):
        r = client.delete("/api/titan_alerts/ALT_DEADBEEF", headers=auth_headers)
        assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────
# titan_alerts.evaluate_alerts — logique core
# ─────────────────────────────────────────────────────────────────

class TestEvaluateAlerts:

    def test_above_triggered_when_threshold_crossed(self, isolated_alerts):
        titan_alerts.add_alert(ticker="AAPL", direction="above", threshold=80.0)
        triggered = titan_alerts.evaluate_alerts({
            "AAPL": {"titan_composite_score": 82.5},
        })
        assert len(triggered) == 1
        assert triggered[0]["ticker"] == "AAPL"
        assert triggered[0]["current_titan"] == 82.5

    def test_above_not_triggered_below_threshold(self, isolated_alerts):
        titan_alerts.add_alert(ticker="AAPL", direction="above", threshold=80.0)
        triggered = titan_alerts.evaluate_alerts({
            "AAPL": {"titan_composite_score": 70.0},
        })
        assert triggered == []

    def test_below_triggered_when_score_drops(self, isolated_alerts):
        titan_alerts.add_alert(ticker="MSFT", direction="below", threshold=60.0)
        triggered = titan_alerts.evaluate_alerts({
            "MSFT": {"titan_composite_score": 55.0},
        })
        assert len(triggered) == 1
        assert triggered[0]["direction"] == "below"

    def test_cooldown_prevents_immediate_refire(self, isolated_alerts):
        titan_alerts.add_alert(ticker="AAPL", direction="above", threshold=80.0)
        scored = {"AAPL": {"titan_composite_score": 85.0}}
        first = titan_alerts.evaluate_alerts(scored)
        assert len(first) == 1
        # Refire immédiat → cooldown 24h actif.
        second = titan_alerts.evaluate_alerts(scored)
        assert second == []

    def test_fire_count_increments(self, isolated_alerts):
        titan_alerts.add_alert(ticker="AAPL", direction="above", threshold=80.0)
        titan_alerts.evaluate_alerts({"AAPL": {"titan_composite_score": 85.0}})
        items = titan_alerts.list_alerts()
        assert items[0]["fire_count"] == 1
        assert items[0]["last_fired_at"] is not None
