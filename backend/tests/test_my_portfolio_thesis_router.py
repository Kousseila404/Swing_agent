"""Tests d'intégration — PATCH /api/my_portfolio/{ticker}/thesis.

Isolation stricte du store disque (autouse) — mêmes garanties que
test_my_portfolio_thesis.py : jamais de lecture/écriture sur le vrai
data/my_portfolio_thesis.json de prod pendant les tests.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api
from modules import api_core
from modules import my_portfolio_thesis as mpt


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(mpt, "STORE_PATH", tmp_path / "my_portfolio_thesis.json")
    monkeypatch.setattr(mpt, "_LOCK_PATH", tmp_path / "my_portfolio_thesis.json.lock")


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(api_core, "API_TOKEN", "")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", True)
    return TestClient(api.app)


def test_patch_unknown_ticker_404(monkeypatch):
    r = _client(monkeypatch).patch("/api/my_portfolio/NOPE/thesis", json={"why_bought": {"catalyseurs": ["x"]}})
    assert r.status_code == 404


def test_patch_case_insensitive_ticker_resolution(monkeypatch):
    r = _client(monkeypatch).patch("/api/my_portfolio/mu/thesis", json={"why_bought": {"catalyseurs": ["x"]}})
    assert r.status_code == 200
    assert r.json()["ticker"] == "MU"


def test_patch_requires_at_least_one_block(monkeypatch):
    r = _client(monkeypatch).patch("/api/my_portfolio/MU/thesis", json={})
    assert r.status_code == 400


def test_patch_why_bought_happy_path(monkeypatch):
    r = _client(monkeypatch).patch("/api/my_portfolio/MU/thesis", json={
        "why_bought": {
            "catalyseurs": ["Prix NAND au plancher du cycle"],
            "valorisation": "P/E 12x vs médiane 18x",
            "role_portefeuille": "Pari cyclique concentré",
        },
    })
    assert r.status_code == 200
    body = r.json()
    assert body["why_bought"]["catalyseurs"] == ["Prix NAND au plancher du cycle"]
    assert body["why_bought"]["valorisation"] == "P/E 12x vs médiane 18x"
    assert body["why_bought"]["role_portefeuille"] == "Pari cyclique concentré"


def test_patch_sell_signals_zero_one_five(monkeypatch):
    client = _client(monkeypatch)

    r0 = client.patch("/api/my_portfolio/MU/thesis", json={"sell_signals": []})
    assert r0.status_code == 200
    assert r0.json()["sell_signals"] == []

    r1 = client.patch("/api/my_portfolio/MU/thesis", json={
        "sell_signals": [{"libelle": "Utilisation capacité < 90%", "statut": "intact"}],
    })
    assert r1.status_code == 200
    assert len(r1.json()["sell_signals"]) == 1

    r5 = client.patch("/api/my_portfolio/MU/thesis", json={
        "sell_signals": [{"libelle": f"S{i}", "statut": "a_surveiller"} for i in range(5)],
    })
    assert r5.status_code == 200
    assert len(r5.json()["sell_signals"]) == 5


def test_patch_sell_signals_invalid_statut_400(monkeypatch):
    r = _client(monkeypatch).patch("/api/my_portfolio/MU/thesis", json={
        "sell_signals": [{"libelle": "A", "statut": "haussier"}],
    })
    assert r.status_code == 400


def test_patch_verification_missing_field_422(monkeypatch):
    # `verdict` est requis dans le schéma pydantic -> 422 (validation FastAPI),
    # pas 400 (validation métier) : échec avant même d'atteindre update_thesis.
    r = _client(monkeypatch).patch("/api/my_portfolio/MU/thesis", json={
        "verification": {"derniere_verification": "2026-09-01"},
    })
    assert r.status_code == 422


def test_patch_verification_empty_verdict_400(monkeypatch):
    r = _client(monkeypatch).patch("/api/my_portfolio/MU/thesis", json={
        "verification": {"derniere_verification": "2026-09-01", "verdict": "   "},
    })
    assert r.status_code == 400


def test_patch_verification_invalid_date_400(monkeypatch):
    r = _client(monkeypatch).patch("/api/my_portfolio/MU/thesis", json={
        "verification": {"derniere_verification": "01/09/2026", "verdict": "ok"},
    })
    assert r.status_code == 400


def test_patch_verification_pushes_previous_into_history(monkeypatch):
    client = _client(monkeypatch)
    r1 = client.patch("/api/my_portfolio/MU/thesis", json={
        "verification": {"derniere_verification": "2026-08-01", "verdict": "thèse intacte"},
    })
    assert r1.json()["verification"]["historique_verifications"] == []

    r2 = client.patch("/api/my_portfolio/MU/thesis", json={
        "verification": {"derniere_verification": "2026-09-01", "verdict": "T2 au-dessus des attentes"},
    })
    body2 = r2.json()
    assert body2["verification"]["derniere_verification"] == "2026-09-01"
    assert body2["verification"]["historique_verifications"] == [
        {"date": "2026-08-01", "verdict": "thèse intacte"},
    ]


def test_patch_requires_auth_when_configured(monkeypatch):
    monkeypatch.setattr(api_core, "API_TOKEN", "secret-token")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", False)
    client = TestClient(api.app)
    r = client.patch("/api/my_portfolio/MU/thesis", json={"why_bought": {"catalyseurs": ["x"]}})
    assert r.status_code == 401


def test_patch_result_visible_via_get_my_portfolio(monkeypatch):
    """Roundtrip live (TestClient bout-en-bout) : le PATCH persiste bien via
    le store réellement lu par GET /api/my_portfolio (routers/my_portfolio.py),
    pas seulement via le module isolé."""
    from tests.test_my_portfolio_router import _flat_price
    import routers.my_portfolio as my_portfolio_router

    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))
    client = _client(monkeypatch)

    client.patch("/api/my_portfolio/MU/thesis", json={
        "why_bought": {"catalyseurs": ["Pari NAND"]},
        "sell_signals": [{"libelle": "Utilisation capacité < 90%", "statut": "intact"}],
        "verification": {"derniere_verification": "2026-09-01", "verdict": "thèse intacte"},
    })

    r = client.get("/api/my_portfolio")
    mu = next(p for p in r.json()["positions"] if p["ticker"] == "MU")
    assert mu["why_bought"]["catalyseurs"] == ["Pari NAND"]
    assert mu["sell_signals"][0]["libelle"] == "Utilisation capacité < 90%"
    assert mu["verification"]["derniere_verification"] == "2026-09-01"
    assert mu["thesis_updated_at"] is not None

    # Un ticker non édité garde le squelette vide par défaut.
    ero = next(p for p in r.json()["positions"] if p["ticker"] == "ERO")
    assert ero["why_bought"]["catalyseurs"] == []
    assert ero["sell_signals"] == []
    assert ero["verification"]["derniere_verification"] is None
