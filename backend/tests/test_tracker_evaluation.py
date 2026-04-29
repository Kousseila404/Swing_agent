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


# ─────────────────────────────────────────────────────────────────
# thesis_stop Phase 2 — alerte BROKEN dans evaluate_trades
# ─────────────────────────────────────────────────────────────────

def test_thesis_break_triggers_alert(monkeypatch, _isolate: Path):
    """Position OPEN avec TITAN_Entry=85 et current=50 → BROKEN → alerte envoyée."""
    monkeypatch.setattr(evaluation, "get_current_price", lambda _t: 102.0)
    # Stub scored_universe : current TITAN très en dessous de l'entrée
    fake_scored = {"AAPL": {
        "sector": "Tech", "titan_composite_score": 50.0,
        "quality_score": 60, "momentum_score": 40,
        "piotroski_score": 50, "f_score": 5, "revisions_score": 60,
        "titan_tilt_flags": [],
    }}
    import modules.sector_metrics as sm
    monkeypatch.setattr(sm, "get_scored_universe", lambda: fake_scored)

    sent: list[tuple] = []
    monkeypatch.setattr(
        evaluation, "send_thesis_break_alert",
        lambda *a, **k: (sent.append((a, k)), True)[1],
    )

    df = _df(_open_trade(
        Ticker="AAPL", Entry=100, Stop_Loss=95, Take_Profit=200,
        Titan_Score_Entry=85, F_Score_Entry="8/9",
    ))
    out, closed, _ = evaluation.evaluate_trades(df)
    assert closed == 0
    assert len(sent) == 1, "Alerte BROKEN attendue (TITAN −35 ≤ −20)"
    # Cooldown persisté
    data = json.loads((_isolate / "alert_cooldown.json").read_text())
    assert "AAPL" in data.get("thesis_break", {})


def test_thesis_intact_no_alert(monkeypatch):
    """TITAN_Entry=80 et current=78 → INTACT → pas d'alerte."""
    monkeypatch.setattr(evaluation, "get_current_price", lambda _t: 102.0)
    fake_scored = {"AAPL": {
        "sector": "Tech", "titan_composite_score": 78.0,
        "quality_score": 75, "momentum_score": 70,
        "piotroski_score": 70, "f_score": 7,
        "titan_tilt_flags": [],
    }}
    import modules.sector_metrics as sm
    monkeypatch.setattr(sm, "get_scored_universe", lambda: fake_scored)

    sent: list = []
    monkeypatch.setattr(
        evaluation, "send_thesis_break_alert",
        lambda *a, **k: sent.append(1),
    )

    df = _df(_open_trade(Ticker="AAPL", Entry=100, Stop_Loss=95, Take_Profit=200,
                         Titan_Score_Entry=80, F_Score_Entry="7/9"))
    evaluation.evaluate_trades(df)
    assert sent == [], "INTACT ne doit pas alerter"


def test_thesis_break_respects_cooldown(monkeypatch, _isolate: Path):
    """Cooldown 24h : alerte envoyée hier → on ne renvoie pas aujourd'hui."""
    recent = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    (_isolate / "alert_cooldown.json").write_text(json.dumps({
        "thesis_break": {"AAPL": recent},
    }))
    monkeypatch.setattr(evaluation, "get_current_price", lambda _t: 102.0)
    fake_scored = {"AAPL": {
        "sector": "Tech", "titan_composite_score": 50.0,
        "titan_tilt_flags": [],
    }}
    import modules.sector_metrics as sm
    monkeypatch.setattr(sm, "get_scored_universe", lambda: fake_scored)

    sent: list = []
    monkeypatch.setattr(
        evaluation, "send_thesis_break_alert",
        lambda *a, **k: sent.append(1),
    )

    df = _df(_open_trade(Ticker="AAPL", Entry=100, Stop_Loss=95, Take_Profit=200,
                         Titan_Score_Entry=85))
    evaluation.evaluate_trades(df)
    assert sent == [], "Cooldown 24h doit bloquer la 2e alerte"


def test_can_send_thesis_alert_past_cooldown(_isolate: Path):
    """24h passées → autorisé."""
    old = (datetime.now() - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    (_isolate / "alert_cooldown.json").write_text(json.dumps({
        "thesis_break": {"AAPL": old},
    }))
    assert evaluation.can_send_thesis_alert("AAPL") is True


# ─────────────────────────────────────────────────────────────────
# Throttle thesis_check — 1×/heure
# ─────────────────────────────────────────────────────────────────

def test_should_run_thesis_check_no_state(_isolate: Path):
    assert evaluation.should_run_thesis_check() is True


def test_should_run_thesis_check_throttled(_isolate: Path):
    """Dernier run il y a 30 min → throttle (interval = 60 min)."""
    recent = (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    (_isolate / "alert_cooldown.json").write_text(json.dumps({
        "thesis_check_last_run": recent,
    }))
    assert evaluation.should_run_thesis_check() is False


def test_should_run_thesis_check_past_interval(_isolate: Path):
    """Dernier run il y a 70 min → autorisé."""
    old = (datetime.now() - timedelta(minutes=70)).strftime("%Y-%m-%d %H:%M:%S")
    (_isolate / "alert_cooldown.json").write_text(json.dumps({
        "thesis_check_last_run": old,
    }))
    assert evaluation.should_run_thesis_check() is True


# ─────────────────────────────────────────────────────────────────
# Price alerts intraday wiring
# ─────────────────────────────────────────────────────────────────

def test_evaluate_trades_fires_intraday_price_alerts(monkeypatch, _isolate: Path):
    """Si un price_alert est posé sur AAPL avec target=99 (below) et prix=98,
    evaluate_trades doit le fire via evaluate_alerts."""
    monkeypatch.setattr(evaluation, "get_current_price", lambda _t: 98.0)

    fired_calls: list[dict] = []

    def fake_evaluate(prices):
        # Simule un fire
        return [{
            "ticker": "AAPL", "direction": "below",
            "target_price": 99.0, "current_price": prices.get("AAPL"),
            "note": "test tier",
        }]

    sent: list[tuple] = []

    import modules.price_alerts as pa_mod
    monkeypatch.setattr(pa_mod, "evaluate_alerts", fake_evaluate)
    monkeypatch.setattr(
        evaluation, "send_price_alert_fired",
        lambda **kw: (sent.append(kw), True)[1],
    )

    df = _df(_open_trade(Ticker="AAPL", Entry=100, Stop_Loss=80, Take_Profit=200))
    evaluation.evaluate_trades(df)
    assert fired_calls == [] and len(sent) == 1
    assert sent[0]["ticker"] == "AAPL"
    # Throttle marqué
    data = json.loads((_isolate / "alert_cooldown.json").read_text())
    assert "price_alerts_last_run" in data


def test_should_run_price_alerts_check_throttled(_isolate: Path):
    recent = (datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
    (_isolate / "alert_cooldown.json").write_text(json.dumps({
        "price_alerts_last_run": recent,
    }))
    assert evaluation.should_run_price_alerts_check() is False


def test_should_run_price_alerts_check_past_interval(_isolate: Path):
    old = (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    (_isolate / "alert_cooldown.json").write_text(json.dumps({
        "price_alerts_last_run": old,
    }))
    assert evaluation.should_run_price_alerts_check() is True
