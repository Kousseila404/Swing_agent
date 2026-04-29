"""Thesis stop fondamental — détection d'une cassure de thèse pour position LT.

En LT, le stop technique (SL %) protège contre une chute brutale, mais ne capture
pas la dégradation lente du *fondamental* — un name peut perdre son TITAN sans
casser de niveau technique, et c'est pourtant le signal d'exit le plus
informatif (la raison d'être de la position a disparu).

Ce module compare les scores capturés à l'entrée (Titan_Score_Entry,
Quality_Entry, F_Score_Entry, …) avec les scores courants du scored_universe et
émet un statut :

  INTACT  : drift dans la marge de tolérance, thèse tient
  WARN    : drift significatif sur ≥ 1 axe (alerter, pas exit)
  BROKEN  : drift sévère ou multiple WARNs cumulés (exit recommandé)

Le module est purement informatif — il NE force PAS l'exit. L'utilisateur
prend la décision en conscience après lecture des `reasons_break`. C'est la
philosophie LT (vs un stop loss déterministe court terme).

Phase 1 (ce tour) : exposition via /api/ticker_analysis pour les positions OPEN.
Phase 2 (futur) : intégration dans tracker.evaluate_trades pour push Telegram.

Seuils calibrés à la louche — à recalibrer dès que 20+ trades clos avec
trade_journal complet (cf. project_titan_entry_scores_capture).
"""
from __future__ import annotations

import math
import re
from typing import Any

# Drifts (entry → now) déclenchant un signal. Drift négatif = score a baissé.
_TITAN_DRIFT_BREAK = -20.0   # TITAN −20 pts = thèse cassée (entry 82 → now 62)
_TITAN_DRIFT_WARN  = -12.0   # TITAN −12 pts = signal d'attention

_QUALITY_DRIFT_WARN  = -25.0
_PIOTROSKI_DRIFT_WARN = -25.0
_MOMENTUM_DRIFT_WARN = -30.0  # Momentum est volatile, marge plus large

_F_SCORE_DROP_BREAK = 3       # F-Score chute de ≥ 3 sur 9 = bilan abîmé
_F_SCORE_DROP_WARN  = 2

# Le current revisions_score se passe d'entrée — c'est un signal absolu (les
# downgrades nets sur 90j sont une rupture de thèse même sans baseline).
_REVISIONS_NEG_WARN = 30.0    # revisions_score < 30 = downgrade momentum

# Tilts négatifs apparus en cours de hold (pas présents à l'entrée).
_TILT_BREAK_FLAGS = {"cheap_junk", "falling_knife"}


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


def _parse_f_score(v: Any) -> int | None:
    """Format '8/9' (capturé en VARCHAR) ou int direct."""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
        return int(v)
    s = str(v).strip()
    if not s:
        return None
    m = re.match(r"^\s*(\d+)\s*(?:/\s*\d+)?\s*$", s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def compute_sector_drift_baseline(
    positions: list[dict[str, Any]],
    *,
    min_n: int = 3,
) -> dict[str, float]:
    """Calcule le drift TITAN médian par secteur sur les positions OPEN.

    Args:
        positions: list de dicts avec au minimum {'sector', 'entry_titan',
            'current_titan'} — entry_titan = Titan_Score_Entry du journal,
            current_titan = titan_composite_score actuel du scored_universe.
        min_n: nombre minimum de positions par secteur pour produire un baseline
            (sinon trop bruité). Default 3.

    Returns:
        {sector: median_drift} pour les secteurs avec ≥ min_n positions.
    """
    by_sector: dict[str, list[float]] = {}
    for p in positions:
        sector = p.get("sector")
        et = _safe_float(p.get("entry_titan"))
        ct = _safe_float(p.get("current_titan"))
        if not sector or et is None or ct is None:
            continue
        by_sector.setdefault(str(sector), []).append(ct - et)

    out: dict[str, float] = {}
    for sector, drifts in by_sector.items():
        if len(drifts) < min_n:
            continue
        drifts_sorted = sorted(drifts)
        n = len(drifts_sorted)
        if n % 2 == 1:
            median = drifts_sorted[n // 2]
        else:
            median = (drifts_sorted[n // 2 - 1] + drifts_sorted[n // 2]) / 2.0
        out[sector] = round(median, 2)
    return out


def compute_thesis_status(
    entry: dict[str, Any],
    current: dict[str, Any],
    *,
    sector_drift_baseline: float | None = None,
) -> dict[str, Any]:
    """Évalue la cassure de thèse fondamentale entre entrée et état courant.

    Args:
        entry: dict avec champs *_Entry capturés au moment de l'achat
            (Titan_Score_Entry, Quality_Entry, F_Score_Entry, Piotroski_Entry,
            Tilt_Flags_Entry, …). Tous optionnels — on travaille avec ce qu'on a.
        current: dict scoré (output get_scored_universe) avec les sous-scores
            actuels (titan_composite_score, quality_score, f_score, …).
        sector_drift_baseline: drift TITAN médian du secteur sur la même fenêtre
            entry → now. Si fourni, on évalue le drift TITAN *relatif* à ce
            baseline — un name qui dérive comme tout son secteur ne casse pas
            sa thèse propre. Le drift absolu reste exposé pour transparence.

    Returns:
        {
          status:        'INTACT' | 'WARN' | 'BROKEN' | 'NO_DATA',
          severity:      0 (INTACT) | 1 (WARN) | 2 (BROKEN),
          reasons_break: list[str],
          reasons_warn:  list[str],
          drift: {
            titan: float | None,           # current - entry (positif = amélioration)
            titan_relative: float | None,  # titan - sector_drift_baseline
            sector_baseline: float | None,
            quality: float | None,
            momentum: float | None,
            f_score: int | None,           # current - entry
          },
        }
    """
    titan_entry    = _safe_float(entry.get("Titan_Score_Entry"))
    quality_entry  = _safe_float(entry.get("Quality_Entry"))
    momentum_entry = _safe_float(entry.get("Momentum_Entry"))
    piotroski_entry = _safe_float(entry.get("Piotroski_Entry"))
    f_entry        = _parse_f_score(entry.get("F_Score_Entry"))
    tilt_entry_csv = (entry.get("Tilt_Flags_Entry") or "").strip()
    tilt_entry = {t.strip() for t in tilt_entry_csv.split(",") if t.strip()}

    titan_now    = _safe_float(current.get("titan_composite_score"))
    quality_now  = _safe_float(current.get("quality_score"))
    momentum_now = _safe_float(current.get("momentum_score"))
    piotroski_now = _safe_float(current.get("piotroski_score"))
    f_now        = current.get("f_score")
    try:
        f_now = int(f_now) if f_now is not None else None
    except (TypeError, ValueError):
        f_now = None
    revisions_now = _safe_float(current.get("revisions_score"))
    tilt_now_list = current.get("titan_tilt_flags") or []
    tilt_now = {str(t) for t in tilt_now_list}

    reasons_break: list[str] = []
    reasons_warn: list[str] = []
    drift: dict[str, float | int | None] = {
        "titan": None, "titan_relative": None, "sector_baseline": None,
        "quality": None, "momentum": None,
        "piotroski": None, "f_score": None,
    }

    # ── TITAN composite ─────────────────────────────────────
    if titan_entry is not None and titan_now is not None:
        d = titan_now - titan_entry
        drift["titan"] = round(d, 2)
        # Sector-relative : on évalue les seuils contre le drift relatif au secteur.
        # Si tout le secteur a chuté de -10 pts et le ticker aussi, le drift
        # propre du ticker est 0 — pas de cassure de thèse.
        if sector_drift_baseline is not None:
            d_rel = d - sector_drift_baseline
            drift["titan_relative"] = round(d_rel, 2)
            drift["sector_baseline"] = round(sector_drift_baseline, 2)
            d_eval = d_rel
        else:
            d_eval = d
        if d_eval <= _TITAN_DRIFT_BREAK:
            suffix = (f" vs secteur {sector_drift_baseline:+.0f}"
                      if sector_drift_baseline is not None else "")
            reasons_break.append(
                f"TITAN {titan_entry:.0f} → {titan_now:.0f} ({d:+.0f} pts{suffix})"
            )
        elif d_eval <= _TITAN_DRIFT_WARN:
            suffix = (f" vs secteur {sector_drift_baseline:+.0f}"
                      if sector_drift_baseline is not None else "")
            reasons_warn.append(
                f"TITAN {titan_entry:.0f} → {titan_now:.0f} ({d:+.0f} pts{suffix})"
            )

    # ── F-Score (Piotroski 0-9) ─────────────────────────────
    if f_entry is not None and f_now is not None:
        d = f_now - f_entry
        drift["f_score"] = d
        if -d >= _F_SCORE_DROP_BREAK:
            reasons_break.append(
                f"F-Score {f_entry}/9 → {f_now}/9 (bilan abîmé)"
            )
        elif -d >= _F_SCORE_DROP_WARN:
            reasons_warn.append(
                f"F-Score {f_entry}/9 → {f_now}/9"
            )

    # ── Quality drift ────────────────────────────────────────
    if quality_entry is not None and quality_now is not None:
        d = quality_now - quality_entry
        drift["quality"] = round(d, 2)
        if d <= _QUALITY_DRIFT_WARN:
            reasons_warn.append(
                f"Quality {quality_entry:.0f} → {quality_now:.0f} ({d:+.0f} pts)"
            )

    # ── Piotroski pillar score (0-100, distinct du F-Score) ─
    if piotroski_entry is not None and piotroski_now is not None:
        d = piotroski_now - piotroski_entry
        drift["piotroski"] = round(d, 2)
        if d <= _PIOTROSKI_DRIFT_WARN:
            reasons_warn.append(
                f"Piotroski pillar {piotroski_entry:.0f} → {piotroski_now:.0f} ({d:+.0f} pts)"
            )

    # ── Momentum (volatile mais informatif) ─────────────────
    if momentum_entry is not None and momentum_now is not None:
        d = momentum_now - momentum_entry
        drift["momentum"] = round(d, 2)
        if d <= _MOMENTUM_DRIFT_WARN:
            reasons_warn.append(
                f"Momentum {momentum_entry:.0f} → {momentum_now:.0f} ({d:+.0f} pts)"
            )

    # ── Revisions négatif (signal absolu, pas comparatif) ──
    if revisions_now is not None and revisions_now < _REVISIONS_NEG_WARN:
        reasons_warn.append(
            f"Revisions analystes {revisions_now:.0f}/100 (downgrade momentum)"
        )

    # ── Nouveaux tilts négatifs apparus depuis l'entrée ────
    new_negative_tilts = (tilt_now & _TILT_BREAK_FLAGS) - tilt_entry
    for t in sorted(new_negative_tilts):
        reasons_break.append(f"Tilt négatif apparu : {t}")

    # ── Statut final ────────────────────────────────────────
    if reasons_break:
        status, severity = "BROKEN", 2
    elif len(reasons_warn) >= 3:
        # 3+ WARNs cumulés = équivalent BROKEN (érosion multi-axe)
        status, severity = "BROKEN", 2
        reasons_break.append(
            f"Érosion multi-axe : {len(reasons_warn)} signaux WARN cumulés"
        )
    elif reasons_warn:
        status, severity = "WARN", 1
    elif titan_entry is None and quality_entry is None and f_entry is None:
        status, severity = "NO_DATA", 0
    else:
        status, severity = "INTACT", 0

    return {
        "status":        status,
        "severity":      severity,
        "reasons_break": reasons_break,
        "reasons_warn":  reasons_warn,
        "drift":         drift,
    }
