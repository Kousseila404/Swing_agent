"""Tests — digest Telegram quotidien (Étape 3 roadmap).

Vérifie : filtrage sur `conviction == "new_signal"` uniquement (pas de
répétition du top-N statique par score), lien `FRONTEND_URL` optionnel,
fail-open sur `qualification=None`.
"""
from __future__ import annotations

import config
from modules import daily_digest


def _proposal(ticker, *, titan_score=50, conviction=None, verdict="BUY",
              narrative=None, created_at="2026-07-01T00:00:00Z"):
    qualification = None
    if conviction is not None:
        qualification = {"conviction": conviction, "narrative": narrative or ticker}
    return {
        "ticker": ticker,
        "created_at": created_at,
        "context": {
            "titan_score": titan_score,
            "buy_signal": {"verdict": verdict},
            "qualification": qualification,
        },
    }


def test_no_new_signals_no_repetition(monkeypatch):
    pending = [
        _proposal("AAPL", titan_score=90, conviction="confirmed"),
        _proposal("MSFT", titan_score=85, conviction="watch"),
    ]
    monkeypatch.setattr(daily_digest.proposals, "list_all", lambda status=None: pending)
    monkeypatch.setattr(daily_digest, "_equity_snapshot", lambda: {})

    text = daily_digest.build_digest_text()

    assert "Aucun nouveau signal" in text
    assert "AAPL" not in text
    assert "MSFT" not in text
    assert "2 proposition(s) en attente" in text


def test_new_signals_listed_with_narrative(monkeypatch):
    pending = [
        _proposal("AAPL", titan_score=90, conviction="confirmed"),
        _proposal(
            "NVDA", titan_score=95, verdict="STRONG_BUY",
            conviction="new_signal", narrative="Signal fort — momentum en hausse.",
        ),
    ]
    monkeypatch.setattr(daily_digest.proposals, "list_all", lambda status=None: pending)
    monkeypatch.setattr(daily_digest, "_equity_snapshot", lambda: {})

    text = daily_digest.build_digest_text()

    assert "1 nouveau(x) signal(aux)" in text
    assert "NVDA" in text
    assert "STRONG_BUY" in text
    assert "Signal fort — momentum en hausse." in text
    assert "AAPL" not in text  # confirmé, stable, pas de bruit répété


def test_fail_open_qualification_none_never_counted_new(monkeypatch):
    pending = [_proposal("TSLA", titan_score=80, conviction=None)]
    monkeypatch.setattr(daily_digest.proposals, "list_all", lambda status=None: pending)
    monkeypatch.setattr(daily_digest, "_equity_snapshot", lambda: {})

    text = daily_digest.build_digest_text()

    assert "Aucun nouveau signal" in text


def test_no_pending_at_all(monkeypatch):
    monkeypatch.setattr(daily_digest.proposals, "list_all", lambda status=None: [])
    monkeypatch.setattr(daily_digest, "_equity_snapshot", lambda: {})

    text = daily_digest.build_digest_text()

    assert "Aucune proposition en attente" in text
    assert "Aucun nouveau signal" in text


def test_link_omitted_when_frontend_url_unset(monkeypatch):
    monkeypatch.setattr(config, "FRONTEND_URL", "")
    monkeypatch.setattr(daily_digest.proposals, "list_all", lambda status=None: [])
    monkeypatch.setattr(daily_digest, "_equity_snapshot", lambda: {})

    text = daily_digest.build_digest_text()

    assert "href" not in text


def test_link_included_when_frontend_url_set(monkeypatch):
    monkeypatch.setattr(config, "FRONTEND_URL", "https://titan.example.com")
    monkeypatch.setattr(daily_digest.proposals, "list_all", lambda status=None: [])
    monkeypatch.setattr(daily_digest, "_equity_snapshot", lambda: {})

    text = daily_digest.build_digest_text()

    assert 'href="https://titan.example.com/#/proposals"' in text


def test_recurring_undecided_section_shown(monkeypatch):
    items = [
        {"ticker": "INCY", "status": "expired", "created_at": "2026-06-29T00:00:00Z",
         "context": {"titan_score": 72.0}},
        {"ticker": "INCY", "status": "expired", "created_at": "2026-07-17T00:00:00Z",
         "context": {"titan_score": 72.0}},
        _proposal("INCY", titan_score=73, conviction=None, created_at="2026-08-10T00:00:00Z"),
    ]
    monkeypatch.setattr(daily_digest.proposals, "list_all", lambda status=None: items)
    monkeypatch.setattr(daily_digest, "_equity_snapshot", lambda: {})

    text = daily_digest.build_digest_text()

    assert "récurrente" in text
    assert "INCY" in text
    assert "recyclée 3×" in text


def test_recurring_undecided_section_omitted_below_threshold(monkeypatch):
    pending = [_proposal("AAPL", titan_score=90, conviction=None)]
    monkeypatch.setattr(daily_digest.proposals, "list_all", lambda status=None: pending)
    monkeypatch.setattr(daily_digest, "_equity_snapshot", lambda: {})

    text = daily_digest.build_digest_text()

    assert "récurrente" not in text


def test_send_daily_digest_fail_open_when_telegram_raises(monkeypatch):
    monkeypatch.setattr(daily_digest.proposals, "list_all", lambda status=None: [])
    monkeypatch.setattr(daily_digest, "_equity_snapshot", lambda: {})

    def _boom(text):
        raise RuntimeError("telegram down")

    monkeypatch.setattr("modules.alerter._send_telegram_message", _boom)

    daily_digest.send_daily_digest()  # ne doit jamais lever
