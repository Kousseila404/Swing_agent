"""Tests unitaires — modules/delisted.py

Couvre :
  • record_diff : ajouts, retraits, réincarnations.
  • Alerte Telegram : trigger ≥ seuil, silence < seuil, silence si alert=False.
  • get_active_at : sémantique point-in-time (first_seen / removed_at).
  • list_delisted : filtre + tri sur removed_at.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from modules import delisted


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch, tmp_path: Path):
    """Redirige `DELISTED_PATH` vers un fichier temporaire — chaque test
    démarre avec un registry vide, peu importe l'état de prod.
    """
    target = tmp_path / "delisted_test.json"
    monkeypatch.setattr(delisted, "DELISTED_PATH", target)
    return target


@pytest.fixture
def captured_alerts(monkeypatch):
    """Intercepte les appels à `_send_telegram_message` pour les inspecter
    sans réellement frapper l'API Telegram.
    """
    sent: list[str] = []

    def _fake_send(text: str, *_args, **_kwargs) -> None:
        sent.append(text)

    # On patch dans le module alerter — c'est l'import dynamique fait par
    # `delisted.record_diff`.
    import modules.alerter as alerter_mod
    monkeypatch.setattr(alerter_mod, "_send_telegram_message", _fake_send)
    return sent


# ─────────────────────────────────────────────────────────────────
# record_diff — sémantique de base
# ─────────────────────────────────────────────────────────────────

def test_record_diff_first_run_records_all_as_added(captured_alerts):
    """prev=None → tous les tickers du new sont marqués added (1er run).
    Aucun retrait → pas d'alerte attendue.
    """
    new = {"AAPL": {"sector": "Technology"}, "MSFT": {"sector": "Technology"}}
    summary = delisted.record_diff(prev_tickers=None, new_tickers=new, today="2026-04-22")

    assert sorted(summary["added"]) == ["AAPL", "MSFT"]
    assert summary["removed"] == []
    assert captured_alerts == []

    registry = delisted.load_registry()
    assert set(registry["tickers"].keys()) == {"AAPL", "MSFT"}
    for tk in ("AAPL", "MSFT"):
        meta = registry["tickers"][tk]
        assert meta["first_seen"] == "2026-04-22"
        assert meta["removed_at"] is None


def test_record_diff_marks_dropped_tickers_as_removed(captured_alerts):
    """Ticker dans prev mais pas new → removed_at + last_payload."""
    delisted.record_diff(
        prev_tickers=None,
        new_tickers={"AAPL": {"sector": "Tech"}, "TEAM": {"sector": "Tech"}},
        today="2026-04-22",
    )
    captured_alerts.clear()

    summary = delisted.record_diff(
        prev_tickers={"AAPL": {"sector": "Tech"}, "TEAM": {"sector": "Tech"}},
        new_tickers={"AAPL": {"sector": "Tech"}},
        today="2026-04-23",
    )

    assert summary["removed"] == ["TEAM"]
    registry = delisted.load_registry()
    team_meta = registry["tickers"]["TEAM"]
    assert team_meta["removed_at"] == "2026-04-23"
    assert team_meta["first_seen"] == "2026-04-22"
    assert team_meta["last_sector"] == "Tech"


def test_record_diff_reentry_clears_removed_at(captured_alerts):
    """Ticker retiré puis ré-ajouté → removed_at remis à None (actif)."""
    # Day 1 : T1 + T2 actifs
    delisted.record_diff(prev_tickers=None,
                         new_tickers={"T1": {}, "T2": {}}, today="2026-04-22")
    # Day 2 : T2 sort
    delisted.record_diff(prev_tickers={"T1": {}, "T2": {}},
                         new_tickers={"T1": {}}, today="2026-04-23")
    # Day 3 : T2 revient (M&A reverse, IPO bis)
    delisted.record_diff(prev_tickers={"T1": {}},
                         new_tickers={"T1": {}, "T2": {}}, today="2026-04-25")

    registry = delisted.load_registry()
    t2 = registry["tickers"]["T2"]
    assert t2["removed_at"] is None  # ré-actif
    # first_seen reste celui du 1er passage
    assert t2["first_seen"] == "2026-04-22"


# ─────────────────────────────────────────────────────────────────
# Alerte Telegram — les 3 cas comportementaux
# ─────────────────────────────────────────────────────────────────

def test_alert_triggers_above_threshold(captured_alerts):
    """7 retraits ≥ seuil 5 → alerte envoyée avec sample + secteurs."""
    prev = {f"T{i}": {"sector": "Energy" if i < 6 else "Tech"} for i in range(10)}
    new = {f"T{i}": {} for i in range(3)}  # retire T3..T9 = 7 tickers

    summary = delisted.record_diff(prev_tickers=prev, new_tickers=new,
                                   today="2026-04-23")

    assert len(summary["removed"]) == 7
    assert len(captured_alerts) == 1, "Alerte attendue (7 ≥ seuil 5)"
    msg = captured_alerts[0]
    assert "7 tickers retirés" in msg
    # Le breakdown sectoriel doit lister Energy en majoritaire (3 sur 6 sortants).
    assert "Energy" in msg
    # Sample : on doit retrouver au moins un ticker retiré dans le payload.
    assert "T3" in msg or "T9" in msg


def test_alert_silent_below_threshold(captured_alerts):
    """4 retraits < seuil 5 → aucune alerte."""
    prev = {f"T{i}": {} for i in range(8)}
    new = {f"T{i}": {} for i in range(4)}  # 4 retraits

    summary = delisted.record_diff(prev_tickers=prev, new_tickers=new,
                                   today="2026-04-23")

    assert len(summary["removed"]) == 4
    assert captured_alerts == [], "Aucune alerte attendue sous le seuil"


def test_alert_skipped_when_alert_false(captured_alerts):
    """`alert=False` → pas d'alerte même sur retrait massif (mode bootstrap)."""
    prev = {f"X{i}": {"sector": "Energy"} for i in range(20)}
    summary = delisted.record_diff(prev_tickers=prev, new_tickers={},
                                   today="2026-04-23", alert=False)

    assert len(summary["removed"]) == 20
    assert captured_alerts == [], "alert=False doit court-circuiter Telegram"


def test_alert_failopen_when_telegram_raises(captured_alerts, monkeypatch):
    """Si Telegram lève, le diff est tout de même persisté (fail-open).

    Sécurité : une panne réseau / token invalide ne doit JAMAIS casser
    `save_universe` côté universe_engine.
    """
    import modules.alerter as alerter_mod

    def _explode(*_a, **_kw):
        raise RuntimeError("Telegram down")

    monkeypatch.setattr(alerter_mod, "_send_telegram_message", _explode)

    prev = {f"T{i}": {} for i in range(10)}
    new = {f"T{i}": {} for i in range(3)}

    # Doit ne pas lever, et doit tout de même persister le registry.
    summary = delisted.record_diff(prev, new, today="2026-04-23")
    assert len(summary["removed"]) == 7

    registry = delisted.load_registry()
    # Les 7 tickers retirés doivent bien être marqués removed_at malgré l'échec.
    removed_in_reg = [t for t, m in registry["tickers"].items()
                      if m.get("removed_at") == "2026-04-23"]
    assert len(removed_in_reg) == 7


# ─────────────────────────────────────────────────────────────────
# get_active_at — sémantique point-in-time
# ─────────────────────────────────────────────────────────────────

def test_get_active_at_respects_first_seen_and_removed_at():
    """A apparu @ J0, retiré @ J3 ; B apparu @ J1, encore actif.

    Vérifie 4 dates :
      - J-1 (avant tout) → vide
      - J0  → {A}
      - J2  → {A, B}
      - J3  → {B} (A retiré)
      - J5  → {B} (toujours)
    """
    # Construire le registry directement
    delisted.record_diff(None, {"A": {}}, today="2026-04-20", alert=False)
    delisted.record_diff({"A": {}}, {"A": {}, "B": {}}, today="2026-04-21", alert=False)
    delisted.record_diff({"A": {}, "B": {}}, {"B": {}}, today="2026-04-23", alert=False)

    assert delisted.get_active_at("2026-04-19") == set()
    assert delisted.get_active_at("2026-04-20") == {"A"}
    assert delisted.get_active_at("2026-04-22") == {"A", "B"}
    assert delisted.get_active_at("2026-04-23") == {"B"}
    assert delisted.get_active_at("2026-04-25") == {"B"}


def test_get_active_at_accepts_date_object():
    """L'API doit accepter un objet `date`, pas seulement une string."""
    delisted.record_diff(None, {"AAPL": {}}, today="2026-04-22", alert=False)
    assert delisted.get_active_at(date(2026, 4, 22)) == {"AAPL"}


def test_get_active_at_empty_registry_returns_current_or_empty():
    """Registry vide → fallback sur `current_tickers` argument (premier run).

    Sans current_tickers → set vide (ne corrompt pas le filtre backtest).
    """
    assert delisted.get_active_at("2026-04-22") == set()
    assert delisted.get_active_at(
        "2026-04-22", current_tickers={"NVDA", "MSFT"}
    ) == {"NVDA", "MSFT"}


# ─────────────────────────────────────────────────────────────────
# list_delisted
# ─────────────────────────────────────────────────────────────────

def test_list_delisted_only_returns_currently_removed():
    """Un ticker ré-incarné ne doit plus apparaître dans list_delisted."""
    # T1 retiré, T2 retiré puis réintroduit
    delisted.record_diff(None, {"T1": {}, "T2": {}, "T3": {}},
                         today="2026-04-20", alert=False)
    delisted.record_diff({"T1": {}, "T2": {}, "T3": {}}, {"T3": {}},
                         today="2026-04-22", alert=False)
    delisted.record_diff({"T3": {}}, {"T2": {}, "T3": {}},
                         today="2026-04-25", alert=False)

    rows = delisted.list_delisted()
    tickers = {r["ticker"] for r in rows}
    assert tickers == {"T1"}, f"Seul T1 devrait être listed, got {tickers}"


def test_list_delisted_sorted_desc_by_removed_at():
    """Tri chronologique inversé — le plus récent en tête."""
    delisted.record_diff(None, {"A": {}, "B": {}, "C": {}},
                         today="2026-04-20", alert=False)
    # A retiré le 22, B le 25, C le 23
    delisted.record_diff({"A": {}, "B": {}, "C": {}}, {"B": {}, "C": {}},
                         today="2026-04-22", alert=False)
    delisted.record_diff({"B": {}, "C": {}}, {"B": {}},
                         today="2026-04-23", alert=False)
    delisted.record_diff({"B": {}}, {},
                         today="2026-04-25", alert=False)

    rows = delisted.list_delisted()
    dates = [r["removed_at"] for r in rows]
    assert dates == sorted(dates, reverse=True), \
        f"Attendu desc, got {dates}"
