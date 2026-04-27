"""Tests historisation universe — write/read/list/iter + helpers de query.

Snapshots isolés via tmp_path (HISTORY_DIR monkey-patché) pour éviter de
polluer le filesystem prod entre tests.
"""
from __future__ import annotations

import gzip
import json
from datetime import date

import pytest

from modules import universe_history as uh


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    """Redirige HISTORY_DIR vers tmp_path → tests indépendants."""
    monkeypatch.setattr(uh, "HISTORY_DIR", tmp_path / ".universe_history")
    return tmp_path / ".universe_history"


def _mk_scored(n: int = 3) -> dict:
    """Mini scored universe pour tests."""
    return {
        f"T{i}": {
            "ticker": f"T{i}",
            "sector": "Technology",
            "titan_composite_score": 50.0 + i,
            "quality_score": 60.0,
            "value_score":   55.0,
            "risk_score":    65.0,
            "momentum_return_pct": 10.0 + i,
        }
        for i in range(n)
    }


# ─────────────────────────────────────────────────────────────────
# WRITE
# ─────────────────────────────────────────────────────────────────

def test_write_snapshot_creates_gzipped_file(isolated_history):
    p = uh.write_snapshot(
        _mk_scored(),
        universe_updated_at="2026-04-22T10:00:00Z",
        macro={"regime": "BULL_MARKET", "vix": 17.5},
        snapshot_date=date(2026, 4, 22),
    )
    assert p.exists()
    assert p.name == "snapshot_20260422.json.gz"
    # Vérif le contenu est lisible
    raw = gzip.decompress(p.read_bytes())
    payload = json.loads(raw)
    assert payload["snapshot_date"] == "2026-04-22"
    assert payload["n_tickers"] == 3
    assert payload["macro"]["regime"] == "BULL_MARKET"
    assert "T0" in payload["tickers"]


def test_write_snapshot_dedup_same_day(isolated_history):
    """2 écritures même jour → la seconde écrase (1 snapshot canonique/jour)."""
    p1 = uh.write_snapshot(
        _mk_scored(2),
        snapshot_date=date(2026, 4, 22),
    )
    p2 = uh.write_snapshot(
        _mk_scored(5),
        snapshot_date=date(2026, 4, 22),
    )
    assert p1 == p2  # même path
    snap = uh.read_snapshot(date(2026, 4, 22))
    assert snap["n_tickers"] == 5  # second write a écrasé


def test_write_snapshot_safe_returns_none_on_error(isolated_history, monkeypatch):
    """Wrapper fail-open : OSError → None, pas d'exception remontée."""
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(uh, "write_snapshot", boom)
    out = uh.write_snapshot_safe(_mk_scored())
    assert out is None


# ─────────────────────────────────────────────────────────────────
# READ
# ─────────────────────────────────────────────────────────────────

def test_read_snapshot_missing_returns_none(isolated_history):
    assert uh.read_snapshot(date(2020, 1, 1)) is None


def test_read_snapshot_corrupt_raises(isolated_history):
    """Snapshot dont le contenu n'est pas du JSON valide → RuntimeError."""
    isolated_history.mkdir(parents=True, exist_ok=True)
    p = isolated_history / "snapshot_20260422.json.gz"
    # Écrire des bytes non-gzip-valides
    p.write_bytes(b"not gzip content")
    with pytest.raises(RuntimeError, match="corrupted"):
        uh.read_snapshot(date(2026, 4, 22))


# ─────────────────────────────────────────────────────────────────
# LIST + ITER
# ─────────────────────────────────────────────────────────────────

def test_list_snapshots_empty_dir(isolated_history):
    assert uh.list_snapshots() == []


def test_list_snapshots_chronological_order(isolated_history):
    for d in [date(2026, 4, 22), date(2026, 4, 20), date(2026, 4, 21)]:
        uh.write_snapshot(_mk_scored(), snapshot_date=d)
    dates = uh.list_snapshots()
    assert dates == [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]


def test_iter_snapshots_filters_by_range(isolated_history):
    for d in [date(2026, 4, 18), date(2026, 4, 20), date(2026, 4, 22), date(2026, 4, 24)]:
        uh.write_snapshot(_mk_scored(), snapshot_date=d)
    out = list(uh.iter_snapshots(start=date(2026, 4, 19), end=date(2026, 4, 23)))
    assert [d for d, _ in out] == [date(2026, 4, 20), date(2026, 4, 22)]


def test_iter_snapshots_skips_corrupt(isolated_history, caplog):
    """Snapshot corrompu en milieu de range → skip + log warning, pas crash."""
    uh.write_snapshot(_mk_scored(), snapshot_date=date(2026, 4, 20))
    # Écrire un snapshot corrompu pour le 21
    isolated_history.mkdir(parents=True, exist_ok=True)
    (isolated_history / "snapshot_20260421.json.gz").write_bytes(b"junk")
    uh.write_snapshot(_mk_scored(), snapshot_date=date(2026, 4, 22))

    dates_yielded = [d for d, _ in uh.iter_snapshots()]
    # Le corrompu (21) skip, les 2 valides yieldés
    assert dates_yielded == [date(2026, 4, 20), date(2026, 4, 22)]


# ─────────────────────────────────────────────────────────────────
# QUERY HELPERS
# ─────────────────────────────────────────────────────────────────

def test_ticker_history_returns_per_date_rows(isolated_history):
    # Snapshot 20 : T0 score=50, T1=51
    uh.write_snapshot({
        "T0": {"titan_composite_score": 50.0, "sector": "Tech"},
        "T1": {"titan_composite_score": 51.0, "sector": "Tech"},
    }, snapshot_date=date(2026, 4, 20))
    # Snapshot 22 : T0 score=55, T1 absent
    uh.write_snapshot({
        "T0": {"titan_composite_score": 55.0, "sector": "Tech"},
    }, snapshot_date=date(2026, 4, 22))

    hist = uh.ticker_history("T0")
    assert len(hist) == 2
    assert hist[0]["date"] == "2026-04-20"
    assert hist[0]["titan_composite_score"] == 50.0
    assert hist[1]["date"] == "2026-04-22"
    assert hist[1]["titan_composite_score"] == 55.0

    # T1 manquant le 22 → uniquement la ligne du 20
    hist_t1 = uh.ticker_history("T1")
    assert len(hist_t1) == 1
    assert hist_t1[0]["date"] == "2026-04-20"


def test_ticker_history_field_filter(isolated_history):
    uh.write_snapshot({
        "T0": {"titan_composite_score": 50.0, "quality_score": 60.0, "sector": "Tech"},
    }, snapshot_date=date(2026, 4, 22))
    hist = uh.ticker_history("T0", fields=["quality_score"])
    assert hist[0]["quality_score"] == 60.0
    assert "titan_composite_score" not in hist[0]
    assert hist[0]["date"] == "2026-04-22"  # injecté quoi qu'il arrive


def test_universe_field_series(isolated_history):
    uh.write_snapshot({
        "T0": {"titan_composite_score": 50.0},
        "T1": {"titan_composite_score": 60.0},
    }, snapshot_date=date(2026, 4, 20))
    uh.write_snapshot({
        "T0": {"titan_composite_score": 55.0},
        "T1": {"titan_composite_score": 65.0},
    }, snapshot_date=date(2026, 4, 22))
    series = uh.universe_field_series("titan_composite_score")
    assert series["T0"] == [("2026-04-20", 50.0), ("2026-04-22", 55.0)]
    assert series["T1"] == [("2026-04-20", 60.0), ("2026-04-22", 65.0)]


def test_atomic_write_no_truncated_file(isolated_history):
    """Vérifie qu'aucun .tmp ne reste après une écriture réussie."""
    uh.write_snapshot(_mk_scored(), snapshot_date=date(2026, 4, 22))
    tmp_files = list(isolated_history.glob("*.tmp"))
    assert tmp_files == []
