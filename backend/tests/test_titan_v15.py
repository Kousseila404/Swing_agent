"""TITAN V15 — value sector-neutre filtrée par la qualité (2026-11-20)."""
from __future__ import annotations

from modules import lt_exit_policy, scoring_lab, shadow_portfolios
from modules.sector_metrics import _scoring


def _row(sector, ev, pe, pb, q=60.0, pio=60.0, comp=50.0):
    return {"sector": sector, "ev_to_ebitda": ev, "forward_pe": pe, "price_to_book": pb,
            "quality_score": q, "piotroski_score": pio, "titan_composite_score": comp}


def _universe():
    u = {}
    for i in range(15):  # ≥ 12 par secteur (sinon repli rang global) ; secteur « cher » : multiples ×3, comparé à ses pairs seulement
        u[f"T{i}"] = _row("Tech", 30 + i * 3, 30 + i * 3, 9 + i)
        u[f"E{i}"] = _row("Energy", 4 + i, 6 + i, 1 + i * 0.2)
    return u


def test_v15_is_sector_neutral_and_cheaper_is_better():
    v = _scoring.compute_v15_scores(_universe())
    # Le moins cher de chaque secteur a le même score (neutralité sectorielle).
    assert v["T0"]["titan_v15_score"] == v["E0"]["titan_v15_score"]
    assert v["T0"]["titan_v15_score"] > v["T5"]["titan_v15_score"] > v["T14"]["titan_v15_score"]


def test_v15_quality_gate_caps_value_traps():
    u = _universe()
    u["E0"]["quality_score"] = 1.0  # le moins cher mais dernier en qualité
    v = _scoring.compute_v15_scores(u)
    assert v["E0"]["v15_gate"] is False
    assert v["E0"]["titan_v15_score"] <= _scoring.V15_GATED_CAP < v["E0"]["v15_value_core"]


def test_v15_needs_two_value_metrics_and_ignores_negatives():
    u = _universe()
    u["E3"].update(ev_to_ebitda=-5, forward_pe=None)  # pertes : multiples non interprétables
    v = _scoring.compute_v15_scores(u)
    assert v["E3"]["titan_v15_score"] is None and v["E3"]["v15_n_metrics"] == 1


def test_apply_v15_replaces_composite_and_keeps_legacy():
    u = _universe()
    _scoring._apply_v15(u, replace_composite=False)
    assert u["T0"]["titan_composite_score"] == 50.0 and "titan_composite_legacy" not in u["T0"]
    u = _universe()
    u["E3"].update(ev_to_ebitda=None, forward_pe=None)
    _scoring._apply_v15(u, replace_composite=True)
    assert u["T0"]["titan_composite_legacy"] == 50.0
    assert u["T0"]["titan_composite_score"] == u["T0"]["titan_v15_score"]
    assert u["E3"]["titan_composite_score"] == 0.0  # pas de données de valeur → jamais classé


def test_compute_ranks_sector_cap_relegates_overflow():
    u = {f"E{i}": {"sector": "Energy", "titan_composite_score": 100 - i} for i in range(5)}
    u |= {f"T{i}": {"sector": "Tech", "titan_composite_score": 50 - i} for i in range(3)}
    plain = lt_exit_policy.compute_ranks(u, max_per_sector=0)
    capped = lt_exit_policy.compute_ranks(u, max_per_sector=2)
    assert plain["T0"] == 6
    assert [t for t, _ in sorted(capped.items(), key=lambda x: x[1])] == ["E0", "E1", "T0", "T1", "E2", "E3", "E4", "T2"]


def test_v15_profile_registered_everywhere():
    assert "v15_value_quality" in _scoring.WEIGHT_PROFILES and "v15_value_quality" in _scoring.V15_PROFILES
    assert shadow_portfolios._profile_weights("v15_value_quality") == {"titan_v15_score": 1.0}
    assert shadow_portfolios.PROFILE_TOP_N["v15_value_quality"] == 30
    assert {v["id"] for v in scoring_lab.VARIANTS} >= {"v15_top30_cap4", "v15_top20"}


def test_lab_v15_rank_fn_applies_sector_cap():
    snap = {"tickers": _universe()}
    top = scoring_lab.make_v15_rank_fn(2)(snap, 4)
    sectors = [snap["tickers"][t]["sector"] for t, _ in top]
    assert len(top) == 4 and sectors.count("Tech") == 2 and sectors.count("Energy") == 2


# ── Bugs audit live 2026-11-20 ───────────────────────────────────────────────

def test_momentum_is_true_12_1_and_52w_high_on_252_sessions():
    import pandas as pd

    from data_providers.yfinance_provider import _days_to_period
    from modules.sector_metrics._momentum import MOMENTUM_FETCH_DAYS, _compute_momentum_stats
    assert _days_to_period(MOMENTUM_FETCH_DAYS) == "2y"  # ≥ 13 mois chez yfinance
    # 500 séances : un pic à 1000 il y a 400 séances (hors 52 sem.), puis 100→ linéaire.
    px = [100.0] * 500
    px[100] = 1000.0
    for i in range(227, 500):
        px[i] = 100.0 + (i - 227)          # rampe sur les 273 dernières clôtures
    st = _compute_momentum_stats(pd.Series(px))
    expected = (px[-22] / px[-273] - 1) * 100  # 252 séances, dernier mois exclu
    assert abs(st["return_pct"] - expected) < 1e-9
    assert abs(st["high_52w_ratio"] - 1.0) < 1e-9  # le pic d'il y a 400 séances est ignoré


def test_basket_gate_uses_catastrophe_floor_not_40():
    from modules import auto_approve as aa
    ok, why = aa._qualifies_basket({"titan_score": 85.0, "risk_score": 25.0}, 1)
    assert ok, why  # bilan 25 (ex. compagnie aérienne) : n'est plus bloqué
    ok, why = aa._qualifies_basket({"titan_score": 85.0, "risk_score": 5.0}, 1)
    assert not ok and "Bilan" in why
