"""Tests router /api/proposals — auth + endpoints + flow approve/execute.

Couvre :
  - GET /api/proposals (public) : liste vide, liste après enqueue
  - POST /api/proposals/refresh : auth required + retourne shape attendu
  - POST /api/proposals/{id}/approve : 401 sans auth, 200 avec, écrit journal
  - POST /api/proposals/{id}/reject : 401 sans auth, 200 + state rejected
"""
from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api
from modules import api_core, auto_proposer, proposals
from modules.utils import CSV_SCHEMA


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.fixture
def auth_token(monkeypatch):
    monkeypatch.setattr(api_core, "API_TOKEN", "test-token")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", False)
    return "test-token"


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    """Redirige proposals.json + trade_journal.csv vers tmp_path + force
    BROKER_MODE=paper pour que les tests n'envoient JAMAIS d'ordre à Alpaca."""
    monkeypatch.setattr(proposals, "PROPOSALS_PATH", tmp_path / "proposals.json")
    monkeypatch.setattr(proposals, "PROPOSALS_LOCK_PATH", tmp_path / "proposals.json.lock")
    monkeypatch.setattr(proposals, "PROPOSALS_AUDIT_PATH", tmp_path / "proposals_audit.jsonl")
    # Isole aussi le stash last_refresh pour ne pas polluer le fichier prod.
    from routers import proposals as proposals_router
    monkeypatch.setattr(
        proposals_router, "_LAST_REFRESH_PATH",
        tmp_path / "proposals_last_refresh.json",
    )
    csv_path = tmp_path / "trade_journal.csv"
    monkeypatch.setattr(api_core, "CSV_PATH", csv_path)
    monkeypatch.setattr(api_core, "CSV_LOCK_PATH", tmp_path / "trade_journal.csv.lock")
    # CRITIQUE : force PaperBroker — sinon /approve → vrais ordres Alpaca (test leak).
    from modules import broker_gateway
    from modules import utils as mod_utils
    monkeypatch.setattr(mod_utils, "CSV_PATH", csv_path)
    monkeypatch.setattr(mod_utils, "CSV_LOCK_PATH", tmp_path / "trade_journal.csv.lock")
    monkeypatch.setattr(broker_gateway, "CSV_PATH", csv_path)
    monkeypatch.setattr(broker_gateway, "CSV_LOCK_PATH", tmp_path / "trade_journal.csv.lock")
    monkeypatch.setattr(broker_gateway, "_broker_instance", None)
    import config as _config
    monkeypatch.setattr(_config, "BROKER_MODE", "paper")
    return tmp_path


def _enqueue_one(ticker="AAPL"):
    p = proposals.make_proposal(
        ticker=ticker, direction="LONG",
        entry=150.0, stop_loss=142.5, take_profit=180.0,
        size=10, sector="Technology", signal="AUTO_PROPOSAL",
        context={"titan_score": 95.0},
    )
    inserted = proposals.enqueue_batch([p])
    return inserted[0]


# ─────────────────────────────────────────────────────────────────
# GET /api/proposals — public
# ─────────────────────────────────────────────────────────────────

def test_list_empty(client, isolated_storage):
    resp = client.get("/api/proposals")
    assert resp.status_code == 200
    body = resp.json()
    assert body["proposals"] == []
    assert body["n_total"] == 0
    assert body["n_pending"] == 0
    # last_refresh peut être None (jamais run) ou un dict (run antérieur)
    assert "last_refresh" in body


def test_list_after_enqueue(client, isolated_storage):
    _enqueue_one("AAPL")
    _enqueue_one("MSFT")
    resp = client.get("/api/proposals")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_total"] == 2
    assert body["n_pending"] == 2
    assert {p["ticker"] for p in body["proposals"]} == {"AAPL", "MSFT"}


def test_list_filter_status(client, isolated_storage):
    _enqueue_one("AAPL")
    item = _enqueue_one("MSFT")
    proposals.update_status(item["id"], "rejected", rejection_reason="manual")
    resp = client.get("/api/proposals?status=pending")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_total"] == 1  # n_total here = filtered length
    assert body["proposals"][0]["ticker"] == "AAPL"


def test_list_exposes_recurrence_streak(client, isolated_storage):
    """Audit 2026-08-14 : un ticker recyclé ≥2× sans décision expose
    `recurrence_streak` dans la réponse — sert au badge UI récurrence."""
    import json
    now = datetime.now(UTC)
    history = [
        {
            "id": "PROP_OLD1", "ticker": "INCY", "status": "expired",
            "created_at": (now - timedelta(days=20)).isoformat().replace("+00:00", "Z"),
            "expires_at": "9999-12-31T23:59:59Z", "direction": "LONG",
            "entry": 90.0, "stop_loss": 80.0, "take_profit": 120.0, "size": 5,
            "sector": "Healthcare", "signal": "AUTO_PROPOSAL", "context": {"titan_score": 72.0},
            "decided_at": (now - timedelta(days=13)).isoformat().replace("+00:00", "Z"),
            "decided_by": "system", "rejection_reason": "stale_max_age", "order_id": None,
        },
    ]
    (isolated_storage / "proposals.json").write_text(json.dumps(history), encoding="utf-8")
    _enqueue_one("INCY")  # 2e occurrence, toujours pending → streak=2

    resp = client.get("/api/proposals")
    assert resp.status_code == 200
    body = resp.json()
    incy = next(p for p in body["proposals"] if p["ticker"] == "INCY")
    assert incy["recurrence_streak"] == 2


# ─────────────────────────────────────────────────────────────────
# POST /api/proposals/refresh — auth + run
# ─────────────────────────────────────────────────────────────────

def test_refresh_requires_auth(client, isolated_storage):
    resp = client.post("/api/proposals/refresh", json={})
    # Sans token configuré ET sans header → 503 (fail-closed)
    assert resp.status_code in (401, 503)


def test_refresh_with_token_runs_proposer(client, isolated_storage,
                                            auth_token, monkeypatch):
    # Patch run_and_enqueue pour éviter de dépendre du scoring réel
    fake_result = auto_proposer.ProposerResult(
        ok=True,
        gates=[auto_proposer.GateResult("killswitch", True, "ok")],
        proposals=[],
        diagnostics={"params": {}},
    )
    monkeypatch.setattr(auto_proposer, "run_and_enqueue", lambda **_: fake_result)

    resp = client.post(
        "/api/proposals/refresh",
        json={"notify_telegram": False},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["proposals"] == []
    assert any(g["name"] == "killswitch" for g in body["gates"])


# ─────────────────────────────────────────────────────────────────
# POST /api/proposals/{id}/approve — auth + écriture journal + transition
# ─────────────────────────────────────────────────────────────────

def test_approve_requires_auth(client, isolated_storage):
    item = _enqueue_one()
    resp = client.post(f"/api/proposals/{item['id']}/approve")
    assert resp.status_code in (401, 503)


def test_approve_writes_journal_and_marks_executed(client, isolated_storage,
                                                     auth_token):
    item = _enqueue_one("AAPL")
    resp = client.post(
        f"/api/proposals/{item['id']}/approve",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["proposal"]["status"] == "executed"
    # order_id vient du broker maintenant (plus de préfixe PROP_ artificiel
    # côté API — Lot 14 a câblé /approve au broker réel).
    assert body["order_id"]  # non-vide
    # PaperBroker format : PAPER-<TICKER>-<timestamp>
    assert "PAPER" in body["order_id"] or len(body["order_id"]) > 8

    # Le journal CSV doit contenir la ligne OPEN
    csv_path = isolated_storage / "trade_journal.csv"
    assert csv_path.exists()
    with csv_path.open() as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["Ticker"] == "AAPL"
    assert rows[0]["Status"] == "OPEN"
    assert rows[0]["Size"] == "10"
    assert rows[0]["Signal"] == "AUTO_PROPOSAL"
    # Schéma complet préservé
    assert set(reader.fieldnames or []) >= set(CSV_SCHEMA)


def test_approve_unknown_id_returns_404(client, isolated_storage, auth_token):
    resp = client.post(
        "/api/proposals/PROP_UNKNOWN/approve",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 404


def test_approve_already_decided_returns_400(client, isolated_storage, auth_token):
    item = _enqueue_one("AAPL")
    proposals.update_status(item["id"], "rejected", rejection_reason="x")
    resp = client.post(
        f"/api/proposals/{item['id']}/approve",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 400
    assert "déjà terminée" in resp.json()["detail"]


# ─────────────────────────────────────────────────────────────────
# POST /api/proposals/{id}/reject
# ─────────────────────────────────────────────────────────────────

def test_reject_requires_auth(client, isolated_storage):
    item = _enqueue_one()
    resp = client.post(f"/api/proposals/{item['id']}/reject", json={})
    assert resp.status_code in (401, 503)


def test_reject_marks_rejected_with_reason(client, isolated_storage, auth_token):
    item = _enqueue_one("AAPL")
    resp = client.post(
        f"/api/proposals/{item['id']}/reject",
        json={"reason": "not_my_thesis"},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["proposal"]["status"] == "rejected"
    assert body["proposal"]["rejection_reason"] == "not_my_thesis"


# ─────────────────────────────────────────────────────────────────
# Sweep expiration : list_all triggers expire_pending
# ─────────────────────────────────────────────────────────────────

def test_list_triggers_expiration_sweep(client, isolated_storage):
    p = proposals.make_proposal(
        ticker="OLD", direction="LONG",
        entry=10.0, stop_loss=9.0, take_profit=15.0, size=1,
        sector="?", signal="AUTO_PROPOSAL",
    )
    p.expires_at = (datetime.now(UTC) - timedelta(hours=1)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    proposals.enqueue_batch([p])

    resp = client.get("/api/proposals")
    assert resp.status_code == 200
    body = resp.json()
    # Après le sweep, la proposition doit être expired (plus pending)
    assert body["n_pending"] == 0
    assert body["proposals"][0]["status"] == "expired"


# ─────────────────────────────────────────────────────────────────
# POST /approve_batch
# ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_live_price(monkeypatch):
    """Mock get_current_price pour éviter les appels réseau pendant les tests
    approve_batch (sinon le live-price realignement consulte yfinance et
    écrase les entry fournis par les tests)."""
    from modules.tracker import market
    monkeypatch.setattr(market, "get_current_price", lambda t: None)


def test_approve_batch_requires_auth(client, isolated_storage):
    item = _enqueue_one()
    resp = client.post(
        "/api/proposals/approve_batch",
        json={"items": [{"id": item["id"]}]},
    )
    assert resp.status_code in (401, 503)


def test_approve_batch_empty_payload_returns_400(client, isolated_storage, auth_token):
    resp = client.post(
        "/api/proposals/approve_batch",
        json={"items": []},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 400


def test_approve_batch_executes_all(client, isolated_storage, auth_token, mock_live_price):
    p1 = _enqueue_one("AAPL")
    p2 = _enqueue_one("MSFT")
    resp = client.post(
        "/api/proposals/approve_batch",
        json={"items": [{"id": p1["id"]}, {"id": p2["id"]}]},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_executed"] == 2
    assert body["n_failed"] == 0
    for r in body["results"]:
        assert r["ok"] is True
        assert r["order_id"]

    # Les deux propositions doivent être executed.
    all_props = proposals.list_all()
    for p in all_props:
        assert p["status"] == "executed"


def test_approve_batch_broker_rejection_reverts_to_pending(
    client, isolated_storage, auth_token, mock_live_price, monkeypatch,
):
    """Audit 2026-08-16 : un rejet broker retryable (ex: NYSE fermée avant
    l'ouverture) ne doit pas laisser la proposition orpheline en `approved`
    pour toujours. Sinon le générateur quotidien ne la voit plus dans le
    pool pending, recrée un doublon le lendemain, qui échoue à son tour
    pour la même raison — boucle infinie du même ticker proposé chaque
    jour (cas réel observé sur CF, 2026-08-10 → 2026-08-15)."""
    from modules import broker_gateway

    class _FakeBroker:
        name = "fake"

        def submit_order(self, scan):
            return broker_gateway.OrderResult(
                success=False, ticker=scan.ticker, order_id="",
                message="NYSE fermée — prochaine ouverture : 09:30:00-04:00",
            )

    monkeypatch.setattr(broker_gateway, "get_broker", lambda: _FakeBroker())

    item = _enqueue_one("AAPL")
    resp = client.post(
        "/api/proposals/approve_batch",
        json={"items": [{"id": item["id"]}]},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_executed"] == 0
    assert body["n_failed"] == 1
    assert body["results"][0]["ok"] is False
    assert "NYSE fermée" in body["results"][0]["message"]

    # La proposition doit être redevenue pending (pas orpheline en
    # `approved`) pour qu'un prochain cycle auto_approve/manuel la revoie.
    updated = proposals.get(item["id"])
    assert updated["status"] == "pending"
    assert "NYSE fermée" in updated["rejection_reason"]


def test_approve_batch_applies_overrides(client, isolated_storage, auth_token, mock_live_price):
    """Entry/SL/TP/size fournis via overrides → écrit dans journal."""
    p = _enqueue_one("AAPL")  # entry=150, sl=142.5, tp=180, size=10
    resp = client.post(
        "/api/proposals/approve_batch",
        json={
            "items": [{
                "id": p["id"],
                "overrides": {
                    "entry": 155.0, "stop_loss": 147.0,
                    "take_profit": 185.0, "size": 8,
                },
            }],
        },
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_executed"] == 1

    # Le journal doit refléter les overrides (size=8, pas 10).
    csv_path = isolated_storage / "trade_journal.csv"
    import csv as _csv
    with csv_path.open() as fh:
        rows = list(_csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["Size"] == "8"
    assert float(rows[0]["Entry"]) == 155.0


def test_approve_batch_rejects_invalid_overrides(client, isolated_storage, auth_token):
    """SL >= Entry doit être refusé sans toucher au broker."""
    p = _enqueue_one("AAPL")
    resp = client.post(
        "/api/proposals/approve_batch",
        json={
            "items": [{
                "id": p["id"],
                "overrides": {"stop_loss": 200.0},  # > entry 150
            }],
        },
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    body = resp.json()
    assert body["n_failed"] == 1
    assert "SL" in body["results"][0]["message"]
    # La proposition reste pending (pas de transition approved).
    fresh = proposals.get(p["id"])
    assert fresh["status"] == "pending"


def test_approve_batch_blocks_over_cap_without_ack(client, isolated_storage, auth_token, mock_live_price):
    """Une proposition avec context.sector_exposure.over_cap=True est refusée
    tant que ack_sector_warning n'est pas True."""
    p = proposals.make_proposal(
        ticker="XYZ", direction="LONG", entry=10.0, stop_loss=9.0,
        take_profit=13.0, size=1, sector="Technology", signal="AUTO_PROPOSAL",
        context={"sector_exposure": {"over_cap": True, "projected_pct": 45.0}},
    )
    proposals.enqueue_batch([p])
    # Sans ack → refusé.
    resp = client.post(
        "/api/proposals/approve_batch",
        json={"items": [{"id": p.id}]},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    body = resp.json()
    assert body["n_failed"] == 1
    assert "sur-exposition" in body["results"][0]["message"]
    assert proposals.get(p.id)["status"] == "pending"

    # Avec ack → passe.
    resp2 = client.post(
        "/api/proposals/approve_batch",
        json={"items": [{"id": p.id, "ack_sector_warning": True}]},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp2.json()["n_executed"] == 1


def test_approve_batch_blocks_top_up_without_flag(client, isolated_storage, auth_token, monkeypatch):
    """Ticker déjà OPEN → refusé sans allow_top_up."""
    p = proposals.make_proposal(
        ticker="AAPL", direction="LONG", entry=150.0, stop_loss=140.0,
        take_profit=180.0, size=5, sector="Technology", signal="AUTO_PROPOSAL",
        context={"already_held": True, "current_shares": 3},
    )
    proposals.enqueue_batch([p])
    # Sans allow_top_up → refusé.
    resp = client.post(
        "/api/proposals/approve_batch",
        json={"items": [{"id": p.id}]},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    body = resp.json()
    assert body["n_failed"] == 1
    assert "déjà OPEN" in body["results"][0]["message"]


def test_approve_batch_rejects_intra_batch_duplicate(client, isolated_storage, auth_token, mock_live_price):
    """2 propositions pour le même ticker dans le même batch → 2e refusé."""
    p1 = _enqueue_one("AAPL")
    # Un 2e enqueue_batch pour AAPL serait skip par dédup pending — on fabrique
    # manuellement un 2e item.
    p2 = proposals.make_proposal(
        ticker="AAPL", direction="LONG", entry=151.0, stop_loss=143.0,
        take_profit=181.0, size=5, sector="Technology", signal="AUTO_PROPOSAL",
    )
    # Force l'insertion en bypassant enqueue_batch (simulates 2 pending pour AAPL).
    import json
    raw = json.loads(proposals.PROPOSALS_PATH.read_text())
    from dataclasses import asdict
    raw.append(asdict(p2))
    proposals.PROPOSALS_PATH.write_text(json.dumps(raw))

    resp = client.post(
        "/api/proposals/approve_batch",
        json={"items": [{"id": p1["id"]}, {"id": p2.id}]},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    body = resp.json()
    assert body["n_executed"] == 1
    assert body["n_failed"] == 1
    assert any("doublon" in r["message"].lower() for r in body["results"])


def test_approve_batch_404_on_unknown_id(client, isolated_storage, auth_token):
    resp = client.post(
        "/api/proposals/approve_batch",
        json={"items": [{"id": "PROP_UNKNOWN"}]},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    body = resp.json()
    assert body["n_failed"] == 1
    assert "introuvable" in body["results"][0]["message"]


# ─────────────────────────────────────────────────────────────────
# POST /reject_batch
# ─────────────────────────────────────────────────────────────────

def test_reject_batch_requires_auth(client, isolated_storage):
    item = _enqueue_one()
    resp = client.post(
        "/api/proposals/reject_batch",
        json={"items": [{"id": item["id"]}]},
    )
    assert resp.status_code in (401, 503)


def test_reject_batch_rejects_all(client, isolated_storage, auth_token):
    p1 = _enqueue_one("AAPL")
    p2 = _enqueue_one("MSFT")
    resp = client.post(
        "/api/proposals/reject_batch",
        json={
            "items": [
                {"id": p1["id"], "reason": "thesis_invalidated"},
                {"id": p2["id"]},  # default reason
            ],
        },
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_rejected"] == 2
    assert body["n_failed"] == 0
    # Vérifie les raisons
    p1_after = proposals.get(p1["id"])
    assert p1_after["rejection_reason"] == "thesis_invalidated"
    assert p1_after["status"] == "rejected"
    p2_after = proposals.get(p2["id"])
    assert p2_after["rejection_reason"] == "user_veto"


def test_reject_batch_404_on_unknown(client, isolated_storage, auth_token):
    resp = client.post(
        "/api/proposals/reject_batch",
        json={"items": [{"id": "PROP_UNKNOWN"}]},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    body = resp.json()
    assert body["n_failed"] == 1


# ─────────────────────────────────────────────────────────────────
# last_refresh persistence
# ─────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────
# POST /regenerate — purge + refresh
# ─────────────────────────────────────────────────────────────────

def test_regenerate_requires_auth(client, isolated_storage):
    _enqueue_one("AAPL")
    resp = client.post("/api/proposals/regenerate", json={})
    assert resp.status_code in (401, 503)


def test_regenerate_expires_pending_then_reruns(client, isolated_storage, auth_token, monkeypatch):
    """Les pending existants passent en expired (pas rejected → pas de cooldown)
    puis le proposer s'exécute avec les nouveaux paramètres."""
    # Pré-seed 3 propositions pending
    _enqueue_one("AAPL")
    _enqueue_one("MSFT")
    _enqueue_one("GOOG")
    assert sum(1 for p in proposals.list_all() if p["status"] == "pending") == 3

    # Mock run_and_enqueue pour retourner 2 nouvelles propositions
    def fake_run(**kwargs):
        new_props = [
            proposals.make_proposal(
                ticker="NVDA", direction="LONG", entry=500.0, stop_loss=450.0,
                take_profit=600.0, size=2, sector="Technology",
                signal="AUTO_PROPOSAL", context={"titan_score": 88.0},
            ),
            proposals.make_proposal(
                ticker="AMD", direction="LONG", entry=120.0, stop_loss=110.0,
                take_profit=145.0, size=5, sector="Technology",
                signal="AUTO_PROPOSAL", context={"titan_score": 85.0},
            ),
        ]
        inserted = proposals.enqueue_batch(new_props)
        return auto_proposer.ProposerResult(
            ok=True,
            gates=[auto_proposer.GateResult("killswitch", True, "ok")],
            proposals=inserted,
            diagnostics={"params": kwargs},
        )
    monkeypatch.setattr(auto_proposer, "run_and_enqueue", fake_run)

    resp = client.post(
        "/api/proposals/regenerate",
        json={"notify_telegram": False, "total_capital": 50_000},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["diagnostics"]["n_expired"] == 3
    assert len(body["proposals"]) == 2

    # Vérifie les états finals : anciens en expired, nouveaux en pending.
    all_props = proposals.list_all()
    by_ticker = {p["ticker"]: p for p in all_props}
    for t in ("AAPL", "MSFT", "GOOG"):
        assert by_ticker[t]["status"] == "expired"
        assert by_ticker[t]["rejection_reason"] == "user_regenerate"
    for t in ("NVDA", "AMD"):
        assert by_ticker[t]["status"] == "pending"


def test_regenerate_does_not_trigger_veto_cooldown(client, isolated_storage, auth_token, monkeypatch):
    """Un ticker pending purgé via regenerate doit être re-proposable au run
    suivant (contraire à rejected qui déclenche un cooldown 7 j)."""
    p = _enqueue_one("AAPL")  # pending
    # Purge via regenerate (mock proposer qui ne re-génère rien)
    def fake_run_empty(**kwargs):
        return auto_proposer.ProposerResult(
            ok=True, gates=[], proposals=[], diagnostics={},
        )
    monkeypatch.setattr(auto_proposer, "run_and_enqueue", fake_run_empty)
    client.post(
        "/api/proposals/regenerate", json={"notify_telegram": False},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    # AAPL est maintenant expired. Un enqueue_batch pour AAPL doit passer
    # (pas de cooldown veto car expired, pas rejected).
    new_p = proposals.make_proposal(
        ticker="AAPL", direction="LONG", entry=150.0, stop_loss=142.0,
        take_profit=180.0, size=10, sector="Technology", signal="AUTO_PROPOSAL",
    )
    inserted = proposals.enqueue_batch([new_p])
    assert len(inserted) == 1
    assert inserted[0]["ticker"] == "AAPL"


def test_regenerate_preserves_rejected_status(client, isolated_storage, auth_token, monkeypatch):
    """Les propositions rejected existantes ne sont PAS touchées par regenerate
    (leur cooldown reste actif → pas d'annulation accidentelle de veto)."""
    p_reject = _enqueue_one("AAPL")
    proposals.update_status(p_reject["id"], "rejected", rejection_reason="manual")
    _enqueue_one("MSFT")  # pending

    monkeypatch.setattr(auto_proposer, "run_and_enqueue",
                        lambda **k: auto_proposer.ProposerResult(
                            ok=True, gates=[], proposals=[], diagnostics={}))

    client.post(
        "/api/proposals/regenerate", json={"notify_telegram": False},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    by_ticker = {p["ticker"]: p for p in proposals.list_all()}
    assert by_ticker["AAPL"]["status"] == "rejected"
    assert by_ticker["AAPL"]["rejection_reason"] == "manual"
    assert by_ticker["MSFT"]["status"] == "expired"


def test_refresh_persists_last_refresh(client, isolated_storage, auth_token, monkeypatch):
    """Après un POST /refresh, GET /proposals expose last_refresh."""
    fake_result = auto_proposer.ProposerResult(
        ok=True,
        gates=[
            auto_proposer.GateResult("killswitch", True, "ok"),
            auto_proposer.GateResult("macro_regime", True, "BULL_MARKET"),
        ],
        proposals=[],
        diagnostics={"params": {"total_capital": 100_000}},
    )
    monkeypatch.setattr(auto_proposer, "run_and_enqueue", lambda **_: fake_result)

    client.post(
        "/api/proposals/refresh",
        json={"notify_telegram": False, "total_capital": 100_000},
        headers={"Authorization": f"Bearer {auth_token}"},
    )

    resp = client.get("/api/proposals")
    body = resp.json()
    assert body["last_refresh"] is not None
    assert body["last_refresh"]["ok"] is True
    assert body["last_refresh"]["ran_at"].endswith("Z")
    assert body["last_refresh"]["requested_params"]["total_capital"] == 100_000
    assert len(body["last_refresh"]["gates"]) == 2
