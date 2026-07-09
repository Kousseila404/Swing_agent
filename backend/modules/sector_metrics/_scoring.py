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
import statistics
from bisect import bisect_left, bisect_right
from datetime import UTC, date, datetime
from typing import Any

from modules.log import logger
from modules.revisions_score import compute_revisions_pillar

from ._utils import _safe_float, _upside_pct, _winsorize, _winsorize_by_sector


def _parse_fetched_at_age_days(
    fetched_at: Any,
    *,
    as_of: date | None = None,
) -> float | None:
    """Calcule l'âge (en jours) du `fetched_at` ISO d'un ticker vs `as_of`
    (ou today si None). Retourne None si parsing impossible.

    Format attendu : "YYYY-MM-DDTHH:MM:SSZ" (UTC). Tolère le suffixe ±HH:MM
    via `datetime.fromisoformat` après normalisation du Z.

    Garde-fous :
      • Négatif (clock skew, fetched_at dans le futur) → 0.0.
      • Round à 1 décimale (12.3 jours suffit, 12.345 est faux signal).
    """
    if not isinstance(fetched_at, str) or not fetched_at:
        return None
    try:
        # ISO normalisation : "Z" → "+00:00" pour fromisoformat.
        clean = fetched_at.replace("Z", "+00:00")
        dt_fetched = datetime.fromisoformat(clean)
        # Référentiel : on compare en UTC. Si as_of est une date, on la
        # convertit en datetime UTC (00:00:00) ; sinon now(UTC).
        if as_of is None:
            now_dt = datetime.now(UTC)
        else:
            now_dt = datetime(
                as_of.year, as_of.month, as_of.day, tzinfo=UTC,
            )
        delta_seconds = (now_dt - dt_fetched).total_seconds()
        age_days = max(0.0, delta_seconds / 86400.0)
        return round(age_days, 1)
    except (ValueError, TypeError):
        return None


def _parse_period_end_age_days(
    period_end: Any,
    *,
    as_of: date | None = None,
) -> float | None:
    """Bug #7 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-7) — âge du report fiscal vs as_of.

    `_parse_fetched_at_age_days` mesure quand on a *téléchargé* la donnée,
    pas quand le rapport financier sous-jacent a été *clos*. Un fetch aujourd'hui
    d'un EPS Q3-2024 a fetched_at_age=0 mais report_age ≈ 240 jours. Pour
    Piotroski Y/Y et tout pilier dépendant de la période fiscale, c'est cette
    métrique qui compte.

    Format attendu : "YYYY-MM-DD" (date) ou "YYYY-MM-DDT..." (datetime).
    Retourne None si parsing impossible.
    """
    if not isinstance(period_end, str) or not period_end:
        return None
    try:
        pe = date.fromisoformat(period_end[:10])
    except (ValueError, TypeError):
        return None
    ref = as_of if as_of is not None else date.today()
    delta = (ref - pe).days
    return round(max(0.0, float(delta)), 1)

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
#
# Lot 18 (2026-07-09) — IC diagnostic empirique sur 44 snapshots (2026-04-22→06-05).
# Sentiment IC20j = −0.108 (19 % fenêtres positives) → seul pilier activement
# nuisible à tous les horizons. Neutralisé : poids 3 % → 0 %.
# Redistribution : Momentum +2 % (IC=+0.119, meilleur prédicteur), Revisions +1 %
# (IC=+0.075, le plus stable, 100 % fenêtres positives).
# Guard : Quality/Value NON réduits — IC négatif régime-bull (tautologique),
# protection bear prouvée académiquement. Un re-rééquilibrage durable requiert
# ≥ 60 jours + plusieurs régimes via WFO.
#
# Lot 19 (2026-07-09) — IC diagnostic sur 78 snapshots (2026-04-22→07-09).
# Insider : IC20j = −0.033 (20 % positif), IC30j = −0.040 (4 % positif) →
# nuisible aux deux horizons, même profil que Sentiment. 8 % → 4 %.
# Piotroski : IC20j = +0.028 (74 % positif), IC30j = +0.038 (98 % positif) →
# le signal le plus STABLE de l'univers (near-zero variance d'IC entre fenêtres).
# Signal fondamental académique robuste (Piotroski 2000, répliqué 20+ ans).
# 9 % → 13 % (+4 % récupérés de Insider).
# Composite IC ≈ 0 (20j) / −0.010 (30j) → Insider le diluait autant que Sentiment.
_W_TITAN_QUALITY    = 0.18
_W_TITAN_VALUE      = 0.13
_W_TITAN_RISK       = 0.10
_W_TITAN_SENTIMENT  = 0.00   # neutralisé — IC −0.108 (Lot 18)
_W_TITAN_MOMENTUM   = 0.17   # +0.02 (Lot 18, IC +0.119)
_W_TITAN_PIOTROSKI  = 0.13   # +0.04 (Lot 19, IC +0.038, 98 % fenêtres+)
_W_TITAN_GROWTH     = 0.13
_W_TITAN_REVISIONS  = 0.12   # +0.01 (Lot 18, IC +0.075 stable)
_W_TITAN_INSIDER    = 0.04   # −0.04 (Lot 19, IC −0.040, 4 % fenêtres+)

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
# top du SP500.
#
# Bug #24 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-24) — 0.95 était quasi-neutre (1.5 pts d'écart)
# → impossible de distinguer un DQ=0.72 (gate juste passé, 2/3 fields critiques
# manquent) d'un DQ=1.0 (toutes données présentes). 0.92 donne 8 pts d'écart
# entre DQ=0.70 et DQ=1.0, plus discriminant sans réintroduire le biais
# mega-caps de l'ancien 0.50 (15 pts d'écart).
_DQ_MIN_COEF = 0.92

# ── Cross-signal tilt thresholds (audit Phase 5, 2026-05-06) ────────────────
# Constantes auditables — déplacées ici depuis le corps de _score_universe.
# Permettent de backtester chaque seuil indépendamment et de visualiser la
# politique de tilt sans grepper la fonction principale.
#
# Bug #13/14/15 fix (audit 2026-05-07) — magnitudes réduites + cap absolu :
# • Les tilts capturent des INTERACTIONS non-linéaires (Q haut ET V haut
#   simultanément ≠ moyenne arithmétique de Q+V), donc défendables.
# • MAIS leur empilement (QARP+GARP+consistency = +10, ou cheap_junk+
#   falling_knife = -16) peut écraser un composite sain.
# • Magnitudes réduites pour atténuer le double-counting partiel avec les
#   piliers (momentum déjà bas → falling_knife re-pénalise).
# • Cap absolu sur tilt_adjust pour borner l'impact total.
#
# Penalties :
_TILT_CHEAP_JUNK_V_MIN     = 80.0   # Value très haut...
_TILT_CHEAP_JUNK_Q_MAX     = 30.0   # ...combiné à Quality très bas → value trap
_TILT_CHEAP_JUNK_PENALTY   = -7.0   # avant -10 (Bug #15 — Q et V déjà piliers)
_TILT_FALLING_KNIFE_V_MIN  = 80.0   # Value très haut...
_TILT_FALLING_KNIFE_M_MAX  = 30.0   # ...combiné à Momentum très négatif → reversal pas confirmé
_TILT_FALLING_KNIFE_PENALTY = -4.0  # avant -6 (Bug #14 — momentum bas déjà capturé pilier M)

# Bonuses :
_TILT_QARP_Q_MIN           = 70.0   # Quality At Reasonable Price (Novy-Marx)
_TILT_QARP_V_MIN           = 70.0
_TILT_QARP_BONUS           = +5.0
_TILT_GARP_G_MIN           = 70.0   # Growth At Reasonable Price (Lynch)
_TILT_GARP_V_MIN           = 70.0
_TILT_GARP_BONUS           = +3.0

# Bug #13 fix — cap absolu cumulé sur tilt_adjust. Empêche un ticker de
# recevoir plus de ±_TILT_TOTAL_CAP même si plusieurs tilts s'empilent.
# Borne défendable : 8 = QARP + GARP + un peu de marge consistency.
_TILT_TOTAL_CAP            = 8.0

# Consistency bonus : récompense les profils homogènes (faible σ entre piliers).
# `_CONSISTENCY_MIN_PILLARS_USED` doit être atteint *parmi les piliers réellement
# disponibles* (pas neutre 50 par défaut) ; sinon le bonus ne s'applique pas.
# Bug #18 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-18) — relevé 6 → 7. Sur 9 piliers, exiger 7
# avec data réelle (count>0) bloque le faux positif "consistency" sur les
# tickers FMP-only (Sentiment + Revisions souvent vides) — ils ne pouvaient
# jamais réunir > 7 piliers avec data simultanément.
_CONSISTENCY_STD_MAX            = 15.0
_CONSISTENCY_MIN_PILLARS_USED   = 7
_CONSISTENCY_BONUS              = +2.0
# Phase 6 audit (2026-05-06) — garde-fou anti "consistent neutral".
# L'ancien check fire si σ < 15 sans contrainte sur la moyenne. Or un ticker
# avec [50,50,50,50,50,50] (univers dégénéré, tous fundamentaux identiques,
# ranks tous neutres) a σ = 0 → bonus déclenché à tort. Le bonus consistency
# vise à récompenser "balanced excellence" (tous piliers >> 50), pas
# "balanced absence of signal". On exige une moyenne ≥ 55 pour fire.
_CONSISTENCY_MIN_MEAN           = 55.0

# Sanity floor pour PEG — un PEG très bas signale un earnings-growth quasi-nul
# au dénominateur (artefact mathématique), pas un bargain. Symétrique aux
# clamps déjà en place pour ev_to_ebitda > 0 et forward_pe > 0.
_PEG_SANITY_MIN = 0.10

# Au-delà de ce nombre de piliers retournés à exactement 50.0 (composante None
# imputée), on flag le ticker comme "low_signal" pour que l'aval puisse trier.
# 4/9 = quasi la moitié du composite est synthétique → ranking peu fiable.
_LOW_SIGNAL_NEUTRAL_PILLAR_THRESHOLD = 4

# Champs fondamentaux utilisés dans le scoring TITAN (métriques réparties
# sur les 6 piliers). data_quality = fraction non-None sur ces champs.
# Un ticker avec < _MIN_DATA_QUALITY est exclu du ranking → évite qu'un
# pennystock à trous API s'infiltre dans le top-20 via des scores neutres.
_TITAN_SCORING_FIELDS: tuple[str, ...] = (
    "return_on_equity",
    # Phase 6 audit (2026-05-06) — return_on_assets ajouté à DQ + Quality.
    # ROE est inflé par le levier (D/E haut → ROE haut artificiellement) ;
    # ROA = NI / Total Assets est leverage-neutre, le proxy académique
    # standard de Quality (Asness "Quality Minus Junk" 2014, Fama-French RMW).
    # Champ déjà collecté par yfinance/Piotroski (F1 absolute) — coût zéro.
    "return_on_assets",
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

    Implémentation : `bisect` sur la liste triée — O(n log n) total au lieu
    du O(n²) naïf (sum-comprehension sur tout le tableau pour chaque ticker).
    Sur un univers 500 × 14 métriques, le scoring passe de ~3.5 M comparaisons
    Python à ~9 k lookups bisect, soit ≈ 50× plus rapide. Numériquement
    équivalent : `bisect_left` compte les < strict, `bisect_right - bisect_left`
    compte les == (formule "fractional ranking" inchangée).
    """
    present = sorted(v for v in values.values() if v is not None and math.isfinite(v))
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
        less = bisect_left(present, v)
        equal = bisect_right(present, v) - less
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
#
# Phase 4 audit (2026-05-06) — relevé à 12. À n=8 la résolution était encore
# 12.5% avec ties écrasants sur Utilities (≈12 tickers SP500), Materials,
# REITs. n=12 donne ~8.3% (~12 buckets), proche du seuil académique standard
# (AQR/Fama-French utilisent ≥ 10 unique values). Pour les secteurs
# au-dessous (rares, ex: Communication Services minoritaire), le fallback
# global reste actif.
_MIN_SECTOR_SIZE_FOR_RELATIVE = 12


def _percentile_rank_by_sector(
    values: dict[str, float | None],
    sectors: dict[str, str],
    higher_is_better: bool = True,
    *,
    fallback_record: set[str] | None = None,
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
        # Bug #12 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-12) — exposition du fallback. Le caller
        # peut passer un set partagé pour collecter tous les tickers qui ont
        # subi un fallback global (intra-secteur dégénéré) sur n'importe
        # quelle métrique → expose côté payload pour transparence.
        if fallback_record is not None:
            fallback_record.update(fallback_tickers)

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


# Bug #10 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-10) — secteurs où certains critères Piotroski
# n'ont pas de sens structurel. Piotroski (2000) est conçu pour value/manuf.
#   • Financials : current_ratio inadapté (bilan classé en maturités, pas
#                  court/long), D/E structurellement 8-12× (banques opèrent
#                  sur leverage), gross_margin non significatif (pas de COGS
#                  industriel). On garde les critères de cash-flow et ROA.
#   • Real Estate (REITs) : gross_margin idem non significatif (revenue =
#                  loyers, pas de manufacturing). Current ratio n'est pas un
#                  signal de santé pour des dépréciations sur biens immo.
# Pour ces secteurs, on retire les critères inappropriés du calcul (n_evaluated
# diminue, n_passed proportionnel) → score sur un dénominateur de 9 reste
# numériquement comparable mais reflète honnêtement les critères applicables.
_PIOTROSKI_SKIP_CRITERIA_BY_SECTOR: dict[str, frozenset[str]] = {
    "Financials":   frozenset({"f5_debt_decreased_yoy", "f7_current_ratio_gt_1",
                               "f6_current_ratio_improved_yoy",
                               "f9_gross_margin_improved_yoy"}),
    "Real Estate":  frozenset({"f7_current_ratio_gt_1",
                               "f6_current_ratio_improved_yoy",
                               "f9_gross_margin_improved_yoy"}),
}


def _piotroski_f_score_absolute(
    row: dict[str, Any],
    yoy_row: dict[str, Any] | None = None,
    *,
    sector: str | None = None,
) -> tuple[int, int, int, dict[str, bool | None]]:
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

    Bug #10 fix : pour Financials/REITs, certains critères sont structurellement
    inappropriés et sont skippés (cf. _PIOTROSKI_SKIP_CRITERIA_BY_SECTOR).

    Returns:
        (n_passed, n_evaluated, n_max_applicable, breakdown)
        - n_passed : nombre de critères vérifiés.
        - n_evaluated : nombre de critères ayant pu être calculés.
        - n_max_applicable : nombre max de critères applicables pour le secteur
                             (9 - len(skip)). Sert de dénominateur honnête au
                             score normalisé pour Financials/REITs.
        - breakdown : {f1: True/False/None, ...} pour audit UI.
    """
    sector_norm = _SECTOR_NORMALIZE.get(sector or "", sector or "")
    skip = _PIOTROSKI_SKIP_CRITERIA_BY_SECTOR.get(sector_norm, frozenset())

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

    # Bug #10 fix — retire les critères skippés pour le secteur du décompte.
    if skip:
        for skip_key in skip:
            if skip_key in breakdown:
                breakdown[skip_key] = None  # marqué non-applicable
    # n_max_applicable = 9 (univers Piotroski complet) MOINS les critères
    # structurellement non-applicables au secteur. Indépendant de la présence
    # ou non de yoy_row : un ticker sans Y/Y data sera juste évalué sur les
    # absolus, mais le dénominateur reste celui du sector (5 pour Financials,
    # 6 pour Real Estate, 9 sinon). Un IPO avec 4/4 absolu passing aura
    # score = 4/9 = 44 (signal partiel honnête, pas 100 % flatteur).
    n_max_applicable = 9 - len(skip)
    evaluated = [v for v in breakdown.values() if v is not None]
    return (
        sum(1 for v in evaluated if v),
        len(evaluated),
        n_max_applicable,
        breakdown,
    )


def _piotroski_score_pillar(
    row: dict[str, Any],
    *,
    ticker: str | None = None,
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
    if ticker is None:
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
        # Phase 1 audit (2026-05-06) — gate strict : as_of=None → today() pour
        # que la prod live valide toujours (filet anti-cache corrompu).
        from datetime import date as _date
        from datetime import timedelta as _td
        as_of_eff = as_of if as_of is not None else _date.today()
        prev_year_yoy_row = {
            "return_on_assets":   row.get("return_on_assets_prev_year"),
            "debt_to_equity":     row.get("debt_to_equity_prev_year"),
            "current_ratio":      row.get("current_ratio_prev_year"),
            "shares_outstanding": row.get("shares_outstanding_prev_year"),
            "gross_margin":       row.get("gross_margin_prev_year"),
        }
        if publication_lag_days > 0:
            period_end_y1 = row.get("fundamentals_period_end_y1")
            if isinstance(period_end_y1, str):
                try:
                    pe = _date.fromisoformat(period_end_y1[:10])
                    publish_date = pe + _td(days=publication_lag_days)
                    if publish_date > as_of_eff:
                        # Y-1 pas encore publiable → fallback sur lookup snapshot
                        # (peut retourner None ; dans ce cas Y/Y simplement non
                        # évalué — pas de fuite via prev_year fields).
                        yoy_row = (
                            _lookup_yoy_snapshot(
                                ticker, as_of=as_of_eff,
                                publication_lag_days=publication_lag_days,
                            ) if ticker else None
                        )
                    else:
                        yoy_row = prev_year_yoy_row
                except ValueError:
                    # Date corrompue → on refuse plutôt que d'accepter en aveugle.
                    yoy_row = (
                        _lookup_yoy_snapshot(
                            ticker, as_of=as_of_eff,
                            publication_lag_days=publication_lag_days,
                        ) if ticker else None
                    )
            else:
                # Pas de date fiscale → comportement legacy (accept).
                yoy_row = prev_year_yoy_row
        else:
            yoy_row = prev_year_yoy_row
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

    # Bug #10 fix — propage le secteur pour skipper les critères inadaptés
    # (Financials, REITs).
    sector = row.get("sector")
    n_passed, n_evaluated, n_max_applicable, breakdown = (
        _piotroski_f_score_absolute(row, yoy_row, sector=sector)
    )
    # Phase 4 audit (2026-05-06) — F-Score dénominateur fixe 9 (Piotroski 2000
    # original) : avant, 1/4 et 5/9 retournaient ~25% et ~55% sans qu'on
    # puisse distinguer le ticker IPO data-pauvre du mature data-riche.
    # Bug #10 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-10) — pour Financials (5 critères skippés)
    # et REITs (3 critères skippés), normalisation sur n_max_applicable au
    # lieu de 9 fixe : un Financial avec 4/4 critères absolus passing aurait
    # eu 4/9 = 44 % (faux signal de faiblesse). Avec dénominateur honnête =
    # 4 (= n_max_applicable), il a 100 % qui reflète la santé fondamentale
    # sur les seuls critères pertinents.
    # Garde-fou IPO/data sparse : si évalués < min(4, n_max_applicable),
    # neutral 50 plutôt que pénaliser à tort.
    min_evaluated_required = min(4, n_max_applicable)
    if n_evaluated == 0 or n_evaluated < min_evaluated_required:
        return _NEUTRAL_SCORE, {
            "f_score":           None,
            "f_score_max":       n_max_applicable,
            "f_score_evaluated": n_evaluated,
            "f_score_breakdown": breakdown,
            "f_score_neutral":   True,  # diagnostic : signal absent, pas mauvais
        }
    score = (n_passed / float(max(1, n_max_applicable))) * 100.0
    return score, {
        "f_score":           n_passed,
        "f_score_max":       n_max_applicable,
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


def _pillar_score_with_count(parts: list[float | None]) -> tuple[float, int]:
    """Variante de `_pillar_score` qui expose aussi le nombre de composantes
    réellement utilisées (non-None). Permet à l'aval de distinguer un pilier
    score 70 calculé sur 4/4 composantes (signal dense) d'un score 70 calculé
    sur 1/4 (signal très sparse, à interpréter avec prudence).

    Retourne (score, n_used). n_used == 0 → pilier neutre par imputation.
    """
    vals = [p for p in parts if p is not None]
    if not vals:
        return _NEUTRAL_SCORE, 0
    return sum(vals) / len(vals), len(vals)


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


def _compute_structural_gaps(ticker_row: dict[str, Any]) -> list[str]:
    """Bug #21 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-21) — diagnostic des champs *non-applicables*
    structurellement mais effectivement absents.

    Ex: pour Financials, `debt_to_equity` est exclu du DQ (le ratio est
    structurellement haut, pas comparable cross-secteur), mais une banque
    *doit* avoir une D/E reportée. Si elle est absente, c'est un trou data
    réel — le DQ sector-aware le masque silencieusement (la banque a 100 %
    DQ malgré le manque). Ce diagnostic expose les trous sans modifier le
    scoring (back-compat) pour que l'aval (UI, alerter) puisse les afficher.
    """
    sector = ticker_row.get("sector")
    fields_applied = set(_fields_for_sector(sector))
    full_set = set(_TITAN_SCORING_FIELDS)
    non_applicable = full_set - fields_applied
    gaps: list[str] = []
    for f in sorted(non_applicable):
        if _safe_float(ticker_row.get(f)) is None:
            gaps.append(f)
    return gaps


def _weighted_mean_scores(tickers: list[dict[str, Any]]) -> dict[str, float]:
    """Moyenne pondérée par market cap des sous-scores TITAN sur un secteur.

    Smart-beta institutionnel : un small-cap avec un score extrême ne doit pas
    dominer la rotation d'un secteur dominé par des mega-caps.

    Fallback si aucun market cap → moyenne simple.
    Fallback si aucune valeur → score neutre 50.

    Phase 8 audit — couvre les 9 piliers (avant : Q/V/R/Sentiment seulement).
    Manquaient Momentum, Piotroski, Growth, Revisions, Insider — ce qui rendait
    impossible le diagnostic sectoriel par pilier dans /api/sectors.
    """
    score_keys = (
        "quality_score", "value_score", "risk_score",
        "sentiment_score", "momentum_score", "piotroski_score",
        "growth_score", "revisions_score", "insider_score",
        "titan_composite_score",
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


def _score_universe(
    tickers_map: dict[str, dict[str, Any]],
    *,
    as_of: date | None = None,
) -> dict[str, dict[str, Any]]:
    """Calcule les sous-scores TITAN (0-100) par ticker via percentile-ranking
    cross-universe sur chaque métrique brute, puis agrégation en 4 piliers
    pondérés pour produire le `titan_composite_score`.

    Args:
        tickers_map : {ticker: row brut} en entrée.
        as_of       : date de contexte pour les gates anti-lookahead Y/Y
                      (Piotroski, snapshot Y-1). Défaut today() en live ;
                      bootstrap rétroactif passe la date du snapshot.

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
    # Phase 8 audit (perf) — DQ est calculée 2× par ticker historiquement (gate
    # ici + pondération composite ligne ~1112). Sur 500 tickers × 12 fields, ça
    # double le coût `_safe_float`. On cache le résultat dans `dq_cache` pour
    # une seule passe d'extraction. Sémantique inchangée.
    dq_cache: dict[str, float] = {
        k: _compute_data_quality(tickers_map[k]) for k in keys_all
    }
    # Bug #19 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-19) — gate hard market_cap > 0.
    # market_cap n'est pas dans _TITAN_SCORING_FIELDS, donc un penny OTC
    # defunct (mcap=0) pouvait passer le DQ gate s'il avait 70 % des autres
    # fields renseignés, puis dériver Value via EV/EBITDA seul (FCF-yield
    # déjà filtré par mcap≤0 ligne ~810). Exclusion explicite plus propre.
    def _has_valid_mcap(row: dict[str, Any]) -> bool:
        mc = _safe_float(row.get("market_cap"))
        return mc is not None and math.isfinite(mc) and mc > 0
    keys = [
        k for k in keys_all
        if dq_cache[k] >= _MIN_DATA_QUALITY and _has_valid_mcap(tickers_map[k])
    ]
    n_excluded = len(keys_all) - len(keys)
    if n_excluded > 0:
        logger.info(
            f"[SectorMetrics] Scoring : {n_excluded}/{len(keys_all)} tickers exclus "
            f"(data_quality < {_MIN_DATA_QUALITY:.0%} ou market_cap ≤ 0)"
        )
    if not keys:
        return {}

    # ── Étape 1 — Extraction des séries brutes cross-universe ───────────
    roe       = {k: _safe_float(tickers_map[k].get("return_on_equity")) for k in keys}
    # Phase 6 audit — ROA ajouté en composante Quality, complémentaire à ROE.
    # ROE peut être inflé par le levier ; ROA est leverage-neutre. Un ticker
    # avec ROE 30 % et ROA 5 % signale un leverage élevé (= ROE artificiel),
    # le rank sectoriel sur les deux mesures simultanément démasque ce biais.
    roa       = {k: _safe_float(tickers_map[k].get("return_on_assets")) for k in keys}
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
    # Bug #9 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-9) — capture du flag d'unprofitability pour
    # exposition aval (turnaround story = signal légitime à ne pas masquer
    # silencieusement). Comportement scoring inchangé : None → fallback Value
    # sur FCF/PEG/EY uniquement, mais l'aval (UI, alerter) sait pourquoi.
    unprofitable_flags: dict[str, list[str]] = {}
    ev_ebitda: dict[str, float | None] = {}
    fwd_pe: dict[str, float | None] = {}
    for k in keys:
        ev_raw = _safe_float(tickers_map[k].get("ev_to_ebitda"))
        fpe_raw = _safe_float(tickers_map[k].get("forward_pe"))
        ev_ebitda[k] = ev_raw if (ev_raw is not None and ev_raw > 0) else None
        fwd_pe[k] = fpe_raw if (fpe_raw is not None and fpe_raw > 0) else None
        flags_v: list[str] = []
        if ev_raw is not None and ev_raw <= 0:
            flags_v.append("ev_ebitda_negative")
        if fpe_raw is not None and fpe_raw <= 0:
            flags_v.append("forward_pe_negative")
        if flags_v:
            unprofitable_flags[k] = flags_v

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
    # yfinance. Exclut PEG ≤ 0 (pertes attendues, mauvais signal pour Value).
    # Phase 5 audit (2026-05-06) — clamp PEG < _PEG_SANITY_MIN (0.10) : un PEG
    # de 0.05 signale un EPS quasi-nul au dénominateur (artefact mathématique),
    # pas un bargain. Symétrique aux clamps EV/EBITDA > 0 et fwd_pe > 0.
    peg = {
        k: (v if (v is not None and v >= _PEG_SANITY_MIN) else None)
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
    # Phase 7 audit (2026-05-06) — quick_ratio ajouté en composante Risk.
    # Plus conservateur que current_ratio (exclut l'inventaire ≈ pas-cash),
    # standard académique pour la liquidité court terme. Déjà collecté par
    # yfinance (info.quickRatio) mais inutilisé jusqu'ici. Optionnel : NON
    # ajouté au DQ gate pour ne pas exclure les tickers où yfinance ne le
    # remplit pas — le Risk pillar dégrade gracieusement à 2 composantes
    # (D/E + Current Ratio) quand QR absent.
    qr         = {k: _safe_float(tickers_map[k].get("quick_ratio")) for k in keys}

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
    roa            = _winsorize_by_sector(roa,            sectors_raw)
    op_margin      = _winsorize_by_sector(op_margin,      sectors_raw)
    gross_margin   = _winsorize_by_sector(gross_margin,   sectors_raw)
    ev_ebitda      = _winsorize_by_sector(ev_ebitda,      sectors_raw)
    fwd_pe         = _winsorize_by_sector(fwd_pe,         sectors_raw)
    fcf_yield      = _winsorize_by_sector(fcf_yield,      sectors_raw)
    peg            = _winsorize_by_sector(peg,            sectors_raw)
    earnings_yield = _winsorize_by_sector(earnings_yield, sectors_raw)
    d2e            = _winsorize_by_sector(d2e,            sectors_raw)
    curr_ratio     = _winsorize_by_sector(curr_ratio,     sectors_raw)
    qr             = _winsorize_by_sector(qr,             sectors_raw)
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

    # Bug #12 fix — collecte des tickers ayant subi un fallback global (sector
    # < 12 tickers valides). On agrège sur toutes les métriques sector-relative
    # car un fallback sur ne serait-ce qu'une métrique vaut signaling.
    sector_fallback_tickers: set[str] = set()

    roe_r      = _percentile_rank_by_sector(roe,       sectors_map, higher_is_better=True, fallback_record=sector_fallback_tickers)
    roa_r      = _percentile_rank_by_sector(roa,       sectors_map, higher_is_better=True, fallback_record=sector_fallback_tickers)
    opm_r      = _percentile_rank_by_sector(op_margin, sectors_map, higher_is_better=True, fallback_record=sector_fallback_tickers)
    gm_r       = _percentile_rank_by_sector(gross_margin, sectors_map, higher_is_better=True, fallback_record=sector_fallback_tickers)
    ev_r       = _percentile_rank_by_sector(ev_ebitda, sectors_map, higher_is_better=False, fallback_record=sector_fallback_tickers)
    fpe_r      = _percentile_rank_by_sector(fwd_pe,    sectors_map, higher_is_better=False, fallback_record=sector_fallback_tickers)
    fcf_r      = _percentile_rank_by_sector(fcf_yield, sectors_map, higher_is_better=True, fallback_record=sector_fallback_tickers)
    peg_r      = _percentile_rank_by_sector(peg,       sectors_map, higher_is_better=False, fallback_record=sector_fallback_tickers)
    ey_r       = _percentile_rank_by_sector(earnings_yield, sectors_map, higher_is_better=True, fallback_record=sector_fallback_tickers)
    d2e_r      = _percentile_rank_by_sector(d2e,       sectors_map, higher_is_better=False, fallback_record=sector_fallback_tickers)
    curr_r     = _percentile_rank_by_sector(curr_ratio, sectors_map, higher_is_better=True, fallback_record=sector_fallback_tickers)
    qr_r       = _percentile_rank_by_sector(qr,        sectors_map, higher_is_better=True, fallback_record=sector_fallback_tickers)
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

        # QUALITY — profitabilité (ROE, ROA) + efficience opérationnelle (op_margin)
        # + moat (gross margin). Phase 6 audit : ajout ROA pour deconfounder le
        # levier dans ROE — un ticker à ROE 30 % / ROA 5 % a un Quality moindre
        # qu'un ROE 25 % / ROA 15 % (même rentabilité comptable, moins de levier).
        q, q_count = _pillar_score_with_count(
            [roe_r[k], roa_r[k], opm_r[k], gm_r[k]]
        )

        # VALUE — EV/EBITDA prioritaire ; fallback Forward P/E ; + FCF yield +
        # PEG (growth-adjusted) + Earnings yield (Graham-style robust).
        # Lot 14.1 : +peg_r +ey_r pour un signal Value plus dense.
        val_primary = ev_r[k] if ev_r[k] is not None else fpe_r[k]
        v, v_count = _pillar_score_with_count(
            [val_primary, fcf_r[k], peg_r[k], ey_r[k]]
        )

        # RISK — bilan sain (low leverage, bonne liquidité). Higher = SAFER.
        # Phase 7 audit : ajout quick_ratio (3e composante). QR exclut l'inventaire
        # → meilleur proxy de la liquidité immédiate qu'un current_ratio gonflé
        # par du stock invendable. Le pilier dégrade à 2 composantes si QR absent.
        r, r_count = _pillar_score_with_count([d2e_r[k], curr_r[k], qr_r[k]])

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
        m, m_count = _pillar_score_with_count(
            [mom_ret_r[k], mom_ra_r[k], mom_52wh_r[k]]
        )

        # PIOTROSKI — F-Score 4 critères absolus (Lot 8). Les 5 Y/Y attendent
        # ≥ 2 ans d'historique Lot 6. Phase 1 audit : as_of propagé pour gate
        # anti-lookahead Y-1 strict (refuse Y-1 si publish_date > as_of).
        p, p_diag = _piotroski_score_pillar(t_base, ticker=k, as_of=as_of)
        p_count = int(p_diag.get("f_score_evaluated") or 0)

        # GROWTH — Revenue CAGR + Earnings CAGR (Lot 12, Novy-Marx/AQR).
        g, g_count = _pillar_score_with_count([rev_r[k], eps_r[k]])

        # REVISIONS — Lot 16. Pilier dédié (poids 12 %). Capture upgrades/
        # downgrades 90j + beat rate 8Q + surprise avg 4Q.
        # `revisions_score` = 50 (neutre) quand aucune composante n'est dispo ;
        # on s'appuie sur `revisions_data_quality` (fraction de composantes
        # renseignées) pour distinguer "vraies données" de "fallback neutre".
        rev_pack = revisions_pillar.get(k) or {}
        rv_raw = rev_pack.get("revisions_score")
        rv_dq = rev_pack.get("revisions_data_quality") or 0.0
        rv = float(rv_raw) if rv_raw is not None else _NEUTRAL_SCORE
        rv_count = 1 if rv_dq > 0 else 0

        # INSIDER — Lot 17. Pilier C-level/director smart money (poids 8 %).
        # Lit `insider_score` directement depuis le row si déjà enrichi par
        # `enrich_universe_with_insider()`. Sinon neutre 50.
        # Phase 4 audit (2026-05-06) — clamp [0, 100] + log explicite si
        # out-of-range (proxy: enrich corrompu, mauvais format upstream).
        ins_raw = _safe_float(t_base.get("insider_score"))
        if ins_raw is None:
            ins = _NEUTRAL_SCORE
            ins_count = 0
        elif 0 <= ins_raw <= 100:
            ins = ins_raw
            ins_count = 1
        else:
            logger.warning(
                f"[Scoring] {k} insider_score={ins_raw} hors [0,100] — "
                f"clamp à neutre 50 (vérifier enrich_universe_with_insider)."
            )
            ins = _NEUTRAL_SCORE
            ins_count = 0

        if sentiment_available:
            s, s_count = _pillar_score_with_count([reco_r[k], upside_r[k]])
        else:
            # Pansement : Sentiment indisponible → exclu (renorm dynamique
            # absorbe ce cas via count=0).
            s = _NEUTRAL_SCORE  # reporté pour traçabilité, non utilisé dans composite
            s_count = 0

        # Bug #17 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-17) — renormalisation DYNAMIQUE des poids.
        # Avant : un pilier entièrement vide (count=0, score imputé à neutre 50)
        # contribuait quand même à hauteur de son poids — pull artificiel vers
        # 50 sur tous les tickers. Si Momentum API down, l'univers entier
        # voyait sa variance composite réduite par la dilution 15 % * 50.
        # Désormais : on assemble (poids, score) pour les piliers dont count>0,
        # puis on divise par la somme des poids effectivement utilisés. Si tout
        # est vide → composite neutre 50. La back-compat avec sentiment_available
        # est préservée : count=0 sur Sentiment exclut bien le pilier.
        pillar_pieces: list[tuple[float, float]] = []
        if q_count > 0:    pillar_pieces.append((_W_TITAN_QUALITY,   q))
        if v_count > 0:    pillar_pieces.append((_W_TITAN_VALUE,     v))
        if r_count > 0:    pillar_pieces.append((_W_TITAN_RISK,      r))
        if sentiment_available and s_count > 0:
                           pillar_pieces.append((_W_TITAN_SENTIMENT, s))
        if m_count > 0:    pillar_pieces.append((_W_TITAN_MOMENTUM,  m))
        if p_count > 0:    pillar_pieces.append((_W_TITAN_PIOTROSKI, p))
        if g_count > 0:    pillar_pieces.append((_W_TITAN_GROWTH,    g))
        if rv_count > 0:   pillar_pieces.append((_W_TITAN_REVISIONS, rv))
        if ins_count > 0:  pillar_pieces.append((_W_TITAN_INSIDER,   ins))
        weight_total = sum(w for w, _ in pillar_pieces)
        if weight_total > 0:
            composite = sum(w * s_ for w, s_ in pillar_pieces) / weight_total
            if sentiment_available:
                weight_mode = "full" if abs(weight_total - 1.0) < 1e-9 else "renormalized"
            else:
                weight_mode = "no_sentiment"
        else:
            composite = _NEUTRAL_SCORE
            weight_mode = "all_empty"

        # ── Lot 14.1 — Cross-signal adjustments ────────────────────────────
        # Signaux composites typiques de la littérature LT (QARP/GARP, value
        # trap detection). Appliqués AVANT le dq_coef pour que le ranking soit
        # influencé mais que la pénalité DQ s'applique quand même.
        #
        # tilt_adjust cumule les bonus (≤0 → pas touché, >0 → meilleur stock).
        # Phase 5 audit (2026-05-06) — seuils extraits en constantes
        # (_TILT_*) pour audit/backtest. Comportement numérique inchangé.
        tilt_adjust = 0.0
        flags: list[str] = []

        # Cheap-junk filter : Value haut + Quality faible = value trap
        # (P/E bas parce que earnings pourrissent). Penalty proportionnelle
        # à la force du signal value + misère quality.
        if v >= _TILT_CHEAP_JUNK_V_MIN and q < _TILT_CHEAP_JUNK_Q_MAX:
            tilt_adjust += _TILT_CHEAP_JUNK_PENALTY
            flags.append("cheap_junk")
        # Value haut + Momentum négatif = falling knife
        if v >= _TILT_FALLING_KNIFE_V_MIN and m < _TILT_FALLING_KNIFE_M_MAX:
            tilt_adjust += _TILT_FALLING_KNIFE_PENALTY
            flags.append("falling_knife")

        # QARP — Quality At Reasonable Price (Novy-Marx 2013, AQR).
        # Stock qui est TRÈS bon sur Quality ET pas cher → bonus. Académiquement
        # le combo le plus robuste sur 30 ans de backtest.
        if q >= _TILT_QARP_Q_MIN and v >= _TILT_QARP_V_MIN:
            tilt_adjust += _TILT_QARP_BONUS
            flags.append("qarp")

        # GARP — Growth At Reasonable Price (Peter Lynch). Growth + Value
        # combinés (évite d'acheter de la growth à tout prix).
        if g >= _TILT_GARP_G_MIN and v >= _TILT_GARP_V_MIN:
            tilt_adjust += _TILT_GARP_BONUS
            flags.append("garp")

        # Consistency bonus : récompense les profils homogènes (faible σ
        # entre piliers). Phase 5 audit — bug fix : on n'inclut PAS Sentiment
        # quand `sentiment_available is False`, sinon `s=50` neutre artificiel
        # écrase la dispersion → faux positif "consistent" sur les tickers
        # FMP-only. On ne compte que les piliers ayant ≥ 1 composante réelle
        # (pillars_data_count > 0) pour avoir un signal honnête.
        pillars_with_data: list[float] = []
        if q_count > 0:   pillars_with_data.append(q)
        if v_count > 0:   pillars_with_data.append(v)
        if r_count > 0:   pillars_with_data.append(r)
        if sentiment_available and s_count > 0:
            pillars_with_data.append(s)
        if m_count > 0:   pillars_with_data.append(m)
        if p_count > 0:   pillars_with_data.append(p)
        if g_count > 0:   pillars_with_data.append(g)
        if rv_count > 0:  pillars_with_data.append(rv)
        if ins_count > 0: pillars_with_data.append(ins)
        if len(pillars_with_data) >= _CONSISTENCY_MIN_PILLARS_USED:
            pillars_std = statistics.pstdev(pillars_with_data)
            pillars_mean = statistics.fmean(pillars_with_data)
            # Phase 6 audit — exige μ ≥ 55 pour distinguer "balanced excellence"
            # (objectif du bonus) de "balanced absence of signal" (univers ou
            # ticker dégénéré avec tous ranks à neutre 50).
            if (
                pillars_std < _CONSISTENCY_STD_MAX
                and pillars_mean >= _CONSISTENCY_MIN_MEAN
            ):
                tilt_adjust += _CONSISTENCY_BONUS
                flags.append("consistent")

        # Bug #13 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-13) — cap absolu cumulé sur tilt_adjust.
        # Empêche l'empilement (QARP+GARP+consistency = +10, cheap_junk+
        # falling_knife = -11) d'écraser le composite. L'INTERACTION est
        # capturée, mais sa magnitude reste bornée.
        tilt_adjust = max(-_TILT_TOTAL_CAP, min(_TILT_TOTAL_CAP, tilt_adjust))

        # Bug #6 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-6) — clipping après dq_coef au lieu d'avant.
        # Ancien : clip(composite + tilt) puis × dq_coef → un ticker à 98 + QARP(+5)
        # = 103 était clip → 100 → ×0.95 = 95.0, alors que l'ordre correct donne
        # 103 × 0.95 = 97.85. Le bonus QARP était partiellement absorbé par le
        # clip avant que dq_coef ne le ramène sous 100. Réordonner conserve
        # l'effet relatif des tilts en présence d'un dq_coef non-trivial.
        composite_with_tilt_unclipped = composite + tilt_adjust

        # Pondération par data_quality — pénalise doucement les profils incomplets.
        # On expose les 2 valeurs pour audit : composite "brut" (avant pénalité,
        # comparable au TITAN historique) et composite "final" (après, utilisé
        # pour le ranking en production).
        # Phase 8 audit (perf) — réutilise dq_cache calculé en amont du gate.
        dq = dq_cache[k]
        dq_coef = _DQ_MIN_COEF + (1.0 - _DQ_MIN_COEF) * dq
        composite_weighted = max(
            0.0, min(100.0, composite_with_tilt_unclipped * dq_coef)
        )
        # composite_raw (display) — clip seulement pour le payload de surface,
        # ne sert plus au calcul du composite final.
        composite_raw_display = max(
            0.0, min(100.0, composite_with_tilt_unclipped)
        )

        # Phase 5 audit — diagnostiques de densité par pilier. Permet à l'aval
        # (UI, backtester, alerter) de distinguer un score 70 dense (4/4
        # composantes valides) d'un score 70 imputé (1/4 composantes, le reste
        # neutre 50). Sur des univers à forte sparsité, deux tickers à
        # composite égal n'ont pas la même fiabilité.
        pillars_data_count = {
            "quality":   q_count,
            "value":     v_count,
            "risk":      r_count,
            "sentiment": s_count,
            "momentum":  m_count,
            "piotroski": p_count,
            "growth":    g_count,
            "revisions": rv_count,
            "insider":   ins_count,
        }
        n_pillars_neutral = sum(1 for c in pillars_data_count.values() if c == 0)
        low_signal = n_pillars_neutral >= _LOW_SIGNAL_NEUTRAL_PILLAR_THRESHOLD

        # Phase 7 audit — âge des fundamentaux par ticker. Permet à l'aval
        # de filtrer les tickers à data stale (cache > 30j → Quality/Value
        # reposent sur du périmé). Calculé vs `as_of` en backtest.
        age_days = _parse_fetched_at_age_days(t_base.get("fetched_at"), as_of=as_of)
        # Bug #7 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-7) — âge du *report fiscal* (period_end)
        # complémentaire à age_days (fetched_at). Un fetch frais d'un report 8
        # mois stale a age_days=0 mais report_age=240 → Piotroski Y/Y miné.
        report_age_days = _parse_period_end_age_days(
            t_base.get("fundamentals_period_end"), as_of=as_of,
        )
        report_age_days_y1 = _parse_period_end_age_days(
            t_base.get("fundamentals_period_end_y1"), as_of=as_of,
        )

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
            "titan_composite_raw":      round(composite_raw_display, 2),
            "titan_composite_score":    round(composite_weighted, 2),
            "titan_weight_mode":        weight_mode,
            "titan_tilt_adjust":        round(tilt_adjust, 2),
            "titan_tilt_flags":         flags,
            "data_quality":             round(dq, 3),
            "data_quality_coef":        round(dq_coef, 3),
            # Phase 5 audit — densité de signal par pilier (diagnostiques).
            "pillars_data_count":       pillars_data_count,
            "n_pillars_neutral":        n_pillars_neutral,
            "low_signal":               low_signal,
            # Phase 7 audit — fraîcheur des fundamentaux (fetch).
            "fundamentals_age_days":    age_days,
            # Bug #7 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-7) — fraîcheur du *report fiscal*.
            # > 200j sur Q-latest = au moins 2 trimestres stale. > 450j sur Y-1
            # = Piotroski Y/Y reposant sur des comparables périmés.
            "fundamentals_report_age_days":     report_age_days,
            "fundamentals_report_age_days_y1":  report_age_days_y1,
            # Bug #9 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-9) — flags structural unprofitability.
            # Liste explicite : ev_ebitda_negative, forward_pe_negative.
            "value_unprofitable_flags": unprofitable_flags.get(k, []),
            # Bug #20 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-20) — divergences inter-providers
            # propagées du FallbackFundamentalProvider. Exposé dans le scored
            # payload pour que /api/universe + UI puissent afficher l'incohérence
            # FMP/YF (déjà loggée mais auparavant invisible côté client).
            "cross_provider_divergence": t_base.get("cross_provider_divergence") or [],
            # Bug #21 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-21) — gaps structurels (champs non
            # applicables au secteur mais effectivement absents). DQ ne les
            # mesure pas (par design), expose-les ici pour visibilité.
            "structural_gaps":           _compute_structural_gaps(t_base),
            # Bug #12 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-12) — flag indiquant que ce ticker a
            # subi un fallback rank global au lieu d'intra-secteur (au moins
            # une métrique sector-relative a fallbacké). Petits secteurs <12.
            "sector_relative_fallback": k in sector_fallback_tickers,
        }

    # ── Phase 6 audit — Composite z-score (universe-relative) ───────────
    # Standardise le composite par rapport à la moyenne et l'écart-type de
    # l'univers scoré. Permet un ranking robuste ("top 1σ", "stocks > 1.5σ
    # au-dessus de la moyenne") au lieu de seuils absolus 70/80 qui dérivent
    # avec la calibration. Calculé sur `titan_composite_score` (post-tilt,
    # post-dq_coef) pour refléter le ranking effectif.
    #   • σ = 0 (universe dégénéré, < 2 tickers) → z = 0 pour tout le monde.
    #   • Round à 3 décimales — un z=0.000 vs 0.001 n'a pas de signification.
    # NB : `rec` (record scoré) et non `r` — `r` est déjà lié plus haut au
    # score du pilier Risk (float), le réutiliser ici ferait croire à mypy que
    # ces enregistrements sont des float.
    composites = [rec["titan_composite_score"] for rec in scored.values()]
    if len(composites) >= 2:
        mu = statistics.fmean(composites)
        sigma = statistics.pstdev(composites)
        if sigma > 0:
            for rec in scored.values():
                rec["titan_composite_z"] = round(
                    (rec["titan_composite_score"] - mu) / sigma, 3
                )
        else:
            for rec in scored.values():
                rec["titan_composite_z"] = 0.0
    else:
        for rec in scored.values():
            rec["titan_composite_z"] = 0.0

    # ── Phase 7 audit — Sector-relative composite metrics ────────────────
    # Pour la construction de portefeuille diversifié : ranker un ticker contre
    # ses pairs sectoriels plutôt que contre l'univers entier. Un Tech à 65 et
    # un Utility à 65 ne signifient pas la même chose — le composite est
    # mécaniquement plus haut sur les secteurs structurellement bien notés.
    #
    # Calcule par secteur :
    #   • titan_composite_sector_pct  → percentile rank intra-secteur (0-100)
    #   • titan_composite_sector_z    → z-score intra-secteur
    #   • titan_composite_sector_n    → taille du secteur (pour interpréter)
    #
    # Permet à l'auto_proposer / portfolio_engine de faire "top-1 par secteur"
    # de manière équitable — pas dominé par les Tech qui ont des composites
    # globalement plus élevés.
    by_sector_scored: dict[str, list[tuple[str, float]]] = {}
    for k_, rec in scored.items():
        sec = rec.get("sector") or "Unknown"
        by_sector_scored.setdefault(sec, []).append((k_, rec["titan_composite_score"]))

    for items in by_sector_scored.values():
        n_sec = len(items)
        scores_sec = [v for _, v in items]
        # Percentile rank intra-secteur via _percentile_rank.
        rank_input: dict[str, float | None] = {k_: v for k_, v in items}
        sec_pct = _percentile_rank(rank_input, higher_is_better=True)
        # z-score intra-secteur (μ_sector, σ_sector).
        if n_sec >= 2:
            mu_sec = statistics.fmean(scores_sec)
            sig_sec = statistics.pstdev(scores_sec)
        else:
            mu_sec, sig_sec = 0.0, 0.0
        for k_, score in items:
            pct_val = sec_pct[k_]
            scored[k_]["titan_composite_sector_pct"] = (
                round(pct_val, 2) if pct_val is not None else None
            )
            if sig_sec > 0:
                scored[k_]["titan_composite_sector_z"] = round(
                    (score - mu_sec) / sig_sec, 3
                )
            else:
                scored[k_]["titan_composite_sector_z"] = 0.0
            scored[k_]["titan_composite_sector_n"] = n_sec

    return scored
