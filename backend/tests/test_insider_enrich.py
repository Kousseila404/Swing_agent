"""Tests insider_enrich — propagation de l'échec SEC EDGAR (Étape 0, 2026-07-20).

`_enrich_one` doit exposer `insider_error` dans la row persistée (avant ce
run, `activity.error`/`pillar["reason"]` étaient calculés mais jamais
écrits dans universe.json — invisibles pour data_confidence.py).
"""
from __future__ import annotations

from modules import insider_enrich
from modules.sec_edgar import InsiderActivity


def test_enrich_one_success_has_no_error(monkeypatch):
    monkeypatch.setattr(
        insider_enrich, "fetch_insider_activity",
        lambda ticker: InsiderActivity(ticker=ticker, n_filings_scanned=6),
    )
    ticker, fields = insider_enrich._enrich_one("AAA")
    assert ticker == "AAA"
    assert fields["insider_error"] is None


def test_enrich_one_quiet_ticker_has_no_error(monkeypatch):
    """0 filing trouvé (insiders calmes) n'est PAS une panne — error=None."""
    monkeypatch.setattr(
        insider_enrich, "fetch_insider_activity",
        lambda ticker: InsiderActivity(ticker=ticker, n_filings_scanned=0),
    )
    _, fields = insider_enrich._enrich_one("BBB")
    assert fields["insider_error"] is None
    # data_quality tombe à 0 aussi dans ce cas — mais SANS insider_error,
    # data_confidence ne doit PAS le traiter comme une panne (cf. module).
    assert fields["insider_data_quality"] == 0.0


def test_enrich_one_propagates_fetch_failure(monkeypatch):
    monkeypatch.setattr(
        insider_enrich, "fetch_insider_activity",
        lambda ticker: InsiderActivity(ticker=ticker, error="sec_fetch_failed"),
    )
    ticker, fields = insider_enrich._enrich_one("CCC")
    assert fields["insider_error"] == "sec_fetch_failed"
    assert fields["insider_data_quality"] == 0.0


def test_enrich_one_propagates_cik_unknown(monkeypatch):
    monkeypatch.setattr(
        insider_enrich, "fetch_insider_activity",
        lambda ticker: InsiderActivity(ticker=ticker, error="cik_unknown"),
    )
    _, fields = insider_enrich._enrich_one("DDD")
    assert fields["insider_error"] == "cik_unknown"


def test_enrich_one_propagates_most_recent_8k(monkeypatch):
    """Étape 9 — la date du 8-K le plus récent doit être persistée."""
    monkeypatch.setattr(
        insider_enrich, "fetch_insider_activity",
        lambda ticker: InsiderActivity(
            ticker=ticker, n_filings_scanned=3, most_recent_8k_date="2026-07-10",
        ),
    )
    _, fields = insider_enrich._enrich_one("EEE")
    assert fields["insider_most_recent_8k"] == "2026-07-10"


def test_enrich_one_no_8k_is_none(monkeypatch):
    monkeypatch.setattr(
        insider_enrich, "fetch_insider_activity",
        lambda ticker: InsiderActivity(ticker=ticker, n_filings_scanned=3),
    )
    _, fields = insider_enrich._enrich_one("FFF")
    assert fields["insider_most_recent_8k"] is None


def test_enrich_one_propagates_most_recent_10k_10q(monkeypatch):
    """Étape 11 — les dates 10-K/10-Q les plus récentes doivent être persistées."""
    monkeypatch.setattr(
        insider_enrich, "fetch_insider_activity",
        lambda ticker: InsiderActivity(
            ticker=ticker, n_filings_scanned=3,
            most_recent_10k_date="2026-02-01",
            most_recent_10q_date="2026-07-10",
        ),
    )
    _, fields = insider_enrich._enrich_one("GGG")
    assert fields["insider_most_recent_10k"] == "2026-02-01"
    assert fields["insider_most_recent_10q"] == "2026-07-10"


def test_enrich_one_no_10k_10q_is_none(monkeypatch):
    monkeypatch.setattr(
        insider_enrich, "fetch_insider_activity",
        lambda ticker: InsiderActivity(ticker=ticker, n_filings_scanned=3),
    )
    _, fields = insider_enrich._enrich_one("HHH")
    assert fields["insider_most_recent_10k"] is None
    assert fields["insider_most_recent_10q"] is None
