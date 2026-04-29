"""Tests scoring TITAN — sanitization, winsorization, ranking sectoriel,
pondération data_quality.

Univers synthétique pour isoler chaque comportement. Les tests vérifient
les invariants méthodologiques (pas les valeurs absolues qui dépendent
de la calibration).
"""
from __future__ import annotations

from modules.sector_metrics._scoring import _score_universe


def _mk_ticker(
    ticker: str = "TEST",
    sector: str = "Technology",
    *,
    roe: float | None = 0.20,
    op_margin: float | None = 0.25,
    ev_to_ebitda: float | None = 15.0,
    forward_pe: float | None = 18.0,
    free_cash_flow: float | None = 1e9,
    operating_cash_flow: float | None = 1.2e9,
    market_cap: float | None = 1e10,
    debt_to_equity: float | None = 50.0,
    current_ratio: float | None = 2.0,
    recommendation_mean: float | None = 2.0,
    price_target_mean: float | None = 120.0,
    current_price: float | None = 100.0,
    num_analysts: int | None = 12,
    momentum_return_pct: float | None = 5.0,
    momentum_risk_adjusted: float | None = 0.4,
    return_on_assets: float | None = 0.10,
    net_income: float | None = 8e8,
    gross_margin: float | None = 0.40,
    shares_outstanding: float | None = 1e8,
    revenue_growth: float | None = 0.12,
    earnings_growth: float | None = 0.18,
) -> dict:
    """Ticker synthétique avec valeurs raisonnables. Override par kwargs."""
    return {
        "ticker": ticker, "sector": sector,
        "return_on_equity": roe,
        "operating_margin": op_margin,
        "ev_to_ebitda": ev_to_ebitda,
        "forward_pe": forward_pe,
        "free_cash_flow": free_cash_flow,
        "operating_cash_flow": operating_cash_flow,
        "market_cap": market_cap,
        "debt_to_equity": debt_to_equity,
        "current_ratio": current_ratio,
        "recommendation_mean": recommendation_mean,
        "price_target_mean": price_target_mean,
        "current_price": current_price,
        "num_analysts": num_analysts,
        "momentum_return_pct": momentum_return_pct,
        "momentum_risk_adjusted": momentum_risk_adjusted,
        "return_on_assets":   return_on_assets,
        "net_income":         net_income,
        "gross_margin":       gross_margin,
        "shares_outstanding": shares_outstanding,
        "revenue_growth":     revenue_growth,
        "earnings_growth":    earnings_growth,
    }


def _build_universe(specs: list[tuple[str, str, dict]]) -> dict:
    """specs = [(ticker, sector, overrides_dict)]"""
    return {
        ticker: _mk_ticker(ticker=ticker, sector=sector, **kw)
        for ticker, sector, kw in specs
    }


# ─────────────────────────────────────────────────────────────────
# LOT 1 — Sanitize ratios négatifs
# ─────────────────────────────────────────────────────────────────

def test_lot1_negative_ev_ebitda_does_not_inflate_value_score():
    """Un ticker avec EV/EBITDA = -100 ne doit PAS être classé top Value."""
    universe = _build_universe([
        ("BAD",  "Technology", {"ev_to_ebitda": -100.0, "forward_pe": 50.0}),
        ("GOOD", "Technology", {"ev_to_ebitda": 12.0,   "forward_pe": 18.0}),
        ("MID",  "Technology", {"ev_to_ebitda": 20.0,   "forward_pe": 22.0}),
        ("MID2", "Technology", {"ev_to_ebitda": 25.0,   "forward_pe": 25.0}),
    ])
    scored = _score_universe(universe)
    # GOOD (cheap) doit avoir un Value > BAD (négatif neutralisé → fallback fpe)
    assert scored["GOOD"]["value_score"] > scored["BAD"]["value_score"], (
        f"GOOD V={scored['GOOD']['value_score']} should beat BAD V={scored['BAD']['value_score']}"
    )


def test_lot1_negative_forward_pe_does_not_inflate_value_score():
    """fwd_pe < 0 (pertes attendues) ne doit PAS donner un score Value haut.
    Cas : ev_to_ebitda manquant → fallback Forward P/E. Si fwd_pe est négatif,
    fallback aussi None → pilier value reposera uniquement sur fcf_yield.
    """
    universe = _build_universe([
        ("LOSS", "Technology", {"ev_to_ebitda": None, "forward_pe": -50.0,
                                 "free_cash_flow": 1e9, "market_cap": 1e10}),
        ("OK",   "Technology", {"ev_to_ebitda": None, "forward_pe": 15.0,
                                 "free_cash_flow": 1e9, "market_cap": 1e10}),
        ("MID",  "Technology", {"ev_to_ebitda": None, "forward_pe": 25.0,
                                 "free_cash_flow": 5e8, "market_cap": 1e10}),
        ("EXP",  "Technology", {"ev_to_ebitda": None, "forward_pe": 80.0,
                                 "free_cash_flow": 1e8, "market_cap": 1e10}),
    ])
    scored = _score_universe(universe)
    # OK (P/E 15, cheap) doit battre LOSS (P/E -50, neutralisé)
    assert scored["OK"]["value_score"] > scored["LOSS"]["value_score"]


# ─────────────────────────────────────────────────────────────────
# LOT 2 — Winsorization p1/p99
# ─────────────────────────────────────────────────────────────────

def test_lot2_winsorize_clamps_low_and_high():
    """Helper pur : valeurs > p99 → p99, valeurs < p1 → p1."""
    from modules.sector_metrics._utils import _winsorize
    # Distribution avec 1 outlier haut + 1 outlier bas + 8 values normales
    values = {
        "OUT_HI":  9999.0,
        "OUT_LO":  -9999.0,
        "T1": 10.0, "T2": 12.0, "T3": 14.0, "T4": 16.0,
        "T5": 18.0, "T6": 20.0, "T7": 22.0, "T8": 24.0,
    }
    out = _winsorize(values, lower_pct=10.0, upper_pct=90.0)
    # OUT_HI doit être borné < 9999, OUT_LO doit être borné > -9999
    assert out["OUT_HI"] < 9999.0
    assert out["OUT_LO"] > -9999.0
    # Valeurs centrales inchangées
    assert out["T4"] == 16.0
    assert out["T5"] == 18.0


def test_lot2_winsorize_preserves_none_and_inf():
    """Les None/NaN/inf doivent être préservés tel quel (pas convertis)."""
    from modules.sector_metrics._utils import _winsorize
    values = {
        "N": None, "T1": 1.0, "T2": 2.0, "T3": 3.0,
        "T4": 4.0, "T5": 5.0, "T6": 6.0,
    }
    out = _winsorize(values)
    assert out["N"] is None


def test_lot2_winsorize_noop_below_4_values():
    """Sous le seuil de 4 valeurs valides, pas de calcul → identité."""
    from modules.sector_metrics._utils import _winsorize
    values = {"A": 100.0, "B": 1.0, "C": 5000.0}
    out = _winsorize(values)
    assert out == values


def test_lot2_extreme_d2e_winsorized_does_not_dominate():
    """Un D/E de 9000 ne doit pas pouvoir descendre tout le pilier Risk
    de l'univers."""
    universe = _build_universe([
        ("OK1",  "Technology", {"debt_to_equity": 30.0}),
        ("OK2",  "Technology", {"debt_to_equity": 50.0}),
        ("OK3",  "Technology", {"debt_to_equity": 70.0}),
        ("OK4",  "Technology", {"debt_to_equity": 90.0}),
        ("OK5",  "Technology", {"debt_to_equity": 110.0}),
        ("OK6",  "Technology", {"debt_to_equity": 130.0}),
        ("HUGE", "Technology", {"debt_to_equity": 9000.0}),
    ])
    scored = _score_universe(universe)
    # HUGE doit avoir le risk_score le plus bas (D/E max → safer=False)
    risks = [(t, scored[t]["risk_score"]) for t in scored]
    risks.sort(key=lambda x: x[1])
    assert risks[0][0] == "HUGE", f"HUGE devrait avoir le pire risk : {risks}"


# ─────────────────────────────────────────────────────────────────
# LOT 3 — Cross-sectional ranking par secteur
# ─────────────────────────────────────────────────────────────────

def test_lot3_sector_relative_levels_playing_field():
    """Un secteur 'low ROE par nature' (Utilities ROE ~10%) doit pouvoir
    placer son meilleur élément au top, même si en absolu ses ROE sont bas
    vs Tech."""
    universe = _build_universe([
        # Tech : ROE haut par nature (5 tickers, le plus petit nb pour rank intra)
        ("T1", "Technology", {"roe": 0.30}),
        ("T2", "Technology", {"roe": 0.40}),
        ("T3", "Technology", {"roe": 0.50}),
        ("T4", "Technology", {"roe": 0.60}),
        ("T5", "Technology", {"roe": 0.70}),
        # Utilities : ROE bas par nature (5 tickers)
        ("U1", "Utilities",  {"roe": 0.05}),
        ("U2", "Utilities",  {"roe": 0.07}),
        ("U3", "Utilities",  {"roe": 0.09}),
        ("U4", "Utilities",  {"roe": 0.11}),
        ("U5", "Utilities",  {"roe": 0.15}),  # top de son secteur
    ])
    scored = _score_universe(universe)
    # En sector-relative, U5 (top des Utilities) doit avoir un Quality_score
    # comparable à T5 (top des Tech). En global, U5 serait au bas du classement.
    q_u5 = scored["U5"]["quality_score"]
    q_t5 = scored["T5"]["quality_score"]
    # Avec 3 signaux Quality (ROE + op_margin + gross_margin, Lot 14.1), un
    # ticker top sur 1 seul signal (roe ici, les 2 autres identiques pour
    # tous = rank neutre 50) plafonne à ~63 (100+50+50)/3. Le seuil ≥ 60 est
    # une borne inférieure correcte pour "mieux que la moyenne".
    assert q_u5 >= 60, f"U5 (top Utility) Q={q_u5} should be >= 60 in sector-relative"
    assert abs(q_u5 - q_t5) < 30, (
        f"U5 Q={q_u5} doit être proche de T5 Q={q_t5} (les deux sont tops sectoriels)"
    )


def test_lot3_small_sector_falls_back_to_global():
    """Si un secteur a < _MIN_SECTOR_SIZE_FOR_RELATIVE tickers, fallback global."""
    from modules.sector_metrics._scoring import _percentile_rank_by_sector
    # 3 tickers en Energy (sous le seuil 5) + 6 en Tech (au-dessus)
    values = {
        "E1": 0.10, "E2": 0.15, "E3": 0.20,
        "T1": 0.30, "T2": 0.40, "T3": 0.50,
        "T4": 0.60, "T5": 0.70, "T6": 0.80,
    }
    sectors = {
        "E1": "Energy", "E2": "Energy", "E3": "Energy",
        "T1": "Technology", "T2": "Technology", "T3": "Technology",
        "T4": "Technology", "T5": "Technology", "T6": "Technology",
    }
    out = _percentile_rank_by_sector(values, sectors, higher_is_better=True)
    # E1/E2/E3 fallback global → leurs valeurs (0.10/0.15/0.20) sont les
    # 3 plus basses sur 9 → percentile-rank ~5/16/27
    assert out["E1"] < 30, "E1 (smallest sector, lowest value) should be low globally"
    # T6 est le max global → ~94
    assert out["T6"] > 80, "T6 (largest value) should be top globally"


def test_lot3_per_sector_ranking_independent_per_metric():
    """Chaque métrique ranke indépendamment dans le secteur — un Tech avec ROE
    moyen mais P/E très bas peut être top Value sans être top Quality."""
    universe = _build_universe([
        # 5 Tech : T1 = top Quality (ROE), T5 = top Value (low P/E)
        ("T1", "Technology", {"roe": 0.50, "ev_to_ebitda": 50.0, "forward_pe": 60.0}),
        ("T2", "Technology", {"roe": 0.40, "ev_to_ebitda": 40.0, "forward_pe": 45.0}),
        ("T3", "Technology", {"roe": 0.30, "ev_to_ebitda": 30.0, "forward_pe": 35.0}),
        ("T4", "Technology", {"roe": 0.20, "ev_to_ebitda": 20.0, "forward_pe": 25.0}),
        ("T5", "Technology", {"roe": 0.10, "ev_to_ebitda": 10.0, "forward_pe": 15.0}),
    ])
    scored = _score_universe(universe)
    # T1 doit dominer sur Quality
    q_ranks = sorted(scored.items(), key=lambda kv: -kv[1]["quality_score"])
    assert q_ranks[0][0] == "T1"
    # T5 doit dominer sur Value (cheap)
    v_ranks = sorted(scored.items(), key=lambda kv: -kv[1]["value_score"])
    assert v_ranks[0][0] == "T5"


# ─────────────────────────────────────────────────────────────────
# LOT 4 — Pondération composite par data_quality
# ─────────────────────────────────────────────────────────────────

def test_lot4_perfect_dq_no_penalty():
    """Un ticker avec DQ=1.0 a coef=1.0 → composite_raw == composite_score."""
    universe = _build_universe([
        ("FULL", "Technology", {}),  # tous les champs renseignés (defaults)
        ("T1",   "Technology", {}),
        ("T2",   "Technology", {}),
        ("T3",   "Technology", {}),
        ("T4",   "Technology", {}),
    ])
    scored = _score_universe(universe)
    # Tous DQ=1.0
    assert scored["FULL"]["data_quality"] == 1.0
    assert scored["FULL"]["data_quality_coef"] == 1.0
    assert scored["FULL"]["titan_composite_raw"] == scored["FULL"]["titan_composite_score"]


def test_lot4_low_dq_penalized():
    """Un ticker avec DQ=0.67 (6 fields renseignés sur 9) doit avoir un
    composite final < composite raw."""
    # Construire un ticker à DQ=0.67 = 6/9 fields parmi _TITAN_SCORING_FIELDS.
    # Champs : roe, op_margin, ev_to_ebitda, fwd_pe, fcf, d2e, curr, reco, target.
    # 6 sur 9 → on null-ifie 3 champs.
    sparse = _mk_ticker(
        ticker="SPARSE", sector="Technology",
        ev_to_ebitda=None, debt_to_equity=None, current_ratio=None,
    )
    # Need at least 5 tickers in sector for sector-relative ranking
    universe = {
        "SPARSE": sparse,
        "T1": _mk_ticker("T1", "Technology"),
        "T2": _mk_ticker("T2", "Technology"),
        "T3": _mk_ticker("T3", "Technology"),
        "T4": _mk_ticker("T4", "Technology"),
        "T5": _mk_ticker("T5", "Technology"),
    }
    scored = _score_universe(universe)
    # Lot 12 : 11 fields total, SPARSE en a 3 None → 8/11 ≈ 0.727
    assert 0.70 <= scored["SPARSE"]["data_quality"] <= 0.75
    # Coef = 0.5 + 0.5 × 0.727 ≈ 0.864
    assert 0.83 <= scored["SPARSE"]["data_quality_coef"] <= 0.90
    # Et composite final < raw (de quelques %)
    assert (
        scored["SPARSE"]["titan_composite_score"]
        < scored["SPARSE"]["titan_composite_raw"]
    )


def test_lot4_dq_coef_monotonic():
    """coef est monotone croissant en DQ : DQ ↑ → coef ↑."""
    # 1 ticker complet (DQ=1.0) + 1 sparse (DQ ~0.67) — comparaison directe
    universe = {
        "FULL":   _mk_ticker("FULL",   "Technology"),
        "SPARSE": _mk_ticker(
            "SPARSE", "Technology",
            ev_to_ebitda=None, debt_to_equity=None, current_ratio=None,
        ),
        "T1": _mk_ticker("T1", "Technology"),
        "T2": _mk_ticker("T2", "Technology"),
        "T3": _mk_ticker("T3", "Technology"),
        "T4": _mk_ticker("T4", "Technology"),
    }
    scored = _score_universe(universe)
    assert (
        scored["FULL"]["data_quality_coef"]
        > scored["SPARSE"]["data_quality_coef"]
    )


def test_lot4_payload_exposes_raw_and_final():
    """Le payload doit exposer les 2 champs : raw (avant pénalité) et score
    (final) pour audit + UI."""
    universe = _build_universe([("T1", "Technology", {}), ("T2", "Technology", {}),
                                 ("T3", "Technology", {}), ("T4", "Technology", {}),
                                 ("T5", "Technology", {})])
    scored = _score_universe(universe)
    for r in scored.values():
        assert "titan_composite_raw" in r
        assert "titan_composite_score" in r
        assert "data_quality_coef" in r


# ─────────────────────────────────────────────────────────────────
# LOT 5 — Pilier Momentum
# ─────────────────────────────────────────────────────────────────

def test_lot5_high_momentum_boosts_composite():
    """Un ticker avec momentum très haut doit avoir un score Momentum élevé,
    et son composite supérieur à un clone fundamentalement identique sans
    momentum."""
    universe = {
        "HOT":  _mk_ticker("HOT",  "Technology",
                            momentum_return_pct=100.0, momentum_risk_adjusted=2.5),
        "COLD": _mk_ticker("COLD", "Technology",
                            momentum_return_pct=-30.0, momentum_risk_adjusted=-0.5),
        # Bruit pour avoir n>=2 dans le percentile-rank global
        "T1": _mk_ticker("T1", "Technology", momentum_return_pct=10.0,  momentum_risk_adjusted=0.3),
        "T2": _mk_ticker("T2", "Technology", momentum_return_pct=20.0,  momentum_risk_adjusted=0.6),
        "T3": _mk_ticker("T3", "Technology", momentum_return_pct=5.0,   momentum_risk_adjusted=0.1),
        "T4": _mk_ticker("T4", "Technology", momentum_return_pct=15.0,  momentum_risk_adjusted=0.4),
    }
    scored = _score_universe(universe)
    assert scored["HOT"]["momentum_score"] > scored["COLD"]["momentum_score"]
    # HOT est le top momentum → M ≈ 100
    assert scored["HOT"]["momentum_score"] >= 80
    # COLD est le pire → M ≈ 0
    assert scored["COLD"]["momentum_score"] <= 20
    # Composite reflète : HOT > COLD malgré fundamentaux identiques
    assert (
        scored["HOT"]["titan_composite_score"]
        > scored["COLD"]["titan_composite_score"]
    )


def test_lot5_no_momentum_data_neutral():
    """Si momentum_return ET momentum_risk_adjusted sont None, le pilier
    Momentum tombe à neutre 50 (pas de pénalité injuste)."""
    universe = {
        "NONE": _mk_ticker("NONE", "Technology",
                            momentum_return_pct=None, momentum_risk_adjusted=None),
        "T1": _mk_ticker("T1", "Technology", momentum_return_pct=20.0),
        "T2": _mk_ticker("T2", "Technology", momentum_return_pct=10.0),
        "T3": _mk_ticker("T3", "Technology", momentum_return_pct=5.0),
        "T4": _mk_ticker("T4", "Technology", momentum_return_pct=15.0),
        "T5": _mk_ticker("T5", "Technology", momentum_return_pct=25.0),
    }
    scored = _score_universe(universe)
    assert scored["NONE"]["momentum_score"] == 50.0


def test_lot5_no_sentiment_renormalizes_to_4_pillars():
    """Quand reco+target absents → composite = 1/4 chacun des 4 piliers
    restants (Q+V+R+M)."""
    # Tous les tickers SANS sentiment
    universe = {
        f"T{i}": _mk_ticker(
            f"T{i}", "Technology",
            recommendation_mean=None, price_target_mean=None,
        )
        for i in range(5)
    }
    scored = _score_universe(universe)
    for r in scored.values():
        assert r["titan_weight_mode"] == "no_sentiment"
        # Composite = (Q + V + R + M) / 4 (tous à ~50 vu universe identique)
        # → composite ≈ 50
        assert 30 <= r["titan_composite_raw"] <= 70


# Garde-fou Σ=1.0 désormais couvert par test_lot8_pillar_weights_sum_to_one_with_piotroski
# (5→6 piliers depuis Lot 8). On garde un alias pour traçabilité historique :
def test_lot5_obsolete_replaced_by_lot8_weight_sum():
    """Lot 5 vérifiait 5 piliers ; Lot 8 a ajouté Piotroski → couvert par
    test_lot8_pillar_weights_sum_to_one_with_piotroski. Test no-op gardé pour
    rappel historique."""
    pass


def test_lot5_payload_exposes_momentum_score():
    """Le payload doit exposer momentum_score pour audit + UI."""
    universe = {
        f"T{i}": _mk_ticker(f"T{i}", "Technology")
        for i in range(5)
    }
    scored = _score_universe(universe)
    for r in scored.values():
        assert "momentum_score" in r
        assert isinstance(r["momentum_score"], (int, float))


# ─────────────────────────────────────────────────────────────────
# LOT 8 — Pilier Piotroski F-Score (4 critères absolus)
# ─────────────────────────────────────────────────────────────────

def test_lot8_f_score_perfect_4_4():
    """Tous critères passent → F-Score 4/4 = pillar 100."""
    from modules.sector_metrics._scoring import _piotroski_score_pillar
    row = {
        "return_on_assets":     0.15,    # F1: > 0 ✓
        "operating_cash_flow":  1e9,     # F2: > 0 ✓
        "net_income":           5e8,     # F4: OCF > NI ✓ (1e9 > 5e8)
        "current_ratio":        2.5,     # F7: > 1 ✓
    }
    score, diag = _piotroski_score_pillar(row)
    assert score == 100.0
    assert diag["f_score"] == 4
    assert diag["f_score_max"] == 4


def test_lot8_f_score_zero_4():
    """Aucun critère ne passe → F-Score 0/4 = pillar 0."""
    from modules.sector_metrics._scoring import _piotroski_score_pillar
    row = {
        "return_on_assets":     -0.05,   # F1: ≤ 0 ✗
        "operating_cash_flow":  -1e8,    # F2: ≤ 0 ✗
        "net_income":           5e8,     # F4: OCF (-1e8) > NI (5e8) ✗
        "current_ratio":        0.5,     # F7: ≤ 1 ✗
    }
    score, diag = _piotroski_score_pillar(row)
    assert score == 0.0
    assert diag["f_score"] == 0


def test_lot8_f_score_partial_data():
    """3 critères dispo (data manquante sur ROA), 2 réussis → 2/3 = 66.7."""
    from modules.sector_metrics._scoring import _piotroski_score_pillar
    row = {
        "return_on_assets":     None,    # F1: pas évalué
        "operating_cash_flow":  1e9,     # F2: ✓
        "net_income":           5e8,     # F4: ✓
        "current_ratio":        0.5,     # F7: ✗
    }
    score, diag = _piotroski_score_pillar(row)
    # 2/3 = 66.67
    assert 65 < score < 68
    assert diag["f_score"] == 2
    assert diag["f_score_max"] == 3


def test_lot8_f_score_no_data_neutral():
    """0 critère évaluable → neutre 50 (pas de pénalité)."""
    from modules.sector_metrics._scoring import _piotroski_score_pillar
    score, diag = _piotroski_score_pillar({})
    assert score == 50.0
    assert diag["f_score"] is None
    assert diag["f_score_max"] == 0


def test_lot8_pillar_weights_sum_to_one_with_piotroski():
    """Lot 12 : 7-pilier full mode + 6-pilier no-sentiment mode doivent sommer à 1."""
    from modules.sector_metrics._scoring import (
        _W_TITAN_G_NO_SENTIMENT,
        _W_TITAN_GROWTH,
        _W_TITAN_M_NO_SENTIMENT,
        _W_TITAN_MOMENTUM,
        _W_TITAN_P_NO_SENTIMENT,
        _W_TITAN_PIOTROSKI,
        _W_TITAN_Q_NO_SENTIMENT,
        _W_TITAN_QUALITY,
        _W_TITAN_R_NO_SENTIMENT,
        _W_TITAN_RISK,
        _W_TITAN_SENTIMENT,
        _W_TITAN_V_NO_SENTIMENT,
        _W_TITAN_VALUE,
    )
    full = (_W_TITAN_QUALITY + _W_TITAN_VALUE + _W_TITAN_RISK
            + _W_TITAN_SENTIMENT + _W_TITAN_MOMENTUM + _W_TITAN_PIOTROSKI
            + _W_TITAN_GROWTH)
    assert abs(full - 1.0) < 1e-9, f"7-pillar weights sum = {full}"
    no_sent = (_W_TITAN_Q_NO_SENTIMENT + _W_TITAN_V_NO_SENTIMENT
               + _W_TITAN_R_NO_SENTIMENT + _W_TITAN_M_NO_SENTIMENT
               + _W_TITAN_P_NO_SENTIMENT + _W_TITAN_G_NO_SENTIMENT)
    assert abs(no_sent - 1.0) < 1e-9, f"6-pillar (no sentiment) sum = {no_sent}"


def test_lot8_payload_exposes_piotroski():
    """Le payload doit exposer piotroski_score + f_score breakdown pour audit/UI."""
    universe = {f"T{i}": _mk_ticker(f"T{i}", "Technology") for i in range(5)}
    scored = _score_universe(universe)
    for r in scored.values():
        assert "piotroski_score" in r
        assert "f_score" in r
        assert "f_score_breakdown" in r
        assert isinstance(r["f_score_breakdown"], dict)


def test_lot8_high_f_score_boosts_composite():
    """Un ticker avec F-Score 4/4 doit avoir un composite > son clone à F-Score 0/4."""
    universe = {
        "GOOD": _mk_ticker(
            "GOOD", "Technology",
            return_on_assets=0.15, operating_cash_flow=2e9,
            net_income=5e8, current_ratio=2.5,
        ),
        "BAD": _mk_ticker(
            "BAD", "Technology",
            return_on_assets=-0.10, operating_cash_flow=-5e8,
            net_income=2e8, current_ratio=0.5,
        ),
        # 3 bruits pour avoir n>=5 dans le secteur
        "T1": _mk_ticker("T1", "Technology"),
        "T2": _mk_ticker("T2", "Technology"),
        "T3": _mk_ticker("T3", "Technology"),
    }
    scored = _score_universe(universe)
    assert scored["GOOD"]["piotroski_score"] == 100.0
    assert scored["BAD"]["piotroski_score"] == 0.0
    assert (
        scored["GOOD"]["titan_composite_score"]
        > scored["BAD"]["titan_composite_score"]
    )


def test_lot1_zero_ratios_treated_as_none():
    """Sécurité : EV/EBITDA = 0 ou Forward P/E = 0 → traité comme None
    (impossible économiquement, signe d'un trou data)."""
    universe = _build_universe([
        ("ZERO", "Technology", {"ev_to_ebitda": 0.0, "forward_pe": 0.0}),
        ("OK",   "Technology", {"ev_to_ebitda": 15.0, "forward_pe": 18.0}),
        ("MID",  "Technology", {"ev_to_ebitda": 20.0, "forward_pe": 22.0}),
        ("EXP",  "Technology", {"ev_to_ebitda": 50.0, "forward_pe": 60.0}),
    ])
    scored = _score_universe(universe)
    # ZERO ne doit PAS être au top Value (ses ratios sont neutralisés,
    # value reposera uniquement sur fcf_yield qui est identique partout).
    assert scored["OK"]["value_score"] >= scored["ZERO"]["value_score"]
