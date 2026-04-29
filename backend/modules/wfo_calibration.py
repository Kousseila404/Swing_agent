"""Walk-Forward IC — calibration des poids piliers TITAN sur historique.

Audit S1.3 (2026-04-27) — pondérations non-validées OOS.
=========================================================
Les poids actuels (Q=0.22, V=0.16, R=0.11, S=0.08, M=0.18, P=0.11, G=0.14)
viennent d'un mix entre littérature (Piotroski, Novy-Marx, Asness…) et
intuition. Ils n'ont jamais été validés out-of-sample sur l'historique
universe_history.

Méthodologie (Walk-Forward, Engle-Granger 1987 style) :
  1. Découpe le calendrier des snapshots en (train, test) glissants.
  2. Sur la fenêtre TRAIN :
       • Pour chaque pilier ∈ {quality, value, risk, sentiment, momentum,
         piotroski, growth}, calcule l'Information Coefficient (Spearman
         rank correlation) entre score_pilier(t) et forward_return(t→t+H)
         de tous les tickers.
       • IC > 0 = le pilier prédit positivement le forward return.
  3. Optimise les poids w_i ≥ 0, Σw_i=1, qui maximisent l'IC du composite
     pondéré sur le TEST (validation).
  4. Roule la fenêtre, agrège les poids optimaux par fold.

Cette version est *minimaliste* — pas de SciPy requis. Optimisation par
recherche gloutonne sur grille (12 piliers ⇒ simplex projeté). C'est plus
lent que LP/quadratic programming mais sans dépendance lourde et amplement
suffisant tant qu'on a < 50 folds.

Sortie : data/wfo_weights.json — historique des poids optimaux par fold +
synthèse moyenne. À comparer avec les poids prod hardcodés dans
sector_metrics/_scoring.py.

Usage :
    python -m modules.wfo_calibration --train-days 60 --test-days 20
    python -m modules.wfo_calibration --json   # sortie machine-readable
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from modules import universe_history
from modules.log import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
WFO_OUTPUT_PATH = _PROJECT_ROOT / "data" / "wfo_weights.json"

# Piliers évalués — doivent matcher les clés produites par sector_metrics.
PILLARS: tuple[str, ...] = (
    "quality_score",
    "value_score",
    "risk_score",
    "sentiment_score",
    "momentum_score",
    "piotroski_score",
    "growth_score",
)

# Pas par pilier sur la grille (granularité du simplex 1.0/STEP par axe).
# 5 % suffit pour départager les piliers au-delà du bruit IC ; finer
# ferait exploser la combinatoire (avec 7 piliers, 5 % → 27 132 simplexes,
# 1 % → ~2 millions).
_GRID_STEP = 0.05


@dataclass
class FoldResult:
    train_start: str
    train_end:   str
    test_start:  str
    test_end:    str
    n_train_snapshots: int
    n_test_snapshots:  int
    pillar_ic_train: dict[str, float]   # IC par pilier (TRAIN)
    pillar_ic_test:  dict[str, float]   # IC par pilier (TEST)
    optimal_weights: dict[str, float]   # poids optimisés sur TRAIN
    composite_ic_train: float
    composite_ic_test:  float


@dataclass
class WfoResult:
    n_folds:        int
    pillars:        list[str]
    folds:          list[FoldResult] = field(default_factory=list)
    avg_weights:    dict[str, float] = field(default_factory=dict)
    avg_ic_test:    float = 0.0
    diagnostics:    dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_folds":     self.n_folds,
            "pillars":     self.pillars,
            "avg_weights": self.avg_weights,
            "avg_ic_test": round(self.avg_ic_test, 4),
            "diagnostics": self.diagnostics,
            "folds":       [asdict(f) for f in self.folds],
        }


# ─────────────────────────────────────────────────────────────────
# IC (Spearman rank correlation)
# ─────────────────────────────────────────────────────────────────

def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank-correlation. None si < 3 points ou variance nulle.

    Implémentation : on convertit en ranks (avec gestion des ties = mid-rank)
    puis Pearson sur les ranks.
    """
    if len(xs) < 3 or len(xs) != len(ys):
        return None

    def _ranks(vals: list[float]) -> list[float]:
        idx = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(vals):
            j = i
            while j + 1 < len(vals) and vals[idx[j + 1]] == vals[idx[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[idx[k]] = avg_rank
            i = j + 1
        return ranks

    rx = _ranks(xs)
    ry = _ranks(ys)
    mx = statistics.mean(rx)
    my = statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=False))
    denx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    deny = math.sqrt(sum((b - my) ** 2 for b in ry))
    if denx <= 0 or deny <= 0:
        return None
    return num / (denx * deny)


# ─────────────────────────────────────────────────────────────────
# Forward returns extraction
# ─────────────────────────────────────────────────────────────────

def _build_pairs(
    snap_score: dict[str, Any],
    snap_price_t: dict[str, Any],
    snap_price_t1: dict[str, Any],
) -> list[dict[str, Any]]:
    """Construit les rows {pillar1, ..., pillar7, fwd_return} en croisant
    trois snapshots :
      • `snap_score`   : source des piliers utilisés pour ranker (peut être
                          *plus ancien* que t pour anti-lookahead).
      • `snap_price_t` : source du prix d'entrée (date de signal).
      • `snap_price_t1`: source du prix de sortie (date de mesure return).

    Pour `snap_score == snap_price_t` (cas par défaut, no-lag), le comportement
    est identique à la version pré-S3.x.
    """
    score_tickers = snap_score.get("tickers") or {}
    px_t_tickers = snap_price_t.get("tickers") or {}
    px_t1_tickers = snap_price_t1.get("tickers") or {}
    rows: list[dict[str, Any]] = []
    for tk, score_row in score_tickers.items():
        row_t = px_t_tickers.get(tk)
        row_t1 = px_t1_tickers.get(tk)
        if not row_t or not row_t1:
            continue
        try:
            px_t = float(row_t.get("current_price"))
            px_t1 = float(row_t1.get("current_price"))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(px_t) and math.isfinite(px_t1) and px_t > 0):
            continue
        fwd = (px_t1 / px_t) - 1.0
        entry: dict[str, Any] = {"ticker": tk, "fwd_return": fwd}
        for p in PILLARS:
            v = score_row.get(p)
            try:
                v = float(v) if v is not None else None
            except (TypeError, ValueError):
                v = None
            if v is None or not math.isfinite(v):
                continue
            entry[p] = v
        rows.append(entry)
    return rows


def _ic_per_pillar(rows: list[dict[str, Any]]) -> dict[str, float]:
    """IC Spearman pillar→fwd_return. Pillier sans assez de couverture = nan
    (signalé None ⇒ exclu de l'optimisation).
    """
    out: dict[str, float] = {}
    fwd = [r["fwd_return"] for r in rows]
    for p in PILLARS:
        xs = [r.get(p) for r in rows]
        # On filtre paire-par-paire les None pour préserver l'alignement.
        paired = [(x, y) for x, y in zip(xs, fwd, strict=False) if x is not None]
        if len(paired) < 5:
            out[p] = float("nan")
            continue
        ic = _spearman([a for a, _ in paired], [b for _, b in paired])
        out[p] = ic if ic is not None else float("nan")
    return out


def _composite_ic(
    rows: list[dict[str, Any]],
    weights: dict[str, float],
) -> float | None:
    """IC du composite pondéré sur les rows. None si pas assez de rows valides."""
    fwd: list[float] = []
    score: list[float] = []
    for r in rows:
        # On veut au moins 1 pilier observé pour ce ticker, sinon on skip.
        s = 0.0
        weight_sum = 0.0
        for p, w in weights.items():
            v = r.get(p)
            if v is None:
                continue
            s += w * v
            weight_sum += w
        if weight_sum <= 0:
            continue
        fwd.append(r["fwd_return"])
        score.append(s / weight_sum)  # rebase sur les piliers observés
    if len(fwd) < 5:
        return None
    return _spearman(score, fwd)


# ─────────────────────────────────────────────────────────────────
# Optimisation simplex (grille gloutonne)
# ─────────────────────────────────────────────────────────────────

def _enum_simplex(n: int, step: float) -> list[tuple[float, ...]]:
    """Énumère les vecteurs (w_1,...,w_n) ≥ 0 sur grille step, Σ=1.

    Pour n=7, step=0.05 ⇒ C(20+6,6) = 38 760 simplexes ⇒ tractable.
    """
    n_steps = round(1.0 / step)
    out: list[tuple[float, ...]] = []

    def _rec(idx: int, remaining: int, acc: tuple[int, ...]) -> None:
        if idx == n - 1:
            out.append(acc + (remaining,))
            return
        for v in range(remaining + 1):
            _rec(idx + 1, remaining - v, acc + (v,))

    _rec(0, n_steps, ())
    return [tuple(round(x * step, 4) for x in pt) for pt in out]


def _optimal_weights(rows: list[dict[str, Any]]) -> tuple[dict[str, float], float]:
    """Trouve les poids w (Σ=1, w≥0) qui max IC composite sur `rows`.

    Réduit aux piliers avec IC observable (au moins 5 paires non-None) pour
    éviter qu'un pilier dégénéré (tout None) reçoive un poids fantôme. Les
    autres reçoivent 0.
    """
    pillar_ic = _ic_per_pillar(rows)
    valid_pillars = [p for p, ic in pillar_ic.items() if not math.isnan(ic)]
    if len(valid_pillars) < 2:
        # Sous 2 piliers valides l'optimisation est triviale / non-informative.
        equal_weights = {p: 1.0 / len(PILLARS) for p in PILLARS}
        ic = _composite_ic(rows, equal_weights) or 0.0
        return equal_weights, ic

    best_ic = -2.0
    best_w_vec: tuple[float, ...] = tuple()
    for pt in _enum_simplex(len(valid_pillars), _GRID_STEP):
        w = {p: pt[i] for i, p in enumerate(valid_pillars)}
        ic = _composite_ic(rows, w)
        if ic is not None and ic > best_ic:
            best_ic = ic
            best_w_vec = pt

    # Reconstitue le dict complet (piliers exclus → 0)
    full: dict[str, float] = {p: 0.0 for p in PILLARS}
    for i, p in enumerate(valid_pillars):
        full[p] = best_w_vec[i] if best_w_vec else 1.0 / len(valid_pillars)
    return full, best_ic if best_ic > -2 else 0.0


# ─────────────────────────────────────────────────────────────────
# Walk-forward driver
# ─────────────────────────────────────────────────────────────────

def run_walk_forward(
    train_days: int = 60,
    test_days: int = 20,
    *,
    publication_lag_days: int = 0,
) -> WfoResult:
    """Walk-forward : roule (train, test) sur les snapshots disponibles.

    Args:
        train_days : taille de la fenêtre TRAIN en jours calendaires.
        test_days  : taille TEST.
        publication_lag_days : décale le snapshot scoring de N jours pour
            éviter le lookahead fondamentaux (défaut 0). Recommandé 90 j en
            production. Une paire est skippée si aucun snapshot suffisamment
            ancien n'est disponible avant la date d'entrée.

    Le fold avance de `test_days` à chaque étape (non-overlapping test).
    """
    all_dates = universe_history.list_snapshots()
    if len(all_dates) < 5:
        raise ValueError(
            f"Au moins 5 snapshots requis pour 1 fold (train+test+forward). "
            f"Disponibles : {len(all_dates)}"
        )

    # Charge tous les snapshots en mémoire (pour 5 ans × 500 tk × 50 KB ≈ 50 MB).
    snapshots: list[tuple[date, dict[str, Any]]] = []
    for d in all_dates:
        snap = universe_history.read_snapshot(d)
        if snap:
            snapshots.append((d, snap))
    if len(snapshots) < 5:
        raise ValueError("Snapshots illisibles")

    # Construit les paires (snap_score, snap_price_t, snap_price_t1) → fwd_return.
    # Avec publication_lag_days > 0 : snap_score = snapshot le plus récent
    # antérieur de ≥ lag à la date d'entrée d0. Sans lag, snap_score = snap[i].
    from datetime import timedelta as _td
    snapshot_dates = [d for d, _ in snapshots]
    snapshots_by_date = {d: s for d, s in snapshots}

    def _score_snap_for(d_signal: date) -> dict[str, Any] | None:
        if publication_lag_days <= 0:
            return snapshots_by_date.get(d_signal)
        cutoff = d_signal - _td(days=publication_lag_days)
        chosen: dict[str, Any] | None = None
        for d_x in snapshot_dates:
            if d_x <= cutoff:
                chosen = snapshots_by_date[d_x]
            else:
                break
        return chosen

    pair_rows: list[tuple[date, list[dict[str, Any]]]] = []
    n_skipped_lag = 0
    for (d0, s0), (_d1, s1) in zip(snapshots[:-1], snapshots[1:], strict=False):
        snap_score = _score_snap_for(d0)
        if snap_score is None:
            n_skipped_lag += 1
            continue
        rows = _build_pairs(snap_score, s0, s1)
        if rows:
            pair_rows.append((d0, rows))

    if len(pair_rows) < 3:
        raise ValueError(
            f"Moins de 3 paires utilisables (snapshots avec prix valides). "
            f"Disponibles : {len(pair_rows)}"
        )

    folds: list[FoldResult] = []
    pair_dates = [d for d, _ in pair_rows]
    start_idx = 0
    while True:
        # TRAIN couvre [start_idx, start_idx+train_days)
        train_end_date = pair_dates[start_idx]
        # On accumule jusqu'à atteindre train_days écoulés
        train_pairs: list[tuple[date, list[dict[str, Any]]]] = []
        while (
            start_idx + len(train_pairs) < len(pair_rows)
            and (pair_dates[start_idx + len(train_pairs)] - pair_dates[start_idx]).days < train_days
        ):
            train_pairs.append(pair_rows[start_idx + len(train_pairs)])
        if not train_pairs:
            break
        train_end_date = train_pairs[-1][0]

        # TEST commence juste après
        test_idx = start_idx + len(train_pairs)
        if test_idx >= len(pair_rows):
            break
        test_start_date = pair_dates[test_idx]
        test_pairs: list[tuple[date, list[dict[str, Any]]]] = []
        while (
            test_idx + len(test_pairs) < len(pair_rows)
            and (pair_dates[test_idx + len(test_pairs)] - test_start_date).days < test_days
        ):
            test_pairs.append(pair_rows[test_idx + len(test_pairs)])
        if not test_pairs:
            break

        train_rows = [r for _, rs in train_pairs for r in rs]
        test_rows = [r for _, rs in test_pairs for r in rs]
        if len(train_rows) < 20 or len(test_rows) < 10:
            # Fold trop fin pour être informatif. Avance.
            start_idx = test_idx
            continue

        opt_w, ic_train = _optimal_weights(train_rows)
        ic_test = _composite_ic(test_rows, opt_w) or 0.0

        fold = FoldResult(
            train_start=train_pairs[0][0].isoformat(),
            train_end=train_end_date.isoformat(),
            test_start=test_start_date.isoformat(),
            test_end=test_pairs[-1][0].isoformat(),
            n_train_snapshots=len(train_pairs),
            n_test_snapshots=len(test_pairs),
            pillar_ic_train={p: round(v, 4) for p, v in _ic_per_pillar(train_rows).items() if not math.isnan(v)},
            pillar_ic_test={p: round(v, 4) for p, v in _ic_per_pillar(test_rows).items() if not math.isnan(v)},
            optimal_weights={p: round(w, 4) for p, w in opt_w.items()},
            composite_ic_train=round(ic_train, 4),
            composite_ic_test=round(ic_test, 4),
        )
        folds.append(fold)

        # Avance d'une fenêtre TEST (non-overlapping test)
        start_idx = test_idx + len(test_pairs)

    # Agrégat : poids moyens, IC moyenne TEST (out-of-sample)
    avg_weights: dict[str, float] = {p: 0.0 for p in PILLARS}
    if folds:
        for f in folds:
            for p, w in f.optimal_weights.items():
                avg_weights[p] += w
        for p in avg_weights:
            avg_weights[p] = round(avg_weights[p] / len(folds), 4)
        avg_ic_test = sum(f.composite_ic_test for f in folds) / len(folds)
    else:
        avg_ic_test = 0.0

    result = WfoResult(
        n_folds=len(folds),
        pillars=list(PILLARS),
        folds=folds,
        avg_weights=avg_weights,
        avg_ic_test=avg_ic_test,
        diagnostics={
            "n_snapshots":          len(snapshots),
            "n_pairs":              len(pair_rows),
            "n_skipped_lag":        n_skipped_lag,
            "train_days":           train_days,
            "test_days":            test_days,
            "publication_lag_days": publication_lag_days,
            "grid_step":            _GRID_STEP,
            "date_range": {
                "start": snapshots[0][0].isoformat(),
                "end":   snapshots[-1][0].isoformat(),
            },
        },
    )
    return result


# ─────────────────────────────────────────────────────────────────
# Persistence + CLI
# ─────────────────────────────────────────────────────────────────

def _persist(result: WfoResult) -> Path:
    WFO_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = WFO_OUTPUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    tmp.replace(WFO_OUTPUT_PATH)
    return WFO_OUTPUT_PATH


def _main() -> int:
    p = argparse.ArgumentParser(prog="wfo_calibration",
        description="Walk-Forward IC — calibration des poids piliers TITAN.")
    p.add_argument("--train-days", type=int, default=60,
                   help="Taille fenêtre TRAIN en jours (défaut 60)")
    p.add_argument("--test-days", type=int, default=20,
                   help="Taille fenêtre TEST en jours (défaut 20)")
    p.add_argument("--json", action="store_true",
                   help="Sortie JSON sur stdout (sinon : tableau)")
    p.add_argument("--publication-lag-days", type=int, default=0,
                   help="Décalage du snapshot scoring (anti-lookahead "
                        "fondamentaux). 0 = pas de lag. 90 = recommandé "
                        "production (lag 10-K).")
    p.add_argument("--no-persist", action="store_true",
                   help="Ne pas écrire data/wfo_weights.json")
    args = p.parse_args()

    try:
        result = run_walk_forward(
            train_days=args.train_days,
            test_days=args.test_days,
            publication_lag_days=args.publication_lag_days,
        )
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2

    if not args.no_persist:
        out_path = _persist(result)
        logger.info(f"[WFO] Résultats persistés → {out_path}")

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return 0

    diag = result.diagnostics
    print("=== WFO IC calibration — TITAN piliers ===")
    print(f"Période : {diag['date_range']['start']} → {diag['date_range']['end']}")
    print(f"Snapshots : {diag['n_snapshots']} | paires : {diag['n_pairs']} | folds : {result.n_folds}")
    print(f"Train : {diag['train_days']}j | Test : {diag['test_days']}j | grid step : {diag['grid_step']}")
    print()
    if not result.folds:
        print("⚠️  Aucun fold valide produit (historique trop court). Ré-essayer "
              "avec --train-days / --test-days plus petits, ou attendre que "
              "universe_history grossisse.")
        return 1

    print("=== Poids optimaux moyens (validation OOS) ===")
    sorted_w = sorted(result.avg_weights.items(), key=lambda kv: -kv[1])
    for p, w in sorted_w:
        bar = "█" * int(w * 60 + 0.5)
        print(f"  {p:<20} {w:.3f}  {bar}")
    print()
    print(f"IC moyenne TEST (out-of-sample) : {result.avg_ic_test:+.4f}")
    print()

    print(f"=== Folds ({result.n_folds}) ===")
    for i, f in enumerate(result.folds, 1):
        sign_train = "+" if f.composite_ic_train >= 0 else ""
        sign_test = "+" if f.composite_ic_test >= 0 else ""
        print(
            f"  #{i}  train {f.train_start}→{f.train_end} "
            f"({f.n_train_snapshots} pairs)  "
            f"IC_train={sign_train}{f.composite_ic_train:.3f}  "
            f"IC_test={sign_test}{f.composite_ic_test:.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
