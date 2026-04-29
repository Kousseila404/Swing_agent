"""Tests pour `modules.audit_summary` — verdict + checklist."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from modules import audit_summary

# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────

def _bt_ok(*, alpha=0.05, sharpe=1.2, hit=0.62, dd=0.08, n_periods=80,
            total=0.10, bench=0.05, lag=90):
    return {
        "_status":          "ok",
        "_cached_at":       0,
        "strategy":         "titan_top_n",
        "top_n":            20,
        "n_periods":        n_periods,
        "total_return":     total,
        "benchmark_return": bench,
        "alpha":            alpha,
        "sharpe_annual":    sharpe,
        "hit_rate":         hit,
        "max_drawdown":     dd,
        "diagnostics":      {"publication_lag_days": lag},
    }


def _bt_insufficient():
    return {"_status": "insufficient_data", "_error": "no snapshots"}


def _wfo_ok(ic=0.06, max_drift=0.02, n_folds=6):
    deltas = {"quality_score": max_drift, "value_score": -max_drift / 2}
    return {
        "n_folds":       n_folds,
        "avg_ic_test":   ic,
        "weight_deltas": deltas,
        "avg_weights":   {"quality_score": 0.24, "value_score": 0.17},
    }


def _wfo_few_folds(ic=0.0, max_drift=0.30, n_folds=1):
    """Cas n_folds < threshold : drift et IC dominés par le bruit stat."""
    deltas = {"momentum_score": max_drift}
    return {
        "n_folds":       n_folds,
        "avg_ic_test":   ic,
        "weight_deltas": deltas,
        "avg_weights":   {"momentum_score": 0.65},
    }


def _delisted(n=8):
    return {
        "n_total":    500,
        "n_delisted": n,
        "n_active":   500 - n,
        "delisted":   [{"ticker": f"T{i}"} for i in range(n)],
    }


def _hist(n=80):
    return {"n_snapshots": n, "first": "2025-01-01", "last": "2026-04-27"}


# ─────────────────────────────────────────────────────────────────
# _compute_global_verdict
# ─────────────────────────────────────────────────────────────────

def test_verdict_data_insufficient_when_backtest_fails():
    bt = _bt_insufficient()
    checks = audit_summary._build_backtest_checks(bt)
    v = audit_summary._compute_global_verdict(checks, bt)
    assert v["verdict"] == "DONNÉES_INSUFFISANTES"
    assert v["color"] == "muted"


def test_verdict_bat_le_marche_when_all_green():
    bt = _bt_ok()
    checks = (
        audit_summary._build_backtest_checks(bt)
        + audit_summary._build_data_checks(_delisted(), 80, bt)
        + audit_summary._build_model_checks(_wfo_ok(), _hist())
    )
    v = audit_summary._compute_global_verdict(checks, bt)
    assert v["verdict"] == "BAT_LE_MARCHÉ"
    assert v["color"] == "success"
    assert "+5.00%" in v["summary"]


def test_verdict_ne_bat_pas_when_alpha_strongly_negative():
    bt = _bt_ok(alpha=-0.04, total=0.01, bench=0.05)
    checks = (
        audit_summary._build_backtest_checks(bt)
        + audit_summary._build_data_checks(_delisted(), 80, bt)
        + audit_summary._build_model_checks(_wfo_ok(), _hist())
    )
    v = audit_summary._compute_global_verdict(checks, bt)
    assert v["verdict"] == "NE_BAT_PAS"
    assert v["color"] == "danger"


def test_verdict_donnees_insuffisantes_when_below_min_periods_for_verdict():
    """Lot 16 — gating verdict global à n_periods >= 60. Sous le seuil, on
    REFUSE de conclure même avec un alpha apparemment positif."""
    bt = _bt_ok(n_periods=4)  # < 60 périodes
    checks = (
        audit_summary._build_backtest_checks(bt)
        + audit_summary._build_data_checks(_delisted(), 80, bt)
        + audit_summary._build_model_checks(_wfo_ok(), _hist())
    )
    v = audit_summary._compute_global_verdict(checks, bt)
    assert v["verdict"] == "DONNÉES_INSUFFISANTES"
    assert v["color"] == "muted"
    assert v["progress_pct"] is not None
    assert v["n_periods"] == 4
    assert v["n_periods_required"] == 60


def test_verdict_incertain_when_alpha_warn_at_sufficient_periods():
    """Si on a assez de périodes mais alpha en warn (pas fail/pas ok), on
    rentre dans INCERTAIN — chemin classique au-dessus du gating Lot 16."""
    bt = _bt_ok(alpha=-0.001, total=0.05, bench=0.051, n_periods=80)
    checks = (
        audit_summary._build_backtest_checks(bt)
        + audit_summary._build_data_checks(_delisted(), 80, bt)
        + audit_summary._build_model_checks(_wfo_ok(), _hist())
    )
    v = audit_summary._compute_global_verdict(checks, bt)
    assert v["verdict"] == "INCERTAIN"


def test_verdict_incertain_when_alpha_near_zero():
    bt = _bt_ok(alpha=-0.001, total=0.05, bench=0.051)
    checks = (
        audit_summary._build_backtest_checks(bt)
        + audit_summary._build_data_checks(_delisted(), 80, bt)
        + audit_summary._build_model_checks(_wfo_ok(), _hist())
    )
    v = audit_summary._compute_global_verdict(checks, bt)
    # alpha en warn (pas fail, pas ok) → INCERTAIN
    assert v["verdict"] == "INCERTAIN"


# ─────────────────────────────────────────────────────────────────
# _build_backtest_checks
# ─────────────────────────────────────────────────────────────────

def test_backtest_checks_all_na_when_insufficient():
    checks = audit_summary._build_backtest_checks(_bt_insufficient())
    assert {c["status"] for c in checks} == {"na"}
    assert {c["category"] for c in checks} == {"performance"}


def test_backtest_checks_alpha_status_thresholds():
    # Alpha franchement positif → ok
    c = audit_summary._build_backtest_checks(_bt_ok(alpha=0.03))
    alpha_chk = next(x for x in c if x["name"] == "alpha_net")
    assert alpha_chk["status"] == "ok"

    # Alpha proche zéro → warn
    c = audit_summary._build_backtest_checks(_bt_ok(alpha=-0.002))
    alpha_chk = next(x for x in c if x["name"] == "alpha_net")
    assert alpha_chk["status"] == "warn"

    # Alpha franchement négatif → fail
    c = audit_summary._build_backtest_checks(_bt_ok(alpha=-0.05))
    alpha_chk = next(x for x in c if x["name"] == "alpha_net")
    assert alpha_chk["status"] == "fail"


def test_backtest_checks_coverage_scales_with_periods():
    chk_high = next(x for x in audit_summary._build_backtest_checks(_bt_ok(n_periods=30))
                    if x["name"] == "coverage")
    assert chk_high["status"] == "ok"

    chk_mid = next(x for x in audit_summary._build_backtest_checks(_bt_ok(n_periods=10))
                    if x["name"] == "coverage")
    assert chk_mid["status"] == "warn"

    chk_low = next(x for x in audit_summary._build_backtest_checks(_bt_ok(n_periods=2))
                    if x["name"] == "coverage")
    assert chk_low["status"] == "fail"


# ─────────────────────────────────────────────────────────────────
# _build_data_checks
# ─────────────────────────────────────────────────────────────────

def test_data_checks_survivorship_warn_when_empty_registry():
    bt = _bt_ok()
    checks = audit_summary._build_data_checks(_delisted(n=0), 80, bt)
    surv = next(c for c in checks if c["name"] == "survivorship_filter")
    assert surv["status"] == "warn"
    assert surv["value"] == 0


def test_data_checks_history_depth_thresholds():
    bt = _bt_ok()
    ok = next(c for c in audit_summary._build_data_checks(_delisted(), 80, bt)
               if c["name"] == "history_depth")
    assert ok["status"] == "ok"

    warn = next(c for c in audit_summary._build_data_checks(_delisted(), 30, bt)
                 if c["name"] == "history_depth")
    assert warn["status"] == "warn"

    fail = next(c for c in audit_summary._build_data_checks(_delisted(), 5, bt)
                 if c["name"] == "history_depth")
    assert fail["status"] == "fail"


# ─────────────────────────────────────────────────────────────────
# _build_model_checks
# ─────────────────────────────────────────────────────────────────

def test_model_checks_all_na_when_no_wfo():
    checks = audit_summary._build_model_checks(None, _hist())
    assert {c["status"] for c in checks} == {"na"}


def test_model_checks_drift_thresholds():
    # Drift faible → ok
    ok = audit_summary._build_model_checks(_wfo_ok(max_drift=0.02), _hist())
    assert next(c for c in ok if c["name"] == "weights_drift")["status"] == "ok"

    # Drift modéré → warn
    warn = audit_summary._build_model_checks(_wfo_ok(max_drift=0.07), _hist())
    assert next(c for c in warn if c["name"] == "weights_drift")["status"] == "warn"

    # Drift gros → fail
    fail = audit_summary._build_model_checks(_wfo_ok(max_drift=0.15), _hist())
    assert next(c for c in fail if c["name"] == "weights_drift")["status"] == "fail"


def test_model_checks_gating_when_few_folds():
    """Issue n°1 — avec n_folds < seuil, drift et signal_alive doivent être
    `na`, pas `fail` (sinon contradictoire avec wfo_folds: warn).
    """
    checks = audit_summary._build_model_checks(
        _wfo_few_folds(ic=0.0, max_drift=0.30, n_folds=1),
        _hist(),
    )
    drift = next(c for c in checks if c["name"] == "weights_drift")
    sig = next(c for c in checks if c["name"] == "signal_alive")
    folds = next(c for c in checks if c["name"] == "wfo_folds")
    assert drift["status"] == "na"
    assert sig["status"] == "na"
    assert folds["status"] == "warn"  # 1/3 folds


def test_model_checks_signal_alive_thresholds():
    # IC robuste → ok
    ok = audit_summary._build_model_checks(_wfo_ok(ic=0.08), _hist())
    assert next(c for c in ok if c["name"] == "signal_alive")["status"] == "ok"

    # IC marginal → warn
    warn = audit_summary._build_model_checks(_wfo_ok(ic=0.025), _hist())
    assert next(c for c in warn if c["name"] == "signal_alive")["status"] == "warn"

    # IC mort → fail
    fail = audit_summary._build_model_checks(_wfo_ok(ic=0.005), _hist())
    assert next(c for c in fail if c["name"] == "signal_alive")["status"] == "fail"


# ─────────────────────────────────────────────────────────────────
# compute_full_audit (intégration légère)
# ─────────────────────────────────────────────────────────────────

def test_data_checks_no_publication_lag_check():
    """Issue n°2 — le check `publication_lag` a été retiré (auditait une
    constante de config, pas un état de santé).
    """
    bt = _bt_ok()
    checks = audit_summary._build_data_checks(_delisted(), 80, bt)
    names = {c["name"] for c in checks}
    assert "publication_lag" not in names
    # Les 2 vrais checks data restent.
    assert "survivorship_filter" in names
    assert "history_depth" in names


def test_cache_freshness_differentiates_ok_and_error(tmp_path, monkeypatch):
    """Issue n°3 — le cache 24h pour OK ne doit pas piéger les erreurs ;
    une entrée `error`/`insufficient_data` expire en 5 min, pas 24h.
    """
    import time as _t
    now = 1_000_000_000.0
    monkeypatch.setattr(audit_summary.time, "time", lambda: now)

    # OK reste frais 24h
    ok_payload = {"_status": "ok", "_cached_at": now - 23 * 3600}
    assert audit_summary._is_fresh(ok_payload) is True
    ok_stale = {"_status": "ok", "_cached_at": now - 25 * 3600}
    assert audit_summary._is_fresh(ok_stale) is False

    # Error expire au bout de 5 min
    err_fresh = {"_status": "error", "_cached_at": now - 60}
    assert audit_summary._is_fresh(err_fresh) is True
    err_stale = {"_status": "error", "_cached_at": now - 600}
    assert audit_summary._is_fresh(err_stale) is False
    insuf_stale = {"_status": "insufficient_data", "_cached_at": now - 600}
    assert audit_summary._is_fresh(insuf_stale) is False


def test_compute_full_audit_returns_expected_shape(tmp_path, monkeypatch):
    """Avec backtest mocké en `insufficient_data`, on doit avoir un payload
    structurellement complet et un verdict DONNÉES_INSUFFISANTES."""
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(audit_summary, "_BACKTEST_CACHE_PATH", cache_path)

    with patch.object(audit_summary, "get_latest_backtest",
                       return_value=_bt_insufficient()):
        payload = audit_summary.compute_full_audit()

    assert set(payload.keys()) >= {
        "verdict", "checks", "backtest", "wfo", "delisted",
        "history", "ic_history", "thresholds",
    }
    assert payload["verdict"]["verdict"] == "DONNÉES_INSUFFISANTES"
    assert isinstance(payload["checks"], list)
    # Au moins 1 check par catégorie attendue
    cats = {c["category"] for c in payload["checks"]}
    assert "performance" in cats
    assert "data" in cats
    assert "model" in cats
