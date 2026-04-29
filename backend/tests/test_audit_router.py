"""Tests d'intégration — routers/audit.py

Verrouille les contrats des 3 endpoints qui seront consommés par l'UI
`AuditPage` :
  • GET /api/delisted     — registry tickers retirés
  • GET /api/wfo          — poids OOS + comparaison vs prod
  • GET /api/wfo/history  — série temporelle IC test

Pour chaque endpoint :
  - Schéma de sortie stable (clés présentes, types attendus).
  - Comportement empty / 404.
  - Edge cases (limite, fichier corrompu).
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import api
from modules import audit_summary, delisted, wfo_calibration, wfo_monitor


# ─────────────────────────────────────────────────────────────────
# Fixtures helpers
# ─────────────────────────────────────────────────────────────────

def _isolate_audit_paths(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    """Redirige les 3 chemins de fichiers consommés par le router audit
    vers tmp_path. Retourne le tuple (delisted, wfo_weights, wfo_history).
    """
    deli = tmp_path / "delisted.json"
    wfo_w = tmp_path / "wfo_weights.json"
    wfo_h = tmp_path / "wfo_history.jsonl"
    monkeypatch.setattr(delisted, "DELISTED_PATH", deli)
    monkeypatch.setattr(wfo_calibration, "WFO_OUTPUT_PATH", wfo_w)
    monkeypatch.setattr(wfo_monitor, "WFO_HISTORY_PATH", wfo_h)
    return deli, wfo_w, wfo_h


def _client() -> TestClient:
    return TestClient(api.app)


# ─────────────────────────────────────────────────────────────────
# /api/delisted
# ─────────────────────────────────────────────────────────────────

def test_delisted_empty_registry_returns_zero_counts(monkeypatch, tmp_path: Path):
    """Registry absent → 200 avec n_total=0 / n_active=0 / n_delisted=0."""
    _isolate_audit_paths(monkeypatch, tmp_path)
    r = _client().get("/api/delisted")
    assert r.status_code == 200
    body = r.json()
    assert body["n_total"] == 0
    assert body["n_active"] == 0
    assert body["n_delisted"] == 0
    assert body["delisted"] == []
    assert "registry_path" in body


def test_delisted_after_diff_exposes_counts_and_rows(monkeypatch, tmp_path: Path):
    """Après un retrait, l'endpoint reflète l'état du registry."""
    _isolate_audit_paths(monkeypatch, tmp_path)

    # 1er run : seed les 3 tickers comme additions (sinon ils ne sont pas dans
    # le registry).
    delisted.record_diff(
        prev_tickers=None,
        new_tickers={"AAPL": {"sector": "Tech"},
                     "TEAM": {"sector": "Tech"},
                     "BXP":  {"sector": "Real Estate"}},
        today="2026-04-22",
        alert=False,
    )
    # 2e run : retire TEAM et BXP, garde AAPL.
    delisted.record_diff(
        prev_tickers={"AAPL": {"sector": "Tech"},
                      "TEAM": {"sector": "Tech"},
                      "BXP":  {"sector": "Real Estate"}},
        new_tickers={"AAPL": {"sector": "Tech"}},
        today="2026-04-23",
        alert=False,
    )

    body = _client().get("/api/delisted").json()
    assert body["n_total"] == 3
    assert body["n_active"] == 1
    assert body["n_delisted"] == 2
    delisted_tickers = {row["ticker"] for row in body["delisted"]}
    assert delisted_tickers == {"TEAM", "BXP"}
    # Chaque row doit exposer les 4 champs critiques de l'UI.
    for row in body["delisted"]:
        assert set(row.keys()) >= {"ticker", "first_seen", "removed_at", "sector"}
        assert row["removed_at"] == "2026-04-23"


def test_delisted_rows_sorted_desc_by_removed_at(monkeypatch, tmp_path: Path):
    """L'UI affiche les retraits les plus récents en tête."""
    _isolate_audit_paths(monkeypatch, tmp_path)

    delisted.record_diff(None, {"A": {}, "B": {}, "C": {}},
                         today="2026-04-20", alert=False)
    delisted.record_diff({"A": {}, "B": {}, "C": {}}, {"C": {}},
                         today="2026-04-25", alert=False)
    delisted.record_diff({"C": {}}, {},
                         today="2026-04-22", alert=False)

    body = _client().get("/api/delisted").json()
    dates = [r["removed_at"] for r in body["delisted"]]
    assert dates == sorted(dates, reverse=True), \
        f"Tri desc attendu, got {dates}"


# ─────────────────────────────────────────────────────────────────
# /api/wfo
# ─────────────────────────────────────────────────────────────────

def test_wfo_returns_404_when_never_run(monkeypatch, tmp_path: Path):
    """Fichier wfo_weights.json absent → 404 explicite avec hint."""
    _isolate_audit_paths(monkeypatch, tmp_path)
    r = _client().get("/api/wfo")
    assert r.status_code == 404
    assert "wfo_weights.json" in r.json()["detail"]


def test_wfo_returns_payload_with_prod_weights_and_deltas(monkeypatch, tmp_path: Path):
    """Le router enrichit le payload avec prod_weights + weight_deltas."""
    _, wfo_w, _ = _isolate_audit_paths(monkeypatch, tmp_path)

    # Payload calibration synthétique : seul momentum à 0.5, le reste à 0.
    fake_payload = {
        "n_folds": 3,
        "pillars": ["quality_score", "value_score", "momentum_score"],
        "avg_weights": {
            "quality_score": 0.10,
            "value_score":   0.05,
            "risk_score":    0.05,
            "sentiment_score": 0.0,
            "momentum_score":  0.50,
            "piotroski_score": 0.10,
            "growth_score":    0.20,
        },
        "avg_ic_test": 0.045,
        "diagnostics": {"n_snapshots": 100, "n_pairs": 80,
                         "train_days": 60, "test_days": 20,
                         "publication_lag_days": 90,
                         "grid_step": 0.05,
                         "date_range": {"start": "2026-01-01",
                                        "end": "2026-04-01"}},
        "folds": [],
    }
    wfo_w.write_text(json.dumps(fake_payload), encoding="utf-8")

    body = _client().get("/api/wfo").json()
    # Le payload original doit être préservé.
    assert body["n_folds"] == 3
    assert body["avg_ic_test"] == 0.045
    # Et augmenté par le router.
    assert "prod_weights" in body
    assert "weight_deltas" in body
    # Les prod_weights viennent de _scoring._W_TITAN_*.
    assert set(body["prod_weights"]) >= {"quality_score", "momentum_score"}
    # Sanity : le delta est cohérent avec OOS - prod.
    for p, opt in body["avg_weights"].items():
        prod = body["prod_weights"].get(p, 0.0)
        assert body["weight_deltas"][p] == round(opt - prod, 4)


def test_wfo_returns_503_on_corrupted_file(monkeypatch, tmp_path: Path):
    """Fichier wfo_weights.json corrompu → 503 plutôt qu'un 500 silencieux."""
    _, wfo_w, _ = _isolate_audit_paths(monkeypatch, tmp_path)
    wfo_w.write_text("{not valid json", encoding="utf-8")
    r = _client().get("/api/wfo")
    assert r.status_code == 503
    assert "corrompu" in r.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────
# /api/wfo/history
# ─────────────────────────────────────────────────────────────────

def test_wfo_history_empty_returns_safe_struct(monkeypatch, tmp_path: Path):
    """Pas de fichier → struct {n_total: 0, history: [], latest: None}."""
    _isolate_audit_paths(monkeypatch, tmp_path)
    r = _client().get("/api/wfo/history")
    assert r.status_code == 200
    body = r.json()
    assert body["n_total"] == 0
    assert body["n_returned"] == 0
    assert body["history"] == []
    assert body["latest"] is None
    # Le seuil de dégradation doit être exposé pour que l'UI affiche
    # une zone "danger" cohérente avec le serveur.
    assert "threshold_ic" in body
    assert isinstance(body["threshold_ic"], (int, float))


def test_wfo_history_returns_entries_chronologically(monkeypatch, tmp_path: Path):
    """3 entrées appendées → history triée chronologiquement, latest = dernière."""
    _, _, wfo_h = _isolate_audit_paths(monkeypatch, tmp_path)
    entries = [
        {"timestamp": "2026-04-01T00:00:00Z", "status": "ok", "avg_ic_test": 0.04},
        {"timestamp": "2026-04-15T00:00:00Z", "status": "ok", "avg_ic_test": 0.03},
        {"timestamp": "2026-05-01T00:00:00Z", "status": "ok", "avg_ic_test": 0.05},
    ]
    with open(wfo_h, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")

    body = _client().get("/api/wfo/history").json()
    assert body["n_total"] == 3
    assert body["n_returned"] == 3
    # Ordre = ordre du fichier (append-only chronologique).
    assert [e["timestamp"] for e in body["history"]] == [
        "2026-04-01T00:00:00Z",
        "2026-04-15T00:00:00Z",
        "2026-05-01T00:00:00Z",
    ]
    # Latest = dernière entrée (plus récente).
    assert body["latest"]["timestamp"] == "2026-05-01T00:00:00Z"


def test_wfo_history_respects_limit(monkeypatch, tmp_path: Path):
    """`limit=2` sur 5 entrées → n_returned=2, n_total=5 (compteur global)."""
    _, _, wfo_h = _isolate_audit_paths(monkeypatch, tmp_path)
    with open(wfo_h, "w", encoding="utf-8") as fh:
        for i in range(5):
            fh.write(json.dumps({"i": i, "status": "ok"}) + "\n")

    body = _client().get("/api/wfo/history?limit=2").json()
    assert body["n_total"] == 5
    assert body["n_returned"] == 2
    # Les 2 plus récentes (queue du fichier).
    assert [e["i"] for e in body["history"]] == [3, 4]


def test_wfo_history_rejects_invalid_limit(monkeypatch, tmp_path: Path):
    """limit=0 → 422 (Query.ge=1)."""
    _isolate_audit_paths(monkeypatch, tmp_path)
    assert _client().get("/api/wfo/history?limit=0").status_code == 422
    assert _client().get("/api/wfo/history?limit=10000").status_code == 422


def test_wfo_history_skips_blank_lines(monkeypatch, tmp_path: Path):
    """Lignes vides ou JSON invalide ignorées (résilience)."""
    _, _, wfo_h = _isolate_audit_paths(monkeypatch, tmp_path)
    wfo_h.write_text(
        '{"i": 1, "status": "ok"}\n'
        '\n'
        'not json\n'
        '{"i": 2, "status": "ok"}\n',
        encoding="utf-8",
    )
    body = _client().get("/api/wfo/history").json()
    # 2 lignes valides ; les 2 garbage sont skip silencieusement par _read_history.
    assert body["n_returned"] == 2
    assert [e["i"] for e in body["history"]] == [1, 2]
    # n_total compte les lignes non-vides (la blank line ne compte pas).
    assert body["n_total"] == 3


# ─────────────────────────────────────────────────────────────────
# /api/audit/full
# ─────────────────────────────────────────────────────────────────

def test_audit_full_returns_complete_payload_shape(monkeypatch, tmp_path):
    """L'endpoint expose toutes les clés que la page React consomme."""
    _isolate_audit_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(audit_summary, "_BACKTEST_CACHE_PATH",
                         tmp_path / "bt_cache.json")

    # On force le backtest à retourner un payload OK (sans appeler yfinance).
    fake_bt = {
        "_status":          "ok",
        "_cached_at":       0,
        "n_periods":        24,
        "total_return":     0.10,
        "benchmark_return": 0.05,
        "alpha":            0.05,
        "sharpe_annual":    1.2,
        "hit_rate":         0.62,
        "max_drawdown":     0.08,
        "diagnostics":      {"publication_lag_days": 90},
    }
    monkeypatch.setattr(audit_summary, "get_latest_backtest",
                         lambda *, refresh=False: fake_bt)

    body = _client().get("/api/audit/full").json()
    assert set(body.keys()) >= {
        "verdict", "checks", "backtest", "wfo", "delisted",
        "history", "ic_history", "thresholds",
    }
    assert body["verdict"]["verdict"] in {
        "BAT_LE_MARCHÉ", "INCERTAIN", "NE_BAT_PAS", "DONNÉES_INSUFFISANTES",
    }
    assert isinstance(body["checks"], list) and body["checks"]
    # Chaque check doit avoir le contrat minimal pour l'UI.
    for c in body["checks"]:
        assert {"name", "category", "status", "label", "value",
                 "message"}.issubset(c.keys())
        assert c["status"] in {"ok", "warn", "fail", "na"}
        assert c["category"] in {"performance", "data", "model"}


def test_audit_full_verdict_data_insufficient_when_backtest_fails(
    monkeypatch, tmp_path,
):
    _isolate_audit_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(audit_summary, "_BACKTEST_CACHE_PATH",
                         tmp_path / "bt_cache.json")
    monkeypatch.setattr(
        audit_summary, "get_latest_backtest",
        lambda *, refresh=False: {
            "_status": "insufficient_data",
            "_error":  "no snapshots",
        },
    )

    body = _client().get("/api/audit/full").json()
    assert body["verdict"]["verdict"] == "DONNÉES_INSUFFISANTES"
    # Tous les checks performance basculent en `na`.
    perf = [c for c in body["checks"] if c["category"] == "performance"]
    assert perf and all(c["status"] == "na" for c in perf)
