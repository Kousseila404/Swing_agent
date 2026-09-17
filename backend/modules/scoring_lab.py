"""Scoring Lab — mesure l'edge du scoring sur la fenêtre live, sans toucher à la prod.

Audit 2026-09-17 : le composite top-20 faisait +18 % sur la fenêtre live…
mais l'univers équipondéré faisait +14 %, le bottom-50 +17,6 % et le
momentum seul +58 %. Sans cette table, « 18 % » se lit comme une preuve
d'efficacité du scoring ; avec elle, on voit que l'edge du composite est
faible et que le seul pilier robuste est Momentum (confirmé par le
diagnostic IC de juin : IC momentum +0,119, revisions +0,075, composite +0,037).

Le lab rejoue le backtest hebdo (équipondéré, 10 bps, lag 5 j) sur les
snapshots **live uniquement** (≥ LIVE_START) pour :
  • tailles de panier : top 5 / 10 / 20 / 30, univers EW, bottom 20 ;
  • piliers seuls : momentum, quality, value, piotroski, revisions ;
  • profils de poids alternatifs (composite recalculé depuis les scores
    de piliers stockés) — le profil prod n'est jamais modifié ici.

Sortie : `data/.scoring_lab.json` (CLI, lancé par run_titan.sh) et
`GET /api/scoring/lab` (lecture seule). ~20 s par variante.

CLI : python -m modules.scoring_lab [--quick]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from modules.log import logger

_BACKEND = Path(__file__).resolve().parents[1]
OUT_PATH = _BACKEND / "data" / ".scoring_lab.json"
LIVE_START = date(2026, 4, 22)
SLIPPAGE_BPS = 10.0

# Profils de poids (somme libre — renormalisée). "prod" = _scoring.py V14.1.
PILLARS = ("quality_score", "value_score", "risk_score", "momentum_score",
           "piotroski_score", "growth_score", "revisions_score", "insider_score")
PROFILES: dict[str, dict[str, float]] = {
    "prod_v14_1": {"quality_score": 0.18, "value_score": 0.13, "risk_score": 0.10,
                   "momentum_score": 0.17, "piotroski_score": 0.13, "growth_score": 0.13,
                   "revisions_score": 0.12, "insider_score": 0.04},
    "momentum_tilt": {"quality_score": 0.14, "value_score": 0.10, "risk_score": 0.08,
                      "momentum_score": 0.30, "piotroski_score": 0.13, "growth_score": 0.10,
                      "revisions_score": 0.15, "insider_score": 0.00},
    "momentum_revisions": {"momentum_score": 0.6, "revisions_score": 0.4},
    "equal_8": {p: 1.0 for p in PILLARS},
}


def _profile_score(row: dict[str, Any], weights: dict[str, float]) -> float | None:
    num = 0.0
    den = 0.0
    for k, w in weights.items():
        v = row.get(k)
        if v is None or w <= 0:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(fv):
            continue
        num += w * fv
        den += w
    return num / den if den > 0 else None


def make_rank_fn(*, field: str | None = None, weights: dict[str, float] | None = None, reverse: bool = True):
    """Fonction de classement compatible `backtest.run_titan_top_n(rank_fn=…)`."""
    def _rank(snapshot: dict[str, Any], top_n: int, *, active_filter=None, score_field: str = "titan_composite_score"):
        out: list[tuple[str, float]] = []
        for t, row in (snapshot.get("tickers") or {}).items():
            if active_filter is not None and t not in active_filter:
                continue
            if weights is not None:
                s = _profile_score(row, weights)
            else:
                v = row.get(field or score_field)
                s = float(v) if isinstance(v, (int, float)) and math.isfinite(float(v)) else None
            if s is None:
                continue
            out.append((t, s))
        out.sort(key=lambda x: x[1], reverse=reverse)
        return out[:top_n]
    return _rank


VARIANTS: list[dict[str, Any]] = [
    {"id": "top5",  "label": "TITAN top 5",  "group": "basket", "top_n": 5},
    {"id": "top10", "label": "TITAN top 10", "group": "basket", "top_n": 10},
    {"id": "top20", "label": "TITAN top 20 (référence)", "group": "basket", "top_n": 20},
    {"id": "top30", "label": "TITAN top 30", "group": "basket", "top_n": 30},
    {"id": "universe_ew", "label": "Univers équipondéré", "group": "baseline", "top_n": 1000},
    {"id": "bottom20", "label": "TITAN bottom 20", "group": "baseline", "top_n": 20, "reverse": False},
    {"id": "momentum_top20", "label": "Momentum seul · top 20", "group": "pillar", "top_n": 20, "field": "momentum_score"},
    {"id": "revisions_top20", "label": "Revisions seul · top 20", "group": "pillar", "top_n": 20, "field": "revisions_score"},
    {"id": "quality_top20", "label": "Quality seul · top 20", "group": "pillar", "top_n": 20, "field": "quality_score"},
    {"id": "value_top20", "label": "Value seul · top 20", "group": "pillar", "top_n": 20, "field": "value_score"},
    {"id": "piotroski_top20", "label": "Piotroski seul · top 20", "group": "pillar", "top_n": 20, "field": "piotroski_score"},
    {"id": "profile_momentum_tilt", "label": "Profil momentum_tilt · top 20", "group": "profile", "top_n": 20, "weights": PROFILES["momentum_tilt"]},
    {"id": "profile_momentum_revisions", "label": "Profil momentum+revisions · top 20", "group": "profile", "top_n": 20, "weights": PROFILES["momentum_revisions"]},
    {"id": "profile_equal_8", "label": "Profil équipondéré 8 piliers · top 20", "group": "profile", "top_n": 20, "weights": PROFILES["equal_8"]},
]
QUICK_IDS = {"top20", "universe_ew", "bottom20", "momentum_top20"}


def _live_stats(periods: list[dict[str, Any]]) -> dict[str, Any]:
    live = [p for p in periods if str(p.get("signal_date", "")) >= LIVE_START.isoformat()]
    idx = 1.0
    peak = 1.0
    mdd = 0.0
    wins = 0
    rets = []
    for p in live:
        r = float(p.get("portfolio_return") or 0.0)
        rets.append(r)
        idx *= 1 + r
        peak = max(peak, idx)
        mdd = min(mdd, idx / peak - 1)
        wins += 1 if r > 0 else 0
    n = len(live)
    mean = sum(rets) / n if n else 0.0
    var = sum((x - mean) ** 2 for x in rets) / (n - 1) if n > 1 else 0.0
    sharpe = (mean / math.sqrt(var) * math.sqrt(52)) if var > 0 else None
    return {
        "return_pct": round((idx - 1) * 100, 2) if n else None,
        "periods": n,
        "hit_rate": round(wins / n, 3) if n else None,
        "max_drawdown_pct": round(mdd * 100, 2) if n else None,
        "sharpe_weekly_ann": round(sharpe, 2) if sharpe is not None else None,
        "worst_week_pct": round(min(rets) * 100, 2) if rets else None,
        "start": live[0]["signal_date"] if live else None,
        "end": live[-1]["next_date"] if live else None,
    }


def run_lab(quick: bool = False) -> dict[str, Any]:
    from modules.backtest import run_titan_top_n

    rows: list[dict[str, Any]] = []
    t0 = time.time()
    for v in VARIANTS:
        if quick and v["id"] not in QUICK_IDS:
            continue
        rank_fn = make_rank_fn(field=v.get("field"), weights=v.get("weights"), reverse=v.get("reverse", True))
        t1 = time.time()
        try:
            res = run_titan_top_n(top_n=v["top_n"], benchmark=None, slippage_bps=SLIPPAGE_BPS,
                                  publication_lag_days=5, rank_fn=rank_fn)
            stats = _live_stats(res.to_dict().get("periods") or [])
        except Exception as exc:
            logger.warning(f"[ScoringLab] {v['id']} échoué : {exc}")
            stats = {"error": str(exc)}
        rows.append({"id": v["id"], "label": v["label"], "group": v["group"], "top_n": v["top_n"],
                     **stats, "elapsed_sec": round(time.time() - t1, 1)})
        logger.info(f"[ScoringLab] {v['id']}: {stats.get('return_pct')}% ({stats.get('periods')} périodes)")

    ref = next((r for r in rows if r["id"] == "top20"), None)
    base = next((r for r in rows if r["id"] == "universe_ew"), None)
    for r in rows:
        if r.get("return_pct") is not None and base and base.get("return_pct") is not None:
            r["excess_vs_universe_pct"] = round(r["return_pct"] - base["return_pct"], 2)
        if r.get("return_pct") is not None and ref and ref.get("return_pct") is not None:
            r["excess_vs_top20_pct"] = round(r["return_pct"] - ref["return_pct"], 2)

    verdict = _verdict(rows)
    return {
        "computed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "live_start": LIVE_START.isoformat(),
        "slippage_bps": SLIPPAGE_BPS,
        "rebalance": "hebdomadaire, équipondéré, lag publication 5 j",
        "rows": rows,
        "verdict": verdict,
        "elapsed_sec": round(time.time() - t0, 1),
        "caveat": "Fenêtre live courte (< 30 semaines) : indicatif, pas significatif. "
                  "Ne jamais re-pondérer le composite sur cette seule base.",
    }


def _verdict(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = {r["id"]: r for r in rows}
    top20 = (by.get("top20") or {}).get("return_pct")
    uni = (by.get("universe_ew") or {}).get("return_pct")
    bot = (by.get("bottom20") or {}).get("return_pct")
    mom = (by.get("momentum_top20") or {}).get("return_pct")
    msgs = []
    edge = None
    if top20 is not None and uni is not None:
        edge = round(top20 - uni, 2)
        msgs.append(f"Composite top-20 vs univers équipondéré : {edge:+.1f} pts")
    if top20 is not None and bot is not None:
        msgs.append(f"Top-20 vs bottom-20 : {top20 - bot:+.1f} pts (discrimination cross-section)")
    if mom is not None and top20 is not None:
        msgs.append(f"Momentum seul vs composite : {mom - top20:+.1f} pts")
    strength = "none"
    if edge is not None:
        strength = "strong" if edge >= 8 else "weak" if edge >= 2 else "none"
    return {"edge_vs_universe_pct": edge, "strength": strength, "messages": msgs}


def load_lab() -> dict[str, Any] | None:
    try:
        if OUT_PATH.exists():
            return json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[ScoringLab] lecture {OUT_PATH} : {exc}")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Scoring Lab — edge du scoring sur la fenêtre live")
    ap.add_argument("--quick", action="store_true", help="4 variantes seulement (~1 min)")
    args = ap.parse_args()
    out = run_lab(quick=args.quick)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT_PATH)
    for r in out["rows"]:
        print(f"{r['label']:44} {str(r.get('return_pct')):>8}%  hit={r.get('hit_rate')}  dd={r.get('max_drawdown_pct')}  vsUniv={r.get('excess_vs_universe_pct')}")
    print("verdict:", out["verdict"])
    print(f"→ {OUT_PATH} ({out['elapsed_sec']} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
