"""Tests unitaires — modules/portfolio_risk.py (Upgrade 1, book my_portfolio).

Toutes les séries de prix sont synthétiques (numpy, seed fixe) — aucun appel
réseau réel, pattern `_MockTicker` de `test_tracker_market.py` adapté ici en
`_FakeTicker` (dispatch par symbole plutôt qu'un prix unique, nécessaire
pour simuler plusieurs tickers + benchmark + paires FX simultanément).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from modules import portfolio_risk as pr


class _FakeTicker:
    def __init__(self, series: pd.Series | None, raise_error: bool = False):
        self._series = series
        self._raise = raise_error

    def history(self, **_kw) -> pd.DataFrame:
        if self._raise:
            raise RuntimeError("simulated network error")
        if self._series is None:
            return pd.DataFrame(columns=["Close"])
        return pd.DataFrame({"Close": self._series.to_numpy()}, index=self._series.index)


def _ticker_factory(series_map: dict[str, pd.Series], *, raise_for: frozenset[str] = frozenset()):
    def _factory(symbol: str):
        if symbol in raise_for:
            return _FakeTicker(None, raise_error=True)
        return _FakeTicker(series_map.get(symbol))
    return _factory


def _price_series(seed: int, n: int = 300, *, k: float | None = None,
                   base_rets: np.ndarray | None = None, start: str = "2023-01-02"):
    """Série de prix synthétique. Si `base_rets` fourni, les returns sont
    `k * base_rets + bruit indépendant` (corrélation contrôlée avec `base_rets`)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start=start, periods=n)
    if base_rets is None:
        rets = rng.normal(0, 0.01, n)
    else:
        noise = rng.normal(0, 0.006, n)
        rets = k * base_rets + noise
    price = 100.0 * np.exp(np.cumsum(rets))
    return pd.Series(price, index=idx), rets


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "_STATE_PATH", tmp_path / "my_portfolio_risk.json")


# ─────────────────────────────────────────────────────────────────
# compute_portfolio_risk — cas nominal
# ─────────────────────────────────────────────────────────────────

def test_happy_path_computes_beta_and_correlation(monkeypatch):
    bench_price, bench_rets = _price_series(seed=1, n=300)
    a_price, _ = _price_series(seed=2, n=300, k=1.5, base_rets=bench_rets)
    b_price, _ = _price_series(seed=3, n=300, k=0.4, base_rets=bench_rets)

    series_map = {"^GSPC": bench_price, "AAA": a_price, "BBB": b_price}
    monkeypatch.setattr(pr.yf, "Ticker", _ticker_factory(series_map))

    positions = [
        {"ticker": "AAA", "beta": 1.0, "target_weight_pct": 60.0},
        {"ticker": "BBB", "beta": 0.4, "target_weight_pct": 40.0},
    ]
    result = pr.compute_portfolio_risk(positions)

    assert result["schema_version"] == pr.SCHEMA_VERSION
    aaa = result["tickers"]["AAA"]
    bbb = result["tickers"]["BBB"]
    assert aaa["data_quality"] == "ok"
    assert bbb["data_quality"] == "ok"
    # k=1.5 avec bruit modéré sur 300 points → beta recalculé proche de 1.5
    assert aaa["beta_recalculated"] == pytest.approx(1.5, abs=0.3)
    # beta statique déclaré 1.0 vs recalculé ~1.5 → écart > 30% → flag
    assert aaa["beta_flag"] is True
    assert bbb["beta_recalculated"] == pytest.approx(0.4, abs=0.3)

    snap = result["risk_snapshot"]
    assert snap["n_tickers_ok"] == 2
    assert snap["n_tickers_missing"] == 0
    assert snap["portfolio_beta"] is not None
    assert snap["avg_weighted_correlation"] is not None
    assert snap["diversification_ratio"] is not None
    assert snap["diversification_ratio"] >= 1.0 - 1e-6
    assert len(snap["most_correlated_pairs"]) == 1
    pair = snap["most_correlated_pairs"][0]
    assert {pair["a"], pair["b"]} == {"AAA", "BBB"}


# ─────────────────────────────────────────────────────────────────
# Cas limites — data_quality
# ─────────────────────────────────────────────────────────────────

def test_insufficient_history_flags_data_quality(monkeypatch):
    bench_price, bench_rets = _price_series(seed=1, n=300)
    short_price, _ = _price_series(seed=4, n=30, k=1.0, base_rets=bench_rets[:30])

    series_map = {"^GSPC": bench_price, "NEW": short_price}
    monkeypatch.setattr(pr.yf, "Ticker", _ticker_factory(series_map))

    positions = [{"ticker": "NEW", "beta": 1.0, "target_weight_pct": 100.0}]
    result = pr.compute_portfolio_risk(positions)

    row = result["tickers"]["NEW"]
    assert row["data_quality"] == "insufficient_history"
    assert row["beta_recalculated"] is None
    assert row["avg_correlation"] is None
    assert result["risk_snapshot"]["n_tickers_ok"] == 0
    assert result["risk_snapshot"]["n_tickers_missing"] == 1
    # jamais de beta halluciné
    assert result["risk_snapshot"]["portfolio_beta"] is None


def test_history_fetch_failure_is_fetch_failed(monkeypatch):
    bench_price, _ = _price_series(seed=1, n=300)
    monkeypatch.setattr(
        pr.yf, "Ticker",
        _ticker_factory({"^GSPC": bench_price}, raise_for=frozenset({"DOWN"})),
    )
    positions = [{"ticker": "DOWN", "beta": 1.0, "target_weight_pct": 100.0}]
    result = pr.compute_portfolio_risk(positions)
    assert result["tickers"]["DOWN"]["data_quality"] == "fetch_failed"


def test_fx_unavailable_marks_eur_ticker_fetch_failed(monkeypatch):
    bench_price, bench_rets = _price_series(seed=1, n=300)
    eur_price, _ = _price_series(seed=5, n=300, k=1.0, base_rets=bench_rets)
    # Prix natif dispo, mais la paire FX EURUSD=X échoue.
    monkeypatch.setattr(
        pr.yf, "Ticker",
        _ticker_factory({"^GSPC": bench_price, "EURT": eur_price}, raise_for=frozenset({"EURUSD=X"})),
    )
    positions = [{"ticker": "EURT", "currency": "EUR", "beta": 1.0, "target_weight_pct": 100.0}]
    result = pr.compute_portfolio_risk(positions)
    assert result["tickers"]["EURT"]["data_quality"] == "fetch_failed"


def test_eur_currency_converts_via_fx_series(monkeypatch):
    """Un ticker EUR avec un beta_static déjà calibré sur returns USD-convertis
    doit produire un beta_recalculated cohérent (pas de biais de change massif
    puisque la FX est ~constante ici)."""
    bench_price, bench_rets = _price_series(seed=1, n=300)
    eur_price, _ = _price_series(seed=6, n=300, k=0.8, base_rets=bench_rets)
    fx_flat = pd.Series(1.08, index=eur_price.index)  # EURUSD quasi constant
    monkeypatch.setattr(
        pr.yf, "Ticker",
        _ticker_factory({"^GSPC": bench_price, "EURT": eur_price, "EURUSD=X": fx_flat}),
    )
    positions = [{"ticker": "EURT", "currency": "EUR", "beta": 0.8, "target_weight_pct": 100.0}]
    result = pr.compute_portfolio_risk(positions)
    row = result["tickers"]["EURT"]
    assert row["data_quality"] == "ok"
    # FX quasi-plate → beta recalculé proche de celui obtenu sans conversion.
    assert row["beta_recalculated"] == pytest.approx(0.8, abs=0.3)


def test_unconfigured_currency_is_fetch_failed(monkeypatch):
    bench_price, _ = _price_series(seed=1, n=300)
    some_price, _ = _price_series(seed=7, n=300)
    monkeypatch.setattr(
        pr.yf, "Ticker",
        _ticker_factory({"^GSPC": bench_price, "JPYT": some_price}),
    )
    positions = [{"ticker": "JPYT", "currency": "JPY", "beta": 1.0, "target_weight_pct": 100.0}]
    result = pr.compute_portfolio_risk(positions)
    assert result["tickers"]["JPYT"]["data_quality"] == "fetch_failed"


# ─────────────────────────────────────────────────────────────────
# correlation_alert — streak "durable" et signal composite BNP.PA (OR)
# ─────────────────────────────────────────────────────────────────

def test_correlation_streak_pure_function_increments_and_resets():
    streak, triggered = pr._correlation_streak(ref_value=0.5, threshold=0.4, persist_weeks=2, prev_streak=0)
    assert (streak, triggered) == (1, False)
    streak, triggered = pr._correlation_streak(ref_value=0.5, threshold=0.4, persist_weeks=2, prev_streak=1)
    assert (streak, triggered) == (2, True)
    streak, triggered = pr._correlation_streak(ref_value=0.1, threshold=0.4, persist_weeks=2, prev_streak=2)
    assert (streak, triggered) == (0, False)


def test_correlation_streak_none_value_carries_over_previous():
    """Data indisponible ce run → le streak n'est ni incrémenté ni reset."""
    streak, triggered = pr._correlation_streak(ref_value=None, threshold=0.4, persist_weeks=2, prev_streak=1)
    assert streak == 1
    assert triggered is False


def test_avg_correlation_alert_triggers_after_persist_weeks(monkeypatch):
    """HRTG-like : ref=None (corrélation moyenne du book), threshold=0.30,
    persist_weeks=2 — construit un panier où le ticker cible est fortement
    corrélé à tous les autres pour dépasser le seuil, sur 2 runs consécutifs."""
    bench_price, bench_rets = _price_series(seed=1, n=300)
    target_price, target_rets = _price_series(seed=8, n=300, k=1.0, base_rets=bench_rets)
    # Deux autres tickers fortement corrélés au ticker cible (pas juste au benchmark).
    c_price, _ = _price_series(seed=9, n=300, k=0.9, base_rets=target_rets)
    d_price, _ = _price_series(seed=10, n=300, k=0.9, base_rets=target_rets)

    series_map = {"^GSPC": bench_price, "TGT": target_price, "CCC": c_price, "DDD": d_price}
    monkeypatch.setattr(pr.yf, "Ticker", _ticker_factory(series_map))

    positions = [
        {"ticker": "TGT", "beta": 1.0, "target_weight_pct": 34.0,
         "correlation_alert": {"ref": None, "threshold": 0.30, "persist_weeks": 2}},
        {"ticker": "CCC", "beta": 1.0, "target_weight_pct": 33.0},
        {"ticker": "DDD", "beta": 1.0, "target_weight_pct": 33.0},
    ]

    run1 = pr.compute_portfolio_risk(positions, prev_streaks={})
    tgt1 = run1["tickers"]["TGT"]
    assert tgt1["avg_correlation"] is not None
    assert tgt1["avg_correlation"] > 0.30
    assert tgt1["correlation_streak_weeks"] == 1
    assert tgt1["correlation_alert_triggered"] is False  # persist_weeks=2, 1er run

    run2 = pr.compute_portfolio_risk(positions, prev_streaks={"TGT": 1})
    tgt2 = run2["tickers"]["TGT"]
    assert tgt2["correlation_streak_weeks"] == 2
    assert tgt2["correlation_alert_triggered"] is True


def test_ero_ref_ticker_correlation_vs_mu_immediate_trigger(monkeypatch):
    """ERO-like : ref='MU', threshold=0.50, persist_weeks=1 → déclenchement
    immédiat dès que corrélation_vs_ref franchit le seuil (pas de mot
    'durable' dans le texte original du sell_signal)."""
    bench_price, bench_rets = _price_series(seed=1, n=300)
    mu_price, mu_rets = _price_series(seed=11, n=300, k=1.0, base_rets=bench_rets)
    ero_price, _ = _price_series(seed=12, n=300, k=0.85, base_rets=mu_rets)

    series_map = {"^GSPC": bench_price, "MU": mu_price, "ERO": ero_price}
    monkeypatch.setattr(pr.yf, "Ticker", _ticker_factory(series_map))

    positions = [
        {"ticker": "MU", "beta": 2.53, "target_weight_pct": 50.0},
        {"ticker": "ERO", "beta": 1.63, "target_weight_pct": 50.0,
         "correlation_alert": {"ref": "MU", "threshold": 0.50, "persist_weeks": 1}},
    ]
    result = pr.compute_portfolio_risk(positions, prev_streaks={})
    ero = result["tickers"]["ERO"]
    assert ero["correlation_vs_ref"] is not None
    assert ero["correlation_vs_ref"] > 0.50
    assert ero["correlation_streak_weeks"] == 1
    assert ero["correlation_alert_triggered"] is True  # persist_weeks=1 → immédiat


def test_bnp_style_composite_signal_beta_and_correlation_independent(monkeypatch):
    """BNP.PA : 'Beta >0.8 durable OU corrélation >0.40' — beta_flag et
    correlation_alert_triggered doivent pouvoir se déclencher indépendamment."""
    bench_price, bench_rets = _price_series(seed=1, n=300)
    # Beta très éloigné du statique (0.36 déclaré) → beta_flag True, mais
    # corrélation avec l'unique autre ticker du panier très faible → alerte
    # corrélation ne doit PAS se déclencher.
    bnp_price, _ = _price_series(seed=13, n=300, k=1.4, base_rets=bench_rets)
    other_price, _ = _price_series(seed=14, n=300)  # bruit indépendant

    series_map = {"^GSPC": bench_price, "BNP": bnp_price, "OTH": other_price}
    monkeypatch.setattr(pr.yf, "Ticker", _ticker_factory(series_map))

    positions = [
        {"ticker": "BNP", "beta": 0.36, "target_weight_pct": 50.0,
         "correlation_alert": {"ref": None, "threshold": 0.40, "persist_weeks": 2}},
        {"ticker": "OTH", "beta": 1.0, "target_weight_pct": 50.0},
    ]
    result = pr.compute_portfolio_risk(positions, prev_streaks={"BNP": 5})
    bnp = result["tickers"]["BNP"]
    assert bnp["beta_flag"] is True
    assert bnp["correlation_alert_triggered"] is False


# ─────────────────────────────────────────────────────────────────
# Persistance — schema versioning, roundtrip, fail-open par run
# ─────────────────────────────────────────────────────────────────

def test_refresh_persists_and_reloads_roundtrip(monkeypatch):
    bench_price, bench_rets = _price_series(seed=1, n=300)
    a_price, _ = _price_series(seed=2, n=300, k=1.0, base_rets=bench_rets)
    monkeypatch.setattr(pr.yf, "Ticker", _ticker_factory({"^GSPC": bench_price, "AAA": a_price}))

    positions = [{"ticker": "AAA", "beta": 1.0, "target_weight_pct": 100.0}]
    result = pr.refresh_portfolio_risk(positions)

    assert pr._STATE_PATH.exists()
    on_disk = json.loads(pr._STATE_PATH.read_text())
    assert on_disk["schema_version"] == pr.SCHEMA_VERSION
    assert on_disk["tickers"]["AAA"]["data_quality"] == "ok"
    assert result["tickers"]["AAA"]["data_quality"] == "ok"


def test_refresh_keeps_previous_snapshot_on_total_failure(monkeypatch):
    bench_price, bench_rets = _price_series(seed=1, n=300)
    a_price, _ = _price_series(seed=2, n=300, k=1.0, base_rets=bench_rets)
    positions = [{"ticker": "AAA", "beta": 1.0, "target_weight_pct": 100.0}]

    monkeypatch.setattr(pr.yf, "Ticker", _ticker_factory({"^GSPC": bench_price, "AAA": a_price}))
    first = pr.refresh_portfolio_risk(positions)
    assert first["risk_snapshot"]["n_tickers_ok"] == 1

    # Panne réseau totale au 2e run — aucun ticker exploitable.
    monkeypatch.setattr(pr.yf, "Ticker", _ticker_factory({}, raise_for=frozenset({"^GSPC", "AAA"})))
    second = pr.refresh_portfolio_risk(positions)
    assert second == first  # état précédent conservé tel quel, pas écrasé


def test_load_state_ignores_mismatched_schema_version(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"schema_version": 999, "tickers": {}}))
    monkeypatch.setattr(pr, "_STATE_PATH", path)
    assert pr._load_state() == {}


def test_load_state_ignores_corrupt_json(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("{not valid json")
    monkeypatch.setattr(pr, "_STATE_PATH", path)
    assert pr._load_state() == {}


# ─────────────────────────────────────────────────────────────────
# Intégration — book my_portfolio réel (10 positions), tout mocké
# ─────────────────────────────────────────────────────────────────

def test_full_book_integration_all_ten_positions(monkeypatch):
    from modules.my_portfolio_data import POSITIONS

    bench_price, bench_rets = _price_series(seed=0, n=300)
    series_map = {"^GSPC": bench_price}
    for i, p in enumerate(POSITIONS):
        price_ticker = p.get("price_ticker", p["ticker"])
        price, _ = _price_series(seed=100 + i, n=300, k=0.3, base_rets=bench_rets)
        series_map[price_ticker] = price
    series_map["EURUSD=X"] = pd.Series(1.08, index=bench_price.index)
    series_map["USDHKD=X"] = pd.Series(7.8, index=bench_price.index)

    monkeypatch.setattr(pr.yf, "Ticker", _ticker_factory(series_map))
    result = pr.compute_portfolio_risk(POSITIONS, cash_weight_pct=10.0)

    assert result["risk_snapshot"]["n_tickers_ok"] == len(POSITIONS)
    for p in POSITIONS:
        row = result["tickers"][p["ticker"]]
        assert row["data_quality"] == "ok"
        assert row["beta_recalculated"] is not None
    assert result["risk_snapshot"]["portfolio_beta"] is not None
    assert result["risk_snapshot"]["diversification_ratio"] is not None


# ─────────────────────────────────────────────────────────────────
# get_price_history — page détail ticker (Mon Portefeuille)
# ─────────────────────────────────────────────────────────────────

def test_get_price_history_usd_ticker(monkeypatch):
    price, _ = _price_series(seed=1, n=30)
    monkeypatch.setattr(pr.yf, "Ticker", _ticker_factory({"MU": price}))
    history = pr.get_price_history("MU", "USD", "1y")
    assert history is not None
    assert len(history) == 30
    assert set(history[0].keys()) == {"date", "price"}
    assert history[0]["price"] == round(float(price.iloc[0]), 4)


def test_get_price_history_converts_eur_via_fx_series(monkeypatch):
    native, _ = _price_series(seed=2, n=20)
    fx = pd.Series(1.1, index=native.index)
    monkeypatch.setattr(pr.yf, "Ticker", _ticker_factory({"BNP.PA": native, "EURUSD=X": fx}))
    history = pr.get_price_history("BNP.PA", "EUR", "1y")
    assert history is not None
    assert history[0]["price"] == round(float(native.iloc[0]) * 1.1, 4)


def test_get_price_history_none_when_fetch_fails(monkeypatch):
    monkeypatch.setattr(pr.yf, "Ticker", _ticker_factory({}, raise_for=frozenset({"XYZ"})))
    assert pr.get_price_history("XYZ", "USD", "1y") is None
