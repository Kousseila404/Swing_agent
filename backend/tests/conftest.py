"""Fixtures communes aux tests SwingQuant."""
from __future__ import annotations

import sys
from pathlib import Path

# Permet `import config`, `from modules.xxx import yyy` depuis les tests
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_universe_history(tmp_path_factory, monkeypatch):
    """Filet de sécurité — empêche les tests qui déclenchent
    `sector_metrics` de toucher au vrai `data/.universe_history/`.

    Le scoring déclenche un hook `write_snapshot_safe()` (voir
    `sector_metrics/__init__.py`). Sans cette isolation, n'importe quel test
    qui appelle le scoring écrase le snapshot du jour live avec ses
    fixtures (incident 2026-04-27).
    """
    from modules import universe_history as uh
    monkeypatch.setattr(
        uh, "HISTORY_DIR",
        tmp_path_factory.mktemp("universe_history_isolated"),
    )


_PROD_DATA_GUARD_FILES = ("trade_journal.csv", "trade_journal.duckdb", "proposals.json")


@pytest.fixture(autouse=True)
def _isolate_trade_journal(tmp_path, monkeypatch):
    """Audit 2026-09-17 (P2-2) — aucun test ne doit lire/écrire le journal
    de trades **prod**. Tous les modules qui portent une copie du chemin sont
    redirigés vers un fichier temporaire vide (un test qui a besoin d'un
    journal l'écrit lui-même). Les fixtures spécifiques d'un test peuvent
    ré-écraser ces attributs (elles s'appliquent après cette fixture)."""
    csv = tmp_path / "trade_journal.csv"
    lock = tmp_path / "trade_journal.csv.lock"
    db = tmp_path / "trade_journal.duckdb"
    targets = {
        "modules.utils": ("CSV_PATH", "CSV_LOCK_PATH"),
        "modules.duckdb_journal": ("CSV_PATH", "DUCKDB_PATH"),
        "modules.api_core": ("CSV_PATH", "CSV_LOCK_PATH"),
        "modules.broker_gateway": ("CSV_PATH", "CSV_LOCK_PATH"),
        "modules.tracker.evaluation": ("CSV_PATH", "CSV_LOCK_PATH"),
    }
    import importlib
    for mod_name, attrs in targets.items():
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        for attr in attrs:
            if not hasattr(mod, attr):
                continue
            value = db if attr == "DUCKDB_PATH" else (lock if "LOCK" in attr else csv)
            monkeypatch.setattr(mod, attr, value)
    yield


def pytest_sessionstart(session):
    """Snapshot des fichiers data prod (garde-fou, activé par SWINGQUANT_TEST_GUARD=1)."""
    import hashlib
    import os
    if os.getenv("SWINGQUANT_TEST_GUARD") != "1":
        return
    snap = {}
    for name in _PROD_DATA_GUARD_FILES:
        p = _BACKEND_ROOT / "data" / name
        snap[name] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    session.config._swingquant_data_snapshot = snap


def pytest_sessionfinish(session, exitstatus):
    import hashlib
    snap = getattr(session.config, "_swingquant_data_snapshot", None)
    if not snap:
        return
    changed = []
    for name, digest in snap.items():
        p = _BACKEND_ROOT / "data" / name
        now = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        if now != digest:
            changed.append(name)
    if changed:
        session.config._swingquant_guard_failed = changed
        session.exitstatus = 3
        print(f"\n[GUARD] Fichiers data prod modifiés pendant les tests : {changed}")


@pytest.fixture
def tmp_csv(tmp_path):
    """Retourne un chemin CSV temporaire pour les tests de journal."""
    return tmp_path / "trade_journal.csv"


@pytest.fixture
def sample_scan():
    """Objet `scan` minimal compatible PaperBroker.submit_order."""
    class _Scan:
        ticker = "AAPL"
        direction = "LONG"
        price = 150.0
        stop_loss = 147.0
        take_profit = 156.0
        position_size = 10
        rr_ratio = 2.0
        signal = "TEST"
        sector_etf = "XLK"
    return _Scan()
