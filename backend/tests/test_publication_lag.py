"""Tests anti-lookahead — modules/sector_metrics/_scoring.py.

Audit S3.x — _lookup_yoy_snapshot doit appliquer un lag de publication
(`_FUNDAMENTAL_PUBLICATION_LAG_DAYS`, default 90 j) :
  • Snapshot Y-1 retenu seulement si sa date ≤ as_of − lag
  • Sinon → None (les fondamentaux Y-1 n'étaient pas publiés)

Ces tests verrouillent la sémantique du gate anti-lookahead. Ils mockent
`universe_history` pour éviter l'IO réelle.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from modules.sector_metrics import _scoring


@pytest.fixture
def fake_history(monkeypatch):
    """Fixture qui stub `universe_history.list_snapshots` + `read_snapshot`
    avec un ensemble fictif de dates et payloads contrôlable.

    Le test passe `setup({date1: payload1, date2: payload2, ...})`.
    """
    state: dict[date, dict] = {}

    def _list_snapshots():
        return sorted(state.keys())

    def _read_snapshot(d: date):
        return state.get(d)

    # Le module fait un import paresseux dans la fonction → on patche le
    # bon module quand il est importé.
    from modules import universe_history as uh
    monkeypatch.setattr(uh, "list_snapshots", _list_snapshots)
    monkeypatch.setattr(uh, "read_snapshot", _read_snapshot)

    def setup(snapshots: dict[date, dict]) -> None:
        state.clear()
        state.update(snapshots)

    return setup


# ─────────────────────────────────────────────────────────────────
# Sémantique de base : trouve un snapshot dans la fenêtre 300-430j
# ─────────────────────────────────────────────────────────────────

def test_lookup_returns_snapshot_within_yoy_window(fake_history):
    """Un snapshot ~365 j avant as_of (et > as_of - 90 j) est retenu."""
    as_of = date(2026, 4, 22)
    yoy_date = as_of - timedelta(days=365)
    fake_history({
        yoy_date: {"tickers": {"AAPL": {"return_on_assets": 0.18}}},
    })

    row = _scoring._lookup_yoy_snapshot(
        "AAPL", as_of=as_of, publication_lag_days=0,
    )
    assert row is not None
    assert row["return_on_assets"] == 0.18


def test_lookup_returns_none_outside_window(fake_history):
    """Snapshot trop récent (< 300 j) → None."""
    as_of = date(2026, 4, 22)
    too_recent = as_of - timedelta(days=200)  # dans la fenêtre lag mais < 300j
    fake_history({
        too_recent: {"tickers": {"AAPL": {"return_on_assets": 0.18}}},
    })

    row = _scoring._lookup_yoy_snapshot(
        "AAPL", as_of=as_of, publication_lag_days=0,
    )
    assert row is None


def test_lookup_returns_none_outside_window_too_old(fake_history):
    """Snapshot > 430 j → None."""
    as_of = date(2026, 4, 22)
    too_old = as_of - timedelta(days=500)
    fake_history({
        too_old: {"tickers": {"AAPL": {"return_on_assets": 0.18}}},
    })
    row = _scoring._lookup_yoy_snapshot(
        "AAPL", as_of=as_of, publication_lag_days=0,
    )
    assert row is None


# ─────────────────────────────────────────────────────────────────
# Anti-lookahead — gate de publication_lag_days
# ─────────────────────────────────────────────────────────────────

def test_lookup_rejects_snapshot_inside_lag_window(fake_history):
    """Snapshot Y-1 valide géographiquement mais publié *après* as_of - lag
    → doit être rejeté.

    Cas réaliste : as_of = 2026-04-22 (signal_date), lag=90.
    Snapshot @ 2026-02-22 (60 j avant) contient des fondamentaux Y-1
    (FY2024). Mais à la date du signal, ces données n'étaient pas encore
    publiées → on doit refuser.

    Ici on prend un snapshot 320 j avant as_of (donc dans 300-430j), mais
    avec un lag agressif qui le rejette.
    """
    as_of = date(2026, 4, 22)
    snap_d = as_of - timedelta(days=320)  # ~01/06/2025
    fake_history({
        snap_d: {"tickers": {"AAPL": {"return_on_assets": 0.18}}},
    })

    # Lag 0 → accepté (compat legacy).
    row = _scoring._lookup_yoy_snapshot(
        "AAPL", as_of=as_of, publication_lag_days=0,
    )
    assert row is not None

    # Lag 365 j → target_max devient as_of − 365 = 2025-04-22, snapshot
    # @ 2025-06-06 est plus récent que ça → rejeté.
    row = _scoring._lookup_yoy_snapshot(
        "AAPL", as_of=as_of, publication_lag_days=365,
    )
    assert row is None


def test_lookup_lag_default_is_90_days():
    """Le défaut est bien _FUNDAMENTAL_PUBLICATION_LAG_DAYS = 90."""
    assert _scoring._FUNDAMENTAL_PUBLICATION_LAG_DAYS == 90


def test_lookup_returns_none_when_lag_exceeds_window(fake_history):
    """Si lag > 430 jours, target_max < target_min → None garanti
    (impossible de trouver un snapshot satisfaisant les deux gates)."""
    as_of = date(2026, 4, 22)
    # Snapshot dans la fenêtre Y-1 standard.
    snap_d = as_of - timedelta(days=365)
    fake_history({
        snap_d: {"tickers": {"AAPL": {"return_on_assets": 0.18}}},
    })

    row = _scoring._lookup_yoy_snapshot(
        "AAPL", as_of=as_of, publication_lag_days=500,
    )
    assert row is None


# ─────────────────────────────────────────────────────────────────
# Sélection — ordre + ticker absent
# ─────────────────────────────────────────────────────────────────

def test_lookup_picks_most_recent_eligible(fake_history):
    """Plusieurs snapshots éligibles → on prend le plus récent dans la
    fenêtre (donné par `for d in reversed(all_dates)` dans l'impl).
    """
    as_of = date(2026, 4, 22)
    snap_old = as_of - timedelta(days=420)
    snap_mid = as_of - timedelta(days=370)
    snap_new = as_of - timedelta(days=310)
    fake_history({
        snap_old: {"tickers": {"AAPL": {"return_on_assets": 0.10}}},
        snap_mid: {"tickers": {"AAPL": {"return_on_assets": 0.15}}},
        snap_new: {"tickers": {"AAPL": {"return_on_assets": 0.20}}},
    })

    row = _scoring._lookup_yoy_snapshot(
        "AAPL", as_of=as_of, publication_lag_days=0,
    )
    # Plus récent = snap_new → ROA 0.20.
    assert row is not None
    assert row["return_on_assets"] == 0.20


def test_lookup_skips_snapshots_without_ticker(fake_history):
    """Si le ticker n'apparaît pas dans le snapshot le plus récent,
    on remonte vers les plus anciens jusqu'à le trouver.
    """
    as_of = date(2026, 4, 22)
    snap_recent = as_of - timedelta(days=310)
    snap_older = as_of - timedelta(days=400)
    fake_history({
        snap_recent: {"tickers": {"OTHER": {}}},  # AAPL absent ici
        snap_older: {"tickers": {"AAPL": {"return_on_assets": 0.15}}},
    })

    row = _scoring._lookup_yoy_snapshot(
        "AAPL", as_of=as_of, publication_lag_days=0,
    )
    assert row is not None
    assert row["return_on_assets"] == 0.15


def test_lookup_returns_none_when_no_history(fake_history):
    """Aucun snapshot → None (pas d'exception)."""
    as_of = date(2026, 4, 22)
    fake_history({})  # vide
    row = _scoring._lookup_yoy_snapshot("AAPL", as_of=as_of)
    assert row is None


# ─────────────────────────────────────────────────────────────────
# Robustesse — exceptions surfacées par universe_history
# ─────────────────────────────────────────────────────────────────

def test_lookup_skips_corrupted_snapshot(monkeypatch):
    """Si `read_snapshot` lève (snapshot corrompu), on continue sur le
    suivant au lieu de remonter l'exception. Garantit le fail-open."""
    as_of = date(2026, 4, 22)
    snap_corrupt = as_of - timedelta(days=315)
    snap_ok = as_of - timedelta(days=380)

    from modules import universe_history as uh
    monkeypatch.setattr(uh, "list_snapshots", lambda: [snap_ok, snap_corrupt])

    def _read(d):
        if d == snap_corrupt:
            raise RuntimeError("Snapshot corrompu")
        return {"tickers": {"AAPL": {"return_on_assets": 0.12}}}

    monkeypatch.setattr(uh, "read_snapshot", _read)

    # publication_lag_days=0 → snap_corrupt est éligible mais doit être skip.
    # On attend que la fonction se rabatte sur snap_ok.
    row = _scoring._lookup_yoy_snapshot(
        "AAPL", as_of=as_of, publication_lag_days=0,
    )
    assert row is not None
    assert row["return_on_assets"] == 0.12


def test_lookup_returns_none_when_universe_history_raises(monkeypatch):
    """`list_snapshots` lève → None (pas d'exception remontée)."""
    from modules import universe_history as uh

    def _boom():
        raise OSError("disk corrupted")

    monkeypatch.setattr(uh, "list_snapshots", _boom)
    row = _scoring._lookup_yoy_snapshot(
        "AAPL", as_of=date(2026, 4, 22), publication_lag_days=0,
    )
    assert row is None


# ─────────────────────────────────────────────────────────────────
# Audit S3.x rigoureux — gate per-ticker via fundamentals_period_end_y1
# ─────────────────────────────────────────────────────────────────

def _row_with_yoy_fields(period_end_y1: str | None) -> dict:
    """Helper : construit un row TickerFundamentals avec TOUS les champs
    Y0 + Y-1 nécessaires pour 9/9 critères Piotroski."""
    return {
        "ticker": "AAPL",
        # Y0 — utilisés par F1, F2, F4, F7 (absolus) + comparaison Y/Y
        "return_on_assets":            0.15,
        "operating_cash_flow":         1e8,
        "net_income":                  5e7,
        "current_ratio":               1.5,
        "debt_to_equity":              0.30,
        "shares_outstanding":          1e9,
        "gross_margin":                0.45,
        # Y-1 — utilisés par F3, F5, F6, F8, F9
        "return_on_assets_prev_year":  0.12,
        "debt_to_equity_prev_year":    0.40,
        "current_ratio_prev_year":     1.4,
        "shares_outstanding_prev_year": 1e9,
        "gross_margin_prev_year":      0.42,
        "fundamentals_period_end_y1":  period_end_y1,
    }


def test_piotroski_accepts_yoy_when_period_end_published(monkeypatch):
    """Y-1 fiscal end + lag ≤ as_of → row est utilisé pour les critères Y/Y.

    Cas : FY ending 2024-12-31, as_of=2025-06-01, lag=90j → publish_date
    = 2025-03-31 ≤ 2025-06-01 → gate passe → on a 9/9 critères évaluables.
    """
    row = _row_with_yoy_fields("2024-12-31")
    # `current_ratio_prev_year=1.4 < current=1.5` → F6 True
    # On lance la scoring fonction directement.
    score, diag = _scoring._piotroski_score_pillar(
        row, as_of=date(2025, 6, 1), publication_lag_days=90,
    )
    # Avec tous les champs Y-1 présents → 9 critères évalués.
    assert diag["f_score_max"] == 9


def test_piotroski_rejects_yoy_inside_lag_window(monkeypatch):
    """Y-1 fiscal end + lag > as_of → scrape Y-1 NON utilisé. Fallback
    sur _lookup_yoy_snapshot (qui retourne None ici → 4 critères absolus).

    Cas : FY ending 2024-12-31, as_of=2025-02-15, lag=90j → publish_date
    = 2025-03-31 > 2025-02-15 → gate refuse → on retombe sur Y-1 absent
    → 4 critères absolus seulement.
    """
    # Stubber le fallback _lookup_yoy_snapshot pour qu'il retourne None
    # (sinon il pourrait remonter un snapshot historique).
    monkeypatch.setattr(_scoring, "_lookup_yoy_snapshot",
                        lambda *a, **kw: None)

    row = _row_with_yoy_fields("2024-12-31")
    score, diag = _scoring._piotroski_score_pillar(
        row, as_of=date(2025, 2, 15), publication_lag_days=90,
    )
    # Y-1 refusé → seuls les 4 critères absolus (F1, F2, F4, F7).
    assert diag["f_score_max"] == 4


def test_piotroski_no_lag_uses_yoy_unconditionally():
    """`publication_lag_days=0` → comportement legacy : Y-1 toujours utilisé
    si présent, peu importe la date fiscale."""
    row = _row_with_yoy_fields("2030-12-31")  # date dans le futur, lookahead pur
    score, diag = _scoring._piotroski_score_pillar(
        row, as_of=date(2025, 6, 1), publication_lag_days=0,
    )
    # lag=0 → pas de gate → Y-1 utilisé → 9 critères.
    assert diag["f_score_max"] == 9


def test_piotroski_falls_back_when_period_end_missing(monkeypatch):
    """Si `fundamentals_period_end_y1` est None → comportement legacy
    (utilise Y-1 sans gate). C'est le cas pour des providers qui n'extraient
    pas la date (legacy data avant le patch S3.x rigoureux)."""
    row = _row_with_yoy_fields(None)
    score, diag = _scoring._piotroski_score_pillar(
        row, as_of=date(2025, 6, 1), publication_lag_days=90,
    )
    # Pas de date → Y-1 accepté par défaut.
    assert diag["f_score_max"] == 9


def test_piotroski_handles_corrupted_period_end_string(monkeypatch):
    """`fundamentals_period_end_y1` invalide (string non-ISO) → ne crash pas,
    et REFUSE les Y-1 (Phase 1 audit : on ne peut pas vérifier la
    publishability d'une date corrompue ⇒ fallback sur snapshot lookup,
    qui retourne None faute d'historique)."""
    # Patch _lookup_yoy_snapshot pour qu'il retourne None (pas d'historique).
    monkeypatch.setattr(_scoring, "_lookup_yoy_snapshot", lambda *a, **kw: None)
    row = _row_with_yoy_fields("not-a-date")
    score, diag = _scoring._piotroski_score_pillar(
        row, as_of=date(2025, 6, 1), publication_lag_days=90,
    )
    # Date corrompue → Y-1 refusé → seuls les 4 critères absolus évalués.
    assert diag["f_score_max"] == 4
