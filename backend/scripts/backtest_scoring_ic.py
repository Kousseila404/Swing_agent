#!/usr/bin/env python
"""Backtest de re-ranking sur l'historique des snapshots TITAN.

Objectif (audit 2026-06-05) : répondre empiriquement à « le scoring est-il
mauvais ? » SANS toucher aux poids à l'aveugle. On mesure l'Information
Coefficient (IC) — corrélation de rang (Spearman) entre chaque score à la date
t et le rendement forward à t+H, moyennée sur toutes les dates de départ.

  IC > 0  : les titres bien notés montent plus (le pilier prédit).
  IC ≈ 0  : bruit.
  IC < 0  : contrarian (le pilier prédit à l'envers).

ATTENTION (lire avant de conclure) : ~44 snapshots calendaires sur UN seul
régime (BULL, avril-juin 2026). Fenêtres fortement chevauchantes → puissance
statistique quasi nulle. Résultat SUGGESTIF, pas une validation OOS. Sert à
décider s'il vaut la peine de lancer une vraie WFO, pas à recalibrer en prod.
"""
from __future__ import annotations

import glob
import gzip
import json
import os
import re
from datetime import date

SNAP_DIR = "data/.universe_history"
PILLARS = [
    "quality_score", "value_score", "risk_score", "sentiment_score",
    "momentum_score", "piotroski_score", "growth_score", "revisions_score",
    "insider_score",
]
COMPOSITES = ["titan_composite_score", "titan_composite_sector_pct"]


def _load() -> dict[date, dict[str, dict]]:
    out: dict[date, dict[str, dict]] = {}
    for f in sorted(glob.glob(f"{SNAP_DIR}/*.json.gz")):
        m = re.search(r"(\d{8})", os.path.basename(f))
        if not m:
            continue
        s = m.group(1)
        d = date(int(s[:4]), int(s[4:6]), int(s[6:]))
        payload = json.loads(gzip.open(f).read())
        tk = payload.get("tickers", {})
        out[d] = tk if isinstance(tk, dict) else {}
    return out


def _f(v):
    try:
        x = float(v)
        return x if x == x else None  # filtre NaN
    except (TypeError, ValueError):
        return None


def _rank(vals: list[float]) -> list[float]:
    """Rangs moyens (gestion des ex-aequo)."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 10:
        return None
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((x - mb) ** 2 for x in b) ** 0.5
    return num / (da * db) if da and db else None


def _spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 10:
        return None
    return _pearson(_rank(a), _rank(b))


def _nearest_forward(dates: list[date], t: date, horizon: int) -> date | None:
    """Snapshot le plus proche de t+horizon (calendaire), tolérance ±3 j."""
    target_ord = t.toordinal() + horizon
    cand = [d for d in dates if d > t]
    if not cand:
        return None
    best = min(cand, key=lambda d: abs(d.toordinal() - target_ord))
    return best if abs(best.toordinal() - target_ord) <= 3 else None


def run(horizon: int, top_n: int = 20) -> None:
    snaps = _load()
    dates = sorted(snaps)
    print(f"\n{'='*72}\nHORIZON = {horizon} jours calendaires | top_n={top_n}\n{'='*72}")

    # IC par pilier + composites, accumulé sur les dates de départ.
    fields = PILLARS + COMPOSITES
    ic_acc: dict[str, list[float]] = {k: [] for k in fields}
    # Backtest portefeuille : rendement forward moyen des top_n par schéma.
    schemes_ret: dict[str, list[float]] = {
        "composite (actuel)": [],
        "value_gate→qualité/growth/momo": [],
        "value_only": [],
        "momentum_only": [],
        "univers (moyenne)": [],
    }

    n_windows = 0
    for t in dates:
        u = _nearest_forward(dates, t, horizon)
        if u is None:
            continue
        cur, fut = snaps[t], snaps[u]
        # tickers présents aux deux dates avec prix valides
        common = []
        fwd = {}
        for tk in cur:
            if tk not in fut:
                continue
            p0 = _f(cur[tk].get("current_price"))
            p1 = _f(fut[tk].get("current_price"))
            if p0 and p1 and p0 > 0:
                common.append(tk)
                fwd[tk] = p1 / p0 - 1.0
        if len(common) < 50:
            continue
        n_windows += 1
        rets = [fwd[tk] for tk in common]

        # IC par champ
        for k in fields:
            xs, ys = [], []
            for tk in common:
                v = _f(cur[tk].get(k))
                if v is not None:
                    xs.append(v)
                    ys.append(fwd[tk])
            ic = _spearman(xs, ys)
            if ic is not None:
                ic_acc[k].append(ic)

        # Schémas de portefeuille. cur/fwd/common liés en défaut pour ne pas
        # capturer les variables de boucle (closures appelées dans l'itération).
        def topret(score_fn, pool=None, *, cur=cur, fwd=fwd, common=common):
            items = pool if pool is not None else common
            scored = [(score_fn(cur[tk]), tk) for tk in items
                      if score_fn(cur[tk]) is not None]
            scored.sort(key=lambda x: x[0], reverse=True)
            sel = [tk for _, tk in scored[:top_n]]
            return sum(fwd[tk] for tk in sel) / len(sel) if sel else None

        schemes_ret["composite (actuel)"].append(
            topret(lambda r: _f(r.get("titan_composite_score"))))
        schemes_ret["value_only"].append(
            topret(lambda r: _f(r.get("value_score"))))
        schemes_ret["momentum_only"].append(
            topret(lambda r: _f(r.get("momentum_score"))))
        schemes_ret["univers (moyenne)"].append(sum(rets) / len(rets))
        # value-gate : garder le tiers le moins cher (value_score haut),
        # puis classer ce sous-ensemble par quality+growth+momentum.
        vals = [(_f(cur[tk].get("value_score")), tk) for tk in common
                if _f(cur[tk].get("value_score")) is not None]
        vals.sort(key=lambda x: x[0], reverse=True)
        cheap = [tk for _, tk in vals[: max(top_n * 3, len(vals) // 3)]]

        def qgm(r):
            parts = [_f(r.get("quality_score")), _f(r.get("growth_score")),
                     _f(r.get("momentum_score"))]
            parts = [p for p in parts if p is not None]
            return sum(parts) / len(parts) if parts else None
        schemes_ret["value_gate→qualité/growth/momo"].append(
            topret(qgm, pool=cheap))

    print(f"fenêtres exploitables : {n_windows}\n")
    print("── Information Coefficient (Spearman) moyen par pilier ──")
    print("   (corr rang score→rendement forward ; >0 = prédit, <0 = contrarian)")
    rows = []
    for k in fields:
        v = ic_acc[k]
        if not v:
            continue
        mean = sum(v) / len(v)
        pos = sum(1 for x in v if x > 0) / len(v)
        rows.append((mean, k, pos, len(v)))
    for mean, k, pos, n in sorted(rows, reverse=True):
        tag = "  <<< COMPOSITE" if k in COMPOSITES else ""
        print(f"  {k:28s} IC={mean:+.3f}  (positif {pos*100:3.0f}% des fenêtres, n={n}){tag}")

    print("\n── Rendement forward moyen des top-20 par schéma de scoring ──")
    base = None
    out = []
    for name, r in schemes_ret.items():
        r = [x for x in r if x is not None]
        if not r:
            continue
        m = sum(r) / len(r)
        out.append((m, name))
        if name == "univers (moyenne)":
            base = m
    for m, name in sorted(out, reverse=True):
        edge = f"  (vs univers {(m-base)*100:+.2f} pts)" if base is not None and name != "univers (moyenne)" else ""
        print(f"  {name:34s} {m*100:+.2f}%{edge}")


if __name__ == "__main__":
    for h in (10, 20, 30):
        run(h)
