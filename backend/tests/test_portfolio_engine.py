"""
Tests du PortfolioManager : filtre bullish, top N, risk parity, imputation σ,
fallback equal-weight, sizing $ + shares.
"""
from __future__ import annotations

from datetime import UTC

import pytest

from modules.portfolio_engine import (
    MIN_VALID_VOLS_FOR_RISK_PARITY,
    PortfolioManager,
)


def _mk(score, mom, vol, price, sector="Tech") -> dict:
    return {
        "titan_composite_score":   score,
        "momentum_return_pct":     mom,
        "momentum_volatility_pct": vol,
        "current_price":           price,
        "sector":                  sector,
        "name":                    "Fake Inc",
    }


# ═══════════════════════════════════════════════════════════════════════════
# Filtres
# ═══════════════════════════════════════════════════════════════════════════

def test_bullish_filter_drops_severely_negative_momentum():
    """Lot 5+ : seuil softened à -10% (vs 0 strict avant). Le momentum est
    désormais dans le composite TITAN, le filtre garde les "minor negative"
    mais bloque toujours les falling knives extrêmes (mom < -10%)."""
    scored = {
        "UP":     _mk(80, 12.0, 20.0, 100.0),
        "FALL":   _mk(85, -25.0, 18.0, 100.0),  # falling knife → exclu (-25 < -10)
        "MINOR":  _mk(70, -3.0, 20.0, 100.0),   # mom -3% → kept (composite gère)
    }
    pm = PortfolioManager(scored)
    plan = pm.calculate_allocations(list(scored.keys()), 10_000).to_dict()
    assert "UP" in plan["allocations"]
    assert "FALL" not in plan["allocations"]
    assert "MINOR" in plan["allocations"]
    assert plan["n_bullish"] == 2


def test_top_n_concentration_cap():
    """Avec 10 bullish + max_holdings=3, seuls les 3 meilleurs scores passent."""
    scored = {
        f"T{i}": _mk(50 + i, 10.0, 15.0 + i * 2, 100.0)
        for i in range(10)
    }
    pm = PortfolioManager(scored)
    plan = pm.calculate_allocations(list(scored.keys()), 10_000, max_holdings=3).to_dict()
    assert plan["n_kept"] == 3
    # Les 3 meilleurs scores : T9 (59), T8 (58), T7 (57)
    assert set(plan["allocations"].keys()) == {"T9", "T8", "T7"}


# ═══════════════════════════════════════════════════════════════════════════
# Risk parity
# ═══════════════════════════════════════════════════════════════════════════

def test_risk_parity_inverse_vol_weighting():
    """W_i = (1/σ_i) / Σ(1/σ_j) — le moins volatil pèse le plus."""
    # σ_A = 10, σ_B = 20, σ_C = 40.  Inverse : 0.1, 0.05, 0.025 = 0.175
    # W_A = 0.1/0.175 = 57.14 %,  W_B = 28.57 %,  W_C = 14.29 %
    scored = {
        "A": _mk(80, 5.0, 10.0, 100.0),
        "B": _mk(80, 5.0, 20.0, 100.0),
        "C": _mk(80, 5.0, 40.0, 100.0),
    }
    pm = PortfolioManager(scored)
    plan = pm.calculate_allocations(["A", "B", "C"], 100_000).to_dict()

    alloc = plan["allocations"]
    assert alloc["A"]["weight_pct"] == pytest.approx(57.1429, abs=0.01)
    assert alloc["B"]["weight_pct"] == pytest.approx(28.5714, abs=0.01)
    assert alloc["C"]["weight_pct"] == pytest.approx(14.2857, abs=0.01)
    # Le moins volatil pèse strictement plus que le plus volatil.
    assert alloc["A"]["weight_pct"] > alloc["C"]["weight_pct"]
    assert plan["equal_weight_fallback"] is False


def test_equal_weights_with_identical_vols():
    """σ identiques → poids strictement égaux."""
    scored = {t: _mk(80, 5.0, 20.0, 50.0) for t in ["A", "B", "C", "D"]}
    pm = PortfolioManager(scored)
    plan = pm.calculate_allocations(list(scored.keys()), 40_000).to_dict()
    weights = {t: a["weight_pct"] for t, a in plan["allocations"].items()}
    for w in weights.values():
        assert w == pytest.approx(25.0, abs=0.001)


# ═══════════════════════════════════════════════════════════════════════════
# σ manquante / imputation
# ═══════════════════════════════════════════════════════════════════════════

def test_missing_vol_imputed_with_median():
    """σ manquante → médiane imputée, flag imputed_vol=True sur le ticker."""
    scored = {
        "A":    _mk(80, 5.0, 10.0, 100.0),
        "B":    _mk(80, 5.0, 20.0, 100.0),
        "C":    _mk(80, 5.0, 30.0, 100.0),
        "HOLE": _mk(80, 5.0, None, 100.0),  # pas assez d'historique
    }
    pm = PortfolioManager(scored)
    plan = pm.calculate_allocations(list(scored.keys()), 100_000).to_dict()
    alloc = plan["allocations"]

    assert "HOLE" in alloc
    assert alloc["HOLE"]["imputed_vol"] is True
    assert alloc["A"]["imputed_vol"] is False
    # σ imputée = médiane(10, 20, 30) = 20 → même poids que B.
    assert alloc["HOLE"]["weight_pct"] == pytest.approx(alloc["B"]["weight_pct"], abs=0.001)
    assert plan["diagnostics"]["n_imputed_vols"] == 1
    assert plan["equal_weight_fallback"] is False


def test_fallback_equal_weight_when_too_few_vols():
    """Moins de 3 σ valides → bascule en equal weight pour TOUT le portefeuille."""
    scored = {
        "A":  _mk(80, 5.0, 10.0, 100.0),
        "B":  _mk(80, 5.0, None, 100.0),
        "C":  _mk(80, 5.0, None, 100.0),
        "D":  _mk(80, 5.0, None, 100.0),
    }
    pm = PortfolioManager(scored)
    plan = pm.calculate_allocations(list(scored.keys()), 10_000).to_dict()

    assert plan["equal_weight_fallback"] is True
    for a in plan["allocations"].values():
        assert a["weight_pct"] == pytest.approx(25.0, abs=0.001)
    # Sanity sur la constante.
    assert MIN_VALID_VOLS_FOR_RISK_PARITY == 3


def test_empty_result_when_no_bullish():
    """Aucun bullish (falling knives < -10%) → allocations vides, cash = total."""
    scored = {
        "A": _mk(80, -25.0, 10.0, 100.0),
        "B": _mk(80, -30.0, 20.0, 100.0),
    }
    pm = PortfolioManager(scored)
    plan = pm.calculate_allocations(list(scored.keys()), 10_000).to_dict()
    assert plan["allocations"] == {}
    assert plan["cash_remaining_usd"] == 10_000
    assert plan["n_kept"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Sizing $ + shares
# ═══════════════════════════════════════════════════════════════════════════

def test_shares_are_integer_by_default():
    scored = {"A": _mk(80, 5.0, 20.0, 137.50)}  # prix non rond
    # On a besoin de 3 vols pour activer risk parity ; on ajoute 2 fakes bullish
    # mais hors top-1.
    scored["B"] = _mk(70, 5.0, 20.0, 100.0)
    scored["C"] = _mk(60, 5.0, 20.0, 100.0)
    pm = PortfolioManager(scored)
    plan = pm.calculate_allocations(["A", "B", "C"], 10_000, max_holdings=1).to_dict()
    alloc = plan["allocations"]["A"]
    # Budget = 10_000, prix = 137.5, shares entières max = 72, notional = 9900.
    assert alloc["shares"] == 72.0
    assert alloc["amount_usd"] == pytest.approx(9900.0, abs=0.01)
    # Target (avant floor) était 10_000 — résidu 100.
    assert alloc["target_usd"] == pytest.approx(10_000.0, abs=0.01)
    assert alloc["cash_residual_usd"] == pytest.approx(100.0, abs=0.01)


def test_fractional_shares_flag():
    scored = {
        "A": _mk(80, 5.0, 20.0, 137.50),
        "B": _mk(70, 5.0, 20.0, 100.0),
        "C": _mk(60, 5.0, 20.0, 100.0),
    }
    pm = PortfolioManager(scored)
    plan = pm.calculate_allocations(
        ["A", "B", "C"], 10_000, max_holdings=1,
        allow_fractional_shares=True,
    ).to_dict()
    alloc = plan["allocations"]["A"]
    # Shares fractionnelles : 10_000 / 137.5 = 72.7273
    assert alloc["shares"] == pytest.approx(72.7273, abs=0.0001)
    assert alloc["cash_residual_usd"] == pytest.approx(0.0, abs=0.01)


def test_dropped_no_price_is_diagnosed():
    """Un ticker top-N mais sans current_price est droppé et listé en diag."""
    scored = {
        "PRICED_A": _mk(80, 5.0, 20.0, 100.0),
        "PRICED_B": _mk(79, 5.0, 20.0, 100.0),
        "PRICED_C": _mk(78, 5.0, 20.0, 100.0),
        "NO_PRICE": _mk(99, 5.0, 20.0, None),  # meilleur score mais pas de prix
    }
    pm = PortfolioManager(scored)
    plan = pm.calculate_allocations(list(scored.keys()), 10_000, max_holdings=4).to_dict()
    assert "NO_PRICE" not in plan["allocations"]
    assert "NO_PRICE" in plan["diagnostics"]["dropped_no_price"]


def test_invalid_inputs_raise():
    pm = PortfolioManager({"A": _mk(80, 5.0, 20.0, 100.0)})
    with pytest.raises(ValueError):
        pm.calculate_allocations(["A"], 0)
    with pytest.raises(ValueError):
        pm.calculate_allocations(["A"], 10_000, max_holdings=0)


# ═══════════════════════════════════════════════════════════════════════════
# Sprint 2 — Sector cap 30% (Fix #6)
# ═══════════════════════════════════════════════════════════════════════════

def test_sector_cap_applied_when_one_sector_dominates():
    """5 Tech avec risk parity poussant Tech à ~80% → cap 30% redistribue."""
    # Vols : 4 Tech avec σ basse (poids ~élevé) + 1 Energy avec σ haute.
    scored = {
        "T1": _mk(80, 10.0, 10.0, 100.0, sector="Technology"),
        "T2": _mk(80, 10.0, 10.0, 100.0, sector="Technology"),
        "T3": _mk(80, 10.0, 10.0, 100.0, sector="Technology"),
        "T4": _mk(80, 10.0, 10.0, 100.0, sector="Technology"),
        "E1": _mk(80, 10.0, 40.0, 100.0, sector="Energy"),
        "F1": _mk(80, 10.0, 20.0, 100.0, sector="Financials"),
        "H1": _mk(80, 10.0, 20.0, 100.0, sector="Healthcare"),
    }
    pm = PortfolioManager(scored, sector_cap=0.30)
    plan = pm.calculate_allocations(list(scored.keys()), 100_000, max_holdings=7).to_dict()
    alloc = plan["allocations"]

    # Tech total ≤ 30% (+ epsilon numérique)
    tech_total = sum(a["weight_pct"] for a in alloc.values() if a["sector"] == "Technology")
    assert tech_total == pytest.approx(30.0, abs=0.5), (
        f"Tech devrait être cappé à ~30%, vu {tech_total:.2f}%"
    )

    # Diag expose le secteur cappé
    assert plan["diagnostics"]["sector_cap"]["cap_applied"] is True
    assert "Technology" in plan["diagnostics"]["sector_cap"]["capped_sectors"]


def test_sector_cap_noop_when_sectors_diverse():
    """Portefeuille déjà équilibré → aucun cap déclenché."""
    scored = {
        "T1": _mk(80, 10.0, 20.0, 100.0, sector="Technology"),
        "E1": _mk(80, 10.0, 20.0, 100.0, sector="Energy"),
        "F1": _mk(80, 10.0, 20.0, 100.0, sector="Financials"),
        "H1": _mk(80, 10.0, 20.0, 100.0, sector="Healthcare"),
    }
    pm = PortfolioManager(scored, sector_cap=0.30)
    plan = pm.calculate_allocations(list(scored.keys()), 100_000).to_dict()
    # Chaque secteur = 25%, tous sous 30% → rien cappé
    assert plan["diagnostics"]["sector_cap"]["cap_applied"] is False


def test_sector_cap_disabled_when_none():
    """sector_cap=None désactive la contrainte (risk parity pure)."""
    scored = {
        "T1": _mk(80, 10.0, 10.0, 100.0, sector="Technology"),
        "T2": _mk(80, 10.0, 10.0, 100.0, sector="Technology"),
        "T3": _mk(80, 10.0, 10.0, 100.0, sector="Technology"),
        "E1": _mk(80, 10.0, 40.0, 100.0, sector="Energy"),
    }
    pm = PortfolioManager(scored, sector_cap=None)
    plan = pm.calculate_allocations(list(scored.keys()), 100_000).to_dict()
    # Tech domine sans contrainte
    tech_total = sum(
        a["weight_pct"] for a in plan["allocations"].values()
        if a["sector"] == "Technology"
    )
    assert tech_total > 60.0
    assert plan["diagnostics"]["sector_cap"]["cap_applied"] is False


def test_sector_cap_infeasible_when_too_few_sectors():
    """cap 30% avec seulement 2 secteurs (min 50%) → no-op, flag infeasible."""
    scored = {
        "T1": _mk(80, 10.0, 10.0, 100.0, sector="Technology"),
        "T2": _mk(80, 10.0, 10.0, 100.0, sector="Technology"),
        "E1": _mk(80, 10.0, 20.0, 100.0, sector="Energy"),
    }
    pm = PortfolioManager(scored, sector_cap=0.30)
    plan = pm.calculate_allocations(list(scored.keys()), 100_000).to_dict()
    assert plan["diagnostics"]["sector_cap"]["cap_applied"] is False
    assert plan["diagnostics"]["sector_cap"]["reason"] == "infeasible"


# ═══════════════════════════════════════════════════════════════════════════
# Sprint 2 — Imputation vol sectorielle (Fix #8)
# ═══════════════════════════════════════════════════════════════════════════

def test_vol_imputed_from_sector_median_not_portfolio():
    """
    Un ticker Tech sans σ reçoit la médiane Tech (40), pas la médiane cross
    (qui serait ~25 avec des Energy à 10, 20). La σ sectorielle est plus
    fidèle à la classe de risque du ticker.
    """
    scored = {
        # Tech : 3 tickers à σ élevée (40, 45, 50) → médiane Tech ≈ 45
        "TECH1": _mk(80, 10.0, 40.0, 100.0, sector="Technology"),
        "TECH2": _mk(80, 10.0, 45.0, 100.0, sector="Technology"),
        "TECH3": _mk(80, 10.0, 50.0, 100.0, sector="Technology"),
        # Energy : 3 tickers à σ basse → médiane Energy ≈ 15
        "ENG1":  _mk(80, 10.0, 10.0, 100.0, sector="Energy"),
        "ENG2":  _mk(80, 10.0, 15.0, 100.0, sector="Energy"),
        "ENG3":  _mk(80, 10.0, 20.0, 100.0, sector="Energy"),
        # Tech sans σ → devrait recevoir la médiane Tech (~45), pas la médiane
        # portfolio (~27.5)
        "HOLE":  _mk(80, 10.0, None, 100.0, sector="Technology"),
    }
    # sector_cap=None pour isoler l'effet imputation
    pm = PortfolioManager(scored, sector_cap=None)
    plan = pm.calculate_allocations(list(scored.keys()), 100_000).to_dict()
    alloc = plan["allocations"]

    assert alloc["HOLE"]["imputed_vol"] is True
    assert alloc["HOLE"]["imputation_method"] == "sector_median_impute"
    # σ imputée = 45 (médiane Tech) → même poids que TECH2
    assert alloc["HOLE"]["weight_pct"] == pytest.approx(
        alloc["TECH2"]["weight_pct"], abs=0.01,
    )
    assert plan["diagnostics"]["n_sector_imputed"] >= 1


def test_vol_imputation_fallback_portfolio_when_sector_too_sparse():
    """
    Secteur avec < MIN_VALID_VOLS_PER_SECTOR (3) échantillons → fallback
    médiane cross-portfolio.
    """
    scored = {
        "TECH1": _mk(80, 10.0, 40.0, 100.0, sector="Technology"),
        "TECH2": _mk(80, 10.0, 45.0, 100.0, sector="Technology"),
        "TECH3": _mk(80, 10.0, 50.0, 100.0, sector="Technology"),
        # Healthcare : un seul sample → insuffisant pour médiane sectorielle
        "HEALTH1": _mk(80, 10.0, 25.0, 100.0, sector="Healthcare"),
        # Healthcare sans σ → fallback cross-portfolio
        "HEALTH_HOLE": _mk(80, 10.0, None, 100.0, sector="Healthcare"),
    }
    pm = PortfolioManager(scored, sector_cap=None)
    plan = pm.calculate_allocations(list(scored.keys()), 100_000).to_dict()
    alloc = plan["allocations"]
    assert alloc["HEALTH_HOLE"]["imputation_method"] == "portfolio_median_impute"


# ═══════════════════════════════════════════════════════════════════════════
# Sprint 2 — Live momentum refresh (Fix #4 & #5)
# ═══════════════════════════════════════════════════════════════════════════

def test_live_momentum_refresh_flips_bullish_to_bear():
    """
    Ticker avec momentum stale positif (→ passerait bullish) mais dont la série
    live montre un crash → filtré bear après refresh.
    """
    import pandas as pd

    class _FakeProvider:
        name = "yfinance"

        def get_daily_history_batch(self, tickers, days):
            # "GOOD" reste haussier : 100 → 120 (+20 %)
            # "CRASH" stale dit +15 mais live montre 100 → 80 (-20 %)
            good_series = pd.Series(
                [100 + i * 0.1 for i in range(200)],
                index=pd.date_range("2025-01-01", periods=200, freq="D"),
            )
            crash_series = pd.Series(
                [100 - i * 0.1 for i in range(200)],
                index=pd.date_range("2025-01-01", periods=200, freq="D"),
            )
            mapping = {"GOOD": good_series, "CRASH": crash_series}
            return {t: mapping.get(t) for t in tickers}

        # Contrat complet MarketDataProviderBase (non-utilisés ici)
        def get_daily_history(self, ticker, days): return None
        def get_latest_prices(self, tickers): return {}

    scored = {
        "GOOD":  _mk(85, 15.0, 20.0, 100.0, sector="Technology"),
        "CRASH": _mk(88, 15.0, 20.0, 100.0, sector="Energy"),  # stale +15 %
        "FILLER1": _mk(70, 10.0, 20.0, 100.0, sector="Healthcare"),
        "FILLER2": _mk(70, 10.0, 20.0, 100.0, sector="Financials"),
    }
    pm = PortfolioManager(
        scored, momentum_provider=_FakeProvider(), sector_cap=None,
    )
    plan = pm.calculate_allocations(list(scored.keys()), 100_000).to_dict()

    # CRASH a été écarté car son momentum live est négatif
    assert "CRASH" not in plan["allocations"]
    assert "GOOD" in plan["allocations"]
    # Diagnostics confirment le refresh a eu lieu
    assert plan["diagnostics"]["momentum_refreshed"] is True
    assert plan["diagnostics"]["n_momentum_live"] >= 2


def test_no_momentum_provider_no_refresh():
    """Sans momentum_provider, le pipeline reste sur les stats stored."""
    scored = {
        "A": _mk(80, 5.0, 10.0, 100.0, sector="Technology"),
        "B": _mk(80, 5.0, 20.0, 100.0, sector="Energy"),
        "C": _mk(80, 5.0, 30.0, 100.0, sector="Healthcare"),
    }
    pm = PortfolioManager(scored)  # no momentum_provider
    plan = pm.calculate_allocations(list(scored.keys()), 100_000).to_dict()
    assert plan["diagnostics"]["momentum_refreshed"] is False
    # Aucun ticker n'a la flag momentum_live
    for a in plan["allocations"].values():
        assert a["momentum_live"] is False


# ═══════════════════════════════════════════════════════════════════════════
# Fix #12 — Régime macro gating
# ═══════════════════════════════════════════════════════════════════════════

def test_crash_panic_blocks_all_entries():
    """regime_multiplier=0.0 → aucune allocation, tout le capital en cash."""
    scored = {
        "A": _mk(80, 10.0, 15.0, 100.0, sector="Technology"),
        "B": _mk(75, 8.0,  18.0, 80.0,  sector="Energy"),
    }
    pm = PortfolioManager(scored)
    plan = pm.calculate_allocations(
        list(scored.keys()), 100_000,
        regime_multiplier=0.0, regime_label="CRASH_PANIC",
    ).to_dict()
    assert plan["allocations"] == {}
    assert plan["invested_usd"] == 0.0
    assert plan["cash_remaining_usd"] == 100_000
    assert plan["diagnostics"]["reason"] == "macro_regime_blocks_new_entries"
    assert plan["diagnostics"]["regime_label"] == "CRASH_PANIC"


def test_bear_market_scales_down_sizing():
    """regime_multiplier=0.5 → chaque ticker reçoit 50 % de son amount normal."""
    scored = {
        "A": _mk(80, 10.0, 15.0, 100.0, sector="Technology"),
        "B": _mk(75, 8.0,  15.0, 100.0, sector="Energy"),
    }
    pm = PortfolioManager(scored, sector_cap=None)  # pas de cap pour tester pur scaling
    plan_bull = pm.calculate_allocations(
        list(scored.keys()), 100_000, allow_fractional_shares=True,
    ).to_dict()
    plan_bear = pm.calculate_allocations(
        list(scored.keys()), 100_000, allow_fractional_shares=True,
        regime_multiplier=0.5, regime_label="BEAR_MARKET",
    ).to_dict()

    # Les poids relatifs sont identiques — seul le scaling capital change.
    for t in scored:
        assert plan_bear["allocations"][t]["weight_pct"] == pytest.approx(
            plan_bull["allocations"][t]["weight_pct"]
        )
        # L'amount déployé en BEAR ≈ 50 % de l'amount BULL.
        assert plan_bear["allocations"][t]["target_usd"] == pytest.approx(
            plan_bull["allocations"][t]["target_usd"] * 0.5, rel=1e-6,
        )

    # Cash ~50k restant (vs ~0 en BULL).
    assert plan_bear["cash_remaining_usd"] == pytest.approx(50_000, abs=1.0)
    assert plan_bear["diagnostics"]["regime_multiplier"] == 0.5
    assert plan_bear["diagnostics"]["effective_capital"] == 50_000.0


def test_regime_multiplier_out_of_range_raises():
    scored = {"A": _mk(80, 10.0, 15.0, 100.0, sector="Tech")}
    pm = PortfolioManager(scored)
    with pytest.raises(ValueError, match="regime_multiplier"):
        pm.calculate_allocations(["A"], 10_000, regime_multiplier=1.5)
    with pytest.raises(ValueError, match="regime_multiplier"):
        pm.calculate_allocations(["A"], 10_000, regime_multiplier=-0.1)


# ═══════════════════════════════════════════════════════════════════════════
# Fix #11 — Staleness fondamentaux par ticker
# ═══════════════════════════════════════════════════════════════════════════

def test_fundamentals_staleness_warning_and_severe():
    """fetched_at > 14j = stale ; > 30j = severe. Fresh = ni l'un ni l'autre."""
    import time as _time
    from datetime import datetime, timedelta, timezone

    now = _time.time()
    fresh_ts = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale_ts = (datetime.now(UTC) - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    severe_ts = (datetime.now(UTC) - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _ = now

    scored = {
        "FRESH":  {**_mk(80, 10.0, 15.0, 100.0, sector="Technology"), "fetched_at": fresh_ts},
        "STALE":  {**_mk(75, 10.0, 15.0, 100.0, sector="Energy"),     "fetched_at": stale_ts},
        "SEVERE": {**_mk(70, 10.0, 15.0, 100.0, sector="Healthcare"), "fetched_at": severe_ts},
    }
    pm = PortfolioManager(scored, sector_cap=None)
    plan = pm.calculate_allocations(list(scored.keys()), 100_000).to_dict()

    assert plan["allocations"]["FRESH"]["fundamentals_stale"] is False
    assert plan["allocations"]["FRESH"]["fundamentals_severe"] is False
    assert plan["allocations"]["STALE"]["fundamentals_stale"] is True
    assert plan["allocations"]["STALE"]["fundamentals_severe"] is False
    assert plan["allocations"]["SEVERE"]["fundamentals_stale"] is True
    assert plan["allocations"]["SEVERE"]["fundamentals_severe"] is True

    # Diagnostics agrégés
    diag = plan["diagnostics"]
    assert diag["n_stale_fundamentals"] == 2
    assert diag["n_severe_fundamentals"] == 1
    assert diag["oldest_fundamental_days"] > 44.0


def test_fundamentals_missing_fetched_at_is_unknown():
    """Absence de fetched_at → âge None, jamais flagé stale (comportement safe)."""
    scored = {
        "NOFIELD": _mk(80, 10.0, 15.0, 100.0, sector="Technology"),
    }
    pm = PortfolioManager(scored)
    plan = pm.calculate_allocations(["NOFIELD"], 10_000).to_dict()
    alloc = plan["allocations"]["NOFIELD"]
    assert alloc["fundamentals_age_days"] is None
    assert alloc["fundamentals_stale"] is False
    assert alloc["fundamentals_severe"] is False


def test_age_days_from_iso_parses_z_and_offset():
    """Le helper doit gérer les deux suffixes ISO (Z et +00:00)."""
    from datetime import datetime, timedelta, timezone

    from modules.portfolio_engine import _age_days_from_iso

    five_days_ago = datetime.now(UTC) - timedelta(days=5)
    iso_z = five_days_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
    iso_off = five_days_ago.isoformat()

    age_z = _age_days_from_iso(iso_z)
    age_off = _age_days_from_iso(iso_off)
    assert age_z is not None and 4.5 < age_z < 5.5
    assert age_off is not None and 4.5 < age_off < 5.5

    # Input invalide → None (pas de crash)
    assert _age_days_from_iso(None) is None
    assert _age_days_from_iso("not-a-date") is None
    assert _age_days_from_iso(42) is None


# ═══════════════════════════════════════════════════════════════════════════
# ADTV cap (liquidity gate)
# ═══════════════════════════════════════════════════════════════════════════

def test_adtv_cap_clamps_position_to_max_pct_of_dollar_volume():
    """Position notional > max_pct_of_adtv × (price × avg_volume_3m) →
    plafonnée. Avec cap=0.5 % et ADTV=100 K shares × $100 = $10 M, le cap
    ressort à $50 K. Une cible $80 K doit être ramenée à $50 K (500 shares)."""
    scored = {
        # Cible théorique: 100% × 80_000 = 80_000$. ADTV = 10 M$. Cap = 50 K$ = 500 shs.
        "ILLIQ": {**_mk(80, 5.0, 20.0, 100.0), "avg_volume_3m": 100_000.0},
    }
    pm = PortfolioManager(scored, max_pct_of_adtv=0.005)
    plan = pm.calculate_allocations(["ILLIQ"], 80_000).to_dict()

    alloc = plan["allocations"]["ILLIQ"]
    # Sans cap : 800 shares × $100 = 80 K. Avec cap : 500 shares × $100 = 50 K.
    assert alloc["shares"] == 500
    assert alloc["amount_usd"] == pytest.approx(50_000.0, abs=1.0)

    diag = plan["diagnostics"]["adtv_cap"]
    assert diag["max_pct_of_adtv"] == 0.005
    assert diag["n_capped"] == 1
    assert diag["capped"][0]["ticker"] == "ILLIQ"
    assert diag["capped"][0]["original_shares"] == 800.0


def test_adtv_cap_disabled_with_none_does_not_clamp():
    """max_pct_of_adtv=None → pas de cap appliqué, sizing nominal."""
    scored = {
        "ILLIQ": {**_mk(80, 5.0, 20.0, 100.0), "avg_volume_3m": 100_000.0},
    }
    pm = PortfolioManager(scored, max_pct_of_adtv=None)
    plan = pm.calculate_allocations(["ILLIQ"], 80_000).to_dict()

    assert plan["allocations"]["ILLIQ"]["shares"] == 800
    assert plan["diagnostics"]["adtv_cap"]["n_capped"] == 0


def test_adtv_cap_fail_open_when_avg_volume_missing():
    """Si avg_volume_3m absent du scored row, on n'applique pas le cap
    (fail-open : universe a déjà filtré sur market_cap > 10 B)."""
    scored = {
        "NOVOL": _mk(80, 5.0, 20.0, 100.0),  # pas d'avg_volume_3m
    }
    pm = PortfolioManager(scored, max_pct_of_adtv=0.005)
    plan = pm.calculate_allocations(["NOVOL"], 80_000).to_dict()

    # Sizing nominal : 800 shares.
    assert plan["allocations"]["NOVOL"]["shares"] == 800
    assert plan["diagnostics"]["adtv_cap"]["n_capped"] == 0


def test_adtv_cap_does_not_clamp_when_position_below_threshold():
    """Position < cap → aucun changement, n_capped=0."""
    scored = {
        # Cible: 5 K$. ADTV = 10 M$. Cap = 50 K$. Position 5K << 50K → pas de cap.
        "LIQ": {**_mk(80, 5.0, 20.0, 100.0), "avg_volume_3m": 100_000.0},
    }
    pm = PortfolioManager(scored, max_pct_of_adtv=0.005)
    plan = pm.calculate_allocations(["LIQ"], 5_000).to_dict()

    assert plan["allocations"]["LIQ"]["shares"] == 50
    assert plan["diagnostics"]["adtv_cap"]["n_capped"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# HRP — Hierarchical Risk Parity (López de Prado 2016)
# ═══════════════════════════════════════════════════════════════════════════

class _FakeMarketProvider:
    """Provider de tests : génère des séries journalières synthétiques.

    Le constructeur accepte un mapping {ticker: factor_seed} où des tickers
    qui partagent la même seed reçoivent des séries fortement corrélées.
    """
    name = "fake-hrp"

    def __init__(self, seed_map: dict[str, int]):
        import numpy as np
        self._seed_map = seed_map
        self._np = np

    def get_daily_history_batch(self, tickers, days):
        import pandas as pd
        np = self._np
        dates = pd.date_range("2025-01-01", periods=days)
        out = {}
        # Construit un facteur de marché par seed unique.
        unique_seeds = sorted(set(self._seed_map.values()))
        factors = {}
        for s in unique_seeds:
            rng = np.random.default_rng(s)
            factors[s] = np.cumprod(1 + rng.normal(0.001, 0.02, days))
        for t in tickers:
            seed = self._seed_map.get(t)
            if seed is None:
                continue
            rng = np.random.default_rng(seed * 1000 + hash(t) % 1000)
            idiosyncratic = np.cumprod(1 + rng.normal(0, 0.003, days))
            out[t] = pd.Series(100 * factors[seed] * idiosyncratic, index=dates)
        return out


def test_hrp_clusters_correlated_tickers_and_reduces_their_share():
    """HRP : 4 tickers très corrélés (même facteur) doivent recevoir au total
    moins de poids que 4 tickers indépendants. Risk-parity 1/σ ignorerait ça."""
    # 4 corrélés (seed=1) + 4 décorrélés (seeds 10/11/12/13)
    seed_map = {
        "TECH1": 1, "TECH2": 1, "TECH3": 1, "TECH4": 1,
        "UTIL": 10, "HEALTH": 11, "FIN": 12, "CONS": 13,
    }
    scored = {t: _mk(80, 5.0, 20.0, 100.0) for t in seed_map}
    provider = _FakeMarketProvider(seed_map)

    pm = PortfolioManager(scored, market_provider=provider, weighting_method="hrp")
    plan = pm.calculate_allocations(list(scored), 100_000).to_dict()

    diag = plan["diagnostics"]["hrp"]
    assert diag["applied"] is True
    assert plan["diagnostics"]["weight_method"] == "hrp"

    alloc = plan["allocations"]
    tech_total = sum(alloc[t]["weight_pct"] for t in ["TECH1", "TECH2", "TECH3", "TECH4"])
    other_total = sum(alloc[t]["weight_pct"] for t in ["UTIL", "HEALTH", "FIN", "CONS"])

    # Les 4 TECH corrélés doivent ensemble peser nettement moins que les 4 autres.
    # (En 1/σ pur ils auraient 50 %.)
    assert tech_total < other_total, (
        f"HRP devrait shrinker les corrélés : TECH={tech_total:.1f}% vs OTHER={other_total:.1f}%"
    )
    # Sanity : somme à 100 %.
    assert abs((tech_total + other_total) - 100.0) < 0.01


def test_hrp_falls_back_to_risk_parity_when_no_market_provider():
    """HRP sans market_provider → fallback transparent vers risk_parity."""
    scored = {
        "A": _mk(80, 5.0, 10.0, 100.0),
        "B": _mk(80, 5.0, 20.0, 100.0),
        "C": _mk(80, 5.0, 40.0, 100.0),
        "D": _mk(80, 5.0, 25.0, 100.0),
    }
    pm = PortfolioManager(scored, market_provider=None, weighting_method="hrp")
    plan = pm.calculate_allocations(list(scored), 100_000).to_dict()

    # HRP applied=False, fallback_reason explicite.
    diag = plan["diagnostics"]["hrp"]
    assert diag["applied"] is False
    assert "no_market_provider" in diag.get("fallback_reason", "")
    # Le poids effectif doit ressembler à du 1/σ (le moins volatil dominant).
    assert plan["allocations"]["A"]["weight_pct"] > plan["allocations"]["C"]["weight_pct"]


def test_hrp_invalid_method_raises():
    """weighting_method invalide → ValueError au constructeur (fail-fast)."""
    scored = {"A": _mk(80, 5.0, 10.0, 100.0)}
    with pytest.raises(ValueError, match="weighting_method"):
        PortfolioManager(scored, weighting_method="markowitz")
