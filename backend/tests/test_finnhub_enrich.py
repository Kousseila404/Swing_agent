"""Tests finnhub_enrich — propagation de l'échec Finnhub (Étape 5, 2026-07-20).

Même patron que test_insider_enrich.py : `finnhub_error` doit être écrit dans
la row persistée (avant ce run, `FinnhubData.error` n'était jamais assigné ni
lu, donc invisible pour data_confidence.py).
"""
from __future__ import annotations

import json

from data_providers.finnhub_provider import FinnhubData
from modules import finnhub_enrich


def _write_universe(path, tickers: dict) -> None:
    path.write_text(json.dumps({"tickers": tickers}), encoding="utf-8")


def test_enrich_success_has_no_finnhub_error(monkeypatch, tmp_path):
    path = tmp_path / "universe.json"
    _write_universe(path, {"AAA": {}})
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    monkeypatch.setattr(finnhub_enrich, "_UNIVERSE_PATH", path)
    monkeypatch.setattr(finnhub_enrich.FinnhubProvider, "is_configured", classmethod(lambda cls: True))
    monkeypatch.setattr(
        finnhub_enrich.FinnhubProvider, "get_revisions_and_earnings",
        lambda self, ticker, use_cache=True: FinnhubData(ticker=ticker, error=None),
    )

    diag = finnhub_enrich.enrich_universe_with_finnhub()

    assert diag["n_enriched"] == 1
    universe = json.loads(path.read_text(encoding="utf-8"))
    assert universe["tickers"]["AAA"]["finnhub_error"] is None


def test_enrich_propagates_fetch_failure(monkeypatch, tmp_path):
    path = tmp_path / "universe.json"
    _write_universe(path, {"BBB": {}})
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    monkeypatch.setattr(finnhub_enrich, "_UNIVERSE_PATH", path)
    monkeypatch.setattr(finnhub_enrich.FinnhubProvider, "is_configured", classmethod(lambda cls: True))
    monkeypatch.setattr(
        finnhub_enrich.FinnhubProvider, "get_revisions_and_earnings",
        lambda self, ticker, use_cache=True: FinnhubData(ticker=ticker, error="recommendation:rate_limited"),
    )

    finnhub_enrich.enrich_universe_with_finnhub()

    universe = json.loads(path.read_text(encoding="utf-8"))
    assert universe["tickers"]["BBB"]["finnhub_error"] == "recommendation:rate_limited"


def test_enrich_heals_previous_error_on_success(monkeypatch, tmp_path):
    """Un ticker précédemment en échec doit repasser à None si le run suivant
    réussit — `finnhub_error` est toujours réécrit, jamais préservé en silence."""
    path = tmp_path / "universe.json"
    _write_universe(path, {"CCC": {"finnhub_error": "fetch_failed"}})
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    monkeypatch.setattr(finnhub_enrich, "_UNIVERSE_PATH", path)
    monkeypatch.setattr(finnhub_enrich.FinnhubProvider, "is_configured", classmethod(lambda cls: True))
    monkeypatch.setattr(
        finnhub_enrich.FinnhubProvider, "get_revisions_and_earnings",
        lambda self, ticker, use_cache=True: FinnhubData(ticker=ticker, error=None),
    )

    finnhub_enrich.enrich_universe_with_finnhub()

    universe = json.loads(path.read_text(encoding="utf-8"))
    assert universe["tickers"]["CCC"]["finnhub_error"] is None


def test_enrich_persists_analyst_actions(monkeypatch, tmp_path):
    """Étape 13 roadmap : le flux nominatif upgrade/downgrade doit être
    persisté dans universe.json sous `finnhub_analyst_actions`."""
    path = tmp_path / "universe.json"
    _write_universe(path, {"DDD": {}})
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    monkeypatch.setattr(finnhub_enrich, "_UNIVERSE_PATH", path)
    monkeypatch.setattr(finnhub_enrich.FinnhubProvider, "is_configured", classmethod(lambda cls: True))
    actions = [{"date": "2026-07-15", "firm": "Morgan Stanley", "action": "up",
                "from_grade": "Equal-Weight", "to_grade": "Overweight"}]
    monkeypatch.setattr(
        finnhub_enrich.FinnhubProvider, "get_revisions_and_earnings",
        lambda self, ticker, use_cache=True: FinnhubData(
            ticker=ticker, error=None, analyst_actions=actions,
        ),
    )

    finnhub_enrich.enrich_universe_with_finnhub()

    universe = json.loads(path.read_text(encoding="utf-8"))
    assert universe["tickers"]["DDD"]["finnhub_analyst_actions"] == actions


def test_enrich_preserves_prior_analyst_actions_when_none_returned(monkeypatch, tmp_path):
    """Pas de nouvelle action nominative ce run ≠ effacer l'historique déjà
    persisté (même garde-fou que `upgrade_downgrade_log`)."""
    path = tmp_path / "universe.json"
    prior_actions = [{"date": "2026-05-01", "firm": "Barclays", "action": "down",
                       "from_grade": "Buy", "to_grade": "Hold"}]
    _write_universe(path, {"EEE": {"finnhub_analyst_actions": prior_actions}})
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    monkeypatch.setattr(finnhub_enrich, "_UNIVERSE_PATH", path)
    monkeypatch.setattr(finnhub_enrich.FinnhubProvider, "is_configured", classmethod(lambda cls: True))
    monkeypatch.setattr(
        finnhub_enrich.FinnhubProvider, "get_revisions_and_earnings",
        lambda self, ticker, use_cache=True: FinnhubData(ticker=ticker, error=None, analyst_actions=[]),
    )

    finnhub_enrich.enrich_universe_with_finnhub()

    universe = json.loads(path.read_text(encoding="utf-8"))
    assert universe["tickers"]["EEE"]["finnhub_analyst_actions"] == prior_actions
