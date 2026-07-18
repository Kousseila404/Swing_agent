"""Tests — fraîcheur fondamentaux (roadmap Étape 0, 2026-07-18).

Bug corrigé : `fundamentals_period_end` n'était calculé que depuis les états
financiers **annuels** (`tk.balance_sheet`/`tk.financials`), jamais
trimestriels. Un ticker ayant publié un 10-Q récent après son dernier 10-K
était donc taggué stale à tort (cas observé en prod : AAPL à 287j).

`_prev_year_ratios` doit maintenant retenir la période la plus récente entre
annuel et trimestriel pour `fundamentals_period_end`, tout en gardant
`fundamentals_period_end_y1` strictement annuel (référentiel des champs
`*_prev_year` utilisés par le pilier Piotroski Y/Y).
"""
from __future__ import annotations

import pandas as pd
import pytest

from data_providers.yfinance_provider import (
    _latest_quarterly_period_end,
    _prev_year_ratios,
)

_ANNUAL_ROWS = {
    "Total Assets": [100.0, 90.0],
    "Total Debt": [10.0, 12.0],
    "Common Stock Equity": [50.0, 45.0],
    "Current Assets": [30.0, 28.0],
    "Current Liabilities": [15.0, 14.0],
    "Ordinary Shares Number": [1_000.0, 990.0],
}


def _annual_df(dates: list[str]) -> pd.DataFrame:
    """DataFrame annuel yfinance-like : colonnes = dates desc, lignes = postes."""
    cols = [pd.Timestamp(d) for d in dates]
    return pd.DataFrame(
        {col: {k: v[i] for k, v in _ANNUAL_ROWS.items()} for i, col in enumerate(cols)},
    )


def _quarterly_df(date: str) -> pd.DataFrame:
    return pd.DataFrame({pd.Timestamp(date): {"Total Assets": 999.0}})


class _FakeTicker:
    def __init__(self, balance_sheet, financials, quarterly_balance_sheet=None,
                 quarterly_financials=None, quarterly_raises=False):
        self.balance_sheet = balance_sheet
        self.financials = financials
        self._quarterly_balance_sheet = quarterly_balance_sheet
        self._quarterly_financials = quarterly_financials
        self._quarterly_raises = quarterly_raises

    @property
    def quarterly_balance_sheet(self):
        if self._quarterly_raises:
            raise RuntimeError("yfinance quarterly scrape KO")
        return self._quarterly_balance_sheet

    @property
    def quarterly_financials(self):
        if self._quarterly_raises:
            raise RuntimeError("yfinance quarterly scrape KO")
        return self._quarterly_financials


def test_period_end_prefers_more_recent_quarterly_over_annual():
    """10-Q plus récent que le dernier 10-K → fundamentals_period_end suit le 10-Q."""
    tk = _FakeTicker(
        balance_sheet=_annual_df(["2025-09-30", "2024-09-30"]),
        financials=_annual_df(["2025-09-30", "2024-09-30"]),
        quarterly_balance_sheet=_quarterly_df("2026-06-30"),
        quarterly_financials=_quarterly_df("2026-06-30"),
    )
    out = _prev_year_ratios(tk)
    assert out["fundamentals_period_end"] == "2026-06-30"
    # Y-1 reste ancré sur l'annuel, pas de glissement vers le trimestriel.
    assert out["fundamentals_period_end_y1"] == "2024-09-30"


def test_period_end_keeps_annual_when_quarterly_older():
    """Si le trimestriel dispo est plus vieux que l'annuel (edge case data), on
    garde l'annuel — on ne veut jamais régresser en fraîcheur."""
    tk = _FakeTicker(
        balance_sheet=_annual_df(["2025-09-30", "2024-09-30"]),
        financials=_annual_df(["2025-09-30", "2024-09-30"]),
        quarterly_balance_sheet=_quarterly_df("2025-03-31"),
        quarterly_financials=_quarterly_df("2025-03-31"),
    )
    out = _prev_year_ratios(tk)
    assert out["fundamentals_period_end"] == "2025-09-30"


def test_period_end_falls_back_to_annual_when_quarterly_missing():
    """Pas de donnée trimestrielle (DataFrame vide) → comportement identique
    à avant le fix, aucune régression."""
    tk = _FakeTicker(
        balance_sheet=_annual_df(["2025-09-30", "2024-09-30"]),
        financials=_annual_df(["2025-09-30", "2024-09-30"]),
        quarterly_balance_sheet=pd.DataFrame(),
        quarterly_financials=pd.DataFrame(),
    )
    out = _prev_year_ratios(tk)
    assert out["fundamentals_period_end"] == "2025-09-30"


def test_period_end_fail_open_when_quarterly_scrape_raises():
    """Le scraping trimestriel pète (ex: rate limit) → fail-open sur l'annuel,
    pas de propagation d'exception."""
    tk = _FakeTicker(
        balance_sheet=_annual_df(["2025-09-30", "2024-09-30"]),
        financials=_annual_df(["2025-09-30", "2024-09-30"]),
        quarterly_raises=True,
    )
    out = _prev_year_ratios(tk)
    assert out["fundamentals_period_end"] == "2025-09-30"


def test_latest_quarterly_period_end_returns_none_on_exception():
    tk = _FakeTicker(balance_sheet=None, financials=None, quarterly_raises=True)
    assert _latest_quarterly_period_end(tk) is None


def test_latest_quarterly_period_end_reads_col_zero():
    tk = _FakeTicker(
        balance_sheet=None, financials=None,
        quarterly_balance_sheet=_quarterly_df("2026-06-30"),
        quarterly_financials=pd.DataFrame(),
    )
    assert _latest_quarterly_period_end(tk) == "2026-06-30"


def test_prev_year_ratios_no_quarterly_attrs_still_works():
    """Fail-open même si l'objet tk n'expose pas du tout les attrs
    trimestriels (AttributeError plutôt qu'une valeur None)."""

    class _MinimalTicker:
        def __init__(self, bs, fin):
            self.balance_sheet = bs
            self.financials = fin

    tk = _MinimalTicker(
        bs=_annual_df(["2025-09-30", "2024-09-30"]),
        fin=_annual_df(["2025-09-30", "2024-09-30"]),
    )
    out = _prev_year_ratios(tk)
    assert out["fundamentals_period_end"] == "2025-09-30"
    assert out["fundamentals_period_end_y1"] == "2024-09-30"
