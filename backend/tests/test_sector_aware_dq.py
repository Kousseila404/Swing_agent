"""Tests du sector-aware data quality — audit 2026-04-23.

Vérifie que les banques ne sont plus injustement pénalisées à 60 % DQ à
cause de ev_to_ebitda et current_ratio (non applicables structurellement).
"""
from __future__ import annotations

from modules.sector_metrics._scoring import (
    _MIN_DATA_QUALITY,
    _TITAN_SCORING_FIELDS,
    _TITAN_SCORING_FIELDS_FINANCIALS,
    _compute_data_quality,
    _fields_for_sector,
)

# ── _fields_for_sector ─────────────────────────────────────────────────────

def test_fields_for_unknown_sector_returns_default():
    assert _fields_for_sector(None) == _TITAN_SCORING_FIELDS
    assert _fields_for_sector("Technology") == _TITAN_SCORING_FIELDS
    assert _fields_for_sector("Healthcare") == _TITAN_SCORING_FIELDS


def test_fields_for_financial_services_excludes_inapplicables():
    fields = _fields_for_sector("Financial Services")
    # 4 fields exclus : bilan bancaire structurellement différent.
    assert "ev_to_ebitda" not in fields
    assert "current_ratio" not in fields
    assert "free_cash_flow" not in fields
    assert "debt_to_equity" not in fields
    # Les autres fields restent présents.
    assert "return_on_equity" in fields
    assert "operating_margin" in fields
    assert "forward_pe" in fields
    assert "recommendation_mean" in fields
    assert "price_target_mean" in fields
    assert "momentum_return_pct" in fields


def test_fields_normalization_accepts_variants():
    """Alias : 'Financial', 'Financials', 'Financial Services' → même set."""
    base = _fields_for_sector("Financial Services")
    assert _fields_for_sector("Financials") == base
    assert _fields_for_sector("Financial") == base


def test_financials_fields_count_is_smaller():
    """Sanity check : Financials a strictement moins de fields que default."""
    assert len(_TITAN_SCORING_FIELDS_FINANCIALS) == len(_TITAN_SCORING_FIELDS) - 4


# ── _compute_data_quality sector-aware ─────────────────────────────────────

def _mk_tech_ticker_full() -> dict:
    """Tech avec tous les fields DQ renseignés → DQ=1.0.
    Lot 12 : ajout de revenue_growth au champs requis."""
    return {
        "ticker": "AAPL", "sector": "Technology",
        "return_on_equity": 0.3, "operating_margin": 0.25,
        "ev_to_ebitda": 15.0, "forward_pe": 20.0,
        "free_cash_flow": 1e11, "debt_to_equity": 1.5,
        "current_ratio": 1.0, "recommendation_mean": 2.0,
        "price_target_mean": 220.0, "momentum_return_pct": 12.0,
        "revenue_growth": 0.12,
    }


def _mk_bank_ticker() -> dict:
    """Banque type : manque ev_to_ebitda, current_ratio, free_cash_flow,
    debt_to_equity — tous non applicables au bilan bancaire (réalité yfinance)."""
    return {
        "ticker": "JPM", "sector": "Financial Services",
        "return_on_equity": 0.17, "operating_margin": 0.30,
        "ev_to_ebitda": None,              # non applicable
        "forward_pe": 12.0,
        "free_cash_flow": None,            # non applicable banking
        "debt_to_equity": None,            # structurellement 8-12× faussé
        "current_ratio": None,             # non applicable
        "recommendation_mean": 2.3,
        "price_target_mean": 210.0, "momentum_return_pct": 8.0,
        "revenue_growth": 0.08,
    }


def test_dq_technology_full_is_100pct():
    dq = _compute_data_quality(_mk_tech_ticker_full())
    assert dq == 1.0


def test_dq_bank_without_inapplicable_fields_is_100pct():
    """Avant fix : 6/10 = 60 %. Après fix : 6/6 = 100 %."""
    dq = _compute_data_quality(_mk_bank_ticker())
    assert dq == 1.0


def test_dq_bank_with_missing_applicable_field_penalized():
    """Si la banque manque un field APPLICABLE (ex: ROE), elle est pénalisée."""
    bank = _mk_bank_ticker()
    bank["return_on_equity"] = None  # field applicable manquant
    dq = _compute_data_quality(bank)
    # Lot 12 : bank applicable fields = 7 (inc. revenue_growth), 6 remplis → 6/7 ≈ 85.7 %
    assert abs(dq - (6/7)) < 0.01


def test_dq_bank_vs_tech_equal_applicable_density_equal_dq():
    """Invariance : une banque et un tech avec le même taux de remplissage sur
    leurs champs applicables doivent avoir le même DQ score."""
    bank = _mk_bank_ticker()  # 6/6 = 100 %
    tech = _mk_tech_ticker_full()  # 10/10 = 100 %
    assert _compute_data_quality(bank) == _compute_data_quality(tech) == 1.0


def test_dq_tech_missing_ev_ebitda_penalized_unlike_bank():
    """Un Tech sans ev_to_ebitda = pénalisé. Une banque sans ev_to_ebitda = non.
    C'est exactement le point de la sector-aware logic."""
    tech_sparse = _mk_tech_ticker_full()
    tech_sparse["ev_to_ebitda"] = None
    tech_dq = _compute_data_quality(tech_sparse)
    bank_dq = _compute_data_quality(_mk_bank_ticker())
    assert tech_dq < bank_dq
    # Lot 12 : Tech applicable = 11 fields, 10 remplis → 10/11 ≈ 90.9 %
    assert abs(tech_dq - (10/11)) < 0.01


def test_dq_unknown_sector_falls_back_to_default_fields():
    """Un ticker sans secteur est scoré sur le jeu complet."""
    t = _mk_bank_ticker()
    t["sector"] = None
    # Lot 12 : default 11 fields, bank ticker a 7 remplis → 7/11 ≈ 63.6 %
    dq = _compute_data_quality(t)
    assert abs(dq - (7/11)) < 0.01


def test_dq_threshold_bumped_to_0_70():
    """Le seuil d'exclusion est à 0.70 après l'audit."""
    assert _MIN_DATA_QUALITY == 0.70
