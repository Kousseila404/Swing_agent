"""Tests — notification Telegram `_notify_new_proposals` (Étape 10 roadmap).

Vérifie : badge de conviction préfixé (même mapping que
`signal_qualification.CONVICTION_BADGES`), narratif affiché à la place du
score brut, fail-open quand `qualification` est absent/`None` (comportement
identique à avant cette étape).
"""
from __future__ import annotations

from routers import proposals as proposals_router


def _item(ticker, *, size=10, entry=100.0, sector="Tech", titan_score=77.0,
          conviction=None, narrative=None):
    qualification = None
    if conviction is not None:
        qualification = {"conviction": conviction, "narrative": narrative}
    return {
        "ticker": ticker,
        "sector": sector,
        "size": size,
        "entry": entry,
        "context": {"titan_score": titan_score, "qualification": qualification},
    }


def _capture_telegram(monkeypatch):
    sent = {}

    def _fake_send(text):
        sent["text"] = text

    monkeypatch.setattr("modules.alerter._send_telegram_message", _fake_send)
    return sent


def test_new_signal_badge_and_narrative(monkeypatch):
    sent = _capture_telegram(monkeypatch)
    items = [_item("NVDA", conviction="new_signal", narrative="Signal fort — momentum en hausse.")]

    proposals_router._notify_new_proposals(items)

    assert "\U0001f525 Nouveau" in sent["text"]
    assert "Signal fort — momentum en hausse." in sent["text"]
    assert "score 77.0" not in sent["text"]


def test_confirmed_and_watch_badges(monkeypatch):
    sent = _capture_telegram(monkeypatch)
    items = [
        _item("AAPL", conviction="confirmed", narrative="Bilan solide."),
        _item("MSFT", conviction="watch", narrative="Proche du seuil."),
    ]

    proposals_router._notify_new_proposals(items)

    assert "⭐ Confirmé" in sent["text"]
    assert "\U0001f441 Surveillance" in sent["text"]


def test_fail_open_qualification_none_keeps_score(monkeypatch):
    sent = _capture_telegram(monkeypatch)
    items = [_item("TSLA", conviction=None, titan_score=42.5)]

    proposals_router._notify_new_proposals(items)

    text = sent["text"]
    assert "TSLA" in text
    assert "score 42.5" in text
    # Aucun badge n'est affiché sans qualification.
    assert "\U0001f525" not in text
    assert "⭐" not in text
    assert "\U0001f441" not in text


def test_fail_open_qualification_empty_dict_keeps_score(monkeypatch):
    sent = _capture_telegram(monkeypatch)
    items = [_item("TSLA", titan_score=42.5)]
    items[0]["context"]["qualification"] = {}

    proposals_router._notify_new_proposals(items)

    assert "score 42.5" in sent["text"]


def test_notify_fail_open_when_telegram_raises(monkeypatch):
    def _boom(text):
        raise RuntimeError("telegram down")

    monkeypatch.setattr("modules.alerter._send_telegram_message", _boom)
    items = [_item("TSLA", conviction="new_signal", narrative="x")]

    proposals_router._notify_new_proposals(items)  # ne doit jamais lever
