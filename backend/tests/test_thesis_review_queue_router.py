"""Tests d'intégration — file de révision de thèse (routers/thesis_review_queue.py)
et la séparation d'authentification qui garantit qu'une routine cloud ne peut
JAMAIS écrire dans verification.* (modules/my_portfolio_thesis.py).

Ces tests sont la preuve technique de la contrainte non négociable : le token
de revue restreint (THESIS_REVIEW_TOKEN) doit échouer contre PATCH .../thesis
et contre PATCH .../thesis_review_queue/{id} (validation), et une soumission
réussie via le token de revue ne doit produire AUCUN changement dans
data/my_portfolio_thesis.json.

Isolation stricte des deux stores disque (autouse) — jamais le vrai
data/my_portfolio_thesis.json ni data/thesis_review_queue.json de prod.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api
from modules import api_core
from modules import my_portfolio_thesis as mpt
from modules import thesis_review_queue as trq

REVIEW_TOKEN = "review-scope-only-token"
FULL_TOKEN = "full-scope-token"


@pytest.fixture(autouse=True)
def _isolate_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(mpt, "STORE_PATH", tmp_path / "my_portfolio_thesis.json")
    monkeypatch.setattr(mpt, "_LOCK_PATH", tmp_path / "my_portfolio_thesis.json.lock")
    monkeypatch.setattr(trq, "STORE_PATH", tmp_path / "thesis_review_queue.json")
    monkeypatch.setattr(trq, "_LOCK_PATH", tmp_path / "thesis_review_queue.json.lock")


def _client(monkeypatch, *, api_token=FULL_TOKEN, review_token=REVIEW_TOKEN) -> TestClient:
    monkeypatch.setattr(api_core, "API_TOKEN", api_token)
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", False)
    monkeypatch.setattr(api_core, "THESIS_REVIEW_TOKEN", review_token)
    return TestClient(api.app)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


_SUBMISSION = {
    "execution_type": "complete",
    "findings": [
        {"topic": "CET1", "constat": "13.1% au T2 2026, stable au-dessus de l'objectif", "source": "communiqué T2 2026"},
        {"topic": "Coût du risque", "constat": "37 bps, sous la guidance de 40 bps", "source": "présentation investisseurs T2 2026"},
    ],
    "proposed_verdict": "Thèse intacte — CET1 et coût du risque conformes à la trajectoire annoncée.",
}


# ─────────────────────────────────────────────────────────────────
# LE test critique : le token de revue ne peut PAS écrire verification.*
# ─────────────────────────────────────────────────────────────────

def test_review_token_cannot_patch_thesis_verification(monkeypatch):
    """LA garantie non négociable : soumettre une proposition via le token
    de revue, puis tenter d'écrire verification.* avec CE MÊME token — doit
    échouer en 401, jamais atteindre update_thesis."""
    client = _client(monkeypatch)

    # La routine peut soumettre sa proposition...
    r_submit = client.post(
        "/api/my_portfolio/BNP.PA/thesis_review_queue", json=_SUBMISSION, headers=_auth(REVIEW_TOKEN),
    )
    assert r_submit.status_code == 200

    # ...mais ne peut PAS écrire directement dans verification via PATCH /thesis.
    r_patch = client.patch(
        "/api/my_portfolio/BNP.PA/thesis",
        json={"verification": {"derniere_verification": "2026-10-01", "verdict": "auto-validé"}},
        headers=_auth(REVIEW_TOKEN),
    )
    assert r_patch.status_code == 401

    # verification.* reste intact (jamais écrit).
    assert mpt.get_thesis("BNP.PA")["verification"]["derniere_verification"] is None


def test_review_token_cannot_validate_its_own_proposal(monkeypatch):
    """La validation (passage à 'validated') est une action humaine réservée
    au token complet — le token de revue ne peut pas s'auto-valider."""
    client = _client(monkeypatch)
    r_submit = client.post(
        "/api/my_portfolio/BNP.PA/thesis_review_queue", json=_SUBMISSION, headers=_auth(REVIEW_TOKEN),
    )
    entry_id = r_submit.json()["id"]

    r_patch = client.patch(
        f"/api/my_portfolio/BNP.PA/thesis_review_queue/{entry_id}",
        json={"status": "validated"}, headers=_auth(REVIEW_TOKEN),
    )
    assert r_patch.status_code == 401
    assert trq.list_entries("BNP.PA")[0]["status"] == "pending"


def test_submission_via_review_token_leaves_my_portfolio_thesis_untouched(monkeypatch, tmp_path):
    """Bout-en-bout : une soumission réussie ne crée même pas le fichier
    data/my_portfolio_thesis.json (aucune écriture n'a jamais eu lieu, pas
    seulement 'verification est resté None')."""
    client = _client(monkeypatch)
    client.post("/api/my_portfolio/BNP.PA/thesis_review_queue", json=_SUBMISSION, headers=_auth(REVIEW_TOKEN))

    assert not mpt.STORE_PATH.exists(), "aucune écriture ne doit jamais toucher my_portfolio_thesis.json"


# ─────────────────────────────────────────────────────────────────
# POST /thesis_review_queue
# ─────────────────────────────────────────────────────────────────

def test_post_review_entry_full_token_also_works(monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/api/my_portfolio/BNP.PA/thesis_review_queue", json=_SUBMISSION, headers=_auth(FULL_TOKEN))
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_post_review_entry_no_token_401(monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/api/my_portfolio/BNP.PA/thesis_review_queue", json=_SUBMISSION)
    assert r.status_code == 401


def test_post_review_entry_wrong_token_401(monkeypatch):
    client = _client(monkeypatch)
    r = client.post(
        "/api/my_portfolio/BNP.PA/thesis_review_queue", json=_SUBMISSION, headers=_auth("random-garbage"),
    )
    assert r.status_code == 401


def test_post_review_entry_unknown_ticker_404(monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/api/my_portfolio/NOPE/thesis_review_queue", json=_SUBMISSION, headers=_auth(REVIEW_TOKEN))
    assert r.status_code == 404


def test_post_review_entry_case_insensitive_ticker(monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/api/my_portfolio/bnp.pa/thesis_review_queue", json=_SUBMISSION, headers=_auth(REVIEW_TOKEN))
    assert r.status_code == 200
    assert r.json()["ticker"] == "BNP.PA"


def test_post_review_entry_invalid_execution_type_400(monkeypatch):
    client = _client(monkeypatch)
    bad = {**_SUBMISSION, "execution_type": "quarterly"}
    r = client.post("/api/my_portfolio/BNP.PA/thesis_review_queue", json=bad, headers=_auth(REVIEW_TOKEN))
    assert r.status_code == 400


def test_post_review_entry_light_no_findings(monkeypatch):
    client = _client(monkeypatch)
    r = client.post(
        "/api/my_portfolio/BNP.PA/thesis_review_queue",
        json={"execution_type": "light", "findings": []}, headers=_auth(REVIEW_TOKEN),
    )
    assert r.status_code == 200
    assert r.json()["findings"] == []
    assert r.json()["proposed_verdict"] is None


# ─────────────────────────────────────────────────────────────────
# GET /thesis_review_queue
# ─────────────────────────────────────────────────────────────────

def test_get_review_queue_review_token_works(monkeypatch):
    client = _client(monkeypatch)
    client.post("/api/my_portfolio/BNP.PA/thesis_review_queue", json=_SUBMISSION, headers=_auth(REVIEW_TOKEN))
    r = client.get("/api/my_portfolio/BNP.PA/thesis_review_queue", headers=_auth(REVIEW_TOKEN))
    assert r.status_code == 200
    assert len(r.json()["entries"]) == 1


def test_get_review_queue_empty_when_never_submitted(monkeypatch):
    client = _client(monkeypatch)
    r = client.get("/api/my_portfolio/BNP.PA/thesis_review_queue", headers=_auth(FULL_TOKEN))
    assert r.status_code == 200
    assert r.json()["entries"] == []


def test_get_review_queue_no_auth_401(monkeypatch):
    client = _client(monkeypatch)
    r = client.get("/api/my_portfolio/BNP.PA/thesis_review_queue")
    assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────
# PATCH /thesis_review_queue/{id} — validation humaine (token complet only)
# ─────────────────────────────────────────────────────────────────

def test_patch_review_entry_status_full_token_validates(monkeypatch):
    client = _client(monkeypatch)
    entry = client.post(
        "/api/my_portfolio/BNP.PA/thesis_review_queue", json=_SUBMISSION, headers=_auth(REVIEW_TOKEN),
    ).json()
    r = client.patch(
        f"/api/my_portfolio/BNP.PA/thesis_review_queue/{entry['id']}",
        json={"status": "validated"}, headers=_auth(FULL_TOKEN),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "validated"


def test_patch_review_entry_status_dismissed(monkeypatch):
    client = _client(monkeypatch)
    entry = client.post(
        "/api/my_portfolio/BNP.PA/thesis_review_queue", json=_SUBMISSION, headers=_auth(FULL_TOKEN),
    ).json()
    r = client.patch(
        f"/api/my_portfolio/BNP.PA/thesis_review_queue/{entry['id']}",
        json={"status": "dismissed"}, headers=_auth(FULL_TOKEN),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "dismissed"


def test_patch_review_entry_status_unknown_id_404(monkeypatch):
    client = _client(monkeypatch)
    client.post("/api/my_portfolio/BNP.PA/thesis_review_queue", json=_SUBMISSION, headers=_auth(FULL_TOKEN))
    r = client.patch(
        "/api/my_portfolio/BNP.PA/thesis_review_queue/rev_doesnotexist",
        json={"status": "validated"}, headers=_auth(FULL_TOKEN),
    )
    assert r.status_code == 404


def test_patch_review_entry_status_invalid_value_400(monkeypatch):
    client = _client(monkeypatch)
    entry = client.post(
        "/api/my_portfolio/BNP.PA/thesis_review_queue", json=_SUBMISSION, headers=_auth(FULL_TOKEN),
    ).json()
    r = client.patch(
        f"/api/my_portfolio/BNP.PA/thesis_review_queue/{entry['id']}",
        json={"status": "approved"}, headers=_auth(FULL_TOKEN),
    )
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────
# GET /api/my_portfolio — auth élargie (review token OU full), toujours read-only
# ─────────────────────────────────────────────────────────────────

def test_get_my_portfolio_accepts_review_token(monkeypatch):
    import routers.my_portfolio as my_portfolio_router

    def _flat_price(p):
        return (100.0, None, 100.0, 1.0)
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price)

    client = _client(monkeypatch)
    r = client.get("/api/my_portfolio", headers=_auth(REVIEW_TOKEN))
    assert r.status_code == 200
    assert len(r.json()["positions"]) == 10


def test_get_my_portfolio_rejects_garbage_token(monkeypatch):
    client = _client(monkeypatch)
    r = client.get("/api/my_portfolio", headers=_auth("garbage"))
    assert r.status_code == 401
