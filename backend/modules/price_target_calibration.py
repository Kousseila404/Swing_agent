"""Boucle de calibration — prix cible fondamental 12 mois (docs/price_target_design.md §6).

Un round = un appel CLI = :
  1. Charge `data/calibration/universe_history_export.jsonl.gz` (§2.1 — jamais
     `universe_history` directement, absent du sandbox cloud).
  2. Construit des paires de dates (t, t+N≈60j).
  3. Pour la config de poids courante (best_weights + une petite grille de
     perturbations autour), calcule `price_target` pour chaque (ticker, date)
     et le rank IC (Spearman, réutilise `wfo_calibration._spearman`) entre
     `(price_target/current_price - 1)` et le rendement réalisé à t+N, sur le
     même échantillon que la baseline consensus analystes
     `(price_target_mean/current_price - 1)`.
  4. Garde la meilleure config (IC modèle le plus haut), met à jour
     `data/.price_target_calibration/state.json` (round, ic_history,
     best_ic_model/best_ic_baseline/best_weights, rounds_since_improvement).
  5. Vérifie les 3 critères d'arrêt (§6) — n'agit pas dessus (juste calculé
     et logué ; c'est la routine appelante qui décide de la suite de phase).

Usage :
    python -m modules.price_target_calibration
"""
from __future__ import annotations

import gzip
import json
from datetime import date as _date
from datetime import timedelta as _timedelta
from typing import Any

from modules import api_core
from modules.price_target import compute_for_universe
from modules.wfo_calibration import _spearman

EXPORT_PATH = api_core.BASE / "data" / "calibration" / "universe_history_export.jsonl.gz"
STATE_PATH = api_core.BASE / "data" / ".price_target_calibration" / "state.json"

_WINDOW_DAYS_TARGET = 60
_WINDOW_TOLERANCE_DAYS = 5
_MIN_PAIRS_REQUIRED = 5

# Critères d'arrêt §6.
_CONVERGENCE_IC_MIN = 0.10
_CONVERGENCE_STABILITY_BAND = 0.02
_CONVERGENCE_STABILITY_ROUNDS = 3
_PLATEAU_ROUNDS = 5
_HARD_CAP_ROUNDS = 20


def load_export(path: Any = None) -> dict[str, dict[str, dict[str, Any]]]:
    """{date_str: {ticker: row}} depuis l'export gzippé (§2.1)."""
    path = path or EXPORT_PATH
    by_date: dict[str, dict[str, dict[str, Any]]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            d = row.get("date")
            t = row.get("ticker")
            if not d or not t:
                continue
            by_date.setdefault(d, {})[t] = row
    return by_date


def select_pairs(
    dates: list[str],
    window_days: int = _WINDOW_DAYS_TARGET,
    tolerance_days: int = _WINDOW_TOLERANCE_DAYS,
) -> list[tuple[str, str]]:
    """Paires (t, t+N) — N ajusté par tolérance si la date exacte n'existe pas
    (snapshots quotidiens mais pas garantis 100% consécutifs, cf. gap 2026-04-23).
    """
    parsed = sorted(_date.fromisoformat(d) for d in dates)
    date_set = set(parsed)
    pairs: list[tuple[str, str]] = []
    for start in parsed:
        target = start + _timedelta(days=window_days)
        best = None
        best_delta = None
        for offset in range(-tolerance_days, tolerance_days + 1):
            candidate = target + _timedelta(days=offset)
            if candidate in date_set:
                delta = abs(offset)
                if best_delta is None or delta < best_delta:
                    best, best_delta = candidate, delta
        if best is not None:
            pairs.append((start.isoformat(), best.isoformat()))
    return pairs


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def evaluate_weights(
    by_date: dict[str, dict[str, dict[str, Any]]],
    pairs: list[tuple[str, str]],
    *,
    weights: dict[str, float],
    band_pct: float,
) -> dict[str, Any]:
    """IC modèle vs IC baseline (consensus analystes) sur les mêmes paires §4/§6.

    Retourne {ic_model, ic_baseline, n_pairs, n_pairs_valid, avg_n_tickers}.
    ic_model/ic_baseline = moyenne des IC par paire (None si aucune paire valide).
    """
    # Cache des price_target par date pour cette config de poids — évite de
    # recalculer si plusieurs paires partagent la même date de départ.
    pt_cache: dict[str, dict[str, dict[str, Any]]] = {}

    def _pt_for_date(d: str) -> dict[str, dict[str, Any]]:
        if d not in pt_cache:
            pt_cache[d] = compute_for_universe(by_date.get(d, {}), weights=weights, band_pct=band_pct)
        return pt_cache[d]

    per_pair_model: list[float] = []
    per_pair_baseline: list[float] = []
    n_tickers_used: list[int] = []
    n_pairs_valid = 0

    for t0, t1 in pairs:
        rows0 = by_date.get(t0, {})
        rows1 = by_date.get(t1, {})
        pt0 = _pt_for_date(t0)

        model_signal: list[float] = []
        baseline_signal: list[float] = []
        realized: list[float] = []

        for ticker, row0 in rows0.items():
            row1 = rows1.get(ticker)
            if row1 is None:
                continue
            p0 = _safe_float(row0.get("current_price"))
            p1 = _safe_float(row1.get("current_price"))
            if p0 is None or p0 <= 0 or p1 is None or p1 <= 0:
                continue

            pt_row = pt0.get(ticker) or {}
            if pt_row.get("method") != "fundamental_blend":
                continue
            model_upside = _safe_float(pt_row.get("upside_pct"))
            if model_upside is None:
                continue

            pt_mean = _safe_float(row0.get("price_target_mean"))
            if pt_mean is None or pt_mean <= 0:
                continue
            baseline_upside = (pt_mean / p0 - 1.0) * 100.0

            realized_return = (p1 / p0 - 1.0) * 100.0

            model_signal.append(model_upside)
            baseline_signal.append(baseline_upside)
            realized.append(realized_return)

        if len(model_signal) < 3:
            continue

        ic_m = _spearman(model_signal, realized)
        ic_b = _spearman(baseline_signal, realized)
        if ic_m is None or ic_b is None:
            continue

        per_pair_model.append(ic_m)
        per_pair_baseline.append(ic_b)
        n_tickers_used.append(len(model_signal))
        n_pairs_valid += 1

    ic_model = sum(per_pair_model) / len(per_pair_model) if per_pair_model else None
    ic_baseline = sum(per_pair_baseline) / len(per_pair_baseline) if per_pair_baseline else None
    avg_n = sum(n_tickers_used) / len(n_tickers_used) if n_tickers_used else 0.0

    return {
        "ic_model": round(ic_model, 4) if ic_model is not None else None,
        "ic_baseline": round(ic_baseline, 4) if ic_baseline is not None else None,
        "n_pairs": len(pairs),
        "n_pairs_valid": n_pairs_valid,
        "avg_n_tickers": round(avg_n, 1),
    }


def _normalize_weights(w: dict[str, float]) -> dict[str, float]:
    keys = ("w_multiple", "w_peg", "w_buffett")
    total = sum(max(0.0, w.get(k, 0.0)) for k in keys)
    if total <= 0:
        return {"w_multiple": 1 / 3, "w_peg": 1 / 3, "w_buffett": 1 / 3}
    return {k: round(max(0.0, w.get(k, 0.0)) / total, 4) for k in keys}


def _candidate_configs(best_weights: dict[str, float]) -> list[dict[str, Any]]:
    """Grille de perturbations autour de `best_weights` (§6 étape 1).

    Déterministe (pas d'aléatoire) — un round est reproductible à l'identique
    en re-jouant le même state.json. Perturbe chaque poids +/-0.1 (renormalisé)
    et le band_pct +/-0.05, en plus de la config courante inchangée.
    """
    base_w = {
        "w_multiple": best_weights.get("w_multiple", 1 / 3),
        "w_peg": best_weights.get("w_peg", 1 / 3),
        "w_buffett": best_weights.get("w_buffett", 1 / 3),
    }
    base_band = best_weights.get("band_pct", 0.15)

    configs: list[dict[str, Any]] = [{"weights": _normalize_weights(base_w), "band_pct": base_band}]

    shift = 0.10
    for dim in ("w_multiple", "w_peg", "w_buffett"):
        for direction in (+1, -1):
            w = dict(base_w)
            w[dim] = max(0.0, w[dim] + direction * shift)
            configs.append({"weights": _normalize_weights(w), "band_pct": base_band})

    for band_delta in (+0.05, -0.05):
        new_band = max(0.05, min(0.35, base_band + band_delta))
        configs.append({"weights": _normalize_weights(base_w), "band_pct": new_band})

    return configs


def _check_stop_conditions(state: dict[str, Any]) -> str | None:
    """§6 — retourne 'convergence' | 'plateau' | 'hard_cap' | None."""
    if state["round"] >= _HARD_CAP_ROUNDS:
        return "hard_cap"
    if state["rounds_since_improvement"] >= _PLATEAU_ROUNDS:
        return "plateau"

    best_ic = state.get("best_ic_model")
    best_ic_baseline = state.get("best_ic_baseline")
    if (
        best_ic is not None
        and best_ic >= _CONVERGENCE_IC_MIN
        and best_ic_baseline is not None
        and best_ic >= best_ic_baseline
    ):
        history = state.get("ic_history", [])
        recent = [h for h in history[-_CONVERGENCE_STABILITY_ROUNDS:] if h.get("ic_model") is not None]
        if len(recent) >= _CONVERGENCE_STABILITY_ROUNDS:
            ics = [h["ic_model"] for h in recent]
            if max(ics) - min(ics) <= _CONVERGENCE_STABILITY_BAND:
                return "convergence"
    return None


def run_round(state: dict[str, Any], by_date: dict[str, dict[str, dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Exécute un round de calibration, retourne le state.json mis à jour."""
    if by_date is None:
        by_date = load_export()

    dates = sorted(by_date.keys())
    pairs = select_pairs(dates)
    window = _WINDOW_DAYS_TARGET
    while len(pairs) < _MIN_PAIRS_REQUIRED and window > 10:
        window -= 10
        pairs = select_pairs(dates, window_days=window)

    state = dict(state)
    state["round"] = state.get("round", 0) + 1

    candidates = _candidate_configs(state.get("best_weights") or {})
    results = []
    for cfg in candidates:
        ev = evaluate_weights(by_date, pairs, weights=cfg["weights"], band_pct=cfg["band_pct"])
        results.append({**cfg, **ev})

    scored = [r for r in results if r["ic_model"] is not None]
    if not scored:
        best_this_round = {
            "weights": state.get("best_weights") or {"w_multiple": 1 / 3, "w_peg": 1 / 3, "w_buffett": 1 / 3},
            "band_pct": (state.get("best_weights") or {}).get("band_pct", 0.15),
            "ic_model": None, "ic_baseline": None,
            "n_pairs": len(pairs), "n_pairs_valid": 0, "avg_n_tickers": 0.0,
        }
    else:
        best_this_round = max(scored, key=lambda r: r["ic_model"])

    prev_best_ic = state.get("best_ic_model")
    improved = best_this_round["ic_model"] is not None and (
        prev_best_ic is None or best_this_round["ic_model"] > prev_best_ic
    )

    round_log = {
        "round": state["round"],
        "weights_tested": best_this_round["weights"],
        "band_pct_tested": best_this_round["band_pct"],
        "ic_model": best_this_round["ic_model"],
        "ic_baseline": best_this_round["ic_baseline"],
        "n_pairs": best_this_round["n_pairs"],
        "n_pairs_valid": best_this_round["n_pairs_valid"],
        "avg_n_tickers": best_this_round["avg_n_tickers"],
        "n_candidates_evaluated": len(candidates),
        "window_days": window,
        "improved": improved,
    }
    state.setdefault("ic_history", []).append(round_log)

    if improved:
        state["best_ic_model"] = best_this_round["ic_model"]
        state["best_ic_baseline"] = best_this_round["ic_baseline"]
        state["best_weights"] = {**best_this_round["weights"], "band_pct": best_this_round["band_pct"]}
        state["rounds_since_improvement"] = 0
    else:
        state["rounds_since_improvement"] = state.get("rounds_since_improvement", 0) + 1

    stop_reason = _check_stop_conditions(state)
    if stop_reason:
        state["stopped_reason"] = stop_reason
        state["phase"] = "integrate"

    return state


def _load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {
        "phase": "calibrate", "round": 0,
        "best_ic_model": None, "best_ic_baseline": None,
        "best_weights": {"w_multiple": 0.34, "w_peg": 0.33, "w_buffett": 0.33, "band_pct": 0.15},
        "rounds_since_improvement": 0, "ic_history": [], "stopped_reason": None,
    }


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def _cli() -> int:
    state = _load_state()
    new_state = run_round(state)
    _save_state(new_state)
    last = new_state["ic_history"][-1]
    print(
        f"[PriceTargetCalibration] round={new_state['round']} "
        f"ic_model={last['ic_model']} ic_baseline={last['ic_baseline']} "
        f"n_pairs_valid={last['n_pairs_valid']}/{last['n_pairs']} "
        f"stopped_reason={new_state.get('stopped_reason')}"
    )
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
