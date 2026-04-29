"""Tests — scripts/seed_delisted.py.

Verrouille la sémantique du bootstrap delisted.json :
  • _extract_date : prend as_of_date / snapshot_date ; fallback sur nom de fichier.
  • _read_snapshot : .json et .json.gz, fail sur garbage.
  • _gather_sources : agrège 3 sources, dédup par date (max tickers).
  • main() (dry-run + write) : append correct au registry, alert=False respecté.
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import date
from pathlib import Path

import pytest

# Le script vit dans backend/scripts/, doit être importable.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from modules import delisted
from scripts import seed_delisted as seed

# ─────────────────────────────────────────────────────────────────
# _read_snapshot
# ─────────────────────────────────────────────────────────────────

def test_read_snapshot_json(tmp_path):
    """Lit un .json clair → dict."""
    p = tmp_path / "u.json"
    p.write_text(json.dumps({"tickers": {"AAPL": {}}}), encoding="utf-8")
    payload = seed._read_snapshot(p)
    assert payload == {"tickers": {"AAPL": {}}}


def test_read_snapshot_gzipped(tmp_path):
    """Lit un .json.gz → dict décompressé."""
    p = tmp_path / "snap.json.gz"
    raw = json.dumps({"tickers": {"MSFT": {}}}).encode("utf-8")
    p.write_bytes(gzip.compress(raw))
    payload = seed._read_snapshot(p)
    assert payload == {"tickers": {"MSFT": {}}}


def test_read_snapshot_returns_none_on_garbage(tmp_path, capsys):
    """Fichier corrompu → None + warning printé."""
    p = tmp_path / "bad.json"
    p.write_text("not valid {", encoding="utf-8")
    assert seed._read_snapshot(p) is None
    out = capsys.readouterr().out
    assert "skip" in out


# ─────────────────────────────────────────────────────────────────
# _extract_date — prefer payload metadata, fallback filename
# ─────────────────────────────────────────────────────────────────

def test_extract_date_prefers_as_of_date(tmp_path):
    """Si `as_of_date` est dans le payload → utilisé."""
    p = tmp_path / "universe_19000101.json"  # mauvaise date dans le nom
    payload = {"as_of_date": "2026-04-22", "tickers": {}}
    assert seed._extract_date(p, payload) == date(2026, 4, 22)


def test_extract_date_falls_back_to_snapshot_date(tmp_path):
    """`snapshot_date` est l'autre clé acceptée."""
    p = tmp_path / "snap.json"
    payload = {"snapshot_date": "2026-04-23T08:00:00Z", "tickers": {}}
    assert seed._extract_date(p, payload) == date(2026, 4, 23)


def test_extract_date_falls_back_to_filename(tmp_path):
    """Sans metadata → parse le nom de fichier (YYYYMMDD)."""
    p = tmp_path / "universe_20260424_060000.json"
    assert seed._extract_date(p, {}) == date(2026, 4, 24)


def test_extract_date_returns_none_when_unparseable(tmp_path):
    """Ni metadata ni nom parsable → None."""
    p = tmp_path / "weird.json"
    assert seed._extract_date(p, {}) is None


# ─────────────────────────────────────────────────────────────────
# _gather_sources — agrégation 3 sources + dedup par date
# ─────────────────────────────────────────────────────────────────

@pytest.fixture
def isolate_paths(monkeypatch, tmp_path: Path):
    """Redirige les 3 paths sources vers tmp_path/{history,backups,universe}."""
    history = tmp_path / "history"
    backups = tmp_path / "backups"
    universe = tmp_path / "universe.json"
    history.mkdir()
    backups.mkdir()
    monkeypatch.setattr(seed, "_HISTORY_DIR", history)
    monkeypatch.setattr(seed, "_BACKUPS_DIR", backups)
    monkeypatch.setattr(seed, "_UNIVERSE_PATH", universe)
    # Le registry du module delisted aussi → on évite de polluer prod.
    monkeypatch.setattr(delisted, "DELISTED_PATH", tmp_path / "delisted.json")
    return history, backups, universe


def _write_history(d: Path, snap_date: str, tickers: dict) -> None:
    """Écrit un snapshot.json.gz dans history dir."""
    p = d / f"snapshot_{snap_date.replace('-', '')}.json.gz"
    payload = {"snapshot_date": snap_date, "tickers": tickers}
    p.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))


def _write_backup(d: Path, snap_date: str, tickers: dict) -> Path:
    """Écrit un universe_YYYYMMDD_HHMMSS.json dans backups dir."""
    p = d / f"universe_{snap_date.replace('-', '')}_120000.json"
    payload = {"as_of_date": snap_date, "tickers": tickers}
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_gather_sources_aggregates_three_sources(isolate_paths):
    """3 sources distinctes (1 history, 1 backup, 1 current) → 3 entries."""
    history, backups, universe = isolate_paths
    _write_history(history, "2026-04-22", {"AAPL": {}})
    _write_backup(backups, "2026-04-23", {"AAPL": {}, "MSFT": {}})
    universe.write_text(
        json.dumps({"as_of_date": "2026-04-25", "tickers": {"AAPL": {}}}),
        encoding="utf-8",
    )

    sources = seed._gather_sources()
    assert len(sources) == 3
    dates = [d for d, _, _ in sources]
    assert dates == [date(2026, 4, 22), date(2026, 4, 23), date(2026, 4, 25)]


def test_gather_sources_dedup_keeps_largest(isolate_paths):
    """2 sources même date → garde celle avec le plus de tickers (max info)."""
    history, backups, _ = isolate_paths
    _write_history(history, "2026-04-22", {"A": {}, "B": {}, "C": {}})  # 3 tk
    _write_backup(backups, "2026-04-22", {"A": {}, "B": {}})            # 2 tk

    sources = seed._gather_sources()
    assert len(sources) == 1
    _, _, tickers = sources[0]
    assert set(tickers) == {"A", "B", "C"}, "Doit garder le plus gros set"


def test_gather_sources_skips_corrupted_files(isolate_paths, capsys):
    """Fichier garbage → ignoré, pas de crash, warning printé."""
    history, _, _ = isolate_paths
    # Fichier history valide
    _write_history(history, "2026-04-22", {"OK": {}})
    # Fichier history corrompu
    bad = history / "snapshot_20260423.json.gz"
    bad.write_bytes(b"not gzip")

    sources = seed._gather_sources()
    # Seul le valide remonte.
    dates = [d for d, _, _ in sources]
    assert dates == [date(2026, 4, 22)]


def test_gather_sources_skips_payload_without_tickers(isolate_paths):
    """Snapshot avec `tickers={}` (vide) → ignoré (rien à diff-er)."""
    history, _, _ = isolate_paths
    _write_history(history, "2026-04-22", {})  # vide
    _write_history(history, "2026-04-23", {"AAPL": {}})
    sources = seed._gather_sources()
    dates = [d for d, _, _ in sources]
    assert dates == [date(2026, 4, 23)]


def test_gather_sources_returns_empty_when_no_sources_dir(isolate_paths, monkeypatch):
    """Si les dirs n'existent pas du tout → liste vide (premier run)."""
    history, backups, universe = isolate_paths
    # Supprime tout
    for p in history.iterdir():
        p.unlink()
    history.rmdir()
    backups.rmdir()
    monkeypatch.setattr(seed, "_HISTORY_DIR", Path("/nonexistent_history"))
    monkeypatch.setattr(seed, "_BACKUPS_DIR", Path("/nonexistent_backups"))
    assert seed._gather_sources() == []


# ─────────────────────────────────────────────────────────────────
# main() — orchestration end-to-end
# ─────────────────────────────────────────────────────────────────

def test_main_dry_run_writes_nothing(isolate_paths, monkeypatch, capsys):
    """`--dry-run` détecte les diffs mais ne touche pas delisted.json."""
    history, backups, _ = isolate_paths
    _write_history(history, "2026-04-22", {"A": {}, "B": {}, "C": {}})
    _write_history(history, "2026-04-23", {"A": {}})  # B et C retirés

    monkeypatch.setattr(sys, "argv", ["seed_delisted", "--dry-run"])
    rc = seed.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "+3 added" in out
    assert "-2 removed" in out
    assert "(dry-run" in out
    # Le registry ne doit PAS exister.
    assert not delisted.DELISTED_PATH.exists()


def test_main_writes_registry_with_alert_false(isolate_paths, monkeypatch):
    """En mode write, le registry est créé. alert=False est respecté
    (le seed historique ne doit pas spammer Telegram).
    """
    history, _, _ = isolate_paths
    _write_history(history, "2026-04-22", {"A": {}, "B": {}, "C": {}, "D": {},
                                              "E": {}, "F": {}, "G": {}, "H": {}})
    _write_history(history, "2026-04-23", {"A": {}})  # 7 retraits → > seuil 5

    sent: list[str] = []
    import modules.alerter as alerter_mod
    monkeypatch.setattr(alerter_mod, "_send_telegram_message",
                        lambda txt, *_a, **_kw: sent.append(txt))

    monkeypatch.setattr(sys, "argv", ["seed_delisted"])
    rc = seed.main()
    assert rc == 0

    # Registry persisté.
    assert delisted.DELISTED_PATH.exists()
    registry = delisted.load_registry()
    assert len(registry["tickers"]) == 8

    # AUCUN alerte Telegram envoyée malgré 7 ≥ 5 (alert=False bypass).
    assert sent == [], "seed_delisted ne doit jamais déclencher Telegram"


def test_main_returns_1_when_no_sources(isolate_paths, monkeypatch, capsys):
    """Aucune source disponible → exit 1 + message clair."""
    history, _, _ = isolate_paths
    # Vide tout history
    for p in history.iterdir():
        p.unlink()

    monkeypatch.setattr(sys, "argv", ["seed_delisted"])
    rc = seed.main()
    assert rc == 1
    out = capsys.readouterr().out
    assert "Aucune source" in out
