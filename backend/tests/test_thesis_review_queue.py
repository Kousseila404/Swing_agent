"""Tests unitaires — modules/thesis_review_queue.py.

Isolation stricte du store disque (autouse) — même précaution que les
autres stores my_portfolio (project_test_leaks_prod_duckdb_2026-08-14).
"""
from __future__ import annotations

import pytest

from modules import thesis_review_queue as trq


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(trq, "STORE_PATH", tmp_path / "thesis_review_queue.json")
    monkeypatch.setattr(trq, "_LOCK_PATH", tmp_path / "thesis_review_queue.json.lock")


# ─────────────────────────────────────────────────────────────────
# Contrainte structurelle — ce module ne peut PAS toucher au bloc verification
# ─────────────────────────────────────────────────────────────────

def test_module_never_imports_my_portfolio_thesis():
    """Garde-fou statique : ce module ne doit même pas pouvoir appeler
    update_thesis() par erreur future — il ne l'importe pas du tout."""
    import inspect

    import modules.thesis_review_queue as mod
    source = inspect.getsource(mod)
    assert "my_portfolio_thesis" not in source
    assert "update_thesis" not in source


# ─────────────────────────────────────────────────────────────────
# add_entry / list_entries
# ─────────────────────────────────────────────────────────────────

def test_add_entry_happy_path():
    entry = trq.add_entry(
        "BNP.PA", execution_type="complete",
        findings=[{"topic": "CET1", "constat": "13.1%, stable vs 13.0% T1", "source": "communiqué T2 2026"}],
        proposed_verdict="Thèse intacte — CET1 stable au-dessus de l'objectif.",
    )
    assert entry["id"].startswith("rev_")
    assert entry["ticker"] == "BNP.PA"
    assert entry["execution_type"] == "complete"
    assert entry["status"] == "pending"
    assert entry["findings"][0]["topic"] == "CET1"
    assert entry["proposed_verdict"].startswith("Thèse intacte")

    entries = trq.list_entries("BNP.PA")
    assert len(entries) == 1
    assert entries[0]["id"] == entry["id"]


def test_add_entry_light_execution_empty_findings_allowed():
    entry = trq.add_entry("BNP.PA", execution_type="light", findings=[])
    assert entry["findings"] == []
    assert entry["proposed_verdict"] is None


def test_add_entry_invalid_execution_type_raises():
    with pytest.raises(ValueError, match="execution_type invalide"):
        trq.add_entry("BNP.PA", execution_type="daily", findings=[])


def test_add_entry_finding_without_constat_raises():
    with pytest.raises(ValueError, match="constat"):
        trq.add_entry("BNP.PA", execution_type="complete", findings=[{"topic": "CET1", "constat": "  "}])


def test_add_entry_custom_run_date():
    entry = trq.add_entry("BNP.PA", execution_type="light", findings=[], run_date="2026-10-01")
    assert entry["date"] == "2026-10-01"


def test_list_entries_unknown_ticker_returns_empty_list():
    assert trq.list_entries("NOPE") == []


def test_list_entries_multiple_entries_chronological_order():
    trq.add_entry("BNP.PA", execution_type="light", findings=[], run_date="2026-08-01")
    trq.add_entry("BNP.PA", execution_type="light", findings=[], run_date="2026-09-01")
    entries = trq.list_entries("BNP.PA")
    assert [e["date"] for e in entries] == ["2026-08-01", "2026-09-01"]


def test_entries_scoped_per_ticker():
    trq.add_entry("BNP.PA", execution_type="light", findings=[])
    trq.add_entry("MU", execution_type="light", findings=[])
    assert len(trq.list_entries("BNP.PA")) == 1
    assert len(trq.list_entries("MU")) == 1


# ─────────────────────────────────────────────────────────────────
# update_entry_status — validation/rejet (action humaine)
# ─────────────────────────────────────────────────────────────────

def test_update_entry_status_validated():
    entry = trq.add_entry("BNP.PA", execution_type="complete", findings=[])
    updated = trq.update_entry_status("BNP.PA", entry["id"], "validated")
    assert updated["status"] == "validated"
    assert updated["status_updated_at"] is not None
    assert trq.list_entries("BNP.PA")[0]["status"] == "validated"


def test_update_entry_status_dismissed():
    entry = trq.add_entry("BNP.PA", execution_type="light", findings=[])
    updated = trq.update_entry_status("BNP.PA", entry["id"], "dismissed")
    assert updated["status"] == "dismissed"


def test_update_entry_status_invalid_value_raises():
    entry = trq.add_entry("BNP.PA", execution_type="light", findings=[])
    with pytest.raises(ValueError, match="status invalide"):
        trq.update_entry_status("BNP.PA", entry["id"], "approved")


def test_update_entry_status_unknown_entry_raises_keyerror():
    trq.add_entry("BNP.PA", execution_type="light", findings=[])
    with pytest.raises(KeyError):
        trq.update_entry_status("BNP.PA", "rev_doesnotexist", "validated")
