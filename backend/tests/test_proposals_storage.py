"""Tests storage propositions — CRUD + transitions + dédup + expiration.

Couvre :
  - make_proposal : valeurs initiales + ID format + TTL
  - enqueue_batch : insertion + dédup ticker pending + audit jsonl
  - list_all : tri descendant + filtre status + sweep expiration
  - update_status : transitions valides/invalides, statuts terminaux
  - expire_pending : marque pending > TTL en expired
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from modules import proposals


@pytest.fixture
def isolated_proposals(tmp_path, monkeypatch):
    """Redirige les chemins vers tmp_path → tests indépendants.

    Inclut aussi l'isolation du trade_journal.csv utilisé par
    `_recently_closed_tickers` (WIN/LOSS cooldown) — sinon les tests
    voient les trades fermés du vrai journal prod.
    """
    p_path = tmp_path / "proposals.json"
    p_lock = tmp_path / "proposals.json.lock"
    p_audit = tmp_path / "proposals_audit.jsonl"
    monkeypatch.setattr(proposals, "PROPOSALS_PATH", p_path)
    monkeypatch.setattr(proposals, "PROPOSALS_LOCK_PATH", p_lock)
    monkeypatch.setattr(proposals, "PROPOSALS_AUDIT_PATH", p_audit)
    # Isole aussi le trade_journal pour `_recently_closed_tickers`.
    from modules import utils as mod_utils
    monkeypatch.setattr(mod_utils, "CSV_PATH", tmp_path / "trade_journal.csv")
    monkeypatch.setattr(mod_utils, "CSV_LOCK_PATH", tmp_path / "trade_journal.csv.lock")
    return p_path, p_audit


def _mk(ticker="AAPL", **kw):
    defaults = dict(
        ticker=ticker, direction="LONG",
        entry=150.0, stop_loss=142.5, take_profit=180.0,
        size=10, sector="Technology", signal="AUTO_PROPOSAL",
    )
    defaults.update(kw)
    return proposals.make_proposal(**defaults)


def test_make_proposal_fields(isolated_proposals):
    p = _mk()
    assert p.id.startswith("PROP_") and len(p.id) == 13
    assert p.status == "pending"
    assert p.ticker == "AAPL"
    assert p.entry == 150.0
    assert p.created_at < p.expires_at  # TTL strictly future


def test_make_proposal_ttl_explicit_36h(isolated_proposals):
    p = _mk(ttl_hours=36)
    created = datetime.fromisoformat(p.created_at.replace("Z", "+00:00"))
    expires = datetime.fromisoformat(p.expires_at.replace("Z", "+00:00"))
    delta = expires - created
    assert delta == timedelta(hours=36)


def test_make_proposal_ttl_disabled_by_default(isolated_proposals, monkeypatch):
    """Audit 2026-05-07 — TTL désactivé par défaut. `expires_at` doit pointer
    sur le sentinel `_NEVER_EXPIRES_ISO` ; `expire_pending` ne doit jamais
    déclencher sur ce sentinel.
    """
    monkeypatch.delenv("PROPOSAL_TTL_HOURS", raising=False)
    p = _mk()  # ttl_hours non précisé → env (default 0)
    assert p.expires_at == proposals._NEVER_EXPIRES_ISO


def test_make_proposal_ttl_env_override(isolated_proposals, monkeypatch):
    """PROPOSAL_TTL_HOURS=12 → expiration 12h après création."""
    monkeypatch.setenv("PROPOSAL_TTL_HOURS", "12")
    p = _mk()  # ttl_hours non précisé → env
    created = datetime.fromisoformat(p.created_at.replace("Z", "+00:00"))
    expires = datetime.fromisoformat(p.expires_at.replace("Z", "+00:00"))
    assert expires - created == timedelta(hours=12)


def test_expire_pending_skips_never_sentinel(isolated_proposals, monkeypatch):
    """Audit 2026-05-07 — une proposition créée sans TTL (sentinel) ne doit
    JAMAIS être expirée par `expire_pending` (filet de sécurité contre une
    régression du comparateur ISO).
    """
    monkeypatch.delenv("PROPOSAL_TTL_HOURS", raising=False)
    proposals.enqueue_batch([_mk("AAPL")])
    assert proposals.expire_pending() == 0
    items = proposals.list_all()
    assert items[0]["status"] == "pending"
    assert items[0]["expires_at"] == proposals._NEVER_EXPIRES_ISO


def test_enqueue_batch_inserts_all(isolated_proposals):
    p_path, _audit = isolated_proposals
    inserted = proposals.enqueue_batch([_mk("AAPL"), _mk("MSFT"), _mk("NVDA")])
    assert len(inserted) == 3
    assert {p["ticker"] for p in inserted} == {"AAPL", "MSFT", "NVDA"}
    # File réellement écrite
    data = json.loads(p_path.read_text())
    assert len(data) == 3


def test_enqueue_batch_dedup_pending_ticker(isolated_proposals):
    proposals.enqueue_batch([_mk("AAPL")])
    inserted = proposals.enqueue_batch([_mk("AAPL"), _mk("MSFT")])
    assert len(inserted) == 1
    assert inserted[0]["ticker"] == "MSFT"
    assert len(proposals.list_all()) == 2


def test_enqueue_batch_writes_audit(isolated_proposals):
    _p, audit = isolated_proposals
    proposals.enqueue_batch([_mk("AAPL"), _mk("MSFT")])
    lines = audit.read_text().strip().split("\n")
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["event"] == "created"
    assert rec["ticker"] in {"AAPL", "MSFT"}


def test_list_all_filter_status(isolated_proposals):
    proposals.enqueue_batch([_mk("AAPL"), _mk("MSFT")])
    items = proposals.list_all()
    assert len(items) == 2
    assert {i["ticker"] for i in items} == {"AAPL", "MSFT"}
    # Filtre par status
    assert len(proposals.list_all(status="pending")) == 2
    assert proposals.list_all(status="executed") == []


def test_update_status_pending_to_rejected(isolated_proposals):
    inserted = proposals.enqueue_batch([_mk("AAPL")])
    pid = inserted[0]["id"]
    updated = proposals.update_status(
        pid, "rejected", rejection_reason="too_risky",
    )
    assert updated["status"] == "rejected"
    assert updated["rejection_reason"] == "too_risky"
    assert updated["decided_at"] is not None
    assert updated["decided_by"] == "user"


def test_update_status_pending_to_approved_to_executed(isolated_proposals):
    inserted = proposals.enqueue_batch([_mk("AAPL")])
    pid = inserted[0]["id"]
    proposals.update_status(pid, "approved")
    updated = proposals.update_status(pid, "executed", order_id="PROP_ABC123")
    assert updated["status"] == "executed"
    assert updated["order_id"] == "PROP_ABC123"


def test_update_status_invalid_transition_raises(isolated_proposals):
    inserted = proposals.enqueue_batch([_mk("AAPL")])
    pid = inserted[0]["id"]
    proposals.update_status(pid, "rejected")
    with pytest.raises(ValueError, match="déjà terminée"):
        proposals.update_status(pid, "approved")


def test_update_status_unknown_id_raises(isolated_proposals):
    with pytest.raises(ValueError, match="introuvable"):
        proposals.update_status("PROP_NOPE", "rejected")


def test_update_status_invalid_target_raises(isolated_proposals):
    inserted = proposals.enqueue_batch([_mk("AAPL")])
    pid = inserted[0]["id"]
    with pytest.raises(ValueError, match="Statut cible invalide"):
        proposals.update_status(pid, "magic")


def test_expire_pending_marks_overdue(isolated_proposals):
    # Crée une proposition déjà expirée (expires_at dans le passé).
    past = datetime.now(UTC) - timedelta(hours=1)
    past_iso = past.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    p = _mk("AAPL")
    p.expires_at = past_iso
    proposals.enqueue_batch([p])

    n_expired = proposals.expire_pending()
    assert n_expired == 1
    items = proposals.list_all()
    assert items[0]["status"] == "expired"
    assert items[0]["decided_by"] == "system"


def test_expire_pending_idempotent(isolated_proposals):
    proposals.enqueue_batch([_mk("AAPL")])  # TTL 36h, pas expiré
    assert proposals.expire_pending() == 0
    assert proposals.expire_pending() == 0


# ─────────────────────────────────────────────────────────────────
# expire_stale_pending — purge par âge (created_at), indépendante de expires_at.
# Audit 2026-07-16 : sans ça, un pending jamais traité par l'utilisateur bloque
# indéfiniment son propre ticker (dédup) et fige l'écran Propositions sur les
# mêmes noms (score/prix figés à la création).
# ─────────────────────────────────────────────────────────────────

def test_expire_stale_pending_marks_overdue_by_age(isolated_proposals, monkeypatch):
    monkeypatch.setenv("PENDING_MAX_AGE_DAYS", "7")
    p = _mk("AAPL")
    old = datetime.now(UTC) - timedelta(days=8)
    p.created_at = old.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    proposals.enqueue_batch([p])

    n = proposals.expire_stale_pending()
    assert n == 1
    items = proposals.list_all()
    assert items[0]["status"] == "expired"
    assert items[0]["decided_by"] == "system"
    assert items[0]["rejection_reason"] == "stale_max_age"


def test_expire_stale_pending_keeps_recent(isolated_proposals, monkeypatch):
    monkeypatch.setenv("PENDING_MAX_AGE_DAYS", "7")
    proposals.enqueue_batch([_mk("AAPL")])  # created_at = now
    assert proposals.expire_stale_pending() == 0
    assert proposals.list_all(status="pending")[0]["ticker"] == "AAPL"


def test_expire_stale_pending_idempotent(isolated_proposals, monkeypatch):
    monkeypatch.setenv("PENDING_MAX_AGE_DAYS", "7")
    p = _mk("AAPL")
    old = datetime.now(UTC) - timedelta(days=10)
    p.created_at = old.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    proposals.enqueue_batch([p])
    assert proposals.expire_stale_pending() == 1
    assert proposals.expire_stale_pending() == 0


def test_expire_stale_pending_disabled_when_zero(isolated_proposals, monkeypatch):
    monkeypatch.setenv("PENDING_MAX_AGE_DAYS", "0")
    p = _mk("AAPL")
    old = datetime.now(UTC) - timedelta(days=30)
    p.created_at = old.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    proposals.enqueue_batch([p])
    assert proposals.expire_stale_pending() == 0
    assert proposals.list_all(status="pending")[0]["ticker"] == "AAPL"


def test_expire_stale_pending_default_7d_when_env_unset(isolated_proposals, monkeypatch):
    monkeypatch.delenv("PENDING_MAX_AGE_DAYS", raising=False)
    p = _mk("AAPL")
    old = datetime.now(UTC) - timedelta(days=7, hours=1)
    p.created_at = old.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    proposals.enqueue_batch([p])
    assert proposals.expire_stale_pending() == 1


def test_list_all_triggers_stale_pending_sweep(isolated_proposals, monkeypatch):
    """`list_all` doit purger les stale au même titre que `expire_pending`."""
    monkeypatch.setenv("PENDING_MAX_AGE_DAYS", "7")
    p = _mk("AAPL")
    old = datetime.now(UTC) - timedelta(days=8)
    p.created_at = old.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    proposals.enqueue_batch([p])

    items = proposals.list_all()
    assert items[0]["status"] == "expired"


def test_enqueue_batch_purges_stale_pending_before_dedup(isolated_proposals, monkeypatch):
    """Un ticker resté pending > PENDING_MAX_AGE_DAYS ne doit plus bloquer sa
    propre case : le prochain enqueue_batch le purge d'abord puis insère la
    proposition fraîche (score/prix à jour) au lieu de la skipper en dédup.
    """
    monkeypatch.setenv("PENDING_MAX_AGE_DAYS", "7")
    stale = _mk("NVDA", entry=100.0)
    old = datetime.now(UTC) - timedelta(days=9)
    stale.created_at = old.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    inserted_stale = proposals.enqueue_batch([stale])
    assert len(inserted_stale) == 1  # inséré (le sweep tourne AVANT, sur la file encore vide)

    fresh = _mk("NVDA", entry=150.0)  # nouveau prix, cycle du jour
    inserted = proposals.enqueue_batch([fresh])
    assert len(inserted) == 1
    assert inserted[0]["entry"] == 150.0

    pending = proposals.list_all(status="pending")
    assert len(pending) == 1
    assert pending[0]["entry"] == 150.0


def test_corrupt_file_recovered_with_backup(isolated_proposals):
    p_path, _audit = isolated_proposals
    p_path.parent.mkdir(parents=True, exist_ok=True)
    p_path.write_text("{not_valid json")
    # Lecture doit retourner liste vide, déplacer le fichier en .corrupt.*
    items = proposals.list_all()
    assert items == []
    backups = list(p_path.parent.glob("*.corrupt.*.json"))
    assert len(backups) == 1


# ─────────────────────────────────────────────────────────────────
# Cooldown anti-veto (audit 2026-04-23)
# ─────────────────────────────────────────────────────────────────

def test_enqueue_skip_ticker_recently_vetoed(isolated_proposals, monkeypatch):
    """Un ticker rejected il y a 2h avec cooldown 7j → skippé."""
    monkeypatch.setenv("VETO_COOLDOWN_DAYS", "7")
    inserted = proposals.enqueue_batch([_mk("AAPL")])
    pid = inserted[0]["id"]
    proposals.update_status(pid, "rejected", rejection_reason="user_veto")

    # Re-propose → doit être skip.
    re_inserted = proposals.enqueue_batch([_mk("AAPL"), _mk("MSFT")])
    assert {p["ticker"] for p in re_inserted} == {"MSFT"}


def test_enqueue_accept_ticker_vetoed_before_cooldown(isolated_proposals, monkeypatch):
    """Un ticker rejected il y a 10j avec cooldown 7j → reproposable."""
    monkeypatch.setenv("VETO_COOLDOWN_DAYS", "7")
    inserted = proposals.enqueue_batch([_mk("AAPL")])
    pid = inserted[0]["id"]
    proposals.update_status(pid, "rejected", rejection_reason="user_veto")

    # Manuel : backdate le decided_at à 10 jours.
    data = json.loads(isolated_proposals[0].read_text())
    old_iso = (datetime.now(UTC) - timedelta(days=10)).replace(
        microsecond=0,
    ).isoformat().replace("+00:00", "Z")
    for item in data:
        if item["id"] == pid:
            item["decided_at"] = old_iso
    isolated_proposals[0].write_text(json.dumps(data))

    # Re-propose → doit passer (veto trop ancien).
    re_inserted = proposals.enqueue_batch([_mk("AAPL")])
    assert len(re_inserted) == 1
    assert re_inserted[0]["ticker"] == "AAPL"


def test_enqueue_cooldown_zero_disables_antiveto(isolated_proposals, monkeypatch):
    """VETO_COOLDOWN_DAYS=0 → comportement legacy (rejected reproposable)."""
    monkeypatch.setenv("VETO_COOLDOWN_DAYS", "0")
    inserted = proposals.enqueue_batch([_mk("AAPL")])
    pid = inserted[0]["id"]
    proposals.update_status(pid, "rejected", rejection_reason="user_veto")

    re_inserted = proposals.enqueue_batch([_mk("AAPL")])
    assert len(re_inserted) == 1


def test_enqueue_pending_beats_cooldown_check(isolated_proposals, monkeypatch):
    """Une proposition pending bloque toujours, peu importe le cooldown."""
    monkeypatch.setenv("VETO_COOLDOWN_DAYS", "0")
    proposals.enqueue_batch([_mk("AAPL")])  # pending
    re_inserted = proposals.enqueue_batch([_mk("AAPL")])
    assert re_inserted == []


def test_enqueue_cooldown_disabled_by_default(isolated_proposals, monkeypatch):
    """Audit 2026-05-07 — cooldown veto OFF par défaut. Un ticker rejected
    doit être reproposable au prochain cycle sans attendre. L'env var
    VETO_COOLDOWN_DAYS=N réactive l'ancien comportement.
    """
    monkeypatch.delenv("VETO_COOLDOWN_DAYS", raising=False)
    inserted = proposals.enqueue_batch([_mk("AAPL")])
    pid = inserted[0]["id"]
    proposals.update_status(pid, "rejected", rejection_reason="user_veto")

    re_inserted = proposals.enqueue_batch([_mk("AAPL")])
    assert len(re_inserted) == 1, (
        "Default cooldown=0 → ticker rejected reproposable immédiatement"
    )
    assert re_inserted[0]["ticker"] == "AAPL"


# ─────────────────────────────────────────────────────────────────
# Cooldown anti-churn WIN/LOSS (audit 2026-04-23 Long-Term)
# ─────────────────────────────────────────────────────────────────

def _write_journal(tmp_path, rows: list[dict]) -> None:
    """Écrit un trade_journal.csv minimal dans tmp_path/data/."""
    import pandas as pd
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    cols = ["Date", "Ticker", "Direction", "Entry", "Stop_Loss", "Take_Profit",
            "Size", "Status", "Exit_Price", "Exit_Date"]
    df = pd.DataFrame([{**{c: "" for c in cols}, **r} for r in rows])
    df.to_csv(data_dir / "trade_journal.csv", index=False)
    return data_dir / "trade_journal.csv"


def test_enqueue_skip_ticker_recently_closed_win(isolated_proposals, monkeypatch, tmp_path):
    """Un ticker WIN clôturé il y a 2h avec cooldown 14j → skippé."""
    monkeypatch.setenv("WIN_COOLDOWN_DAYS", "14")
    monkeypatch.delenv("VETO_COOLDOWN_DAYS", raising=False)
    recent = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    csv_path = _write_journal(tmp_path, [
        {"Ticker": "CTRA", "Status": "WIN", "Exit_Date": recent},
    ])
    import modules.utils as mutils
    monkeypatch.setattr(mutils, "CSV_PATH", csv_path)

    inserted = proposals.enqueue_batch([_mk("CTRA"), _mk("MSFT")])
    assert {p["ticker"] for p in inserted} == {"MSFT"}


def test_enqueue_skip_ticker_recently_closed_loss(isolated_proposals, monkeypatch, tmp_path):
    """Un ticker LOSS (SL touché) déclenche aussi le cooldown — même logique
    anti-churn, on évite de remettre les doigts dans la prise dans les 14j.
    """
    monkeypatch.setenv("WIN_COOLDOWN_DAYS", "14")
    recent = (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")
    csv_path = _write_journal(tmp_path, [
        {"Ticker": "LVS", "Status": "LOSS", "Exit_Date": recent},
    ])
    import modules.utils as mutils
    monkeypatch.setattr(mutils, "CSV_PATH", csv_path)

    inserted = proposals.enqueue_batch([_mk("LVS")])
    assert inserted == []


def test_enqueue_accept_ticker_closed_before_cooldown(isolated_proposals, monkeypatch, tmp_path):
    """Un ticker WIN clôturé il y a 20j avec cooldown 14j → reproposable."""
    monkeypatch.setenv("WIN_COOLDOWN_DAYS", "14")
    old = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d %H:%M")
    csv_path = _write_journal(tmp_path, [
        {"Ticker": "REGN", "Status": "WIN", "Exit_Date": old},
    ])
    import modules.utils as mutils
    monkeypatch.setattr(mutils, "CSV_PATH", csv_path)

    inserted = proposals.enqueue_batch([_mk("REGN")])
    assert len(inserted) == 1 and inserted[0]["ticker"] == "REGN"


def test_enqueue_win_cooldown_zero_disables(isolated_proposals, monkeypatch, tmp_path):
    """WIN_COOLDOWN_DAYS=0 → comportement legacy (ticker WIN reproposable immédiatement)."""
    monkeypatch.setenv("WIN_COOLDOWN_DAYS", "0")
    recent = datetime.now().strftime("%Y-%m-%d %H:%M")
    csv_path = _write_journal(tmp_path, [
        {"Ticker": "CTRA", "Status": "WIN", "Exit_Date": recent},
    ])
    import modules.utils as mutils
    monkeypatch.setattr(mutils, "CSV_PATH", csv_path)

    inserted = proposals.enqueue_batch([_mk("CTRA")])
    assert len(inserted) == 1


def test_enqueue_win_cooldown_default_14d_when_env_unset(isolated_proposals, monkeypatch, tmp_path):
    """Sans env var, cooldown par défaut = 14j → skip un WIN frais."""
    monkeypatch.delenv("WIN_COOLDOWN_DAYS", raising=False)
    recent = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M")
    csv_path = _write_journal(tmp_path, [
        {"Ticker": "CTRA", "Status": "WIN", "Exit_Date": recent},
    ])
    import modules.utils as mutils
    monkeypatch.setattr(mutils, "CSV_PATH", csv_path)

    inserted = proposals.enqueue_batch([_mk("CTRA")])
    assert inserted == []


def test_enqueue_ignores_open_positions_for_cooldown(isolated_proposals, monkeypatch, tmp_path):
    """Une position OPEN (pas encore clôturée) ne déclenche pas le cooldown —
    la dedup OPEN est gérée ailleurs (already_held dans auto_proposer)."""
    monkeypatch.setenv("WIN_COOLDOWN_DAYS", "14")
    csv_path = _write_journal(tmp_path, [
        {"Ticker": "MU", "Status": "OPEN", "Exit_Date": ""},
    ])
    import modules.utils as mutils
    monkeypatch.setattr(mutils, "CSV_PATH", csv_path)

    inserted = proposals.enqueue_batch([_mk("MU")])
    # MU OPEN dans le journal ne doit PAS bloquer enqueue (OPEN ≠ WIN/LOSS).
    assert len(inserted) == 1


def test_enqueue_handles_missing_journal_gracefully(isolated_proposals, monkeypatch, tmp_path):
    """Pas de fichier trade_journal.csv → cooldown WIN n'explose pas, passe tout."""
    monkeypatch.setenv("WIN_COOLDOWN_DAYS", "14")
    import modules.utils as mutils
    monkeypatch.setattr(mutils, "CSV_PATH", tmp_path / "nonexistent.csv")

    inserted = proposals.enqueue_batch([_mk("AAPL")])
    assert len(inserted) == 1


# ─────────────────────────────────────────────────────────────────
# Historique des vétos — list_veto_history + veto_history_summary
# (audit 2026-05-07 : facilite la consultation, plus de cooldown imposé)
# ─────────────────────────────────────────────────────────────────

def _veto(ticker: str, reason: str = "user_veto") -> str:
    """Insère + rejette un ticker, retourne l'ID."""
    inserted = proposals.enqueue_batch([_mk(ticker)])
    pid = inserted[0]["id"]
    proposals.update_status(pid, "rejected", rejection_reason=reason)
    return pid


def test_list_veto_history_empty_when_no_rejected(isolated_proposals):
    """Pas de rejected en file → liste vide (pas d'erreur)."""
    proposals.enqueue_batch([_mk("AAPL")])  # pending, pas un veto
    assert proposals.list_veto_history() == []


def test_list_veto_history_returns_rejected_only(isolated_proposals, monkeypatch):
    """Doit ignorer pending/approved/expired/executed — seulement rejected."""
    monkeypatch.delenv("VETO_COOLDOWN_DAYS", raising=False)
    _veto("AAPL")
    _veto("MSFT")
    # Un pending qui ne deviendra pas rejected
    proposals.enqueue_batch([_mk("NVDA")])
    history = proposals.list_veto_history()
    assert {h["ticker"] for h in history} == {"AAPL", "MSFT"}


def test_list_veto_history_filter_by_ticker(isolated_proposals, monkeypatch):
    """Filtre exact (case-insensitive) sur ticker."""
    monkeypatch.delenv("VETO_COOLDOWN_DAYS", raising=False)
    _veto("AAPL", reason="too_expensive")
    _veto("AAPL", reason="just_bought_competitor")
    _veto("MSFT")
    history = proposals.list_veto_history(ticker="aapl")  # lowercase volontaire
    assert len(history) == 2
    assert all(h["ticker"] == "AAPL" for h in history)


def test_list_veto_history_sorted_decided_at_desc(isolated_proposals, monkeypatch):
    """Le plus récent d'abord (ordre des décisions humaines, pas créations)."""
    monkeypatch.delenv("VETO_COOLDOWN_DAYS", raising=False)
    _veto("AAPL")
    _veto("MSFT")
    _veto("NVDA")
    history = proposals.list_veto_history()
    decisions = [h.get("decided_at") for h in history]
    assert decisions == sorted(decisions, reverse=True)


def test_list_veto_history_filter_since_days(isolated_proposals, monkeypatch):
    """`since_days=1` exclut un veto vieux de 5 jours."""
    monkeypatch.delenv("VETO_COOLDOWN_DAYS", raising=False)
    _veto("RECENT")
    pid_old = _veto("OLD")
    # Backdate decided_at de OLD à -5 jours
    p_path = isolated_proposals[0]
    data = json.loads(p_path.read_text())
    old_iso = (datetime.now(UTC) - timedelta(days=5)).replace(
        microsecond=0,
    ).isoformat().replace("+00:00", "Z")
    for item in data:
        if item["id"] == pid_old:
            item["decided_at"] = old_iso
    p_path.write_text(json.dumps(data))

    history = proposals.list_veto_history(since_days=1)
    assert {h["ticker"] for h in history} == {"RECENT"}


def test_list_veto_history_respects_limit(isolated_proposals, monkeypatch):
    """`limit=2` tronque même si 3 vétos disponibles."""
    monkeypatch.delenv("VETO_COOLDOWN_DAYS", raising=False)
    for t in ("A", "B", "C"):
        _veto(t)
    history = proposals.list_veto_history(limit=2)
    assert len(history) == 2


def test_veto_history_summary_aggregates(isolated_proposals, monkeypatch):
    """Synthèse compacte : top tickers + top reasons + last_veto_at."""
    monkeypatch.delenv("VETO_COOLDOWN_DAYS", raising=False)
    _veto("AAPL", reason="too_expensive")
    _veto("AAPL", reason="too_expensive")
    _veto("MSFT", reason="sector_overweight")
    _veto("NVDA", reason="too_expensive")
    summary = proposals.veto_history_summary()
    assert summary["n_total"] == 4
    assert summary["last_veto_at"] is not None
    # AAPL apparaît 2× → premier en top_tickers
    assert summary["top_tickers"][0]["ticker"] == "AAPL"
    assert summary["top_tickers"][0]["n"] == 2
    # too_expensive apparaît 3× → premier en top_reasons
    assert summary["top_reasons"][0]["reason"] == "too_expensive"
    assert summary["top_reasons"][0]["n"] == 3


def test_veto_history_summary_empty(isolated_proposals):
    """Pas de vétos → shape stable mais valeurs vides."""
    summary = proposals.veto_history_summary()
    assert summary["n_total"] == 0
    assert summary["top_tickers"] == []
    assert summary["top_reasons"] == []
    assert summary["last_veto_at"] is None
