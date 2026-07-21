"""Enrichit universe.json avec `insider_score` (pilier Insider — Lot 17).

Pipeline appelé après build_universe → reads tickers, fetches SEC EDGAR Form 4
en parallèle (rate-limit 5 req/sec), calcule pillar score 0-100, écrit dans
chaque row.

Cache disque 24h via `sec_edgar._read_cache` → 2e run quasi-instantané.

Usage CLI :
    python -m modules.insider_enrich [--budget 100] [--workers 5]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from modules.log import logger
from modules.sec_edgar import (
    compute_insider_pillar_score,
    fetch_insider_activity,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_UNIVERSE_PATH = _PROJECT_ROOT / "data" / "universe.json"


def _enrich_one(ticker: str) -> tuple[str, dict[str, object]]:
    activity = fetch_insider_activity(ticker)
    pillar = compute_insider_pillar_score(activity)
    return ticker, {
        "insider_score": pillar.get("score"),
        "insider_components": pillar.get("components"),
        "insider_data_quality": pillar.get("data_quality"),
        "insider_n_filings": activity.n_filings_scanned,
        "insider_cluster_buying": activity.cluster_buying,
        "insider_buy_count_30d": activity.buy_count_30d,
        "insider_distinct_30d": activity.distinct_insiders_buying_30d,
        "insider_most_recent": activity.most_recent_filing_date,
        "insider_most_recent_8k": activity.most_recent_8k_date,
        # Propagation explicite de activity.error (ex: "cik_unknown",
        # "sec_fetch_failed") — jusqu'ici calculé (via pillar["reason"])
        # mais jamais persisté, donc invisible pour data_confidence.
        # None sur succès, y compris "0 filing trouvé" (pas une panne).
        "insider_error": pillar.get("reason"),
    }


def enrich_universe_with_insider(
    *,
    budget: int | None = None,
    workers: int = 5,
) -> dict[str, object]:
    """Lit universe.json, enrichit avec insider_score, persiste.

    Args:
      budget: limite le nombre de tickers traités (défaut = tous).
      workers: ThreadPool size pour les calls SEC parallèles.

    Returns:
      diagnostics : {n_total, n_enriched, n_errors, elapsed_s, ...}
    """
    if not _UNIVERSE_PATH.exists():
        raise FileNotFoundError(f"universe.json absent : {_UNIVERSE_PATH}")
    universe = json.loads(_UNIVERSE_PATH.read_text(encoding="utf-8"))
    tickers_map = universe.get("tickers") or {}
    tickers = list(tickers_map.keys())
    if budget is not None:
        tickers = tickers[:budget]

    t0 = time.time()
    logger.info(
        f"[insider_enrich] START on {len(tickers)} tickers, workers={workers}"
    )

    n_enriched = 0
    n_errors = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_enrich_one, t): t for t in tickers}
        for fut in as_completed(futures):
            try:
                ticker, fields = fut.result()
            except Exception as e:
                n_errors += 1
                logger.warning(f"[insider_enrich] {futures[fut]} crashed: {e}")
                continue
            row = tickers_map.get(ticker)
            if not isinstance(row, dict):
                continue
            row.update(fields)
            n_enriched += 1

    universe["tickers"] = tickers_map
    tmp = _UNIVERSE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(universe, indent=2), encoding="utf-8")
    tmp.replace(_UNIVERSE_PATH)

    elapsed = time.time() - t0
    diag: dict[str, object] = {
        "n_total":    len(tickers),
        "n_enriched": n_enriched,
        "n_errors":   n_errors,
        "elapsed_s":  round(elapsed, 1),
    }
    logger.info(f"[insider_enrich] DONE — {diag}")
    return diag


def _main() -> int:
    parser = argparse.ArgumentParser(prog="insider_enrich")
    parser.add_argument("--budget", type=int, default=None)
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    diag = enrich_universe_with_insider(
        budget=args.budget,
        workers=args.workers,
    )
    print(f"[insider_enrich] {diag}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
