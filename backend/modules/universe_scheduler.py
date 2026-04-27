"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  MODULE — UNIVERSE SCHEDULER (Staggered Fundamental Refresh)                ║
║                                                                              ║
║  Gère le rafraîchissement tournant des ratios fondamentaux via FMP :        ║
║    • Budget quotidien strict (défaut 50 tickers/jour) adapté au tier gratuit║
║      FMP (250 calls/j ÷ 3 calls par ticker = ~80 tickers/j théoriques,      ║
║      50 laisse coussin pour alerter / sector metrics / retries).            ║
║    • Priorité : oldest first via `fetched_at` (le plus vieux passe en 1er). ║
║    • Skip automatique si le ticker a été rafraîchi depuis < 14 jours,       ║
║      sauf `force=True` ou liste explicite `force_tickers`.                  ║
║    • Merge non-destructif : les tickers non rafraîchis conservent leur      ║
║      ancien payload dans universe.json → pas de trou dans la source        ║
║      of truth entre deux runs du scheduler.                                 ║
║                                                                              ║
║  Cohérence avec build_universe() :                                           ║
║    • Mêmes providers via get_providers() si non injectés.                   ║
║    • Même FileLock + persistence atomique (save_universe).                  ║
║    • Scrape de la liste S&P/NDX à chaque run pour capturer les nouveaux     ║
║      entrants : ils sortent "most stale" (never fetched) et passent en top. ║
║                                                                              ║
║  CLI :                                                                       ║
║    python -m modules.universe_scheduler [--budget 50] [--min-age-days 14]   ║
║                                         [--force TICKER1,TICKER2]           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

from data_providers import (
    FundamentalProviderBase,
    MarketDataProviderBase,
    ProviderQuotaExceeded,
    get_providers,
)
from modules.log import logger
from modules.universe_engine import (
    DEFAULT_MIN_MARKET_CAP,
    TickerFundamentals,
    _collect_tickers,
    _enrich_momentum_via_provider,
    _enrich_ticker_via_provider,
    load_universe,
    save_universe,
)

# ─────────────────────────────────────────────────────────────────────────────
# DEFAULTS
# ─────────────────────────────────────────────────────────────────────────────

# 50 tickers × 3 calls FMP = 150 calls, laisse ~100 calls de marge sur le
# quota 250/jour pour alerter, sector_metrics, retries réseau.
DEFAULT_DAILY_BUDGET = 50

# Un ticker est considéré frais si son `fetched_at` est < 14 jours :
# les fondamentaux trimestriels bougent lentement, pas besoin de churn quotidien.
DEFAULT_MIN_AGE_DAYS = 14


# ─────────────────────────────────────────────────────────────────────────────
# STALENESS — ordonnancement prioritaire
# ─────────────────────────────────────────────────────────────────────────────

def _parse_iso(ts: str | None) -> datetime | None:
    """Parse 'YYYY-MM-DDTHH:MM:SSZ' → datetime naïf UTC.

    On retire volontairement la tzinfo : le reste du module compare à
    `datetime.utcnow()` (naïf). Homogénéité > rigueur tz."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        parsed = datetime.strptime(ts.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
    except (ValueError, TypeError):
        return None
    return parsed.replace(tzinfo=None)


def _age_days(ts: str | None, now: datetime) -> float:
    """Retourne l'âge en jours. None / parse-fail → +inf (priorité maximale)."""
    parsed = _parse_iso(ts)
    if parsed is None:
        return float("inf")
    return (now - parsed).total_seconds() / 86_400.0


def select_refresh_candidates(
    existing_tickers: dict[str, dict[str, Any]],
    all_known_tickers: list[str],
    *,
    budget: int = DEFAULT_DAILY_BUDGET,
    min_age_days: float = DEFAULT_MIN_AGE_DAYS,
    force_tickers: Sequence[str] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """
    Sélectionne au plus `budget` tickers à rafraîchir, oldest first.

    Règles :
      1. `force_tickers` passent TOUJOURS, consomment le budget en priorité.
      2. Les tickers inconnus de l'univers existant (never fetched) → age=+inf
         → priorisés juste après les force.
      3. Les tickers avec `fetched_at < now - min_age_days` sont candidats.
      4. Les autres (frais) sont ignorés.

    Retourne la liste ordonnée (oldest first, force prepended, uniques).
    """
    now = now or datetime.utcnow()
    forced = list(dict.fromkeys(force_tickers or []))  # préserve l'ordre, dédupe

    # age par ticker pour l'ensemble des tickers actuellement dans l'univers
    age_map = {t: _age_days(existing_tickers.get(t, {}).get("fetched_at"), now)
               for t in all_known_tickers}

    # Never-fetched : présents dans all_known mais pas dans existing_tickers
    # ou sans fetched_at → age = +inf donc triés en tête après les forced.

    # Candidats = age >= min_age_days (inclut les inf)
    stale = [t for t in all_known_tickers
             if t not in forced and age_map[t] >= min_age_days]
    stale.sort(key=lambda t: age_map[t], reverse=True)  # vieux en premier

    # Budget : forced consomment en priorité, puis stale dans l'ordre.
    selected: list[str] = []
    for t in forced:
        if t in all_known_tickers and t not in selected:
            selected.append(t)
        if len(selected) >= budget:
            return selected[:budget]

    for t in stale:
        if t in selected:
            continue
        selected.append(t)
        if len(selected) >= budget:
            break

    return selected


# ─────────────────────────────────────────────────────────────────────────────
# REFRESH PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def refresh_universe_staggered(
    *,
    indices: Sequence[str] = ("sp500", "ndx100"),
    budget: int = DEFAULT_DAILY_BUDGET,
    min_age_days: float = DEFAULT_MIN_AGE_DAYS,
    force_tickers: Sequence[str] | None = None,
    workers: int = 4,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP,
    fundamental_provider: FundamentalProviderBase | None = None,
    market_provider: MarketDataProviderBase | None = None,
) -> dict[str, Any]:
    """
    Rafraîchit au plus `budget` tickers fondamentaux par run, + leur momentum.
    Merge non-destructif : les autres tickers gardent leur ancien payload.

    Retourne le payload universe complet mergé (prêt pour save_universe()).
    """
    t0 = time.time()
    if fundamental_provider is None or market_provider is None:
        f, m = get_providers()
        fundamental_provider = fundamental_provider or f
        market_provider = market_provider or m

    # 1. Source de vérité actuelle
    existing = load_universe()
    existing_tickers: dict[str, dict[str, Any]] = existing.get("tickers") or {}
    existing_sources: dict[str, list[str]] = {}
    for t, f in existing_tickers.items():
        existing_sources[t] = list(f.get("source_indices") or [])

    # 2. Tickers connus = union(existing, scraping Wikipedia actuel)
    live_sources = _collect_tickers(indices)
    all_known = sorted(set(existing_tickers.keys()) | set(live_sources.keys()))
    if not all_known:
        raise RuntimeError(
            "[Scheduler] Aucun ticker connu — univers existant vide ET "
            "scrape Wikipedia a échoué."
        )

    # Fusion des sources (Wikipedia à jour prime sur le snapshot persisté).
    for t in all_known:
        if t in live_sources:
            existing_sources[t] = live_sources[t]

    # 3. Sélection des candidats au refresh (oldest first, forced prepended)
    now = datetime.utcnow()
    to_refresh = select_refresh_candidates(
        existing_tickers=existing_tickers,
        all_known_tickers=all_known,
        budget=budget,
        min_age_days=min_age_days,
        force_tickers=force_tickers,
        now=now,
    )
    n_skipped = len(all_known) - len(to_refresh)

    logger.info(
        f"[Scheduler] Budget {budget} / min_age {min_age_days}j → "
        f"refresh {len(to_refresh)} tickers, skip {n_skipped} (frais ou hors budget). "
        f"Providers: fund={fundamental_provider.name}, market={market_provider.name}"
    )
    print(
        f"[Scheduler] Refresh {len(to_refresh)}/{len(all_known)} tickers "
        f"(budget={budget}, min_age={min_age_days}j)",
        flush=True,
    )

    # 4. Enrichissement fondamental parallèle, quota-aware
    refreshed: dict[str, TickerFundamentals] = {}
    n_errors = 0
    aborted_quota = False
    quota_msg: str | None = None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _enrich_ticker_via_provider, t,
                existing_sources.get(t, []),
                fundamental_provider,
            ): t for t in to_refresh
        }
        for i, fut in enumerate(as_completed(futures), start=1):
            t = futures[fut]
            try:
                refreshed[t] = fut.result()
            except ProviderQuotaExceeded as e:
                aborted_quota = True
                quota_msg = str(e)[:160]
                logger.warning(
                    f"[Scheduler] Quota FMP sur {t} — on stoppe le refresh, "
                    f"les tickers restants gardent leur ancien payload."
                )
                # On NE LÈVE PAS : l'univers existant est préservé tel quel.
                # Les futures en vol vont elles aussi raise — on ignore.
                break
            except Exception as e:
                logger.debug(f"[Scheduler] {t} refresh crashed: {e}")
                n_errors += 1
            if i % 10 == 0:
                print(f"[Scheduler] Progress {i}/{len(to_refresh)}", flush=True)

    # 5. Momentum batch : UNIQUEMENT sur les tickers rafraîchis (budget Polygon
    #    respecté — 5/min sur free tier). Les autres gardent leur momentum existant.
    momentum: dict[str, dict[str, float | None]] = {}
    if refreshed:
        tickers_for_momentum = sorted(refreshed.keys())
        momentum = _enrich_momentum_via_provider(tickers_for_momentum, market_provider)

    # 6. Merge non-destructif dans l'univers existant
    merged_tickers: dict[str, dict[str, Any]] = {**existing_tickers}
    for t, fund in refreshed.items():
        # fund est déjà un TickerFundamentals — conversion dict
        d = fund.to_dict()
        mom = momentum.get(t) or {}
        d["momentum_return_pct"]     = mom.get("return_pct")
        d["momentum_volatility_pct"] = mom.get("volatility_pct")
        d["momentum_risk_adjusted"]  = mom.get("risk_adjusted")
        d["momentum_high_52w_ratio"] = mom.get("high_52w_ratio")
        merged_tickers[t] = d

    # 7. Filtre market cap (re-applique sur l'union, au cas où un ticker
    #    nouveau-entré aurait fetched_at=None et market_cap manquant).
    kept: dict[str, dict[str, Any]] = {}
    rejected_low_cap = 0
    rejected_no_cap = 0
    for t, f in merged_tickers.items():
        mc = f.get("market_cap")
        if mc is None:
            rejected_no_cap += 1
            continue
        if mc < min_market_cap:
            rejected_low_cap += 1
            continue
        kept[t] = f

    # 8. Sectors : recalcul d'index inverse
    sectors: dict[str, list[str]] = {}
    for t, f in kept.items():
        sec = f.get("sector") or "Unknown"
        sectors.setdefault(sec, []).append(t)
    for sec in sectors:
        sectors[sec].sort()

    elapsed = time.time() - t0
    logger.info(
        f"[Scheduler] Done in {elapsed:.1f}s — "
        f"refreshed {len(refreshed)}, errors {n_errors}, kept {len(kept)}. "
        f"Quota abort: {aborted_quota}"
    )

    return {
        "version":        1,
        "updated_at":     now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_indices": list(indices),
        "filter": {
            "min_market_cap_usd": min_market_cap,
        },
        "stats": {
            "n_scanned":          len(all_known),
            "n_kept":             len(kept),
            "n_rejected_low_cap": rejected_low_cap,
            "n_rejected_no_cap":  rejected_no_cap,
            "n_errors":           n_errors,
            "elapsed_seconds":    round(elapsed, 1),
            "workers":            workers,
        },
        "scheduler": {
            "mode":               "staggered",
            "budget":             budget,
            "min_age_days":       min_age_days,
            "n_refreshed":        len(refreshed),
            "n_skipped_fresh":    n_skipped,
            "forced":             list(force_tickers or []),
            "quota_aborted":      aborted_quota,
            "quota_message":      quota_msg,
            "next_stale_count":   sum(
                1 for t in kept
                if _age_days(kept[t].get("fetched_at"),
                             now + timedelta(days=1)) >= min_age_days
            ),
        },
        "sectors": sectors,
        "tickers": kept,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI — intégrable dans cron quotidien
# ─────────────────────────────────────────────────────────────────────────────

def _parse_indices(raw: str) -> list[str]:
    wanted = [x.strip().lower() for x in raw.split(",") if x.strip()]
    allowed = {"sp500", "ndx100"}
    unknown = [x for x in wanted if x not in allowed]
    if unknown:
        raise SystemExit(
            f"[Scheduler] Indices inconnus: {unknown}. Autorisés: {sorted(allowed)}"
        )
    return wanted


def _main() -> int:
    parser = argparse.ArgumentParser(
        prog="universe_scheduler",
        description="Refresh tournant de universe.json (staggered, quota-friendly).",
    )
    parser.add_argument("--indices", default="sp500,ndx100")
    parser.add_argument("--budget", type=int, default=DEFAULT_DAILY_BUDGET)
    parser.add_argument("--min-age-days", type=float, default=DEFAULT_MIN_AGE_DAYS)
    parser.add_argument(
        "--force", default="",
        help="Liste CSV de tickers à forcer (ignore min-age).",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--reason", default="scheduler (staggered)")
    args = parser.parse_args()

    indices = _parse_indices(args.indices)
    forced = [t.strip().upper() for t in args.force.split(",") if t.strip()]

    print(
        f"[Scheduler] START indices={indices} budget={args.budget} "
        f"min_age={args.min_age_days}j force={forced or 'none'}",
        flush=True,
    )

    try:
        payload = refresh_universe_staggered(
            indices=indices,
            budget=args.budget,
            min_age_days=args.min_age_days,
            force_tickers=forced or None,
            workers=args.workers,
        )
    except Exception as e:
        logger.error(f"[Scheduler] FATAL: {e}", exc_info=True)
        print(f"[Scheduler] FATAL: {e}", flush=True)
        return 2

    try:
        save_universe(payload, reason=args.reason)
    except Exception as e:
        logger.error(f"[Scheduler] PERSIST FAILED: {e}", exc_info=True)
        return 3

    st = payload["scheduler"]
    print(
        f"[Scheduler] DONE refreshed={st['n_refreshed']} "
        f"skipped={st['n_skipped_fresh']} quota_abort={st['quota_aborted']} "
        f"next_stale={st['next_stale_count']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
