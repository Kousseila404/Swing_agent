"""Enrichit universe.json avec les vraies revisions/earnings Finnhub.

Lot 17 — Quand FINNHUB_API_KEY est configurée, ce module remplace les
champs Revisions/Earnings scrappés par yfinance (qui sont pauvres) par
les vraies données Finnhub (datées, nominatives, granulaires).

Mode automatique : pas-op silencieux si la clé n'est pas configurée.
Cron-friendly : cache 24h via finnhub_provider.

Usage :
    python -m modules.finnhub_enrich [--budget 100] [--workers 1]

Note: workers=1 par défaut car le throttle 60/min est global au process.
Augmenter workers ne sert à rien (ralenti = throttle force séquentiel).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from data_providers.finnhub_provider import FinnhubProvider
from modules.log import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_UNIVERSE_PATH = _PROJECT_ROOT / "data" / "universe.json"


def enrich_universe_with_finnhub(
    *,
    budget: int | None = None,
    use_cache: bool = True,
) -> dict[str, object]:
    """Lit universe.json, enrichit Revisions/Earnings via Finnhub, persiste.

    Args:
      budget: limite le nombre de tickers (default = tous, ~9 min pour 491).
      use_cache: lit le cache 24h (recommandé).

    Returns:
      diagnostics : {n_total, n_enriched, n_errors, elapsed_s}
    """
    if not FinnhubProvider.is_configured():
        logger.info(
            "[finnhub_enrich] FINNHUB_API_KEY absent — pas-op. "
            "Add `FINNHUB_API_KEY=xxx` to backend/.env to enable."
        )
        return {"skipped": True, "reason": "no_api_key"}

    if not _UNIVERSE_PATH.exists():
        raise FileNotFoundError(f"universe.json absent : {_UNIVERSE_PATH}")
    universe = json.loads(_UNIVERSE_PATH.read_text(encoding="utf-8"))
    tickers_map = universe.get("tickers") or {}
    tickers = list(tickers_map.keys())
    if budget is not None:
        tickers = tickers[:budget]

    provider = FinnhubProvider()
    t0 = time.time()
    n_enriched = 0
    n_errors = 0

    logger.info(f"[finnhub_enrich] START on {len(tickers)} tickers")
    for i, ticker in enumerate(tickers, start=1):
        try:
            fdata = provider.get_revisions_and_earnings(ticker, use_cache=use_cache)
        except Exception as e:
            n_errors += 1
            logger.warning(f"[finnhub_enrich] {ticker} crashed: {e}")
            continue
        row = tickers_map.get(ticker)
        if not isinstance(row, dict):
            continue
        # Écrase uniquement si Finnhub a une valeur ; sinon préserve yfinance.
        for f in (
            "upgrades_30d", "downgrades_30d", "upgrades_90d", "downgrades_90d",
            "revisions_net_score",
            "earnings_surprise_pct_last", "earnings_surprise_avg_4q",
            "earnings_beat_rate_8q",
            "next_earnings_date",
        ):
            v = getattr(fdata, f, None)
            if v is not None:
                row[f] = v
        if fdata.next_earnings_eps_estimate is not None:
            row["next_earnings_eps_estimate"] = fdata.next_earnings_eps_estimate
        if fdata.target_price_consensus is not None and not row.get("price_target_mean"):
            row["price_target_mean"] = fdata.target_price_consensus
        if fdata.upgrade_downgrade_log:
            row["upgrade_downgrade_log"] = fdata.upgrade_downgrade_log
        row["finnhub_enriched_at"] = fdata.fetched_at
        # Toujours écrasé (même à None) — permet à un ticker de "guérir"
        # au run suivant si l'échec était transitoire (même mécanique que
        # insider_enrich.py → insider_error, Étape 5 roadmap).
        row["finnhub_error"] = fdata.error
        n_enriched += 1
        if i % 50 == 0:
            logger.info(f"[finnhub_enrich] progress {i}/{len(tickers)}")

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
    logger.info(f"[finnhub_enrich] DONE — {diag}")
    return diag


def _main() -> int:
    parser = argparse.ArgumentParser(prog="finnhub_enrich")
    parser.add_argument("--budget", type=int, default=None)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    diag = enrich_universe_with_finnhub(
        budget=args.budget,
        use_cache=not args.no_cache,
    )
    print(f"[finnhub_enrich] {diag}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
