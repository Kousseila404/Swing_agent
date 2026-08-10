"""Boucle de calibration — prix cible fondamental 12 mois.

Audit 2026-08-10 — la V1 (rounds de hill-climbing sur TOUT l'historique
disponible, IC reportée sur le même échantillon qui a servi à choisir les
poids) était en-sample par construction, et avec ~110j d'historique et une
fenêtre forward de 60j, les 54 "paires" évaluées se chevauchaient à ~98% —
un seul régime de marché compté 54 fois, pas 54 essais indépendants.
`wfo_calibration.py` avait déjà résolu ce problème pour les poids des
piliers TITAN (walk-forward train/test non-overlapping) ; cette V2 reprend
la même discipline ici :

  1. Charge `data/calibration/universe_history_export.jsonl.gz` (§2.1 — jamais
     `universe_history` directement, absent du sandbox cloud).
  2. Construit des paires de dates (t, t+N≈60j) via `select_pairs`.
  3. Découpe ces paires en folds (TRAIN, TEST) glissants et **non-overlapping
     sur le TEST** via `select_folds` (même logique que
     `wfo_calibration.run_walk_forward`).
  4. Pour chaque fold : grid-search des poids sur TRAIN uniquement (grille
     fixe autour de l'équi-pondération, indépendante des folds précédents —
     pas de warm-start pour éviter toute fuite inter-fold), puis évalue l'IC
     obtenu sur TEST (jamais vu pendant la sélection) = l'estimateur honnête
     out-of-sample pour ce fold.
  5. Agrège : poids moyens + IC TEST moyen sur tous les folds.
  6. Gate `validated_oos` : tant que `n_folds < min_folds_required` (défaut
     3), les poids optimisés ne sont PAS dignes de confiance (pas assez de
     fenêtres indépendantes) → `best_weights` reste l'équi-pondération neutre
     non-overfit, indépendamment de ce que le grid-search a trouvé. Ce gate
     se lève tout seul au fil des jours à mesure que `universe_history`
     grossit (cron quotidien) et que `n_folds` augmente.

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
from modules.price_target import DEFAULT_BAND_PCT, compute_for_universe
from modules.wfo_calibration import _spearman

EXPORT_PATH = api_core.BASE / "data" / "calibration" / "universe_history_export.jsonl.gz"
STATE_PATH = api_core.BASE / "data" / ".price_target_calibration" / "state.json"

_WINDOW_DAYS_TARGET = 60
_WINDOW_TOLERANCE_DAYS = 5
_MIN_PAIRS_REQUIRED = 5

_DEFAULT_WEIGHTS_NEUTRAL = {"w_multiple": 1 / 3, "w_peg": 1 / 3, "w_buffett": 1 / 3}

# Walk-forward — plus courts que wfo_calibration (60j train/20j test) car
# l'historique dispo pour ce module est bien plus court aujourd'hui (~110j
# vs 500+j pour les piliers TITAN). Réévaluer à la hausse une fois que
# `universe_history` couvre plusieurs mois de plus.
_TRAIN_DAYS_DEFAULT = 30
_TEST_DAYS_DEFAULT = 15
_MIN_FOLDS_FOR_VALIDATION = 3
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


def select_folds(
    pairs: list[tuple[str, str]],
    train_days: int = _TRAIN_DAYS_DEFAULT,
    test_days: int = _TEST_DAYS_DEFAULT,
) -> list[dict[str, list[tuple[str, str]]]]:
    """Découpe `pairs` (par t0 croissant) en folds walk-forward.

    Même logique que `wfo_calibration.run_walk_forward` : le TRAIN accumule
    les paires jusqu'à couvrir `train_days`, le TEST commence juste après et
    couvre `test_days`, puis le prochain fold repart après la fin du TEST
    (jamais de chevauchement sur le TEST, contrairement à la V1).
    """
    parsed = sorted(pairs, key=lambda p: p[0])
    t0_dates = [_date.fromisoformat(p[0]) for p in parsed]
    n = len(parsed)
    folds: list[dict[str, list[tuple[str, str]]]] = []
    start_idx = 0
    while start_idx < n:
        train_pairs: list[tuple[str, str]] = []
        while (
            start_idx + len(train_pairs) < n
            and (t0_dates[start_idx + len(train_pairs)] - t0_dates[start_idx]).days < train_days
        ):
            train_pairs.append(parsed[start_idx + len(train_pairs)])
        if not train_pairs:
            break

        test_idx = start_idx + len(train_pairs)
        if test_idx >= n:
            break
        test_start_date = t0_dates[test_idx]
        test_pairs: list[tuple[str, str]] = []
        while (
            test_idx + len(test_pairs) < n
            and (t0_dates[test_idx + len(test_pairs)] - test_start_date).days < test_days
        ):
            test_pairs.append(parsed[test_idx + len(test_pairs)])
        if not test_pairs:
            break

        folds.append({"train_pairs": train_pairs, "test_pairs": test_pairs})
        start_idx = test_idx + len(test_pairs)

    return folds


def run_walk_forward(
    by_date: dict[str, dict[str, dict[str, Any]]] | None = None,
    *,
    train_days: int = _TRAIN_DAYS_DEFAULT,
    test_days: int = _TEST_DAYS_DEFAULT,
    window_days: int = _WINDOW_DAYS_TARGET,
    tolerance_days: int = _WINDOW_TOLERANCE_DAYS,
    min_folds_required: int = _MIN_FOLDS_FOR_VALIDATION,
) -> dict[str, Any]:
    """Calibration walk-forward honnête (poids sélectionnés sur TRAIN,
    IC reportée sur TEST jamais vu pendant la sélection).

    Retourne le state.json complet — `best_weights` n'est mis à jour avec les
    poids optimisés que si `n_folds >= min_folds_required` ; sinon on sert
    l'équi-pondération neutre plutôt qu'un résultat non-validé.
    """
    if by_date is None:
        by_date = load_export()

    dates = sorted(by_date.keys())
    pairs = select_pairs(dates, window_days=window_days, tolerance_days=tolerance_days)
    fold_specs = select_folds(pairs, train_days=train_days, test_days=test_days)

    fold_results: list[dict[str, Any]] = []
    for spec in fold_specs:
        train_pairs = spec["train_pairs"]
        test_pairs = spec["test_pairs"]

        # Grille fixe autour de l'équi-pondération — pas de warm-start sur le
        # meilleur poids d'un fold précédent, pour ne rien faire fuiter d'un
        # TEST déjà consommé vers le TRAIN d'un fold suivant.
        candidates = _candidate_configs(_DEFAULT_WEIGHTS_NEUTRAL)
        train_evals = [
            {**cfg, **evaluate_weights(by_date, train_pairs, weights=cfg["weights"], band_pct=cfg["band_pct"])}
            for cfg in candidates
        ]
        scored = [r for r in train_evals if r["ic_model"] is not None]
        if not scored:
            continue
        best_on_train = max(scored, key=lambda r: r["ic_model"])

        test_eval = evaluate_weights(
            by_date, test_pairs, weights=best_on_train["weights"], band_pct=best_on_train["band_pct"]
        )

        fold_results.append({
            "train_start": train_pairs[0][0], "train_end": train_pairs[-1][0],
            "test_start": test_pairs[0][0], "test_end": test_pairs[-1][0],
            "n_train_pairs": len(train_pairs), "n_test_pairs": len(test_pairs),
            "weights_selected": best_on_train["weights"],
            "ic_train": best_on_train["ic_model"],
            "ic_test": test_eval["ic_model"],
            "ic_baseline_test": test_eval["ic_baseline"],
        })

    n_folds = len(fold_results)
    validated = n_folds >= min_folds_required

    if fold_results:
        avg_weights = {"w_multiple": 0.0, "w_peg": 0.0, "w_buffett": 0.0}
        for f in fold_results:
            for k in avg_weights:
                avg_weights[k] += f["weights_selected"].get(k, 0.0)
        for k in avg_weights:
            avg_weights[k] = round(avg_weights[k] / n_folds, 4)
        ic_tests = [f["ic_test"] for f in fold_results if f["ic_test"] is not None]
        ic_baselines = [f["ic_baseline_test"] for f in fold_results if f["ic_baseline_test"] is not None]
        avg_ic_test = round(sum(ic_tests) / len(ic_tests), 4) if ic_tests else None
        avg_ic_baseline_test = round(sum(ic_baselines) / len(ic_baselines), 4) if ic_baselines else None
    else:
        avg_weights = dict(_DEFAULT_WEIGHTS_NEUTRAL)
        avg_ic_test = None
        avg_ic_baseline_test = None

    # Gate OOS — voir docstring module. Tant que trop peu de folds
    # indépendants existent, on ne fait pas confiance au grid-search.
    best_weights = avg_weights if validated else dict(_DEFAULT_WEIGHTS_NEUTRAL)

    return {
        "phase": "calibrate_wfo",
        "last_run": _date.today().isoformat(),
        "params": {
            "train_days": train_days, "test_days": test_days,
            "window_days": window_days, "tolerance_days": tolerance_days,
        },
        "n_folds": n_folds,
        "min_folds_required": min_folds_required,
        "validated_oos": validated,
        "avg_ic_test": avg_ic_test,
        "avg_ic_baseline_test": avg_ic_baseline_test,
        "avg_weights": avg_weights,
        "best_weights": {**best_weights, "band_pct": DEFAULT_BAND_PCT},
        "fold_history": fold_results,
    }


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def _cli() -> int:
    state = run_walk_forward()
    _save_state(state)
    print(
        f"[PriceTargetCalibration] n_folds={state['n_folds']} "
        f"(min requis={state['min_folds_required']}) "
        f"validated_oos={state['validated_oos']} "
        f"avg_ic_test={state['avg_ic_test']} avg_ic_baseline_test={state['avg_ic_baseline_test']} "
        f"best_weights={state['best_weights']}"
    )
    if not state["validated_oos"]:
        print(
            f"[PriceTargetCalibration] pas assez de folds OOS indépendants "
            f"({state['n_folds']}/{state['min_folds_required']}) — "
            f"équi-pondération neutre servie en attendant plus d'historique."
        )
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
