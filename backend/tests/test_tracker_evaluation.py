"""Tests unitaires — modules/tracker/evaluation.py

Couvre :
  - evaluate_trades : SL/TP LONG, SL/TP SHORT (asymétrie Bug D), time exit,
    trailing stop % fixe, current_price=None ignoré, gap overnight clamp
  - can_send_sl_alert / mark_sl_alert_sent : cooldown 4h respecté
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from modules.tracker import evaluation, state

# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path: Path):
    cooldown = tmp_path / "alert_cooldown.json"
    monkeypatch.setattr(state, "ALERT_COOLDOWN_PATH", cooldown)
    monkeypatch.setattr(evaluation, "ALERT_COOLDOWN_PATH", cooldown)
    # Stub broker (évite dépendance externe)
    class _StubBroker:
        name = "PaperBroker"
        def close_position(self, *a, **k): pass
        def update_stop_loss(self, *a, **k): pass
    import modules.broker_gateway as bg
    monkeypatch.setattr(bg, "get_broker", lambda: _StubBroker())
    # Stub send_close_alert (pas de Telegram)
    monkeypatch.setattr(evaluation, "send_close_alert", lambda *a, **k: None)
    # Stub read_ohlcv (désactive le mode trailing ATR)
    import modules.market_db as mdb
    monkeypatch.setattr(mdb, "read_ohlcv", lambda *a, **k: None)
    # Stub yf.download fallback (évite d'hit le réseau pour l'historique OHLCV
    # quand read_ohlcv renvoie None — voir _fetch dans evaluation.py).
    import yfinance as _yf
    monkeypatch.setattr(_yf, "download", lambda *a, **k: pd.DataFrame())
    return tmp_path


def _open_trade(**kwargs) -> dict:
    """Construit un trade OPEN avec des valeurs par défaut saines."""
    base = {
        "Ticker": "AAPL", "Direction": "LONG", "Entry": 100, "Size": 10,
        "Stop_Loss": 95, "Take_Profit": 110, "Status": "OPEN",
        "Exit_Price": "", "Exit_Date": "", "Last_TS_Update": "",
        "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    base.update(kwargs)
    return base


def _df(*rows: dict) -> pd.DataFrame:
    cols = list(_open_trade().keys())
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame([{**_open_trade(), **r} for r in rows])


# ─────────────────────────────────────────────────────────────────
# evaluate_trades — SL/TP LONG
# ─────────────────────────────────────────────────────────────────

def test_evaluate_long_tp_hit(monkeypatch):
    """LONG : prix ≥ TP → WIN fermé."""
    monkeypatch.setattr(evaluation, "get_current_price", lambda _t: 111.0)
    df = _df(_open_trade(Ticker="AAPL", Entry=100, Stop_Loss=95, Take_Profit=110))
    out, closed, _ = evaluation.evaluate_trades(df)
    assert closed == 1
    assert out.iloc[0]["Status"] == "WIN"


def test_evaluate_long_sl_hit(monkeypatch):
    """LONG : prix ≤ SL → LOSS fermé."""
    monkeypatch.setattr(evaluation, "get_current_price", lambda _t: 94.0)
    df = _df(_open_trade(Entry=100, Stop_Loss=95, Take_Profit=110))
    out, closed, _ = evaluation.evaluate_trades(df)
    assert closed == 1
    assert out.iloc[0]["Status"] == "LOSS"


def test_evaluate_long_in_range_stays_open(monkeypatch):
    """LONG : SL < prix < TP → reste OPEN."""
    monkeypatch.setattr(evaluation, "get_current_price", lambda _t: 102.0)
    df = _df(_open_trade(Entry=100, Stop_Loss=95, Take_Profit=110))
    out, closed, _ = evaluation.evaluate_trades(df)
    assert closed == 0
    assert out.iloc[0]["Status"] == "OPEN"


# ─────────────────────────────────────────────────────────────────
# evaluate_trades — SL/TP SHORT (asymétrie Bug D)
# ─────────────────────────────────────────────────────────────────

def test_evaluate_short_tp_hit(monkeypatch):
    """SHORT : prix ≤ TP → WIN (direction inverse)."""
    monkeypatch.setattr(evaluation, "get_current_price", lambda _t: 89.0)
    df = _df(_open_trade(Ticker="TSLA", Direction="SHORT",
                         Entry=100, Stop_Loss=105, Take_Profit=90))
    out, closed, _ = evaluation.evaluate_trades(df)
    assert closed == 1
    assert out.iloc[0]["Status"] == "WIN"


def test_evaluate_short_sl_hit(monkeypatch):
    """SHORT : prix ≥ SL → LOSS."""
    monkeypatch.setattr(evaluation, "get_current_price", lambda _t: 106.0)
    df = _df(_open_trade(Direction="SHORT",
                         Entry=100, Stop_Loss=105, Take_Profit=90))
    out, closed, _ = evaluation.evaluate_trades(df)
    assert closed == 1
    assert out.iloc[0]["Status"] == "LOSS"


# ─────────────────────────────────────────────────────────────────
# evaluate_trades — time exit
# ─────────────────────────────────────────────────────────────────

def test_evaluate_time_exit_after_max_holding(monkeypatch, tmp_path: Path):
    """Position ouverte il y a 40 jours → time exit (bat aussi param_time_stop=25 de best_strategy.json)."""
    monkeypatch.setattr(evaluation, "get_current_price", lambda _t: 102.0)  # pas TP/SL
    # chdir tmp pour que Path("data/best_strategy.json") n'existe pas → fallback config.MAX_HOLDING_DAYS
    monkeypatch.chdir(tmp_path)
    import config
    monkeypatch.setattr(config, "MAX_HOLDING_DAYS", 10, raising=False)

    old_date = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    df = _df(_open_trade(Entry=100, Stop_Loss=95, Take_Profit=110, Date=old_date))
    out, closed, _ = evaluation.evaluate_trades(df)
    assert closed == 1
    assert out.iloc[0]["Status"] == "WIN"  # pct_gain = +2% > 0


# ─────────────────────────────────────────────────────────────────
# evaluate_trades — current_price=None → ignoré (reste OPEN)
# ─────────────────────────────────────────────────────────────────

def test_evaluate_skips_when_price_unavailable(monkeypatch):
    monkeypatch.setattr(evaluation, "get_current_price", lambda _t: None)
    df = _df(_open_trade(Entry=100, Stop_Loss=95, Take_Profit=110))
    out, closed, modified = evaluation.evaluate_trades(df)
    assert closed == 0 and modified == 0
    assert out.iloc[0]["Status"] == "OPEN"


# ─────────────────────────────────────────────────────────────────
# evaluate_trades — trailing stop % fixe (pas d'ATR dispo)
# ─────────────────────────────────────────────────────────────────

def test_evaluate_long_trailing_stop_updated(monkeypatch):
    """LONG : profit 5% > activation 3% → SL remonte (50% du gain locké)."""
    import config
    monkeypatch.setattr(config, "TRAILING_STOP_ACTIVATION_PCT", 3.0, raising=False)
    monkeypatch.setattr(config, "TRAILING_STOP_LOCK_PCT", 0.5, raising=False)
    monkeypatch.setattr(evaluation, "get_current_price", lambda _t: 105.0)

    df = _df(_open_trade(Entry=100, Stop_Loss=95, Take_Profit=120))
    out, closed, modified = evaluation.evaluate_trades(df)
    assert closed == 0
    assert modified == 1
    # new_sl = 100 + (105-100)*0.5 = 102.5
    assert out.iloc[0]["Stop_Loss"] == pytest.approx(102.5)


def test_evaluate_long_trailing_stop_NOT_triggered_at_10pct_default(monkeypatch):
    """Régression V4.1 Long-Term (2026-04-23) : avec les defaults LT
    (activation 15 %), un profit de +10 % NE doit PAS activer le TS.
    Sinon on retombe dans le bug du 22/04 : swings clôturés sur retracement
    intraday avant d'atteindre le TP (+18-40 % typique en LT).
    """
    # On ne force aucune valeur → les defaults de config.py s'appliquent (LT).
    monkeypatch.setattr(evaluation, "get_current_price", lambda _t: 110.0)
    df = _df(_open_trade(Entry=100, Stop_Loss=95, Take_Profit=140))
    out, closed, modified = evaluation.evaluate_trades(df)
    assert closed == 0
    assert modified == 0, "TS ne doit pas se déclencher à +10 % avec activation=15 %"
    assert out.iloc[0]["Stop_Loss"] == pytest.approx(95.0)  # SL inchangé


def test_evaluate_long_trailing_stop_triggered_at_20pct_default(monkeypatch):
    """Avec les defaults V4.1 LT (activation 15 %, lock 25 %), +20 % active
    le TS et verrouille 25 % du gain → new_sl = 100 + (120-100)*0.25 = 105.0.
    """
    monkeypatch.setattr(evaluation, "get_current_price", lambda _t: 120.0)
    df = _df(_open_trade(Entry=100, Stop_Loss=95, Take_Profit=140))
    out, closed, modified = evaluation.evaluate_trades(df)
    assert closed == 0
    assert modified == 1
    assert out.iloc[0]["Stop_Loss"] == pytest.approx(105.0)


# ─────────────────────────────────────────────────────────────────
# evaluate_trades — empty
# ─────────────────────────────────────────────────────────────────

def test_evaluate_empty_df_returns_zero():
    df = _df()
    out, closed, modified = evaluation.evaluate_trades(df)
    assert closed == 0 and modified == 0
    assert len(out) == 0


# ─────────────────────────────────────────────────────────────────
# SL alert cooldown
# ─────────────────────────────────────────────────────────────────

def test_can_send_sl_alert_no_file():
    """Pas de fichier cooldown → autorisé."""
    assert evaluation.can_send_sl_alert("AAPL") is True


def test_can_send_sl_alert_respects_cooldown(_isolate: Path):
    """Cooldown 4h : dernière alerte il y a 1h → refusé."""
    recent = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    (_isolate / "alert_cooldown.json").write_text(json.dumps({
        "sl_proximity": {"AAPL": recent},
    }))
    assert evaluation.can_send_sl_alert("AAPL") is False


def test_can_send_sl_alert_past_cooldown(_isolate: Path):
    """Cooldown 4h : dernière alerte il y a 5h → autorisé."""
    old = (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    (_isolate / "alert_cooldown.json").write_text(json.dumps({
        "sl_proximity": {"AAPL": old},
    }))
    assert evaluation.can_send_sl_alert("AAPL") is True


def test_mark_sl_alert_sent_persists(_isolate: Path):
    evaluation.mark_sl_alert_sent("AAPL")
    data = json.loads((_isolate / "alert_cooldown.json").read_text())
    assert "AAPL" in data["sl_proximity"]
