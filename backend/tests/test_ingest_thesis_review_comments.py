"""Tests — scripts/ingest_thesis_review_comments.py.

Deux gardes-fous structurels testés :
  1. ce script ne peut techniquement pas écrire dans
     data/my_portfolio_thesis.json (même garantie que
     routers/thesis_review_queue.py, voir test_thesis_review_queue.py) ;
  2. un `ticker` absent ou hors du book (`my_portfolio_data.POSITIONS`) est
     ignoré comme un commentaire malformé — jamais une entrée fantôme dans
     la file de revue (généralisé multi-ticker, 2026-09-04).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from modules import thesis_review_queue as trq
from scripts import ingest_thesis_review_comments as ingest_mod


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(trq, "STORE_PATH", tmp_path / "thesis_review_queue.json")
    monkeypatch.setattr(trq, "_LOCK_PATH", tmp_path / "thesis_review_queue.json.lock")
    monkeypatch.setattr(ingest_mod, "STATE_PATH", tmp_path / ".thesis_review_ingest_state.json")


def _comment(id_, body):
    return {"id": id_, "body": body}


def _body(ticker, execution_type="complete", findings=None, proposed_verdict="Proposition : thèse intacte."):
    import json as _json
    payload = {
        "ticker": ticker, "execution_type": execution_type,
        "findings": findings if findings is not None else [{"topic": "CET1", "constat": "13.1%", "source": "communiqué"}],
        "proposed_verdict": proposed_verdict,
    }
    return f"Voici mon analyse.\n\n```json\n{_json.dumps(payload)}\n```\n"


_VALID_BODY = _body("BNP.PA")
_LIGHT_EMPTY_BODY = '```json\n{"ticker": "BNP.PA", "execution_type": "light", "findings": [], "proposed_verdict": null}\n```\n'


# ─────────────────────────────────────────────────────────────────
# Contrainte structurelle — jamais my_portfolio_thesis
# ─────────────────────────────────────────────────────────────────

def test_module_never_imports_my_portfolio_thesis():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(ingest_mod))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    assert not any("my_portfolio_thesis" in name for name in imported_names), imported_names
    call_names = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "update_thesis" not in call_names


# ─────────────────────────────────────────────────────────────────
# parse_submission
# ─────────────────────────────────────────────────────────────────

def test_parse_submission_valid_block():
    payload = ingest_mod.parse_submission(_VALID_BODY)
    assert payload["ticker"] == "BNP.PA"
    assert payload["execution_type"] == "complete"
    assert payload["findings"][0]["topic"] == "CET1"


def test_parse_submission_no_json_block_returns_none():
    assert ingest_mod.parse_submission("juste du texte, pas de bloc json") is None


def test_parse_submission_invalid_json_returns_none():
    assert ingest_mod.parse_submission("```json\n{not valid json\n```") is None


def test_parse_submission_missing_execution_type_returns_none():
    assert ingest_mod.parse_submission('```json\n{"ticker": "BNP.PA", "findings": []}\n```') is None


def test_parse_submission_missing_ticker_returns_none():
    assert ingest_mod.parse_submission('```json\n{"execution_type": "light", "findings": []}\n```') is None


def test_parse_submission_unknown_ticker_returns_none():
    body = _body("TSLA")
    assert ingest_mod.parse_submission(body) is None


def test_parse_submission_ticker_case_insensitive():
    body = '```json\n{"ticker": "bnp.pa", "execution_type": "light", "findings": []}\n```'
    payload = ingest_mod.parse_submission(body)
    assert payload["ticker"] == "BNP.PA"


# ─────────────────────────────────────────────────────────────────
# ingest() — bout en bout (requests mocké)
# ─────────────────────────────────────────────────────────────────

def test_ingest_happy_path(monkeypatch):
    monkeypatch.setattr(ingest_mod, "fetch_comments", lambda: [_comment(1, _VALID_BODY)])
    n = ingest_mod.ingest()
    assert n == 1
    entries = trq.list_entries("BNP.PA")
    assert len(entries) == 1
    assert entries[0]["execution_type"] == "complete"
    assert entries[0]["findings"][0]["topic"] == "CET1"


def test_ingest_routes_to_correct_ticker(monkeypatch):
    monkeypatch.setattr(
        ingest_mod, "fetch_comments",
        lambda: [_comment(1, _body("BNP.PA")), _comment(2, _body("FMX", findings=[]))],
    )
    n = ingest_mod.ingest()
    assert n == 2
    assert len(trq.list_entries("BNP.PA")) == 1
    assert len(trq.list_entries("FMX")) == 1
    assert trq.list_entries("PSX") == []


def test_ingest_idempotent_does_not_reingest(monkeypatch):
    monkeypatch.setattr(ingest_mod, "fetch_comments", lambda: [_comment(1, _VALID_BODY)])
    ingest_mod.ingest()
    n_second = ingest_mod.ingest()  # même liste de commentaires renvoyée par GitHub
    assert n_second == 0
    assert len(trq.list_entries("BNP.PA")) == 1


def test_ingest_only_processes_new_comments(monkeypatch):
    monkeypatch.setattr(ingest_mod, "fetch_comments", lambda: [_comment(1, _VALID_BODY)])
    ingest_mod.ingest()
    monkeypatch.setattr(
        ingest_mod, "fetch_comments",
        lambda: [_comment(1, _VALID_BODY), _comment(2, _LIGHT_EMPTY_BODY)],
    )
    n = ingest_mod.ingest()
    assert n == 1
    assert len(trq.list_entries("BNP.PA")) == 2


def test_ingest_malformed_comment_skipped_without_crashing(monkeypatch):
    monkeypatch.setattr(
        ingest_mod, "fetch_comments",
        lambda: [_comment(1, "commentaire humain sans JSON"), _comment(2, _VALID_BODY)],
    )
    n = ingest_mod.ingest()
    assert n == 1
    assert len(trq.list_entries("BNP.PA")) == 1
    # L'état avance quand même au-delà du commentaire malformé (pas de retraitement infini).
    state = ingest_mod._load_state()
    assert state["last_comment_id"] == 2


def test_ingest_unknown_ticker_skipped_without_crashing(monkeypatch):
    monkeypatch.setattr(
        ingest_mod, "fetch_comments",
        lambda: [_comment(1, _body("TSLA")), _comment(2, _VALID_BODY)],
    )
    n = ingest_mod.ingest()
    assert n == 1
    assert len(trq.list_entries("BNP.PA")) == 1


def test_ingest_invalid_execution_type_skipped(monkeypatch):
    bad = '```json\n{"ticker": "BNP.PA", "execution_type": "yearly", "findings": []}\n```'
    monkeypatch.setattr(ingest_mod, "fetch_comments", lambda: [_comment(1, bad)])
    n = ingest_mod.ingest()
    assert n == 0
    assert trq.list_entries("BNP.PA") == []
    # State avance quand même — pas de boucle infinie sur un commentaire invalide.
    assert ingest_mod._load_state()["last_comment_id"] == 1


def test_fetch_comments_no_token_raises(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        ingest_mod.fetch_comments()
