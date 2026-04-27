"""Fixtures communes aux tests SwingQuant."""
from __future__ import annotations

import sys
from pathlib import Path

# Permet `import config`, `from modules.xxx import yyy` depuis les tests
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import pytest  # noqa: E402


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
