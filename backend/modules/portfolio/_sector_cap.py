"""Cap itératif par secteur GICS (défaut 30 %)."""
from __future__ import annotations

from typing import Any

from modules.log import logger

# Plafond par secteur : empêche qu'un rally tech concentre 80 % du book.
# Les tickers sur-pondérés sont scalés down uniformément ; le surplus est
# redistribué proportionnellement aux secteurs sous le cap. Itératif jusqu'à
# convergence (la redistribution peut pousser un autre secteur au-dessus → itérer).
DEFAULT_SECTOR_CAP = 0.30
_SECTOR_CAP_EPSILON = 1e-4
_SECTOR_CAP_MAX_ITERATIONS = 20


def apply_sector_cap(
    weights: dict[str, float],
    sector_by_ticker: dict[str, str],
    cap: float,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Cap chaque secteur à `cap × total` (défaut 30 %).

    Algorithme itératif :
      1. Identifier le secteur le plus au-dessus du cap.
      2. Scaler down ses tickers uniformément (× cap/total_sector).
      3. Redistribuer le surplus aux autres tickers PROPORTIONNELLEMENT à
         leur poids courant — conserve la structure risk-parity relative.
      4. Répéter jusqu'à convergence ou max 20 passes.

    Faisabilité : impossible de capper tout le monde sous `cap` si
    `cap × n_sectors < 1`. Dans ce cas no-op + log, le caller voit
    `cap_applied=False` dans les diagnostics.
    """
    w = {t: float(v) for t, v in weights.items()}
    tickers = list(w.keys())
    if not tickers:
        return w, {"cap_applied": False, "reason": "empty"}

    sectors: dict[str, list[str]] = {}
    for t in tickers:
        sectors.setdefault(sector_by_ticker.get(t, "Unknown"), []).append(t)
    n_sectors = len(sectors)

    if n_sectors == 1:
        return w, {"cap_applied": False, "reason": "single_sector",
                   "sectors_count": 1, "cap": cap}

    if cap * n_sectors < 1.0 - _SECTOR_CAP_EPSILON:
        logger.warning(
            f"[PortfolioManager] sector_cap={cap:.0%} infaisable avec "
            f"{n_sectors} secteurs (min faisable={1.0 / n_sectors:.2%}). "
            f"Cap non appliqué."
        )
        return w, {"cap_applied": False, "reason": "infeasible",
                   "cap": cap, "sectors_count": n_sectors}

    capped_history: dict[str, dict[str, float]] = {}
    iterations = 0
    converged = False
    for iteration in range(_SECTOR_CAP_MAX_ITERATIONS):
        iterations = iteration + 1
        sector_totals = {s: sum(w[t] for t in ts) for s, ts in sectors.items()}
        over_list = [
            (s, tot) for s, tot in sector_totals.items()
            if tot > cap + _SECTOR_CAP_EPSILON
        ]
        if not over_list:
            converged = True
            break
        over_list.sort(key=lambda x: -x[1])
        s_over, total = over_list[0]
        excess = total - cap
        scale = cap / total
        for t in sectors[s_over]:
            w[t] *= scale
        # On ne LOG que la première fois qu'un secteur est cappé (valeur originale),
        # pas les ré-ajustements successifs pendant la convergence.
        capped_history.setdefault(s_over, {
            "original_weight": round(total, 4),
            "capped_at":       cap,
        })
        under_tickers = [
            t for t in tickers
            if sector_by_ticker.get(t, "Unknown") != s_over
        ]
        under_total = sum(w[t] for t in under_tickers)
        if under_total > 0:
            for t in under_tickers:
                w[t] += excess * (w[t] / under_total)
        else:
            break

    # Phase 5 audit (2026-05-06) — log si convergence non atteinte (souvent
    # signe d'une situation pathologique : 2 secteurs > cap mutually
    # impossible). Le rebal final renormalise mais le portefeuille n'est pas
    # vraiment "capped".
    if not converged and capped_history:
        logger.warning(
            f"[SectorCap] Non-convergence après {iterations} itérations "
            f"(cap={cap:.0%}, secteurs cappés : {list(capped_history.keys())}). "
            f"Sectors finaux : "
            + ", ".join(f"{s}={round(sum(w[t] for t in ts), 3)}"
                        for s, ts in sectors.items())
        )

    # Renormalisation pour gommer la dérive numérique.
    total_w = sum(w.values())
    if total_w > 0:
        w = {t: v / total_w for t, v in w.items()}

    return w, {
        "cap_applied":      bool(capped_history),
        "cap":              cap,
        "capped_sectors":   capped_history,
        "iterations":       iterations,
        "converged":        converged,
        "sectors_count":    n_sectors,
    }
