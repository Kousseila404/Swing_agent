"""Tests unitaires pour modules.data_validation.sanitize_ratios.

Le module rejette les valeurs aberrantes retournées par yfinance/FMP avant
qu'elles ne polluent le cache 24h. Audit 2026-04-23.
"""
from __future__ import annotations

from data_providers.base import FinancialRatios
from modules.data_validation import sanitize_ratios


def _mk(ticker: str = "TEST", **kw) -> FinancialRatios:
    return FinancialRatios(ticker=ticker, **kw)


# ── Cas passants : aucun flag ──────────────────────────────────────────────

def test_sanitize_clean_payload_no_flags():
    r = _mk(
        return_on_equity=0.22,
        operating_margin=0.15,
        ev_to_ebitda=12.0,
        forward_pe=25.0,
        debt_to_equity=0.8,
        current_ratio=1.5,
        dividend_yield=0.03,
        market_cap=1e12,
        current_price=200.0,
        shares_outstanding=5e9,
    )
    out, flags = sanitize_ratios(r)
    assert flags == []
    assert out is r  # aucune copie si pas de flag → perf


def test_sanitize_all_none_no_flags():
    r = _mk()
    out, flags = sanitize_ratios(r)
    assert flags == []


# ── Rejet per field ────────────────────────────────────────────────────────

def test_sanitize_rejects_negative_ev_ebitda():
    r = _mk(ev_to_ebitda=-2179.0)  # cas CRWD observé
    out, flags = sanitize_ratios(r)
    assert out.ev_to_ebitda is None
    assert "out_of_bounds:ev_to_ebitda" in flags
    assert out.error is not None and "ev_to_ebitda" in out.error


def test_sanitize_rejects_negative_forward_pe():
    r = _mk(forward_pe=-1649.0)  # cas WBD observé
    out, flags = sanitize_ratios(r)
    assert out.forward_pe is None
    assert "out_of_bounds:forward_pe" in flags


def test_sanitize_rejects_absurd_roe():
    r = _mk(return_on_equity=15.0)  # 1500 % ROE = bug
    out, flags = sanitize_ratios(r)
    assert out.return_on_equity is None
    assert "out_of_bounds:return_on_equity" in flags


def test_sanitize_keeps_high_but_legit_roe():
    """REITs mortgage peuvent légitimement afficher 300 % ROE."""
    r = _mk(return_on_equity=3.0)
    out, flags = sanitize_ratios(r)
    assert out.return_on_equity == 3.0
    assert flags == []


def test_sanitize_rejects_absurd_dividend_yield():
    r = _mk(dividend_yield=1.85)  # yfinance confond yield et payout
    out, flags = sanitize_ratios(r)
    assert out.dividend_yield is None
    assert "out_of_bounds:dividend_yield" in flags


def test_sanitize_keeps_high_yield_reits():
    """Yields de 8–10 % sur REITs et MLPs sont légitimes."""
    r = _mk(dividend_yield=0.095)
    out, flags = sanitize_ratios(r)
    assert out.dividend_yield == 0.095
    assert flags == []


def test_sanitize_rejects_negative_market_cap():
    r = _mk(market_cap=-100.0)
    out, flags = sanitize_ratios(r)
    assert out.market_cap is None
    assert "out_of_bounds:market_cap" in flags


def test_sanitize_rejects_out_of_range_recommendation_mean():
    r = _mk(recommendation_mean=7.5)  # Yahoo convention : [1, 5]
    out, flags = sanitize_ratios(r)
    assert out.recommendation_mean is None
    assert "out_of_bounds:recommendation_mean" in flags


# ── Cross-check market cap ─────────────────────────────────────────────────

def test_sanitize_mcap_mismatch_flagged_not_cleared():
    """Si market_cap diverge de price × shares > 5 %, on flag mais on garde."""
    r = _mk(
        market_cap=1_000_000_000,      # $1B reporté
        current_price=100.0,
        shares_outstanding=20_000_000,  # price × shares = $2B → 50 % mismatch
    )
    out, flags = sanitize_ratios(r)
    assert "mcap_mismatch" in flags
    # On ne touche ni mcap ni price ni shares : trop d'edge cases sectoriels.
    assert out.market_cap == 1_000_000_000
    assert out.current_price == 100.0
    assert out.shares_outstanding == 20_000_000


def test_sanitize_mcap_within_tolerance_no_flag():
    """< 5 % de divergence = OK (stock split récent, buyback, rounding)."""
    r = _mk(
        market_cap=1_000_000_000,
        current_price=100.0,
        shares_outstanding=10_200_000,  # price × shares = $1.02B → 2 % mismatch
    )
    out, flags = sanitize_ratios(r)
    assert "mcap_mismatch" not in flags


def test_sanitize_mcap_check_needs_all_three_fields():
    """Si un champ manque, pas de cross-check possible → pas de flag."""
    r = _mk(market_cap=1e9, current_price=100.0, shares_outstanding=None)
    out, flags = sanitize_ratios(r)
    assert "mcap_mismatch" not in flags


# ── Immutabilité + préservation error d'origine ───────────────────────────

def test_sanitize_does_not_mutate_input():
    r = _mk(ev_to_ebitda=-100.0, forward_pe=-5.0, error="original_error")
    out, flags = sanitize_ratios(r)
    # L'input reste intact.
    assert r.ev_to_ebitda == -100.0
    assert r.forward_pe == -5.0
    assert r.error == "original_error"
    # Le résultat est nettoyé + error enrichi.
    assert out.ev_to_ebitda is None
    assert out.forward_pe is None
    assert out.error is not None
    assert "original_error" in out.error
    assert "dq_sanitize" in out.error


def test_sanitize_preserves_unaffected_fields():
    r = _mk(
        ev_to_ebitda=-50.0,              # rejeté
        return_on_equity=0.25,           # OK
        operating_margin=0.15,           # OK
        sector="Technology",
        name="Test Corp",
    )
    out, flags = sanitize_ratios(r)
    assert out.ev_to_ebitda is None
    assert out.return_on_equity == 0.25  # inchangé
    assert out.operating_margin == 0.15  # inchangé
    assert out.sector == "Technology"
    assert out.name == "Test Corp"


def test_sanitize_multiple_flags_all_recorded():
    r = _mk(
        ev_to_ebitda=-100.0,
        forward_pe=-5.0,
        dividend_yield=1.2,
        return_on_equity=20.0,
    )
    out, flags = sanitize_ratios(r)
    assert len(flags) == 4
    assert all(f.startswith("out_of_bounds:") for f in flags)
    # Tous les champs concernés sont None.
    assert out.ev_to_ebitda is None
    assert out.forward_pe is None
    assert out.dividend_yield is None
    assert out.return_on_equity is None
