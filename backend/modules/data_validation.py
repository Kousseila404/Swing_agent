"""Module — sanitize & validate FinancialRatios à l'entrée du cache.

Audit data 2026-04-23 : yfinance retourne régulièrement des valeurs
aberrantes (EV/EBITDA = -2179 sur CRWD, ROE = 450 sur un spinoff récent,
dividend_yield = 1.85 parce que yf a confondu yield et payout). Le scoring
aval winsorize déjà ces valeurs, mais elles polluent le cache 24h et
peuvent faire remonter un faux signal.

Stratégie : **rejet in-place** juste avant le `_store.put()` dans le cache.
Un champ hors bornes → `None` + un flag consolidé dans `error` pour audit.
On ne *retire* pas le ticker — on laisse les autres champs valides faire leur
boulot, et le data_quality gate exclut si trop de trous.

Cross-check mcap : si `market_cap` et `price × shares_outstanding` divergent
de > 5 %, on tag le ticker (`mcap_mismatch`) sans modifier — trop d'edge
cases (buybacks, Class A/B shares non comptées, stock splits récents) pour
trancher automatiquement. Le tag remonte dans `/api/data_health`.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, NamedTuple

from data_providers.base import FinancialRatios


class Bound(NamedTuple):
    """Borne [low, high] inclusive. Valeur hors bornes → None + flag."""
    low: float
    high: float


# ── Bornes par field ───────────────────────────────────────────────────────
# Choix conservateurs : on rejette uniquement ce qui ne peut pas être légitime.
# Secteurs atypiques (REIT, banks, utilities) restent dans les bornes larges.
_BOUNDS: dict[str, Bound] = {
    # Valuation multiples : négatifs et zéro = non-significatifs, très hauts = data bug.
    "forward_pe":      Bound(0.0, 1000.0),   # 1000× = déjà absurde, pricing growth extrême
    "trailing_pe":     Bound(0.0, 2000.0),   # plus permissif car TTM peut avoir des artefacts
    "peg_ratio":       Bound(-50.0, 100.0),
    "price_to_book":   Bound(-50.0, 500.0),
    # EV/EBITDA négatif = EBITDA négatif (pertes) → déjà exclu au scoring, on rejette en amont aussi.
    "ev_to_ebitda":    Bound(0.0, 500.0),
    "ev_to_revenue":   Bound(0.0, 100.0),

    # Ratios en décimal : ROE/ROA/margins/yield.
    "return_on_equity":   Bound(-2.0, 5.0),     # -200 % à +500 % (REIT mortgage, spinoffs)
    # Phase 6 audit (2026-05-06) — ROA bound élargi à 1.5 (était 1.0). Les
    # REITs/Utilities peuvent légitimement spike à 100-150% sur un trimestre
    # (distributions spéciales, asset sales) ; rejeter à 1.0 perdait du signal
    # réel. Override sector-aware ci-dessous (_SECTOR_OVERRIDES) pousse à 3.0
    # pour Real Estate.
    "return_on_assets":   Bound(-1.0, 1.5),
    "operating_margin":   Bound(-2.0, 1.0),     # certains SaaS early-stage à -150 %
    "profit_margin":      Bound(-5.0, 1.0),     # net margin peut être très négative
    "gross_margin":       Bound(-1.0, 1.0),
    "dividend_yield":     Bound(0.0, 0.25),     # 25 % = on flag, au-delà = data bug yfinance
    "beta":               Bound(-5.0, 10.0),

    # Bilan : liquidity + leverage.
    "debt_to_equity":   Bound(-20.0, 50.0),     # negative = equity négatif (real case)
    "current_ratio":    Bound(0.0, 50.0),       # > 50 = asset manager avec cash massif
    "quick_ratio":      Bound(0.0, 50.0),

    # Analystes : recommendation_mean ∈ [1, 5] par convention Yahoo.
    "recommendation_mean": Bound(1.0, 5.0),

    # Growth (Lot 12) : en décimal. Croissance > 500 % = data bug / base très faible.
    "revenue_growth":             Bound(-1.0, 5.0),
    "earnings_growth":            Bound(-10.0, 10.0),   # earnings peut basculer de neg à pos
    "earnings_quarterly_growth":  Bound(-10.0, 10.0),

    # Taille & cash flow : le seul outlier est le signe/zéro.
    # market_cap, FCF, OCF, net_income : pas de borne haute — laisser passer les mega-caps.
    # On rejette seulement les valeurs < 0 pour market_cap (impossible).
    "market_cap":        Bound(0.0, float("inf")),
    "shares_outstanding": Bound(0.0, float("inf")),
    "current_price":     Bound(0.0, float("inf")),
    "price_target_mean": Bound(0.0, float("inf")),
    "price_target_high": Bound(0.0, float("inf")),
    "price_target_low":  Bound(0.0, float("inf")),
    # free_cash_flow, operating_cash_flow, net_income : peuvent être négatifs → pas de bound.
}


# Seuil de tolérance du cross-check market_cap.
_MCAP_TOLERANCE = 0.05   # 5 %


# Phase 6 audit (2026-05-06) — overrides sector-specific. Pour les secteurs
# avec des distributions structurellement atypiques, on relâche les bornes
# afin de ne PAS rejeter du signal légitime (ex: REIT ROA 1.2 sur quarter
# avec asset sale).
_SECTOR_OVERRIDES: dict[str, dict[str, Bound]] = {
    "Real Estate": {
        "return_on_assets":   Bound(-1.5, 3.0),    # REIT spikes legitimate
        "return_on_equity":   Bound(-3.0, 8.0),
        "debt_to_equity":     Bound(-50.0, 100.0), # leverage structurel REIT
    },
    "Utilities": {
        "return_on_assets":   Bound(-1.0, 2.0),
        "debt_to_equity":     Bound(-20.0, 80.0),  # leverage structurel
    },
    "Financial Services": {
        # Banks ont des bilans avec leverage 8-12× = legit
        "debt_to_equity":     Bound(-50.0, 100.0),
        "return_on_assets":   Bound(-1.0, 2.0),
    },
}


def _out_of_bounds(
    field: str, value: float | None, sector: str | None = None,
) -> bool:
    """True si la valeur dépasse les bornes. None = pas out-of-bounds.

    Phase 6 audit — sector-aware : si le secteur a un override, on l'utilise
    en priorité au lieu de la borne globale.
    """
    if value is None:
        return False
    if sector and sector in _SECTOR_OVERRIDES:
        b = _SECTOR_OVERRIDES[sector].get(field)
        if b is not None:
            return value < b.low or value > b.high
    b = _BOUNDS.get(field)
    if b is None:
        return False
    return value < b.low or value > b.high


def _check_mcap_consistency(r: FinancialRatios) -> bool:
    """True si divergence détectée. Requiert les 3 champs."""
    mcap = r.market_cap
    price = r.current_price
    shares = r.shares_outstanding
    if mcap is None or price is None or shares is None:
        return False
    if mcap <= 0 or price <= 0 or shares <= 0:
        return False
    expected = price * shares
    if expected <= 0:
        return False
    rel_err = abs(mcap - expected) / max(mcap, expected)
    return rel_err > _MCAP_TOLERANCE


def sanitize_ratios(r: FinancialRatios) -> tuple[FinancialRatios, list[str]]:
    """Clamp les valeurs out-of-bounds → None ; retourne (ratios_nettoyés, flags).

    Mutation : retourne une **nouvelle instance** (dataclasses.replace), l'input
    n'est pas modifié. Le champ `error` du résultat est enrichi avec un résumé
    compact des flags (préservant l'error d'origine s'il y en avait).

    Flags conventions :
      - "out_of_bounds:field_name" par champ rejeté
      - "mcap_mismatch" si cross-check échoue
    """
    flags: list[str] = []
    # dict[str, Any] (et non float | None) : les valeurs alimentent
    # dataclasses.replace(**updates) dont les champs ont des types variés —
    # mypy ne peut pas matcher **dict[str, float|None] contre str/int/list.
    updates: dict[str, Any] = {}
    sector = getattr(r, "sector", None)

    for field, _bound in _BOUNDS.items():
        val = getattr(r, field, None)
        if _out_of_bounds(field, val, sector=sector):
            flags.append(f"out_of_bounds:{field}")
            updates[field] = None

    if _check_mcap_consistency(r):
        flags.append("mcap_mismatch")

    if not flags:
        return r, flags

    # Concat error : on garde l'original + on suffixe les flags pour traçabilité.
    base_error = r.error or ""
    sanitize_tag = "|".join(flags)
    new_error = (
        f"{base_error}; dq_sanitize={sanitize_tag}"
        if base_error
        else f"dq_sanitize={sanitize_tag}"
    )
    return replace(r, error=new_error, **updates), flags
