"""Tests FallbackFundamentalProvider — composition FMP primary + YF fallback.

Cas couverts :
  - Primary complet → fallback jamais appelé, source_provider inchangé.
  - Primary vide (critical fields None) → fallback appelé, merge correct.
  - Primary error → fallback appelé, error effacée si backfill réussit.
  - Fallback crash → primary retourné sans lever.
  - Merge : primary gagne, fallback comble uniquement les None.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from data_providers import FallbackFundamentalProvider, FinancialRatios
from data_providers.base import FundamentalProviderBase, ProviderUnavailable


class _StubProvider(FundamentalProviderBase):
    def __init__(self, name: str, ratios: FinancialRatios | Exception):
        self.name = name
        self._ratios = ratios
        self.calls = 0

    def get_financial_ratios(self, ticker: str) -> FinancialRatios:
        self.calls += 1
        if isinstance(self._ratios, Exception):
            raise self._ratios
        return self._ratios


def _full_ratios(ticker: str, source: str) -> FinancialRatios:
    """Ratios avec tous les critical fields remplis."""
    return FinancialRatios(
        ticker=ticker,
        name="Full Inc",
        sector="Tech",
        market_cap=1e9,
        current_price=100.0,
        return_on_equity=0.25,
        operating_margin=0.30,
        profit_margin=0.20,
        ev_to_ebitda=15.0,
        free_cash_flow=1e8,
        debt_to_equity=0.5,
        source_provider=source,
    )


def _empty_ratios(ticker: str, source: str, error: str | None = None) -> FinancialRatios:
    """Ratios avec identité partielle (profile-only) mais aucun critical field."""
    return FinancialRatios(
        ticker=ticker,
        name="Partial Inc",
        market_cap=5e8,
        current_price=50.0,
        source_provider=source,
        error=error,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Primary complet → fallback non appelé
# ═══════════════════════════════════════════════════════════════════════════

def test_fallback_not_called_when_primary_has_critical_fields():
    primary = _StubProvider("fmp", _full_ratios("AAPL", "fmp"))
    fallback = _StubProvider("yf", _full_ratios("AAPL", "yf"))
    composite = FallbackFundamentalProvider(primary, fallback)

    r = composite.get_financial_ratios("AAPL")

    assert primary.calls == 1
    assert fallback.calls == 0
    assert r.source_provider == "fmp"  # inchangé
    assert r.backfill_fields is None


# ═══════════════════════════════════════════════════════════════════════════
# Primary vide → fallback comble
# ═══════════════════════════════════════════════════════════════════════════

def test_fallback_backfills_when_all_critical_fields_none():
    primary  = _StubProvider("fmp", _empty_ratios("MSFT", "fmp"))
    fb_data  = _full_ratios("MSFT", "yf")
    fallback = _StubProvider("yf", fb_data)
    composite = FallbackFundamentalProvider(primary, fallback)

    r = composite.get_financial_ratios("MSFT")

    assert fallback.calls == 1
    # Primary gagne sur les champs qu'il avait remplis (name, market_cap, price).
    assert r.name == "Partial Inc"
    assert r.market_cap == 5e8
    assert r.current_price == 50.0
    # Fallback a comblé les critical fields.
    assert r.return_on_equity == 0.25
    assert r.ev_to_ebitda == 15.0
    assert r.debt_to_equity == 0.5
    # Traçabilité.
    assert r.source_provider == "fmp+yf"
    assert r.backfill_fields is not None
    assert "return_on_equity" in r.backfill_fields
    assert "debt_to_equity" in r.backfill_fields
    assert "name" not in r.backfill_fields  # primary l'avait déjà


def test_fallback_backfills_when_primary_has_error():
    """Primary renvoie un FinancialRatios avec .error set → fallback appelé."""
    primary  = _StubProvider("fmp", _empty_ratios("XYZ", "fmp", error="FMP empty"))
    fallback = _StubProvider("yf", _full_ratios("XYZ", "yf"))
    composite = FallbackFundamentalProvider(primary, fallback)

    r = composite.get_financial_ratios("XYZ")

    assert fallback.calls == 1
    # Error effacée parce que le fallback a rempli au moins un champ.
    assert r.error is None
    assert r.return_on_equity == 0.25


# ═══════════════════════════════════════════════════════════════════════════
# Robustesse : fallback crash
# ═══════════════════════════════════════════════════════════════════════════

def test_primary_returned_when_fallback_raises_provider_error():
    """Fallback Unavailable ne doit pas tuer la run — on rend le primary tel quel."""
    primary  = _StubProvider("fmp", _empty_ratios("ABC", "fmp"))
    fallback = _StubProvider("yf", ProviderUnavailable("yfinance breaker OPEN"))
    composite = FallbackFundamentalProvider(primary, fallback)

    r = composite.get_financial_ratios("ABC")

    assert r.source_provider == "fmp"
    assert r.return_on_equity is None  # pas backfill


def test_primary_returned_when_fallback_raises_generic_exception():
    """Exception non-ProviderError aussi swallow (safety net)."""
    primary  = _StubProvider("fmp", _empty_ratios("ABC", "fmp"))
    fallback = _StubProvider("yf", RuntimeError("boom"))
    composite = FallbackFundamentalProvider(primary, fallback)

    r = composite.get_financial_ratios("ABC")
    assert r.source_provider == "fmp"


# ═══════════════════════════════════════════════════════════════════════════
# Merge semantics : primary gagne par champ
# ═══════════════════════════════════════════════════════════════════════════

def test_primary_wins_per_field():
    """Un champ rempli par primary ne doit jamais être écrasé par le fallback,
    même si le fallback a une valeur différente."""
    primary = FinancialRatios(
        ticker="T", name="PrimaryName", market_cap=100, current_price=10,
        # Critical fields partiellement remplis : ROE présent, le reste absent.
        return_on_equity=0.50,
        source_provider="fmp",
    )
    # Primary a ROE → _needs_backfill = False si on considère qu'un seul
    # critical suffit. On le force vide pour tester le merge.
    primary_empty = _empty_ratios("T", "fmp")
    # Mais on remplit manuellement un champ pour vérifier qu'il n'est pas écrasé.
    primary_empty.name = "PrimaryName"
    primary_empty.market_cap = 100.0

    fallback = FinancialRatios(
        ticker="T",
        name="FallbackName",         # devrait être ignoré
        market_cap=999.0,            # devrait être ignoré
        current_price=50.0,          # même valeur que primary, no-op
        return_on_equity=0.25,       # devrait être utilisé (primary=None)
        operating_margin=0.10,
        source_provider="yf",
    )
    composite = FallbackFundamentalProvider(
        _StubProvider("fmp", primary_empty),
        _StubProvider("yf", fallback),
    )

    r = composite.get_financial_ratios("T")

    assert r.name == "PrimaryName"   # primary wins
    assert r.market_cap == 100.0
    assert r.return_on_equity == 0.25  # backfill
    assert r.operating_margin == 0.10
