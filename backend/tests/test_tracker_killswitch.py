"""Tests unitaires — modules/tracker/killswitch.py

Couvre :
  - estimate_portfolio_equity : LONG/SHORT/mixed, trades corrompus ignorés,
    positions OPEN valorisées via get_current_price (pas Exit_Price)
  - get_starting_equity : reset nouveau jour, lecture du jour courant
  - is_trading_allowed : pas de fichier, blocked hier (auto-reset), blocked aujourd'hui
  - check_daily_drawdown : drawdown < seuil vs ≥ seuil
  - emergency_liquidate_all : positions OPEN fermées + blocked=True
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from modules.tracker import killswitch, state


@pytest.fixture(autouse=True)
def _isolate_paths(monkeypatch, tmp_path: Path):
    """Redirige toutes les paths persistantes vers tmp_path.

    Force BROKER_MODE=paper : la branche broker.get_account_equity (Audit S2.4)
    n'est exercée que par tests dédiés ; les tests d'arithmétique CSV pure
    doivent rester déterministes même quand le runner pointe sur un compte
    Alpaca paper réel.
    """
    import config as _config
    equity = tmp_path / "equity_state.json"
    trading = tmp_path / "trading_state.json"
    monkeypatch.setattr(state, "EQUITY_STATE_PATH", equity)
    monkeypatch.setattr(state, "TRADING_STATE_PATH", trading)
    monkeypatch.setattr(killswitch, "EQUITY_STATE_PATH", equity)
    monkeypatch.setattr(killswitch, "TRADING_STATE_PATH", trading)
    monkeypatch.setattr(_config, "BROKER_MODE", "paper", raising=False)
    return tmp_path


def _df(*rows: dict) -> pd.DataFrame:
    """Construit un DataFrame CSV-like avec les colonnes minimales attendues."""
    base_cols = ["Ticker", "Direction", "Entry", "Status", "Size", "Exit_Price",
                 "Stop_Loss", "Take_Profit", "Date"]
    if not rows:
        return pd.DataFrame(columns=base_cols)
    return pd.DataFrame([{**{c: "" for c in base_cols}, **r} for r in rows])


# ─────────────────────────────────────────────────────────────────
# estimate_portfolio_equity
# ─────────────────────────────────────────────────────────────────

def test_estimate_equity_empty_journal():
    """DataFrame vide → base_equity (100k par défaut)."""
    equity = killswitch.estimate_portfolio_equity(_df())
    assert equity == pytest.approx(100_000)


def test_estimate_equity_long_win():
    """LONG +5$ × 10 = +50 → 100_050."""
    df = _df({"Ticker": "AAPL", "Direction": "LONG", "Entry": 100, "Size": 10,
              "Exit_Price": 105, "Status": "WIN"})
    equity = killswitch.estimate_portfolio_equity(df)
    assert equity == pytest.approx(100_050)


def test_estimate_equity_short_win():
    """SHORT (entry-exit)×size : 200→190, size=5 → +50 → 100_050."""
    df = _df({"Ticker": "TSLA", "Direction": "SHORT", "Entry": 200, "Size": 5,
              "Exit_Price": 190, "Status": "WIN"})
    equity = killswitch.estimate_portfolio_equity(df)
    assert equity == pytest.approx(100_050)


def test_estimate_equity_mixed_with_corrupted_row():
    """Trades valides comptés + ligne corrompue ignorée (sans crash)."""
    df = _df(
        {"Ticker": "AAPL", "Direction": "LONG", "Entry": 100, "Size": 10,
         "Exit_Price": 110, "Status": "WIN"},
        {"Ticker": "BAD",  "Direction": "LONG", "Entry": "not_a_number",
         "Size": 1, "Exit_Price": 50, "Status": "LOSS"},
        {"Ticker": "MSFT", "Direction": "LONG", "Entry": 400, "Size": 2,
         "Exit_Price": 390, "Status": "LOSS"},
    )
    equity = killswitch.estimate_portfolio_equity(df)
    # +100 (AAPL WIN) - 20 (MSFT LOSS) = +80 → 100_080
    assert equity == pytest.approx(100_080)


def test_estimate_equity_open_uses_live_price_not_exit_price(monkeypatch):
    """Audit 2026-08-14 — régression : les positions OPEN ont Exit_Price vide
    par construction (rempli seulement à la clôture). La fonction doit
    utiliser `get_current_price`, pas `Exit_Price`, pour le PnL latent —
    sinon le killswitch/circuit breaker ignore silencieusement tout PnL
    latent dans le fallback CSV (exercé quand l'API broker est down)."""
    monkeypatch.setattr(killswitch, "get_current_price", lambda _t: 110.0)
    df = _df({"Ticker": "AAPL", "Direction": "LONG", "Entry": 100, "Size": 10,
              "Exit_Price": "", "Status": "OPEN"})
    equity = killswitch.estimate_portfolio_equity(df)
    # +10$ × 10 = +100 → 100_100 (pas 100_000 comme avant le fix)
    assert equity == pytest.approx(100_100)


def test_estimate_equity_open_skips_when_live_price_unavailable(monkeypatch):
    """Fail-open : si `get_current_price` échoue (None), la position est
    ignorée plutôt que de crasher — cohérent avec `save_equity_snapshot`."""
    monkeypatch.setattr(killswitch, "get_current_price", lambda _t: None)
    df = _df({"Ticker": "AAPL", "Direction": "LONG", "Entry": 100, "Size": 10,
              "Exit_Price": "", "Status": "OPEN"})
    equity = killswitch.estimate_portfolio_equity(df)
    assert equity == pytest.approx(100_000)


def test_estimate_equity_open_dedupes_duplicate_ticker_rows(monkeypatch):
    """Deux lignes OPEN pour le même ticker (ex. top-up) ne comptent le PnL
    latent qu'une fois — cohérent avec `save_equity_snapshot`."""
    monkeypatch.setattr(killswitch, "get_current_price", lambda _t: 110.0)
    df = _df(
        {"Ticker": "AAPL", "Direction": "LONG", "Entry": 100, "Size": 10,
         "Exit_Price": "", "Status": "OPEN"},
        {"Ticker": "AAPL", "Direction": "LONG", "Entry": 100, "Size": 10,
         "Exit_Price": "", "Status": "OPEN"},
    )
    equity = killswitch.estimate_portfolio_equity(df)
    assert equity == pytest.approx(100_100)


# ─────────────────────────────────────────────────────────────────
# get_starting_equity
# ─────────────────────────────────────────────────────────────────

def test_get_starting_equity_new_day_resets(_isolate_paths: Path):
    """Premier appel du jour → current_equity devient starting."""
    starting = killswitch.get_starting_equity(current_equity=102_500)
    assert starting == 102_500
    persisted = json.loads((_isolate_paths / "equity_state.json").read_text())
    assert persisted["starting_equity"] == 102_500
    assert persisted["date"] == date.today().isoformat()


def test_get_starting_equity_same_day_reuses(_isolate_paths: Path):
    """Si date stockée = aujourd'hui, ne resette pas."""
    (_isolate_paths / "equity_state.json").write_text(json.dumps({
        "starting_equity": 98_000, "date": date.today().isoformat(),
    }))
    starting = killswitch.get_starting_equity(current_equity=95_000)
    assert starting == 98_000  # pas écrasé


# ─────────────────────────────────────────────────────────────────
# is_trading_allowed
# ─────────────────────────────────────────────────────────────────

def test_is_trading_allowed_no_file_returns_true():
    assert killswitch.is_trading_allowed() is True


def test_is_trading_allowed_blocked_today_returns_false(_isolate_paths: Path):
    (_isolate_paths / "trading_state.json").write_text(json.dumps({
        "blocked": True, "date": date.today().isoformat(),
    }))
    assert killswitch.is_trading_allowed() is False


def test_is_trading_allowed_blocked_yesterday_auto_resets(_isolate_paths: Path):
    """Si blocked=True mais date=hier → auto-reset, retourne True."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    trading_path = _isolate_paths / "trading_state.json"
    trading_path.write_text(json.dumps({"blocked": True, "date": yesterday}))

    assert killswitch.is_trading_allowed() is True
    # Vérifie le reset persisté
    persisted = json.loads(trading_path.read_text())
    assert persisted["blocked"] is False


# ─────────────────────────────────────────────────────────────────
# check_daily_drawdown
# ─────────────────────────────────────────────────────────────────

def test_check_daily_drawdown_below_threshold(monkeypatch, _isolate_paths: Path):
    """DD -2% < seuil -4% → False (pas de killswitch)."""
    # Positionne starting = 100k pour aujourd'hui
    (_isolate_paths / "equity_state.json").write_text(json.dumps({
        "starting_equity": 100_000, "date": date.today().isoformat(),
    }))
    # Force estimate_portfolio_equity à 98_000 (DD = -2%)
    monkeypatch.setattr(killswitch, "estimate_portfolio_equity", lambda df: 98_000)
    assert killswitch.check_daily_drawdown(_df()) is False


def test_check_daily_drawdown_exceeds_threshold(monkeypatch, _isolate_paths: Path):
    """DD -5% ≥ seuil -4% → True (killswitch!)."""
    (_isolate_paths / "equity_state.json").write_text(json.dumps({
        "starting_equity": 100_000, "date": date.today().isoformat(),
    }))
    monkeypatch.setattr(killswitch, "estimate_portfolio_equity", lambda df: 95_000)
    assert killswitch.check_daily_drawdown(_df()) is True


# ─────────────────────────────────────────────────────────────────
# emergency_liquidate_all
# ─────────────────────────────────────────────────────────────────

def test_emergency_liquidate_all_marks_open_positions(monkeypatch, _isolate_paths: Path):
    """Toutes les positions OPEN → EMERGENCY_CLOSED + trading bloqué."""
    # Stub broker (évite les appels réseau)
    class _StubBroker:
        name = "PaperBroker"
        def close_position(self, *a, **k): pass
    import modules.broker_gateway as bg
    monkeypatch.setattr(bg, "get_broker", lambda: _StubBroker())

    df = _df(
        {"Ticker": "AAPL", "Direction": "LONG", "Entry": 100, "Status": "OPEN"},
        {"Ticker": "MSFT", "Direction": "LONG", "Entry": 400, "Status": "OPEN"},
        {"Ticker": "OLD",  "Direction": "LONG", "Entry": 50,  "Status": "WIN"},  # déjà fermé
    )
    out = killswitch.emergency_liquidate_all(df)

    # 2 positions OPEN → EMERGENCY_CLOSED, la WIN inchangée
    assert (out["Status"] == "EMERGENCY_CLOSED").sum() == 2
    assert (out["Status"] == "WIN").sum() == 1
    # Trading bloqué persisté
    persisted = json.loads((_isolate_paths / "trading_state.json").read_text())
    assert persisted["blocked"] is True


def test_emergency_liquidate_all_no_open_still_blocks(_isolate_paths: Path):
    """Pas de position OPEN → juste bloque le trading sans toucher au df."""
    df = _df({"Ticker": "AAPL", "Status": "WIN"})
    out = killswitch.emergency_liquidate_all(df)
    assert (out["Status"] == "WIN").sum() == 1
    persisted = json.loads((_isolate_paths / "trading_state.json").read_text())
    assert persisted["blocked"] is True
