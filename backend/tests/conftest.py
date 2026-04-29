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
