"""Multi-factor scoring TITAN — percentile ranks + piliers + composite.

Scoring pipeline (par ticker, cross-universe) :
  1. Percentile-rank cross-universe sur chaque métrique brute.
  2. Agrégation en 4 sous-scores 0-100 (Quality / Value / Risk / Sentiment).
  3. TITAN Composite = 0.30·Q + 0.25·V + 0.20·R + 0.25·S.

Imputation robuste :
  1. EV/EBITDA manquant → fallback Forward P/E.
  2. Composantes None d'un pilier → ignorées dans la moyenne.
  3. Pilier entièrement None → score neutre 50 (pas de pénalité).

Pansement Sentiment (FMP stable) :
  - Si reco ET upside absents → renormalisation Q/V/R (1/3 chacun).
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from modules.log import logger
from modules.revisions_score import compute_revisions_pillar

from ._utils import _safe_float, _upside_pct, _winsorize, _winsorize_by_sector

# Score neutre quand toutes les composantes d'un pilier sont absentes.
# Pas 0 pour éviter de pénaliser injustement un trou d'API.
_NEUTRAL_SCORE = 50.0

# ── TITAN Composite — 7 piliers Smart-Beta ────────────────────────────
# Lot 5  : ajout pilier Momentum (Jegadeesh-Titman 1993).
# Lot 8  : ajout pilier Piotroski F-Score (4 critères absolus).
# Lot 12 : ajout pilier Growth (Revenue + Earnings CAGR YoY, Novy-Marx, AQR).
#
# AUDIT 2026-04-23 — rebalance final 7 piliers (Σ = 1.00) :
#   Quality   0.22 → 0.22 (stable, pilier dominant)
#   Value     0.18 → 0.16
#   Risk      0.13 → 0.11
#   Sentiment 0.18 → 0.15
#   Momentum  0.21 → 0.18
#   Piotroski 0.05 → 0.04 (toujours bas tant que Y/Y manquent)
#   Growth    ----  → 0.14 (nouveau)
# Growth reçoit 14 % car académiquement bien établi mais corrélé à Momentum
# (les growers forts montent souvent) — on évite le double-comptage en ne
# poussant pas trop haut. À rebump après analyse corr OOS.
# Lot 14 (2026-04-24) — rééquilibrage LT-optimized :
#   Sentiment  0.15 → 0.08  (bruit analyste court-terme sur-pondéré)
#   Piotroski  0.04 → 0.11  (signal LT académique robuste — Piotroski 2000,
#                            activé 9/9 grâce au scrape yfinance annuels)
# Total inchangé (0.07 transféré S→P). Pour du pur LT fondamental, Piotroski
# porte plus d'information que le consensus analyste 3M.
#
# Lot 16 (2026-04-28) — ajout pilier Revisions (8e pilier).
# Lot 17 (2026-04-28) — ajout pilier Insider (9e pilier, gratuit via SEC EDGAR).
# IC empirique des C-level/director purchases : ~0.05 sur 6M (Lakonishok &
# Lee 2001, Cohen-Malloy-Pomorski 2012). Signal smart-money classique avec
# fond gratuit illimité.
# Re-balance final 9 piliers (Σ = 1.00) :
#   Quality   0.20 → 0.18
#   Value     0.14 → 0.13
#   Risk      0.10 → 0.10
#   Sentiment 0.04 → 0.03
#   Momentum  0.16 → 0.15
#   Piotroski 0.10 → 0.09
#   Growth    0.14 → 0.13
#   Revisions 0.12 → 0.11
#   Insider   ----  → 0.08 (NEW)
_W_TITAN_QUALITY    = 0.18
_W_TITAN_VALUE      = 0.13
_W_TITAN_RISK       = 0.10
_W_TITAN_SENTIMENT  = 0.03
_W_TITAN_MOMENTUM   = 0.15
_W_TITAN_PIOTROSKI  = 0.09
_W_TITAN_GROWTH     = 0.13
_W_TITAN_REVISIONS  = 0.11
_W_TITAN_INSIDER    = 0.08

# Pansement Sentiment — si reco+upside tous deux absents (FMP stable), on
# renormalise Q/V/R/M/P/G/Revisions/Insider en préservant leur ratio relatif.
_SENTIMENT_FALLBACK_SUM = (
    _W_TITAN_QUALITY + _W_TITAN_VALUE + _W_TITAN_RISK
    + _W_TITAN_MOMENTUM + _W_TITAN_PIOTROSKI + _W_TITAN_GROWTH
    + _W_TITAN_REVISIONS + _W_TITAN_INSIDER
)
_W_TITAN_Q_NO_SENTIMENT  = _W_TITAN_QUALITY   / _SENTIMENT_FALLBACK_SUM
_W_TITAN_V_NO_SENTIMENT  = _W_TITAN_VALUE     / _SENTIMENT_FALLBACK_SUM
_W_TITAN_R_NO_SENTIMENT  = _W_TITAN_RISK      / _SENTIMENT_FALLBACK_SUM
_W_TITAN_M_NO_SENTIMENT  = _W_TITAN_MOMENTUM  / _SENTIMENT_FALLBACK_SUM
_W_TITAN_P_NO_SENTIMENT  = _W_TITAN_PIOTROSKI / _SENTIMENT_FALLBACK_SUM
_W_TITAN_G_NO_SENTIMENT  = _W_TITAN_GROWTH    / _SENTIMENT_FALLBACK_SUM
_W_TITAN_RV_NO_SENTIMENT = _W_TITAN_REVISIONS / _SENTIMENT_FALLBACK_SUM
_W_TITAN_IN_NO_SENTIMENT = _W_TITAN_INSIDER   / _SENTIMENT_FALLBACK_SUM

# Pondération du composite par la data_quality.
# Formule : coef = _DQ_MIN_COEF + (1 - _DQ_MIN_COEF) × DQ
#
# Audit S3.1 (2026-04-27) — _DQ_MIN_COEF=0.5 produisait une double pénalité :
# tickers DQ<0.70 sont DÉJÀ filtrés par le gate hard `_MIN_DATA_QUALITY`. Au-
# dessus du gate, pénaliser linéairement -15 pts entre DQ=0.70 et DQ=1.0
# avantageait systématiquement les mega-caps US (couverture yfinance/FMP
# parfaite) au détriment de mid-caps légitimes — biais structurel vers le
# top du SP500. Le coef est désormais quasi-neutre : 0.95 à 0.70 → 1.00 à
# 1.00 (5 pts d'écart max), juste assez pour départager des ex-aequo.
_DQ_MIN_COEF = 0.95

# Champs fondamentaux utilisés dans le scoring TITAN (métriques réparties
# sur les 6 piliers). data_quality = fraction non-None sur ces champs.
# Un ticker avec < _MIN_DATA_QUALITY est exclu du ranking → évite qu'un
# pennystock à trous API s'infiltre dans le top-20 via des scores neutres.
_TITAN_SCORING_FIELDS: tuple[str, ...] = (
    "return_on_equity",
    "operating_margin",
    "ev_to_ebitda",
    "forward_pe",
    "free_cash_flow",
    "debt_to_equity",
    "current_ratio",
    "recommendation_mean",
    "price_target_mean",
    # Lot 5 : ajout momentum_return_pct dans le calcul DQ. Un ticker sans
    # momentum live (universe stale, < 20j d'history) sera pénalisé par
    # data_quality au lieu d'être imputé silencieusement à neutre 50.
    "momentum_return_pct",
    # Lot 12 : Growth. revenue_growth plus fiable que earnings_growth
    # (earnings peut basculer de négatif → None).
    "revenue_growth",
)

# Overrides sector-specific — audit 2026-04-23.
# Pour Financial Services, 4 fields ne sont **pas applicables** structurellement :
#   • ev_to_ebitda     — pas de notion d'EBITDA industriel pour une banque
#   • current_ratio    — bilan bancaire classé en maturités, pas court/long terme
#   • free_cash_flow   — dominé par dépôts/prêts, pas capex → sens ambigu (30/69
#                        banques n'ont pas de FCF rempli par yfinance en prod)
#   • debt_to_equity   — leverage structurellement 8-12× (vs 1-2× industriels)
#                        → le percentile rank cross-universe classe toujours
#                        les banques "haut risque" à tort (19/69 sans D/E aussi)
#
# Les compter comme "missing" dans DQ pénalisait JPM/BAC/WFC/MS à 60 % alors
# qu'ils sont parfaitement renseignés sur les métriques applicables. Jeu réduit
# à 6 fields pour Financials.
_TITAN_SCORING_FIELDS_FINANCIALS: tuple[str, ...] = tuple(
    f for f in _TITAN_SCORING_FIELDS
    if f not in {"ev_to_ebitda", "current_ratio", "free_cash_flow", "debt_to_equity"}
)

# Alias pour les noms de secteur variants (yfinance → nom canonique).
_SECTOR_NORMALIZE: dict[str, str] = {
    "Financial Services": "Financials",
    "Financial": "Financials",
    "Financials": "Financials",
}


def _fields_for_sector(sector: str | None) -> tuple[str, ...]:
    """Retourne la liste de fields DQ applicable selon le secteur.

    Par défaut : jeu complet. Override pour Financials (exclut ev_to_ebitda et
    current_ratio, non applicables structurellement).
    """
    if sector is None:
        return _TITAN_SCORING_FIELDS
    normalized = _SECTOR_NORMALIZE.get(sector, sector)
    if normalized == "Financials":
        return _TITAN_SCORING_FIELDS_FINANCIALS
    return _TITAN_SCORING_FIELDS


# Audit 2026-04-23 : passage 0.60 → 0.70 après sector-aware DQ. Avec le jeu
# Financials réduit (8 fields au lieu de 10), les banques passent de 60 % à
# 75 % DQ — on peut serrer le filtre sans exclure de secteurs légitimes.
# À ce seuil, un seul ticker est exclu (FISV, sector=None + 40 % DQ).
_MIN_DATA_QUALITY = 0.70


def _percentile_rank(
    values: dict[str, float | None],
    higher_is_better: bool = True,
) -> dict[str, float | None]:
    """Percentile-rank cross-universe 0-100 (fractional ranking, ties = mid-rank).
    Plus robuste qu'un z-score quand les distributions ont des queues épaisses
    (cas des ratios financiers : EV/EBITDA peut atteindre 200+ sur une startup).

    • None stays None (signalé comme absent → imputé à l'étage au-dessus).
    • < 2 valeurs présentes → tout le monde reçoit 50 (pas de signal).
    • higher_is_better=False inverse le score (ex: pour Debt/Equity).
    """
    present = sorted([v for v in values.values() if v is not None and math.isfinite(v)])
    n = len(present)
    if n < 2:
        return {
            k: (_NEUTRAL_SCORE if (v is not None and math.isfinite(v)) else None)
            for k, v in values.items()
        }
    out: dict[str, float | None] = {}
    for k, v in values.items():
        if v is None or not math.isfinite(v):
            out[k] = None
            continue
        less  = sum(1 for x in present if x < v)
        equal = sum(1 for x in present if x == v)
        pct = (less + 0.5 * equal) / n * 100.0
        out[k] = pct if higher_is_better else (100.0 - pct)
    return out


# Seuil de bascule sector-relative → global. Sous ce nombre de tickers dans le
# secteur, le rank intra-secteur n'a pas de sens statistique (ties dominants,
# variance trop faible) → on retombe sur le rank global pour ce secteur.
#
# Audit S3.2 (2026-04-27) — relevé de 5 à 8. Avec n=5 le percentile rank ne
# produit que 5 buckets distincts (10/30/50/70/90), à peine plus expressif
# qu'un rank global avec ties. n=8 donne au minimum 8 buckets (12.5 % de
# résolution) — pas idéal mais signal moins bruité sur les petits secteurs
# (Real Estate, Utilities tournent autour de 10-15 tickers dans l'univers).
_MIN_SECTOR_SIZE_FOR_RELATIVE = 8


def _percentile_rank_by_sector(
    values: dict[str, float | None],
    sectors: dict[str, str],
    higher_is_better: bool = True,
) -> dict[str, float | None]:
    """Percentile-rank intra-secteur — chaque ticker comparé à ses pairs
    GICS, pas à l'univers entier.

    Pourquoi : l'univers est dominé par certains secteurs (Tech 88/480,
    Industrials 74/480) ; un ranking global pénalise systématiquement
    les secteurs structurellement différents (Utilities → ROE bas par
    nature de capital-intensive, REITs → leverage par nature). En
    sector-relative, NVDA est rankée vs autres Tech, NEE vs autres
    Utilities — chacun à armes égales contre ses vrais pairs.

    Fallback : si un secteur a < `_MIN_SECTOR_SIZE_FOR_RELATIVE` tickers
    avec valeur valide, on bascule sur le rank global pour ces tickers
    (intra-secteur dégénéré sinon : 3 tickers → seulement 0/50/100 possibles).
    """
    # 1. Group values by sector (en gardant les None)
    by_sector: dict[str, dict[str, float | None]] = {}
    for ticker, sec in sectors.items():
        if ticker not in values:
            continue
        by_sector.setdefault(sec, {})[ticker] = values[ticker]

    # 2. Compte des valeurs valides par secteur → décide rank intra ou global
    out: dict[str, float | None] = {}
    fallback_tickers: list[str] = []
    for sub in by_sector.values():
        n_valid = sum(
            1 for v in sub.values()
            if v is not None and math.isfinite(v)
        )
        if n_valid < _MIN_SECTOR_SIZE_FOR_RELATIVE:
            # Secteur trop petit → fallback global. On note les tickers à
            # rescorer ensemble depuis l'univers entier.
            fallback_tickers.extend(sub.keys())
            continue
        sub_ranked = _percentile_rank(sub, higher_is_better=higher_is_better)
        out.update(sub_ranked)

    # 3. Fallback global pour les secteurs trop petits — on rank parmi
    #    l'univers entier (pas seulement les fallback_tickers, sinon le
    #    référentiel serait biaisé par le seul échantillon dégénéré).
    if fallback_tickers:
        global_ranked = _percentile_rank(values, higher_is_better=higher_is_better)
        for t in fallback_tickers:
            out[t] = global_ranked.get(t)

    return out


# Audit S3.x (2026-04-27) — Lag de publication des fondamentaux.
# Les comptes annuels (10-K) sont déposés 60-90 jours après la clôture
# fiscale ; les trimestriels (10-Q) ~45 jours. Toute lecture YoY directe
# depuis yfinance peut donc inclure des chiffres pas encore publiés à la
# date du snapshot, créant un look-ahead silencieux dans Piotroski Y/Y.
#
# Mitigation : on n'autorise comme `yoy_row` qu'un snapshot suffisamment
# ancien pour que la période fiscale Y-1 ait été *publiquement disponible*
# au moment du snapshot recherché. 90 jours est l'intervalle typique
# 10-K pour les large-caps US (limite SEC à 60-90 j selon catégorie).
_FUNDAMENTAL_PUBLICATION_LAG_DAYS = 90


def _lookup_yoy_snapshot(
    ticker: str,
    *,
    as_of: date | None = None,
    publication_lag_days: int = _FUNDAMENTAL_PUBLICATION_LAG_DAYS,
) -> dict[str, Any] | None:
    """Retourne le row du ticker dans le snapshot Y-1 (~365 jours avant `as_of`).

    Anti-lookahead : on n'accepte qu'un snapshot dont la date est antérieure
    de `publication_lag_days` à `as_of` (les fondamentaux Y-1 doivent avoir
    été *publiés* au moment du snapshot considéré, pas seulement *clos*).

    Tolérance : on accepte un snapshot entre 300 et 430 jours d'ancienneté
    (buffer jours fériés / gaps), ET au moins `publication_lag_days` plus
    vieux que `as_of`. None si aucun snapshot satisfait les deux gates.

    Args:
        ticker : ticker recherché.
        as_of : date du contexte (défaut today). En backtest, c'est la
                signal_date courante — pas la date d'aujourd'hui.
        publication_lag_days : marge minimum entre `as_of` et le snapshot
                               retourné (défaut 90 j = 10-K horizon).
    """
    from datetime import date, timedelta
    try:
        from modules import universe_history
    except ImportError:
        return None

    if as_of is None:
        as_of = date.today()
    target_min = as_of - timedelta(days=430)
    target_max_yoy = as_of - timedelta(days=300)
    # Anti-lookahead : pas plus récent que (as_of - lag).
    target_max_lag = as_of - timedelta(days=max(0, publication_lag_days))
    target_max = min(target_max_yoy, target_max_lag)
    if target_max < target_min:
        # Lag plus large que la fenêtre → aucun snapshot éligible.
        return None

    try:
        all_dates = universe_history.list_snapshots()
    except Exception:
        return None

    for d in reversed(all_dates):
        if d > target_max:
            continue
        if d < target_min:
            break
        try:
            snap = universe_history.read_snapshot(d)
        except Exception:
            continue
        if not snap:
            continue
        tickers = snap.get("tickers") or {}
        row = tickers.get(ticker)
        if row:
            return row
    return None


def _piotroski_f_score_absolute(
    row: dict[str, Any],
    yoy_row: dict[str, Any] | None = None,
) -> tuple[int, int, dict[str, bool | None]]:
    """Calcule le F-Score Piotroski — jusqu'à 9/9 critères si yoy_row fourni.

    Critères ABSOLUS (1 snapshot) :
      F1 : ROA > 0                  (return_on_assets > 0)
      F2 : Operating CF > 0         (operating_cash_flow > 0)
      F4 : Operating CF > Net Income (qualité des earnings)
      F7 : Current Ratio > 1        (liquidité court terme)

    Critères Y/Y (Lot 13 — activés si yoy_row fourni) :
      F3 : ROA Y/Y improved            (ROA_now > ROA_year_ago)
      F5 : Long-term debt decreased    (debt_to_equity_now < D/E_year_ago)
      F6 : Current ratio improved      (CR_now > CR_year_ago)
      F8 : No new shares issued        (shares_outstanding_now ≤ shares_year_ago × 1.02)
      F9 : Gross margin improved       (gross_margin_now > gross_margin_year_ago)

    Returns:
        (n_passed, n_evaluated, breakdown)
        - n_passed : nombre de critères vérifiés.
        - n_evaluated : nombre de critères ayant pu être calculés.
        - breakdown : {f1: True/False/None, ...} pour audit UI.
    """
    roa = _safe_float(row.get("return_on_assets"))
    ocf = _safe_float(row.get("operating_cash_flow"))
    ni  = _safe_float(row.get("net_income"))
    cr  = _safe_float(row.get("current_ratio"))

    f1 = (roa > 0) if roa is not None else None
    f2 = (ocf > 0) if ocf is not None else None
    f4 = (ocf > ni) if (ocf is not None and ni is not None) else None
    f7 = (cr > 1.0) if cr is not None else None

    breakdown: dict[str, bool | None] = {
        "f1_roa_positive":      f1,
        "f2_ocf_positive":      f2,
        "f4_ocf_gt_ni":         f4,
        "f7_current_ratio_gt_1": f7,
    }

    # ── Critères Y/Y (activés si historique dispo) ──────────────────────
    # Garde tol 1.02× sur shares (F8) pour accommoder le bruit round-lot (buybacks
    # partiels, grants RSU normaux).
    _SHARES_TOL = 1.02
    if yoy_row is not None:
        roa_y = _safe_float(yoy_row.get("return_on_assets"))
        d2e_now = _safe_float(row.get("debt_to_equity"))
        d2e_y = _safe_float(yoy_row.get("debt_to_equity"))
        cr_y = _safe_float(yoy_row.get("current_ratio"))
        sh_now = _safe_float(row.get("shares_outstanding"))
        sh_y = _safe_float(yoy_row.get("shares_outstanding"))
        gm_now = _safe_float(row.get("gross_margin"))
        gm_y = _safe_float(yoy_row.get("gross_margin"))

        f3 = (roa > roa_y) if (roa is not None and roa_y is not None) else None
        f5 = (d2e_now < d2e_y) if (d2e_now is not None and d2e_y is not None) else None
        f6 = (cr > cr_y) if (cr is not None and cr_y is not None) else None
        f8 = (sh_now <= sh_y * _SHARES_TOL) if (sh_now is not None and sh_y is not None) else None
        f9 = (gm_now > gm_y) if (gm_now is not None and gm_y is not None) else None

        breakdown.update({
            "f3_roa_improved_yoy":        f3,
            "f5_debt_decreased_yoy":      f5,
            "f6_current_ratio_improved_yoy": f6,
            "f8_no_new_shares_yoy":       f8,
            "f9_gross_margin_improved_yoy": f9,
        })

    evaluated = [v for v in breakdown.values() if v is not None]
    return sum(1 for v in evaluated if v), len(evaluated), breakdown


def _piotroski_score_pillar(
    row: dict[str, Any],
    *,
    as_of: date | None = None,
    publication_lag_days: int = _FUNDAMENTAL_PUBLICATION_LAG_DAYS,
) -> tuple[float, dict[str, Any]]:
    """Convertit le F-Score brut en score 0-100 normalisé par #critères évalués.

    Si 0 critère évaluable → neutral 50 (pas de pénalité, signal absent).
    Sinon → (n_passed / n_evaluated) × 100.

    Lot 14 : lit les champs `*_prev_year` scrappés par yfinance (tk.balance_sheet +
    tk.financials) — ça active F3/F5/F6/F8/F9 immédiatement, sans attendre 1 an
    d'universe_history. Fallback sur `_lookup_yoy_snapshot` si les champs Y-1
    sont absents (ex: IPO < 1 an, scraping annuels KO).
    """
    ticker = row.get("ticker")

    # Préférence : champs scrappés au niveau du row (disponibles immédiatement).
    prev_fields = (
        "return_on_assets_prev_year",
        "debt_to_equity_prev_year",
        "current_ratio_prev_year",
        "shares_outstanding_prev_year",
        "gross_margin_prev_year",
    )
    if any(row.get(f) is not None for f in prev_fields):
        # Audit S3.x rigoureux (2026-04-27) — gate par-ticker via
        # `fundamentals_period_end_y1`. Si la période fiscale Y-1 + LAG
        # n'était pas encore publiée à `as_of` (cas backtest), on refuse
        # ce Y-1 — sinon look-ahead silencieux.
        if as_of is not None and publication_lag_days > 0:
            from datetime import date as _date
            from datetime import timedelta as _td
            period_end_y1 = row.get("fundamentals_period_end_y1")
            if isinstance(period_end_y1, str):
                try:
                    pe = _date.fromisoformat(period_end_y1[:10])
                    publish_date = pe + _td(days=publication_lag_days)
                    if publish_date > as_of:
                        # Y-1 pas encore publiable → fallback sur lookup snapshot
                        # (qui peut lui-même retourner None si la fenêtre est
                        # fermée).
                        yoy_row = (
                            _lookup_yoy_snapshot(
                                ticker, as_of=as_of,
                                publication_lag_days=publication_lag_days,
                            ) if ticker else None
                        )
                    else:
                        yoy_row = {
                            "return_on_assets":   row.get("return_on_assets_prev_year"),
                            "debt_to_equity":     row.get("debt_to_equity_prev_year"),
                            "current_ratio":      row.get("current_ratio_prev_year"),
                            "shares_outstanding": row.get("shares_outstanding_prev_year"),
                            "gross_margin":       row.get("gross_margin_prev_year"),
                        }
                except ValueError:
                    yoy_row = {
                        "return_on_assets":   row.get("return_on_assets_prev_year"),
                        "debt_to_equity":     row.get("debt_to_equity_prev_year"),
                        "current_ratio":      row.get("current_ratio_prev_year"),
                        "shares_outstanding": row.get("shares_outstanding_prev_year"),
                        "gross_margin":       row.get("gross_margin_prev_year"),
                    }
            else:
                # Pas de date fiscale → comportement legacy (accept).
                yoy_row = {
                    "return_on_assets":   row.get("return_on_assets_prev_year"),
                    "debt_to_equity":     row.get("debt_to_equity_prev_year"),
                    "current_ratio":      row.get("current_ratio_prev_year"),
                    "shares_outstanding": row.get("shares_outstanding_prev_year"),
                    "gross_margin":       row.get("gross_margin_prev_year"),
                }
        else:
            yoy_row = {
                "return_on_assets":   row.get("return_on_assets_prev_year"),
                "debt_to_equity":     row.get("debt_to_equity_prev_year"),
                "current_ratio":      row.get("current_ratio_prev_year"),
                "shares_outstanding": row.get("shares_outstanding_prev_year"),
                "gross_margin":       row.get("gross_margin_prev_year"),
            }
    else:
        # Fallback legacy : snapshot universe_history Y-1 (anti-lookahead).
        yoy_row = (
            _lookup_yoy_snapshot(
                ticker,
                as_of=as_of,
                publication_lag_days=publication_lag_days,
            )
            if ticker else None
        )

    n_passed, n_evaluated, breakdown = _piotroski_f_score_absolute(row, yoy_row)
    if n_evaluated == 0:
        return _NEUTRAL_SCORE, {
            "f_score":         None,
            "f_score_max":     0,
            "f_score_evaluated": 0,
            "f_score_breakdown": breakdown,
        }
    score = (n_passed / n_evaluated) * 100.0
    return score, {
        "f_score":           n_passed,
        "f_score_max":       n_evaluated,
        "f_score_evaluated": n_evaluated,
        "f_score_breakdown": breakdown,
    }


def _pillar_score(parts: list[float | None]) -> float:
    """Moyenne arithmétique des composantes non-None du pilier.
    Si TOUTES absentes → score neutre 50 (imputation finale).
    Règle anti-data-sale : aucun ticker n'est puni d'office.
    """
    vals = [p for p in parts if p is not None]
    if not vals:
        return _NEUTRAL_SCORE
    return sum(vals) / len(vals)


def _compute_data_quality(ticker_row: dict[str, Any]) -> float:
    """Fraction de champs TITAN non-None sur le jeu applicable au secteur.
    Retourne un float dans [0, 1] — 0 = aucune donnée, 1 = complet.

    Sector-aware : pour Financials, le dénominateur exclut ev_to_ebitda et
    current_ratio (non applicables structurellement). Évite que BAC/JPM soient
    injustement pénalisés à 60 % DQ à cause de fields non-applicables.
    """
    fields = _fields_for_sector(ticker_row.get("sector"))
    if not fields:
        return 0.0
    present = sum(
        1 for f in fields
        if _safe_float(ticker_row.get(f)) is not None
    )
    return present / len(fields)


def _weighted_mean_scores(tickers: list[dict[str, Any]]) -> dict[str, float]:
    """Moyenne pondérée par market cap des sous-scores TITAN sur un secteur.

    Smart-beta institutionnel : un small-cap avec un score extrême ne doit pas
    dominer la rotation d'un secteur dominé par des mega-caps.

    Fallback si aucun market cap → moyenne simple.
    Fallback si aucune valeur → score neutre 50.
    """
    score_keys = (
        "quality_score", "value_score", "risk_score",
        "sentiment_score", "titan_composite_score",
    )
    out: dict[str, float] = {}
    for key in score_keys:
        w_num = 0.0
        w_den = 0.0
        simple: list[float] = []
        for t in tickers:
            v = _safe_float(t.get(key))
            if v is None:
                continue
            mcap = _safe_float(t.get("market_cap"))
            w = mcap if (mcap is not None and mcap > 0) else 0.0
            simple.append(v)
            w_num += v * w
            w_den += w
        if w_den > 0:
            out[key] = round(w_num / w_den, 2)
        elif simple:
            out[key] = round(sum(simple) / len(simple), 2)
        else:
            out[key] = _NEUTRAL_SCORE
    return out


def _score_universe(tickers_map: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Calcule les sous-scores TITAN (0-100) par ticker via percentile-ranking
    cross-universe sur chaque métrique brute, puis agrégation en 4 piliers
    pondérés pour produire le `titan_composite_score`.

    Filtrage data quality : tickers avec data_quality < _MIN_DATA_QUALITY (0.6)
    EXCLUS du résultat — trop de trous fondamentaux pour un ranking fiable.
    Ils restent dans universe.json mais ne participent pas aux recos.

    Pansement Sentiment : si un ticker n'a ni recommendation_mean ni
    price_target_mean, TITAN composite renormalisé Q/V/R (1/3 chacun).
    Weight_mode tracé pour audit.

    Retourne un *nouveau* dict {ticker: {...original, *_score: ...}},
    sans muter l'input.
    """
    keys_all = list(tickers_map.keys())
    if not keys_all:
        return {}

    # ── Gate data_quality : seulement les tickers suffisamment remplis ──
    keys = [k for k in keys_all if _compute_data_quality(tickers_map[k]) >= _MIN_DATA_QUALITY]
    n_excluded = len(keys_all) - len(keys)
    if n_excluded > 0:
        logger.info(
            f"[SectorMetrics] Scoring : {n_excluded}/{len(keys_all)} tickers exclus "
            f"(data_quality < {_MIN_DATA_QUALITY:.0%})"
        )
    if not keys:
        return {}

    # ── Étape 1 — Extraction des séries brutes cross-universe ───────────
    roe       = {k: _safe_float(tickers_map[k].get("return_on_equity")) for k in keys}
    op_margin = {k: _safe_float(tickers_map[k].get("operating_margin")) for k in keys}
    # Lot 14.1 — gross_margin absolu (moat indicator, Buffett/Munger).
    # Un gross margin élevé = pricing power = barrière à l'entrée. Warren
    # Buffett considère >40% comme signe de moat durable.
    gross_margin = {k: _safe_float(tickers_map[k].get("gross_margin")) for k in keys}

    # EV/EBITDA et Forward P/E ≤ 0 → None.
    # Un EV/EBITDA négatif (CRWD=-2179, BA=-61, BRK-B=-2) signale un EBITDA
    # négatif (perte structurelle), pas un actif "ultra cheap". Le percentile
    # rank avec higher_is_better=False les classerait au top Value alors qu'ils
    # méritent un signal d'absence (None → imputé pilier neutre par fallback).
    # Idem Forward P/E < 0 = pertes attendues (WBD=-1649, MRNA=-12).
    ev_ebitda = {
        k: (v if (v is not None and v > 0) else None)
        for k, v in ((k, _safe_float(tickers_map[k].get("ev_to_ebitda"))) for k in keys)
    }
    fwd_pe = {
        k: (v if (v is not None and v > 0) else None)
        for k, v in ((k, _safe_float(tickers_map[k].get("forward_pe"))) for k in keys)
    }

    # FCF yield = FCF / Market Cap. PAS de fallback OCF → biais haussier
    # sur les capital-intensive (Utilities/Energy/Telecom : OCF ≫ FCF car
    # leurs CapEx massifs ne sont pas soustraits). On préfère None honnête
    # → le pilier Value reposera sur EV/EBITDA pour ces tickers.
    # Garde stricte : mcap fini ET > 0, fcf fini → évite inf/NaN dans percentile-rank.
    fcf_yield: dict[str, float | None] = {}
    for k in keys:
        t = tickers_map[k]
        fcf = _safe_float(t.get("free_cash_flow"))
        mcap = _safe_float(t.get("market_cap"))
        if fcf is None or mcap is None or not math.isfinite(mcap) or mcap <= 0 or not math.isfinite(fcf):
            fcf_yield[k] = None
            continue
        ratio = fcf / mcap
        fcf_yield[k] = ratio if math.isfinite(ratio) else None

    # Lot 14.1 — PEG ratio (growth-adjusted P/E). Acheter de la croissance pas
    # chère. PEG < 1 = bargain, 1-2 = raisonnable, >3 = cher. Déjà scrapé
    # yfinance.  Exclut PEG ≤ 0 (pertes attendues, mauvais signal pour Value).
    peg = {
        k: (v if (v is not None and v > 0) else None)
        for k, v in ((k, _safe_float(tickers_map[k].get("peg_ratio"))) for k in keys)
    }

    # Lot 14.1 — Earnings yield = 1/trailing_pe. Plus robuste aux outliers que
    # trailing_pe brut (les très bas P/E dominent l'échelle). Signal de valeur
    # Graham-style. Exclut PE ≤ 0 comme EV/EBITDA.
    earnings_yield: dict[str, float | None] = {}
    for k in keys:
        pe = _safe_float(tickers_map[k].get("trailing_pe"))
        earnings_yield[k] = (1.0 / pe) if (pe is not None and pe > 0) else None

    d2e        = {k: _safe_float(tickers_map[k].get("debt_to_equity")) for k in keys}
    curr_ratio = {k: _safe_float(tickers_map[k].get("current_ratio")) for k in keys}

    reco   = {k: _safe_float(tickers_map[k].get("recommendation_mean")) for k in keys}
    upside = {k: _upside_pct(tickers_map[k]) for k in keys}

    # Lot 5 — Momentum 12M-1M : return brut + risk-adjusted (≈ Sharpe).
    # Source : universe.json déjà enrichi par universe_engine via momentum module.
    # Les ranks sont GLOBAUX (momentum est une anomalie cross-sectionnelle
    # market-wide — Jegadeesh-Titman 1993, Asness 1994).
    mom_return = {k: _safe_float(tickers_map[k].get("momentum_return_pct")) for k in keys}
    mom_risk_adj = {k: _safe_float(tickers_map[k].get("momentum_risk_adjusted")) for k in keys}
    # Lot 14 — 52-week high ratio (last/max sur 272d). Proche 1.0 = stock à ou
    # près de son 52wh → anti-reversal bullish (Grinblatt-Han 2002). Décorrélé
    # du return 12M-1M car capte l'état *final* vs la pente.
    mom_52wh = {k: _safe_float(tickers_map[k].get("momentum_high_52w_ratio")) for k in keys}

    # Lot 12 — Growth : Revenue CAGR + Earnings CAGR YoY. Source yfinance .info.
    # Rank SECTOR-RELATIVE : les mega-caps matures (AAPL, BAC) ont structurellement
    # des croissances < 10 %, les small/mid tech 20-50 %. Comparer intra-secteur
    # pour identifier les "best growers" de leur classe.
    rev_growth = {k: _safe_float(tickers_map[k].get("revenue_growth")) for k in keys}
    eps_growth = {k: _safe_float(tickers_map[k].get("earnings_growth")) for k in keys}

    # ── Étape 1bis — Winsorization p1/p99 ───────────────────────────────
    # Borne les outliers (MAS ROE=71x, CLX D/E=9000+) avant percentile-rank.
    # Le rank reste max pour les outliers mais leur valeur brute exposée dans
    # le payload est crédible, et les agrégats market-cap sectoriels ne sont
    # plus dominés par un seul ticker pathologique.
    #
    # INTRA-SECTEUR pour Q/V/R (mêmes métriques dont les distributions diffèrent
    # structurellement par secteur : Tech ROE haut, Utilities ROE bas, REITs
    # leverage élevé) — évite que les p1/p99 globaux soient dominés par les
    # secteurs majoritaires et laissent passer des outliers intra-secteur.
    #
    # On ne winsorise PAS reco (échelle bornée 1-5) ni upside (sémantique :
    # un upside +200 % est un signal légitime de sur-réaction analyste).
    # Idem mom_return : un ticker à +300 % en 12M (NVDA-style) est un signal
    # cross-sectionnel légitime à NE PAS écraser.
    sectors_raw = {k: (tickers_map[k].get("sector") or "Unknown") for k in keys}
    roe            = _winsorize_by_sector(roe,            sectors_raw)
    op_margin      = _winsorize_by_sector(op_margin,      sectors_raw)
    gross_margin   = _winsorize_by_sector(gross_margin,   sectors_raw)
    ev_ebitda      = _winsorize_by_sector(ev_ebitda,      sectors_raw)
    fwd_pe         = _winsorize_by_sector(fwd_pe,         sectors_raw)
    fcf_yield      = _winsorize_by_sector(fcf_yield,      sectors_raw)
    peg            = _winsorize_by_sector(peg,            sectors_raw)
    earnings_yield = _winsorize_by_sector(earnings_yield, sectors_raw)
    d2e            = _winsorize_by_sector(d2e,            sectors_raw)
    curr_ratio     = _winsorize_by_sector(curr_ratio,     sectors_raw)
    rev_growth     = _winsorize_by_sector(rev_growth,     sectors_raw)
    eps_growth     = _winsorize_by_sector(eps_growth,     sectors_raw)
    # Risk-adjusted momentum : winsorisé GLOBAL car le momentum est une anomalie
    # cross-sectionnelle (pas sector-specific). Même raisonnement que pour le rank.
    mom_risk_adj = _winsorize(mom_risk_adj)

    # ── Étape 2 — Percentile-ranks ──────────────────────────────────────
    # SECTOR-RELATIVE pour Quality / Value / Risk : chaque ticker comparé
    # à ses pairs GICS, pas à l'univers entier. Évite le biais qui sur-pénalise
    # Utilities (ROE bas par nature) ou REITs (leverage par nature) face aux
    # Tech (ROE haut par modèle business).
    #
    # GLOBAL pour Sentiment : consensus analyste + upside target sont par
    # design des évaluations relatives au benchmark mondial (les analystes
    # comparent une action à toutes les opportunités), pas à son secteur.
    sectors_map = sectors_raw  # même mapping que pour le winsorize sectoriel

    roe_r      = _percentile_rank_by_sector(roe,       sectors_map, higher_is_better=True)
    opm_r      = _percentile_rank_by_sector(op_margin, sectors_map, higher_is_better=True)
    gm_r       = _percentile_rank_by_sector(gross_margin, sectors_map, higher_is_better=True)
    ev_r       = _percentile_rank_by_sector(ev_ebitda, sectors_map, higher_is_better=False)
    fpe_r      = _percentile_rank_by_sector(fwd_pe,    sectors_map, higher_is_better=False)
    fcf_r      = _percentile_rank_by_sector(fcf_yield, sectors_map, higher_is_better=True)
    peg_r      = _percentile_rank_by_sector(peg,       sectors_map, higher_is_better=False)
    ey_r       = _percentile_rank_by_sector(earnings_yield, sectors_map, higher_is_better=True)
    d2e_r      = _percentile_rank_by_sector(d2e,       sectors_map, higher_is_better=False)
    curr_r     = _percentile_rank_by_sector(curr_ratio, sectors_map, higher_is_better=True)
    reco_r     = _percentile_rank(reco,      higher_is_better=False)  # GLOBAL
    upside_r   = _percentile_rank(upside,    higher_is_better=True)   # GLOBAL

    # Lot 5 — Momentum : ranks GLOBAUX (anomalie cross-sectionnelle).
    mom_ret_r   = _percentile_rank(mom_return,   higher_is_better=True)
    mom_ra_r    = _percentile_rank(mom_risk_adj, higher_is_better=True)
    mom_52wh_r  = _percentile_rank(mom_52wh,     higher_is_better=True)

    # Lot 12 — Growth : sector-relative (un Tech 15 % = moyen, un Utility 15 %
    # = superstar).
    rev_r = _percentile_rank_by_sector(rev_growth, sectors_map, higher_is_better=True)
    eps_r = _percentile_rank_by_sector(eps_growth, sectors_map, higher_is_better=True)

    # Lot 16 — Revisions : pilier dédié (analyste upgrades/downgrades 90j +
    # earnings beat rate + surprise avg). Sources scrappées par yfinance_provider
    # via _scrape_revisions_and_earnings. Calcul dans modules/revisions_score.
    revisions_pillar = compute_revisions_pillar(
        {k: tickers_map[k] for k in keys}
    )

    # ── Étape 3 — Assemblage sous-scores + composite TITAN ──────────────
    scored: dict[str, dict[str, Any]] = {}
    for k in keys:
        t_base = tickers_map[k]

        # QUALITY — profitabilité & efficience capital + moat (gross margin).
        # Lot 14.1 : +gross_margin (pricing power / barrière d'entrée, Buffett).
        q = _pillar_score([roe_r[k], opm_r[k], gm_r[k]])

        # VALUE — EV/EBITDA prioritaire ; fallback Forward P/E ; + FCF yield +
        # PEG (growth-adjusted) + Earnings yield (Graham-style robust).
        # Lot 14.1 : +peg_r +ey_r pour un signal Value plus dense.
        val_primary = ev_r[k] if ev_r[k] is not None else fpe_r[k]
        v = _pillar_score([val_primary, fcf_r[k], peg_r[k], ey_r[k]])

        # RISK — bilan sain (low leverage, bonne liquidité). Higher = SAFER.
        r = _pillar_score([d2e_r[k], curr_r[k]])

        # SENTIMENT — consensus analyste + upside sur target.
        # Si les DEUX sources brutes sont absentes (typique FMP stable), on
        # bascule sur des poids renormalisés Q/V/R/M pour éviter d'ancrer 20%
        # du score à la valeur neutre 50 (qui ne discriminerait rien).
        reco_raw = reco[k]
        upside_raw = upside[k]
        sentiment_available = (reco_raw is not None) or (upside_raw is not None)

        # MOMENTUM — return 12M-1M (Jegadeesh-Titman) + Sharpe approx + 52wh ratio
        # (Grinblatt-Han anti-reversal, Lot 14). _pillar_score retourne 50 si
        # tout None (universe stale).
        m = _pillar_score([mom_ret_r[k], mom_ra_r[k], mom_52wh_r[k]])

        # PIOTROSKI — F-Score 4 critères absolus (Lot 8). Les 5 Y/Y attendent
        # ≥ 2 ans d'historique Lot 6.
        p, p_diag = _piotroski_score_pillar(t_base)

        # GROWTH — Revenue CAGR + Earnings CAGR (Lot 12, Novy-Marx/AQR).
        g = _pillar_score([rev_r[k], eps_r[k]])

        # REVISIONS — Lot 16. Pilier dédié (poids 12 %). Capture upgrades/
        # downgrades 90j + beat rate 8Q + surprise avg 4Q.
        rev_pack = revisions_pillar.get(k) or {}
        rv = float(rev_pack.get("revisions_score") or _NEUTRAL_SCORE)

        # INSIDER — Lot 17. Pilier C-level/director smart money (poids 8 %).
        # Lit `insider_score` directement depuis le row si déjà enrichi par
        # `enrich_universe_with_insider()`. Sinon neutre 50.
        ins_raw = _safe_float(t_base.get("insider_score"))
        ins = ins_raw if ins_raw is not None else _NEUTRAL_SCORE

        if sentiment_available:
            s = _pillar_score([reco_r[k], upside_r[k]])
            composite = (
                _W_TITAN_QUALITY   * q
                + _W_TITAN_VALUE     * v
                + _W_TITAN_RISK      * r
                + _W_TITAN_SENTIMENT * s
                + _W_TITAN_MOMENTUM  * m
                + _W_TITAN_PIOTROSKI * p
                + _W_TITAN_GROWTH    * g
                + _W_TITAN_REVISIONS * rv
                + _W_TITAN_INSIDER   * ins
            )
            weight_mode = "full"
        else:
            # Pansement : Sentiment indisponible → renormalisation 8 piliers.
            s = _NEUTRAL_SCORE  # reporté pour traçabilité, non utilisé dans composite
            composite = (
                _W_TITAN_Q_NO_SENTIMENT  * q
                + _W_TITAN_V_NO_SENTIMENT  * v
                + _W_TITAN_R_NO_SENTIMENT  * r
                + _W_TITAN_M_NO_SENTIMENT  * m
                + _W_TITAN_P_NO_SENTIMENT  * p
                + _W_TITAN_G_NO_SENTIMENT  * g
                + _W_TITAN_RV_NO_SENTIMENT * rv
                + _W_TITAN_IN_NO_SENTIMENT * ins
            )
            weight_mode = "no_sentiment"

        # ── Lot 14.1 — Cross-signal adjustments ────────────────────────────
        # Signaux composites typiques de la littérature LT (QARP/GARP, value
        # trap detection). Appliqués AVANT le dq_coef pour que le ranking soit
        # influencé mais que la pénalité DQ s'applique quand même.
        #
        # tilt_adjust cumule les bonus (≤0 → pas touché, >0 → meilleur stock).
        tilt_adjust = 0.0
        flags: list[str] = []

        # Cheap-junk filter : Value haut + Quality faible = value trap
        # (P/E bas parce que earnings pourrissent). Penalty proportionnelle
        # à la force du signal value + misère quality.
        if v >= 80.0 and q < 30.0:
            tilt_adjust -= 10.0
            flags.append("cheap_junk")
        # Value haut + Momentum négatif = falling knife
        if v >= 80.0 and m < 30.0:
            tilt_adjust -= 6.0
            flags.append("falling_knife")

        # QARP — Quality At Reasonable Price (Novy-Marx 2013, AQR).
        # Stock qui est TRÈS bon sur Quality ET pas cher → bonus. Académiquement
        # le combo le plus robuste sur 30 ans de backtest.
        if q >= 70.0 and v >= 70.0:
            tilt_adjust += 5.0
            flags.append("qarp")

        # GARP — Growth At Reasonable Price (Peter Lynch). Growth + Value
        # combinés (évite d'acheter de la growth à tout prix).
        if g >= 70.0 and v >= 70.0:
            tilt_adjust += 3.0
            flags.append("garp")

        # Consistency bonus : si les 7 piliers sont homogènes (faible écart-type)
        # → stock équilibré, moins de concentration de risque. Un ticker à
        # [70,75,70,72,78,76,74] est plus robuste qu'un [95,40,90,30,95,50,90].
        pillars_valid = [x for x in (q, v, r, s, m, p, g) if x is not None]
        if len(pillars_valid) >= 6:
            try:
                import statistics as _stat
                pillars_std = _stat.pstdev(pillars_valid)
                if pillars_std < 15.0:
                    tilt_adjust += 2.0
                    flags.append("consistent")
            except Exception:
                pass

        composite = max(0.0, min(100.0, composite + tilt_adjust))

        # Pondération par data_quality — pénalise doucement les profils incomplets.
        # On expose les 2 valeurs pour audit : composite "brut" (avant pénalité,
        # comparable au TITAN historique) et composite "final" (après, utilisé
        # pour le ranking en production).
        dq = _compute_data_quality(t_base)
        dq_coef = _DQ_MIN_COEF + (1.0 - _DQ_MIN_COEF) * dq
        composite_weighted = composite * dq_coef

        scored[k] = {
            **t_base,
            "quality_score":            round(q, 2),
            "value_score":              round(v, 2),
            "risk_score":               round(r, 2),
            "sentiment_score":          round(s, 2),
            "momentum_score":           round(m, 2),
            "piotroski_score":          round(p, 2),
            "growth_score":             round(g, 2),
            # Lot 16 — Revisions pillar.
            "revisions_score":          round(rv, 2),
            "revisions_components":     rev_pack.get("revisions_components"),
            "revisions_data_quality":   rev_pack.get("revisions_data_quality"),
            # Lot 17 — Insider pillar (smart money).
            "insider_score":            round(ins, 2),
            "f_score":                  p_diag.get("f_score"),
            "f_score_max":              p_diag.get("f_score_max"),
            "f_score_breakdown":        p_diag.get("f_score_breakdown"),
            "titan_composite_raw":      round(composite, 2),
            "titan_composite_score":    round(composite_weighted, 2),
            "titan_weight_mode":        weight_mode,
            "titan_tilt_adjust":        round(tilt_adjust, 2),
            "titan_tilt_flags":         flags,
            "data_quality":             round(dq, 3),
            "data_quality_coef":        round(dq_coef, 3),
        }
    return scored
