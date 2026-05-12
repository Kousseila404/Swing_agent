"""Tests gates auto_proposer — chaque gate testée isolément + intégration.

Stratégie : on monkey-patche les helpers que chaque gate consomme (killswitch,
CB state, macro JSON, universe JSON, scored universe…). On vérifie que :
  - une gate KO bloque la chaîne (proposals=[], ok=False).
  - les gates KO retournent un détail explicite (utilisable pour l'UI).
  - quand toutes les gates passent, on passe à l'allocation.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from modules import api_core, auto_proposer, proposals


@pytest.fixture(autouse=True)
def _no_network_correlation(monkeypatch):
    """Audit 2026-05-12 — correlation_check fait un appel yfinance.
    Pour les tests gates (mock allocations), on no-op le module pour éviter
    des timeouts réseau. Les tests dédiés au correlation_check restent intacts.
    """
    from modules import correlation_check as _cc
    from modules.correlation_check import CorrelationResult
    monkeypatch.setattr(
        _cc, "compute_correlation",
        lambda *a, **kw: CorrelationResult(
            applied=False, n_tickers=len(a[0]) if a else 0,
            reason="test_mock_no_network",
        ),
    )
    # Patch aussi le symbole importé dans auto_proposer (déjà importé en local).
    monkeypatch.setattr(
        "modules.auto_proposer.compute_correlation",
        lambda *a, **kw: CorrelationResult(
            applied=False, n_tickers=len(a[0]) if a else 0,
            reason="test_mock_no_network",
        ),
        raising=False,
    )


@pytest.fixture
def isolated_proposals(tmp_path, monkeypatch):
    monkeypatch.setattr(proposals, "PROPOSALS_PATH", tmp_path / "proposals.json")
    monkeypatch.setattr(proposals, "PROPOSALS_LOCK_PATH", tmp_path / "proposals.json.lock")
    monkeypatch.setattr(proposals, "PROPOSALS_AUDIT_PATH", tmp_path / "proposals_audit.jsonl")


@pytest.fixture
def fresh_universe_json(tmp_path, monkeypatch):
    """Crée un universe.json updated_at=now (pas stale)."""
    p = tmp_path / "universe.json"
    p.write_text(json.dumps({
        "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "tickers": [],
    }))
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", p)
    return p


@pytest.fixture
def bull_macro(monkeypatch):
    """load_macro renvoie BULL_MARKET / VIX bas."""
    monkeypatch.setattr(api_core, "load_macro",
                        lambda: {"confirmed_regime": "BULL_MARKET", "vix": 17.0})


@pytest.fixture
def trading_allowed(monkeypatch):
    from modules.tracker import killswitch
    monkeypatch.setattr(killswitch, "is_trading_allowed", lambda: True)


@pytest.fixture
def cb_clean(monkeypatch):
    from modules.tracker import state
    monkeypatch.setattr(state, "read_circuit_breaker_state",
                        lambda: {"is_paused": False, "size_multiplier": 1.0, "peak_equity": 100_000})


@pytest.fixture
def empty_portfolio(monkeypatch):
    """Aucune position OPEN — slots tous libres, budget total dispo."""
    import modules.portfolio as portfolio_pkg
    monkeypatch.setattr(portfolio_pkg, "read_open_positions", lambda: [])


# ─────────────────────────────────────────────────────────────────
# Gate-by-gate KO
# ─────────────────────────────────────────────────────────────────

def test_killswitch_blocks(monkeypatch, isolated_proposals, bull_macro,
                            cb_clean, fresh_universe_json, empty_portfolio):
    from modules.tracker import killswitch
    monkeypatch.setattr(killswitch, "is_trading_allowed", lambda: False)
    result = auto_proposer.plan_proposals()
    assert result.ok is False
    assert result.proposals == []
    g = next(g for g in result.gates if g.name == "killswitch")
    assert g.ok is False
    assert "TRADING_BLOCKED" in g.detail


def test_circuit_breaker_paused_blocks(monkeypatch, isolated_proposals,
                                        trading_allowed, bull_macro,
                                        fresh_universe_json, empty_portfolio):
    from modules.tracker import state
    monkeypatch.setattr(state, "read_circuit_breaker_state",
                        lambda: {"is_paused": True, "size_multiplier": 0.0, "peak_equity": 100_000})
    result = auto_proposer.plan_proposals()
    assert result.ok is False
    g = next(g for g in result.gates if g.name == "circuit_breaker")
    assert g.ok is False


def test_regime_unknown_blocks(monkeypatch, isolated_proposals,
                                trading_allowed, cb_clean,
                                fresh_universe_json, empty_portfolio):
    monkeypatch.setattr(api_core, "load_macro", lambda: {})
    result = auto_proposer.plan_proposals()
    assert result.ok is False
    g = next(g for g in result.gates if g.name == "macro_regime")
    assert g.ok is False
    assert "inconnu" in g.detail


def test_regime_not_in_allowed_blocks(monkeypatch, isolated_proposals,
                                       trading_allowed, cb_clean,
                                       fresh_universe_json, empty_portfolio):
    monkeypatch.setattr(api_core, "load_macro",
                        lambda: {"confirmed_regime": "BEAR_MARKET", "vix": 30.0})
    result = auto_proposer.plan_proposals(allowed_regimes=("BULL_MARKET",))
    assert result.ok is False
    g = next(g for g in result.gates if g.name == "macro_regime")
    assert "BEAR_MARKET" in g.detail


def test_regime_multiplier_zero_blocks(monkeypatch, isolated_proposals,
                                        trading_allowed, cb_clean,
                                        fresh_universe_json, empty_portfolio):
    # CRASH_PANIC accepté côté allowed_regimes mais multiplier=0 doit bloquer
    monkeypatch.setattr(api_core, "load_macro",
                        lambda: {"confirmed_regime": "CRASH_PANIC", "vix": 50.0})
    result = auto_proposer.plan_proposals(allowed_regimes=("CRASH_PANIC",))
    assert result.ok is False
    g = next(g for g in result.gates if g.name == "regime_multiplier")
    assert g.ok is False


def test_universe_severe_stale_blocks(tmp_path, monkeypatch, isolated_proposals,
                                       trading_allowed, cb_clean, bull_macro,
                                       empty_portfolio):
    p = tmp_path / "universe.json"
    very_old = datetime.now(UTC) - timedelta(hours=72)
    p.write_text(json.dumps({
        "updated_at": very_old.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }))
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", p)
    result = auto_proposer.plan_proposals()
    assert result.ok is False
    g = next(g for g in result.gates if g.name == "universe_freshness")
    assert "stale severe" in g.detail


def test_no_free_slots_blocks(monkeypatch, isolated_proposals, trading_allowed,
                               cb_clean, bull_macro, fresh_universe_json):
    import modules.portfolio as portfolio_pkg
    # 20 positions OPEN → 0 slot libre vs max_holdings=20
    fake_positions = [
        {"ticker": f"T{i}", "size": 10, "entry": 100, "sector": "Technology",
         "notional_usd": 1000, "current_price": None, "unrealized_pnl": None,
         "direction": "LONG", "order_id": ""}
        for i in range(20)
    ]
    monkeypatch.setattr(portfolio_pkg, "read_open_positions", lambda: fake_positions)
    result = auto_proposer.plan_proposals(max_holdings=20, min_free_slots=1)
    assert result.ok is False
    g = next(g for g in result.gates if g.name == "free_slots")
    assert "0/20 libres" in g.detail


def test_cash_below_minimum_blocks(monkeypatch, isolated_proposals,
                                    trading_allowed, cb_clean, bull_macro,
                                    fresh_universe_json):
    import modules.portfolio as portfolio_pkg
    # 1 position OPEN qui a quasi tout brûlé → budget < min_proposal_usd
    fake = [{"ticker": "AAPL", "size": 999, "entry": 100, "sector": "Technology",
             "notional_usd": 99_900, "current_price": None, "unrealized_pnl": None,
             "direction": "LONG", "order_id": ""}]
    monkeypatch.setattr(portfolio_pkg, "read_open_positions", lambda: fake)
    result = auto_proposer.plan_proposals(
        total_capital=100_000, min_proposal_usd=500,
    )
    assert result.ok is False
    g = next(g for g in result.gates if g.name == "cash_available")
    assert "min" in g.detail


# ─────────────────────────────────────────────────────────────────
# Happy path — toutes gates OK + scored universe vide → ok=True, 0 props
# ─────────────────────────────────────────────────────────────────

def test_all_gates_pass_empty_universe_returns_ok_no_proposals(
    monkeypatch, isolated_proposals, trading_allowed, cb_clean, bull_macro,
    fresh_universe_json, empty_portfolio,
):
    """Toutes gates OK mais get_scored_universe vide → ok=True, proposals=[].

    On vérifie qu'on arrive bien jusqu'à l'allocation (gate scored_universe).
    """
    from modules import sector_metrics
    monkeypatch.setattr(sector_metrics, "get_scored_universe", lambda: {})
    result = auto_proposer.plan_proposals()
    # ok=False car scored_universe vide est traité comme une gate KO
    assert result.proposals == []
    g = next((g for g in result.gates if g.name == "scored_universe"), None)
    assert g is not None
    assert g.ok is False
    # Toutes les gates précédentes doivent être OK
    for name in ("killswitch", "circuit_breaker", "macro_regime",
                 "regime_multiplier", "universe_freshness", "free_slots",
                 "cash_available"):
        gx = next(g for g in result.gates if g.name == name)
        assert gx.ok is True, f"Gate {name} a échoué : {gx.detail}"


# ─────────────────────────────────────────────────────────────────
# Nouveaux flags : include_held, top_n_mode, over_cap exposé
# ─────────────────────────────────────────────────────────────────

def _fake_allocations(tickers):
    """Construit un AllocationResult mock avec N tickers sector Tech."""
    class Result:
        def __init__(self):
            self.allocations = {
                t: {
                    "sector": "Technology", "price": 100.0, "shares": 10,
                    "amount_usd": 1000.0, "titan_score": 90.0 - i,
                    "volatility_pct": 0.25, "momentum_pct": 0.1,
                    "weight_pct": 5.0,
                } for i, t in enumerate(tickers)
            }
            self.invested_usd = 1000.0 * len(tickers)
            self.n_candidates = len(tickers)
            self.n_bullish = len(tickers)
            self.n_kept = len(tickers)
            self.equal_weight_fallback = False
            self.diagnostics = {"weight_method": "inv_vol"}
    return Result()


def test_include_held_flag_keeps_held_tickers(
    monkeypatch, isolated_proposals, trading_allowed, cb_clean, bull_macro,
    fresh_universe_json,
):
    """include_held=True → un ticker déjà OPEN reste dans proposals avec
    context.already_held=True au lieu d'être skippé."""
    import modules.portfolio as portfolio_pkg
    monkeypatch.setattr(portfolio_pkg, "read_open_positions", lambda: [
        {"ticker": "AAPL", "size": 5, "entry": 100, "sector": "Technology",
         "notional_usd": 500, "current_price": None, "unrealized_pnl": None,
         "direction": "LONG", "order_id": ""},
    ])

    from modules import sector_metrics
    monkeypatch.setattr(sector_metrics, "get_scored_universe", lambda: {
        "AAPL": {"sector": "Technology"},
        "MSFT": {"sector": "Technology"},
    })

    from modules.portfolio_engine import PortfolioManager
    monkeypatch.setattr(
        PortfolioManager, "calculate_allocations",
        lambda *a, **kw: _fake_allocations(["AAPL", "MSFT"]),
    )

    # Default (include_held=False) → skip AAPL
    r1 = auto_proposer.plan_proposals(max_holdings=20, min_proposal_usd=100)
    tickers_r1 = [p["ticker"] for p in r1.proposals]
    assert "AAPL" not in tickers_r1
    assert "MSFT" in tickers_r1

    # include_held=True → AAPL reste, flagué already_held
    r2 = auto_proposer.plan_proposals(
        max_holdings=20, min_proposal_usd=100, include_held=True,
    )
    tickers_r2 = [p["ticker"] for p in r2.proposals]
    assert "AAPL" in tickers_r2
    aapl_prop = next(p for p in r2.proposals if p["ticker"] == "AAPL")
    assert aapl_prop["context"]["already_held"] is True
    assert aapl_prop["context"]["current_shares"] == 5


def test_over_cap_ticker_is_exposed_not_filtered(
    monkeypatch, isolated_proposals, trading_allowed, cb_clean, bull_macro,
    fresh_universe_json, empty_portfolio,
):
    """Un ticker en sur-exposition secteur reste dans proposals avec flag
    over_cap=True — ne doit plus être filtré en silence."""
    from modules import sector_metrics
    # 5 tickers tous Technology → projected sector pct sera ~100% > cap 30%
    tickers = ["A", "B", "C", "D", "E"]
    monkeypatch.setattr(sector_metrics, "get_scored_universe", lambda: {
        t: {"sector": "Technology"} for t in tickers
    })

    from modules.portfolio_engine import PortfolioManager
    # Fabrique des allocs énormes (5 × 10_000 = 50k sur 100k total).
    class BigResult:
        def __init__(self):
            self.allocations = {
                t: {
                    "sector": "Technology", "price": 100.0, "shares": 100,
                    "amount_usd": 10_000.0, "titan_score": 90.0 - i,
                    "volatility_pct": 0.25, "momentum_pct": 0.1, "weight_pct": 20.0,
                } for i, t in enumerate(tickers)
            }
            self.invested_usd = 50_000.0
            self.n_candidates = 5
            self.n_bullish = 5
            self.n_kept = 5
            self.equal_weight_fallback = False
            self.diagnostics = {"weight_method": "inv_vol"}
    monkeypatch.setattr(
        PortfolioManager, "calculate_allocations",
        lambda *a, **kw: BigResult(),
    )

    r = auto_proposer.plan_proposals(max_holdings=20, min_proposal_usd=100)
    # Aucune proposition filtrée pour over_cap — toutes présentes.
    assert len(r.proposals) == 5
    # Les propositions hors du premier burst doivent avoir over_cap=True
    # (le 1er ticker peut passer sous le cap selon calcul — on vérifie juste
    # qu'au moins un est flaggé).
    over_cap_count = sum(
        1 for p in r.proposals
        if (p["context"].get("sector_exposure") or {}).get("over_cap")
    )
    assert over_cap_count >= 1


def test_top_n_mode_max_holdings_exceeds_free_slots(
    monkeypatch, isolated_proposals, trading_allowed, cb_clean, bull_macro,
    fresh_universe_json,
):
    """top_n_mode=max_holdings → on remplit jusqu'à max_holdings tickers
    même si free_slots est bas (rebalance complet pour l'UI)."""
    import modules.portfolio as portfolio_pkg
    # 5 positions OPEN → free_slots = 15 en mode cron.
    monkeypatch.setattr(portfolio_pkg, "read_open_positions", lambda: [
        {"ticker": f"OPEN{i}", "size": 1, "entry": 100, "sector": "Health Care",
         "notional_usd": 100, "current_price": None, "unrealized_pnl": None,
         "direction": "LONG", "order_id": ""}
        for i in range(5)
    ])

    from modules import sector_metrics
    tickers = [f"NEW{i}" for i in range(20)]
    monkeypatch.setattr(sector_metrics, "get_scored_universe", lambda: {
        t: {"sector": "Industrials"} for t in tickers
    })

    from modules.portfolio_engine import PortfolioManager
    monkeypatch.setattr(
        PortfolioManager, "calculate_allocations",
        lambda *a, **kw: _fake_allocations(tickers),
    )

    # Mode default "free_slots" → cap à 15 (20 - 5).
    r_default = auto_proposer.plan_proposals(
        max_holdings=20, min_proposal_usd=100,
    )
    assert len(r_default.proposals) == 15

    # Mode "max_holdings" → cap à 20 (tout).
    r_max = auto_proposer.plan_proposals(
        max_holdings=20, min_proposal_usd=100, top_n_mode="max_holdings",
    )
    assert len(r_max.proposals) == 20
