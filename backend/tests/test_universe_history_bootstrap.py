"""Tests du bootstrap rétroactif universe_history (sans réseau).

On mock yfinance pour produire des séries déterministes — on vérifie :
  • Construction des dates samples (fréquence + buffer momentum).
  • Matérialisation d'un snapshot complet (compute momentum, scoring, write).
  • Skip d'un snapshot déjà existant (reprise incrémentale).
  • Résilience : ticker sans prix → exclu du snapshot (pas de crash).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from modules import api_core, universe_history
from modules import universe_history_bootstrap as boot


def _make_universe_payload(tmp_path, tickers: list[str]):
    """Écrit un universe.json minimal pour le test."""
    import json

    payload = {
        "version": 1,
        "updated_at": "2026-04-28T00:00:00Z",
        "tickers": {
            t: {
                "ticker": t,
                "name": f"{t} Inc.",
                "sector": "Technology",
                "market_cap": 50e9,
                "return_on_equity": 0.20,
                "operating_margin": 0.25,
                "ev_to_ebitda": 12.0,
                "forward_pe": 20.0,
                "free_cash_flow": 1e9,
                "debt_to_equity": 0.5,
                "current_ratio": 2.0,
                "recommendation_mean": 2.0,
                "price_target_mean": 200.0,
                "revenue_growth": 0.10,
                "earnings_growth": 0.15,
            }
            for t in tickers
        },
    }
    path = tmp_path / "universe.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _fake_yf_download(tickers, start, end, **kwargs):
    """Mock yfinance.download : produit des MultiIndex (ticker, OHLCV) avec
    Close croissant linéairement (assure un momentum positif déterministe).
    """
    if isinstance(tickers, str):
        tickers = [tickers]
    dates = pd.date_range(start, end, freq="B")
    if len(dates) == 0:
        return pd.DataFrame()
    cols = pd.MultiIndex.from_product(
        [tickers, ["Open", "High", "Low", "Close", "Volume"]],
        names=[None, None],
    )
    data = np.zeros((len(dates), len(cols)))
    for i, t in enumerate(tickers):
        # Trend croissant ~10 % an avec un peu de bruit reproductible.
        rng = np.random.default_rng(abs(hash(t)) % (2**32))
        prices = 100.0 * np.cumprod(1 + rng.normal(0.0004, 0.012, len(dates)))
        for j, col in enumerate(["Open", "High", "Low", "Close", "Volume"]):
            data[:, i * 5 + j] = prices if col != "Volume" else 1e6
    return pd.DataFrame(data, index=dates, columns=cols)


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    """Isole HISTORY_DIR + UNIVERSE_QUANTAMENTAL_PATH dans tmp_path."""
    hist = tmp_path / "history"
    monkeypatch.setattr(universe_history, "HISTORY_DIR", hist)
    universe_path = _make_universe_payload(tmp_path, ["AAPL", "MSFT", "NVDA", "GOOGL"])
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", universe_path)
    return hist


def test_frequency_step_days_known_values():
    assert boot._frequency_step_days("daily") == 1
    assert boot._frequency_step_days("weekly") == 7
    assert boot._frequency_step_days("monthly") == 30


def test_frequency_step_days_invalid_raises():
    with pytest.raises(ValueError, match="frequency invalide"):
        boot._frequency_step_days("yearly")


def test_build_sample_dates_respects_buffer_and_step():
    earliest = pd.Timestamp("2024-01-01")
    end = date(2026, 4, 28)
    samples = boot._build_sample_dates(earliest, end, step_days=30)
    # Le 1er sample doit être ≥ earliest + buffer (272 + 30 = 302 jours).
    assert samples[0] >= (earliest + pd.Timedelta(days=302)).date()
    # Step 30 : écart entre samples adjacents = 30 jours.
    assert (samples[1] - samples[0]).days == 30
    # Le dernier sample est ≤ end.
    assert samples[-1] <= end


def test_bootstrap_writes_snapshots_with_mocked_yf(isolated_history):
    """Le pipeline complet doit produire ≥ 1 snapshot en mode hebdo / 2 ans."""
    with patch("yfinance.download", side_effect=_fake_yf_download):
        audit = boot.bootstrap(
            years=2,
            frequency="monthly",   # plus rapide à itérer en test
            max_tickers=4,
            end=date(2026, 4, 1),
        )
    assert audit["n_tickers_dl"] == 4
    assert audit["n_snapshots_written"] >= 1
    # Au moins un snapshot existe maintenant sur disque.
    snaps = universe_history.list_snapshots()
    assert len(snaps) >= 1
    # Lecture round-trip — payload non vide, _bootstrap_lookahead taggé.
    snap = universe_history.read_snapshot(snaps[0])
    assert snap is not None
    rows = snap.get("tickers") or {}
    assert len(rows) >= 1
    a_row = next(iter(rows.values()))
    assert a_row.get("_bootstrap_lookahead") is True
    # Le scoring a bien tourné — composite TITAN présent.
    assert "titan_composite_score" in a_row


def test_bootstrap_skip_existing_does_not_overwrite(isolated_history):
    """Un 2ᵉ run avec skip_existing=True doit incrémenter n_snapshots_skipped
    et ne plus rien écrire de nouveau pour les dates déjà présentes."""
    with patch("yfinance.download", side_effect=_fake_yf_download):
        run1 = boot.bootstrap(
            years=2, frequency="monthly", max_tickers=4, end=date(2026, 4, 1),
        )
        run2 = boot.bootstrap(
            years=2, frequency="monthly", max_tickers=4, end=date(2026, 4, 1),
        )
    assert run1["n_snapshots_written"] >= 1
    assert run2["n_snapshots_skipped"] == run1["n_snapshots_written"]
    assert run2["n_snapshots_written"] == 0


def test_bootstrap_resilient_to_missing_prices(isolated_history):
    """Si yfinance retourne 0 séries pour un ticker, le ticker est juste exclu
    du snapshot — pas d'exception levée."""

    def _partial_download(tickers, start, end, **kwargs):
        # On ne renvoie de la data que pour AAPL et MSFT.
        return _fake_yf_download(["AAPL", "MSFT"], start, end, **kwargs)

    with patch("yfinance.download", side_effect=_partial_download):
        audit = boot.bootstrap(
            years=2, frequency="monthly", max_tickers=4, end=date(2026, 4, 1),
        )
    assert audit["n_tickers_dl"] == 2
    assert audit["n_snapshots_written"] >= 1


def test_bootstrap_aborts_when_universe_json_empty(tmp_path, monkeypatch):
    """universe.json absent → SystemExit explicite."""
    monkeypatch.setattr(api_core, "UNIVERSE_QUANTAMENTAL_PATH", tmp_path / "missing.json")
    with pytest.raises(SystemExit, match="introuvable"):
        boot.bootstrap(years=1, frequency="monthly")
