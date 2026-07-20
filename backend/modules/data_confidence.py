"""Score de confiance des notations — Phase 1 Buffett-data hardening (2026-04-29).

Buffett : « I demand a margin of safety in the data itself. »

Le système Buffett (sizing tilt, niveaux SL/TP, lt_exit_policy) repose sur
quality_score, f_score, value_score, peg_ratio. Si ces inputs sont stales,
incomplets, ou aberrants, toute la chaîne décisionnelle est compromise. Ce
module agrège trois signaux orthogonaux en un score 0-100 par ticker :

  • Coverage   — fraction des champs fondamentaux disponibles (data_quality
                 du scoring sector-aware déjà calculé). 0 = vide, 1 = complet.
  • Freshness  — âge des données fondamentales en jours. ≤ 30j = neutre,
                 décroissance linéaire jusqu'à 365j = pénalité max.
  • Sanity     — détection d'aberrations sectorielles (PEG ratio absurde,
                 quality_score outlier vs sector median, contradictions
                 internes type Q-90 mais ROE manquant).
  • MultiSource — pannes explicites des sources d'enrichissement au-delà
                 des fondamentaux (Étape 0 généralisation, 2026-07-20).
                 Neutre (×1.00) par défaut ; ne pénalise QUE sur un signal
                 d'échec sans ambiguïté déjà calculé ailleurs dans le
                 pipeline — jamais sur une simple absence de données (qui
                 peut vouloir dire "rien à signaler", pas "source cassée").
                 Aujourd'hui : uniquement `insider_error` (SEC Form 4 —
                 "cik_unknown"/"sec_fetch_failed", écrit par
                 `insider_enrich._enrich_one`). Finnhub (revisions/
                 earnings) et news n'ont pas encore de signal d'échec
                 par-ticker persisté (fail-open silencieux — un champ
                 `None` peut aussi bien dire "pas d'info Finnhub" que
                 "endpoint en échec") ; les filings SEC 10-K/Q sont
                 fetchés à la demande (`routers/sec_filings.py`), jamais
                 persistés dans `universe.json` — donc invisibles ici
                 sans I/O réseau, que ce module s'interdit (déterministe).
                 Ajouter ces sources demandera d'abord de leur donner un
                 signal d'échec par-ticker aussi propre que celui
                 d'insider — pas un nouveau jugement d'isolation, juste
                 du travail d'instrumentation supplémentaire.

Logique :
  confidence = 100 × (coverage × freshness × sanity × multi_source)

Tous les facteurs sont des multiplicateurs dans [0, 1]. Un seul effondre le
score : un ticker stale 12 mois (freshness=0.4) + outlier sectoriel
(sanity=0.7) tombe à 100 × 1.0 × 0.4 × 0.7 ≈ 28/100, même si la coverage
est complète.

Le module est purement informatif et déterministe (aucun I/O réseau).
Utilisé par :
  - `_sizing_buffett.apply_buffett_tilt` — atténue le tilt si confidence < 60
  - `lt_exit_policy.decide` — inhibe EXIT_VALUATION si confidence < 50
                              (le signal pourrait venir d'une donnée cassée)
  - `/api/lt_decision` — exposé sous `confidence_score` + `confidence_breakdown`
  - UI Portfolio — badge 🛡 X/100 sur PositionCard

Calibration initiale (à reviser après 20+ trades clos) :
  ≥ 80 : très fiable (concentrer/agir avec confiance)
  60-80 : correct (utilisation normale du Buffett tilt)
  40-60 : douteux (atténuation tilt, signaux d'alerte modérés)
  < 40  : peu fiable (refuser EXIT_VALUATION, ne pas augmenter sizing)
"""
from __future__ import annotations

import math
from typing import Any

# ─── Bornes freshness ──────────────────────────────────────────
# Données fondamentales : on tolère 30 jours sans pénalité (cycle earnings
# trimestriel = ~90j entre updates). Décroissance linéaire jusqu'à 365j où
# on plafonne à 0.4 (ne pas tomber à 0 — un compounder type KO peut rester
# stable même avec data 1 an).
_FRESHNESS_FREE_DAYS = 30
_FRESHNESS_FLOOR_DAYS = 365
_FRESHNESS_FLOOR_VALUE = 0.40

# ─── Sanity checks ─────────────────────────────────────────────
# PEG : > 5 ou négatif < -5 = aberrant. Yfinance retourne parfois 100+
# sur growth fledgling — on ne fait pas confiance.
_PEG_SANITY_MIN = -5.0
_PEG_SANITY_MAX = 5.0

# Forward P/E : > 200 = absurde même pour growth/biotech.
_FWD_PE_SANITY_MAX = 200.0

# Outlier sector-relative : un quality_score qui dépasse le sector median
# de plus de 40 pts est suspect (probable data anomaly, à investiguer).
_QUALITY_OUTLIER_DELTA = 40.0

# ─── Multi-source (au-delà des fondamentaux) ──────────────────
# Même magnitude que les pénalités sanity existantes (PEG/Fwd-PE aberrants)
# — pas de sévérité inventée, cohérent avec le reste du module.
_MULTI_SOURCE_ERROR_PENALTY = 0.85


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _freshness_factor(age_days: float | None) -> tuple[float, str]:
    """Retourne (facteur ∈ [0.4, 1.0], label explicatif)."""
    if age_days is None:
        return 0.7, "Âge données inconnu → ×0.70 (prudent)"
    if age_days <= _FRESHNESS_FREE_DAYS:
        return 1.0, f"Données fraîches ({age_days:.0f}j) → ×1.00"
    if age_days >= _FRESHNESS_FLOOR_DAYS:
        return _FRESHNESS_FLOOR_VALUE, f"Données très anciennes ({age_days:.0f}j) → ×{_FRESHNESS_FLOOR_VALUE:.2f}"
    # Décroissance linéaire entre _FRESHNESS_FREE_DAYS et _FRESHNESS_FLOOR_DAYS
    span = _FRESHNESS_FLOOR_DAYS - _FRESHNESS_FREE_DAYS
    progress = (age_days - _FRESHNESS_FREE_DAYS) / span
    factor = 1.0 - progress * (1.0 - _FRESHNESS_FLOOR_VALUE)
    return factor, f"Données vieillissantes ({age_days:.0f}j) → ×{factor:.2f}"


def _sanity_factor(
    *,
    quality_score: float | None,
    f_score: float | None,
    peg_ratio: float | None,
    forward_pe: float | None,
    sector_quality_median: float | None = None,
) -> tuple[float, list[str]]:
    """Détecte aberrations. Multiplicateur ∈ [0.5, 1.0], pénalités cumulatives."""
    factor = 1.0
    notes: list[str] = []

    # Aberration PEG
    if peg_ratio is not None and not (
        _PEG_SANITY_MIN <= peg_ratio <= _PEG_SANITY_MAX
    ):
        factor *= 0.85
        notes.append(f"PEG aberrant {peg_ratio:.1f} → ×0.85")

    # Forward P/E absurde
    if forward_pe is not None and forward_pe > _FWD_PE_SANITY_MAX:
        factor *= 0.85
        notes.append(f"Fwd P/E extrême {forward_pe:.0f} → ×0.85")

    # Outlier sectoriel — quality très au-dessus de la médiane secteur
    # peut-être génial OU peut-être donnée cassée. Sans cross-source
    # automatique, on flagge comme prudence.
    if (quality_score is not None
            and sector_quality_median is not None
            and quality_score - sector_quality_median > _QUALITY_OUTLIER_DELTA):
        factor *= 0.90
        notes.append(
            f"Quality {quality_score:.0f} >> médiane secteur "
            f"{sector_quality_median:.0f} (+{quality_score - sector_quality_median:.0f}) → ×0.90"
        )

    # Contradiction interne : Q-score haut mais F-score nul = data probable broken
    # (un business « wonderful » a normalement un bilan sain).
    if (quality_score is not None and quality_score >= 80
            and f_score is not None and f_score <= 3):
        factor *= 0.75
        notes.append(
            f"Contradiction Q-{quality_score:.0f} ↔ F-{f_score:.0f}/9 → ×0.75"
        )

    # Plancher sanity à 0.5 — on n'efface pas complètement le score.
    factor = max(0.5, factor)
    if not notes:
        notes.append("Pas d'aberration détectée → ×1.00")
    return factor, notes


def _multi_source_factor(info: dict[str, Any]) -> tuple[float, list[str]]:
    """Facteur ∈ {0.85, 1.00} — pannes explicites hors fondamentaux.

    Neutre (1.00) si absent/None, y compris pour un ticker jamais enrichi
    par `insider_enrich` (pas de régression sur le comportement actuel).
    Ne pénalise que sur `insider_error` truthy (échec SEC EDGAR sans
    ambiguïté — cf. docstring module). Autres sources : voir docstring
    module, pas encore de signal sûr disponible.
    """
    factor = 1.0
    notes: list[str] = []
    insider_err = info.get("insider_error")
    if insider_err:
        factor *= _MULTI_SOURCE_ERROR_PENALTY
        notes.append(f"Insider SEC EDGAR en échec ({insider_err}) → ×{_MULTI_SOURCE_ERROR_PENALTY:.2f}")
    if not notes:
        notes.append("Sources d'enrichissement OK (ou non encore instrumentées) → ×1.00")
    return factor, notes


def compute_confidence(
    info: dict[str, Any] | None,
    *,
    fundamentals_age_days: float | None = None,
    sector_quality_median: float | None = None,
) -> dict[str, Any]:
    """Calcule le score de confiance d'un ticker scoré.

    Args:
        info: Dict scoré (output get_scored_universe) — peut contenir
              quality_score, f_score, value_score, peg_ratio, forward_pe,
              data_quality (déjà calculée par sector_metrics, fraction 0-1
              des champs présents), sector.
        fundamentals_age_days: Âge des données fondamentales (jours). Si None,
              on tente `info.get('fundamentals_age_days')`, sinon prudent.
        sector_quality_median: Médiane quality_score du secteur (pour outlier
              detection). Optionnel.

    Returns:
        {
          score:        0-100 (entier),
          tier:         'high' | 'medium' | 'low' | 'very_low',
          coverage:     0-1,
          freshness:    0-1,
          sanity:       0-1,
          multi_source: 0-1,
          breakdown:    list[str] — explication ligne par ligne,
        }
    """
    if not info:
        return {
            "score": 0, "tier": "very_low",
            "coverage": 0.0, "freshness": 0.0, "sanity": 0.0, "multi_source": 0.0,
            "breakdown": ["Aucune donnée disponible"],
        }

    coverage = _safe_float(info.get("data_quality"))
    if coverage is None:
        coverage = 0.5  # prudent default
    coverage = max(0.0, min(1.0, coverage))

    age = _safe_float(fundamentals_age_days)
    if age is None:
        age = _safe_float(info.get("fundamentals_age_days"))
    fresh_factor, fresh_label = _freshness_factor(age)

    # Phase 6 audit (2026-05-06) — propage le flag "stale" depuis le cache.
    # Si `source_provider` contient "/stale" (cf. fundamentals_cache._stale_or_raise)
    # ou "stale_fallback", on multiplie freshness par 0.5 — le scoring restera
    # actif mais la confidence chute, ce qui inhibe ADD_ON et EXIT_VALUATION
    # via lt_exit_policy. Avant : un cache 3j-stale après échec live n'avait
    # AUCUN impact sur la confidence (silent).
    src = (info.get("source_provider") or "").lower()
    if "stale" in src or "/stale" in src:
        fresh_factor *= 0.5
        fresh_label += " | STALE provider-fallback (×0.50)"

    sanity_factor_v, sanity_notes = _sanity_factor(
        quality_score=_safe_float(info.get("quality_score")),
        f_score=_safe_float(info.get("f_score")),
        peg_ratio=_safe_float(info.get("peg_ratio")),
        forward_pe=_safe_float(info.get("forward_pe")),
        sector_quality_median=_safe_float(sector_quality_median),
    )

    multi_source_v, multi_source_notes = _multi_source_factor(info)

    # Composition multiplicative — un facteur faible suffit à effondrer.
    raw = 100.0 * coverage * fresh_factor * sanity_factor_v * multi_source_v
    score = int(round(max(0.0, min(100.0, raw))))

    if score >= 80:
        tier = "high"
    elif score >= 60:
        tier = "medium"
    elif score >= 40:
        tier = "low"
    else:
        tier = "very_low"

    breakdown = [
        f"Coverage {coverage*100:.0f}% (champs fondamentaux disponibles) → ×{coverage:.2f}",
        fresh_label,
        *sanity_notes,
        *multi_source_notes,
        f"Score final = {coverage:.2f} × {fresh_factor:.2f} × {sanity_factor_v:.2f} × {multi_source_v:.2f} × 100 = {score}",
    ]

    return {
        "score":        score,
        "tier":         tier,
        "coverage":     round(coverage, 3),
        "freshness":    round(fresh_factor, 3),
        "sanity":       round(sanity_factor_v, 3),
        "multi_source": round(multi_source_v, 3),
        "breakdown":    breakdown,
    }


def confidence_modifier(score: int) -> float:
    """Multiplicateur à appliquer aux signaux Buffett selon la confiance.

    Utilisé par `_sizing_buffett` et autres pour atténuer l'effet d'un
    tilt/exit basé sur des données peu fiables.

      ≥ 80 : ×1.00 (data fiable, pleine puissance Buffett)
      60-80 : ×0.85 (atténuation modérée)
      40-60 : ×0.70 (atténuation forte)
      < 40 : ×0.50 (très peu fiable — Buffett en mode prudent)
    """
    if score >= 80:
        return 1.00
    if score >= 60:
        return 0.85
    if score >= 40:
        return 0.70
    return 0.50
