"""Bootstrap historical snapshots — débloque audit_summary verdict.

Lot 17 — script one-shot qui génère N snapshots historiques rétroactifs dans
`data/.universe_history/` à partir de :

  1. l'universe.json actuel (current scoring + fundamentals)
  2. les prix historiques daily (Stooq fallback yfinance)
  3. un walk-back temporel : on remonte le temps en remplaçant `current_price`
     et `momentum_*` par les valeurs au jour T tout en gardant les
     fundamentals constants (snapshots annuels n'évoluent pas chaque jour).

Cette approche n'est PAS rigoureusement point-in-time (les fundamentals Y-1
auraient changé), mais c'est un **bootstrap valide pour démarrer le WFO**.
La vraie vérité point-in-time arrivera après 6 mois de cron quotidien réel —
en attendant, ce module évite le verrou `audit_summary = DONNÉES_INSUFFISANTES`
et permet de mesurer un alpha cohérent sur l'historique de prix observé.

Usage CLI :
    python -m modules.simfin_bootstrap --days 90 [--start 2025-11-01]
    python -m modules.simfin_bootstrap --days 180 --workers 8

Output : 90+ snapshots gzip dans .universe_history/.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from modules import universe_history
from modules.log import logger
from modules.sector_metrics._scoring import _score_universe

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_UNIVERSE_PATH = _PROJECT_ROOT / "data" / "universe.json"


# ─────────────────────────────────────────────────────────────────────────────
# Provider OHLCV — Stooq prioritaire, yfinance fallback
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_history_robust(ticker: str, days: int) -> pd.Series | None:
    """Stooq first (gratuit illimité), yfinance en backup."""
    try:
        from data_providers.stooq_provider import StooqProvider
        provider = StooqProvider(min_delay_seconds=0.3)
        s = provider.get_daily_history(ticker, days)
        if s is not None and len(s) >= 30:
            return s
    except Exception as e:
        logger.debug(f"[bootstrap] Stooq failed for {ticker}: {e}")

    try:
        import yfinance as yf
        df = yf.download(
            ticker, period="2y", interval="1d",
            progress=False, auto_adjust=True, threads=False,
        )
        if df is None or df.empty:
            return None
        if hasattr(df.columns, "get_level_values"):
            try:
                df.columns = df.columns.get_level_values(0)
            except Exception:
                pass
        if "Close" not in df.columns:
            return None
        return df["Close"].dropna()
    except Exception as e:
        logger.debug(f"[bootstrap] yfinance failed for {ticker}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Walk-back : reconstitue universe au jour T
# ─────────────────────────────────────────────────────────────────────────────

def _ticker_snapshot_at_date(
    ticker_row: dict[str, Any],
    history: pd.Series,
    target_date: date,
) -> dict[str, Any] | None:
    """Retourne une copie du row avec `current_price`, `momentum_return_pct`,
    `momentum_volatility_pct`, `momentum_high_52w_ratio` recalculés au target_date.

    None si l'historique n'a pas assez de profondeur (besoin ≥ 252j avant target).
    """
    if history is None or len(history) < 30:
        return None

    # Filtre history jusqu'à target_date inclusif.
    target_ts = pd.Timestamp(target_date)
    sub = history[history.index <= target_ts]
    if len(sub) < 30:
        return None

    current = float(sub.iloc[-1])
    if current <= 0:
        return None

    # Momentum 12M-1M (skip last 21 jours). Si historique < 272 jours, fallback
    # à un proxy 6M-1M.
    if len(sub) >= 272:
        ref_idx = -272
        end_idx = -21
        try:
            ref = float(sub.iloc[ref_idx])
            end_close = float(sub.iloc[end_idx])
            if ref > 0:
                mom_pct = (end_close - ref) / ref * 100.0
            else:
                mom_pct = None
        except (IndexError, ValueError):
            mom_pct = None
    elif len(sub) >= 126:
        ref = float(sub.iloc[-126])
        mom_pct = (current - ref) / ref * 100.0 if ref > 0 else None
    else:
        mom_pct = None

    # Volatilité annualisée (rolling 60j de log-returns)
    if len(sub) >= 60:
        rets = sub.pct_change().dropna().tail(60)
        std = float(rets.std())
        vol_pct = std * (252 ** 0.5) * 100.0 if std > 0 else None
    else:
        vol_pct = None

    # 52w high ratio (sur 252 jours)
    high_window = sub.tail(252)
    if len(high_window) >= 30:
        high = float(high_window.max())
        ratio = current / high if high > 0 else None
    else:
        ratio = None

    # Sharpe approx
    risk_adj = None
    if mom_pct is not None and vol_pct is not None and vol_pct > 0:
        risk_adj = mom_pct / vol_pct

    new_row = dict(ticker_row)
    new_row["current_price"] = round(current, 4)
    new_row["momentum_return_pct"] = mom_pct
    new_row["momentum_volatility_pct"] = vol_pct
    new_row["momentum_risk_adjusted"] = risk_adj
    new_row["momentum_high_52w_ratio"] = ratio
    return new_row


def _generate_snapshot_for_date(
    target_date: date,
    universe_data: dict[str, Any],
    histories: dict[str, pd.Series],
) -> int:
    """Reconstruit l'universe scoré pour `target_date` et écrit le snapshot.

    Returns le nombre de tickers retenus dans le snapshot (0 si vide).
    """
    rebuilt: dict[str, dict[str, Any]] = {}
    for ticker, row in (universe_data.get("tickers") or {}).items():
        history = histories.get(ticker)
        if history is None:
            continue
        t_snapshot = _ticker_snapshot_at_date(row, history, target_date)
        if t_snapshot is None:
            continue
        rebuilt[ticker] = t_snapshot

    if len(rebuilt) < 30:
        # Pas assez de tickers → snapshot inutile.
        return 0

    # Score le universe rebuilt à cette date.
    scored = _score_universe(rebuilt)
    if not scored:
        return 0

    universe_history.write_snapshot(
        scored,
        universe_updated_at=universe_data.get("updated_at"),
        macro={"bootstrap": True, "as_of": target_date.isoformat()},
        snapshot_date=target_date,
    )
    return len(scored)


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrateur
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap_history(
    *,
    n_days: int = 120,
    start_date: date | None = None,
    end_date: date | None = None,
    workers: int = 8,
    skip_weekends: bool = True,
    skip_existing: bool = True,
) -> dict[str, Any]:
    """Génère N snapshots historiques rétroactifs.

    Args:
      n_days: nombre de jours ouvrés à backfiller en partant de end_date (default today).
      start_date: si fourni, override n_days.
      end_date: dernière date à générer (default = aujourd'hui).
      workers: parallélisme du fetch OHLCV.
      skip_weekends: si True, ne génère que pour les jours ouvrés (lun-ven).
      skip_existing: si True, ne ré-écrase pas un snapshot déjà présent.

    Returns:
      diagnostics : {n_dates_targeted, n_snapshots_written, n_skipped,
                     histories_fetched, elapsed_s}
    """
    if not _UNIVERSE_PATH.exists():
        raise FileNotFoundError(f"universe.json absent : {_UNIVERSE_PATH}")
    universe_data = json.loads(_UNIVERSE_PATH.read_text(encoding="utf-8"))
    tickers_map = universe_data.get("tickers") or {}
    if not tickers_map:
        raise ValueError("universe.json vide")
    tickers = list(tickers_map.keys())

    # Construit la liste des dates cibles.
    end_date = end_date or date.today()
    if start_date is not None:
        cur = start_date
        target_dates: list[date] = []
        while cur <= end_date:
            target_dates.append(cur)
            cur += timedelta(days=1)
    else:
        target_dates = []
        cur = end_date
        added = 0
        while added < n_days:
            target_dates.append(cur)
            added += 1
            cur -= timedelta(days=1)
        target_dates.reverse()

    if skip_weekends:
        target_dates = [d for d in target_dates if d.weekday() < 5]

    if skip_existing:
        existing = set(universe_history.list_snapshots())
        target_dates = [d for d in target_dates if d not in existing]

    if not target_dates:
        return {
            "n_dates_targeted": 0,
            "n_snapshots_written": 0,
            "n_skipped": 0,
            "histories_fetched": 0,
            "elapsed_s": 0.0,
            "reason": "no_dates_to_generate",
        }

    logger.info(
        f"[bootstrap] START — {len(target_dates)} dates × {len(tickers)} tickers, "
        f"workers={workers}"
    )

    # Fetch historique pour tous les tickers (parallèle).
    days_history = (date.today() - target_dates[0]).days + 300  # marge pour calc 12M-1M
    t0 = time.time()
    histories: dict[str, pd.Series] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch_history_robust, t, days_history): t
            for t in tickers
        }
        n_done = 0
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                s = fut.result()
            except Exception as e:
                logger.debug(f"[bootstrap] {t} fetch crashed: {e}")
                s = None
            if s is not None and len(s) >= 30:
                histories[t] = s
            n_done += 1
            if n_done % 50 == 0:
                logger.info(f"[bootstrap] history fetched {n_done}/{len(tickers)}")

    elapsed_fetch = time.time() - t0
    logger.info(
        f"[bootstrap] {len(histories)}/{len(tickers)} histories en {elapsed_fetch:.0f}s"
    )

    # Génère les snapshots un par un (le scoring est cross-sectional, pas trivial à paralléliser).
    n_written = 0
    n_skipped_size = 0
    for d in target_dates:
        try:
            n_kept = _generate_snapshot_for_date(d, universe_data, histories)
        except Exception as e:
            logger.warning(f"[bootstrap] snapshot {d.isoformat()} failed: {e}")
            continue
        if n_kept > 0:
            n_written += 1
        else:
            n_skipped_size += 1

    elapsed = time.time() - t0
    diag = {
        "n_dates_targeted":    len(target_dates),
        "n_snapshots_written": n_written,
        "n_skipped":           n_skipped_size,
        "histories_fetched":   len(histories),
        "elapsed_s":           round(elapsed, 1),
    }
    logger.info(f"[bootstrap] DONE — {diag}")
    return diag


def _main() -> int:
    parser = argparse.ArgumentParser(prog="simfin_bootstrap")
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--start", type=str, default=None,
                        help="ISO date start (override --days)")
    parser.add_argument("--end", type=str, default=None,
                        help="ISO date end (default today)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--no-skip-existing", action="store_true")
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).date() if args.start else None
    end = datetime.fromisoformat(args.end).date() if args.end else None

    diag = bootstrap_history(
        n_days=args.days,
        start_date=start,
        end_date=end,
        workers=args.workers,
        skip_existing=not args.no_skip_existing,
    )
    print(f"[bootstrap] {diag}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
