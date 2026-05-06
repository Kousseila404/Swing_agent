"""Bootstrap rétroactif de universe_history depuis prix yfinance 5y.

OBJECTIF : matérialiser des snapshots historiques pour débloquer la WFO et le
backtest sur fenêtre raisonnable (3-5 ans), sans tier payant FMP/Polygon.

STRATÉGIE :
  1. Lit `data/universe.json` actuel pour la liste de tickers + fundamentals.
  2. Télécharge l'historique de prix 5y via `yf.download(tickers, period="5y")`
     en bundles de 100 tickers (limite pratique yfinance pour éviter le 429).
  3. Pour chaque date sample (par défaut hebdomadaire = ~260 snapshots/5y),
     reconstruit pour chaque ticker un row identique au format universe.json
     avec :
       - Fundamentals figés à aujourd'hui (LIMITATION : pas point-in-time —
         ROE, EV/EBITDA, Piotroski Y-1 sont ceux d'aujourd'hui, pas ceux
         publiés à la date du snapshot ; documenté dans le payload).
       - current_price = prix de clôture de la date snapshot
       - momentum_return_pct, momentum_volatility_pct, momentum_high_52w_ratio
         calculés sur la fenêtre [t-272, t] (Jegadeesh-Titman 12M-1M)
  4. Re-score via `_score_universe` sur le row reconstruit.
  5. Persiste avec `write_snapshot(scored, snapshot_date=d)`.

LIMITATION ASSUMÉE — fundamentals lookahead :
  Tous les piliers fondamentaux (Quality/Value/Risk/Sentiment/Piotroski/
  Growth/Revisions/Insider) seront figés à la valeur d'aujourd'hui. Seuls
  Momentum et current_price sont vrais point-in-time. Conséquence pour la
  WFO : l'IC mesuré sur ces snapshots reflète surtout le pilier momentum +
  un *biais de survie* sur les fondamentaux. C'est mieux que rien (permet
  de calibrer Momentum, weighting_method HRP, TS rules) mais inférieur à
  un vrai backtest as-reported. Documenté dans le diagnostic du snapshot
  via `bootstrap_lookahead=true`.

USAGE :
    python -m modules.universe_history_bootstrap \\
        --years 5 --frequency weekly --max-tickers 0
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from modules import api_core, universe_history
from modules.log import logger
from modules.sector_metrics._momentum import (
    _MOMENTUM_HISTORY_DAYS,
    _compute_momentum_stats,
)

# Bundle size pour `yf.download(...)` — au-delà de 100 tickers en un appel
# yfinance retourne souvent des Closes manquants. 100 = sweet-spot empirique.
_YF_BUNDLE_SIZE = 100

# Buffer de jours en début de fenêtre pour pouvoir calculer momentum 272 j sur
# le 1er snapshot demandé (sinon les premiers samples manquent de data).
_HISTORY_BUFFER_DAYS = _MOMENTUM_HISTORY_DAYS + 30


def _frequency_step_days(freq: str) -> int:
    """Convertit une fréquence symbolique en pas en jours calendaires."""
    f = (freq or "weekly").lower().strip()
    if f == "daily":
        return 1
    if f == "weekly":
        return 7
    if f == "monthly":
        return 30
    raise ValueError(f"frequency invalide: {freq!r} (attendu daily|weekly|monthly)")


def _load_universe_tickers() -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Lit data/universe.json → (tickers triés, fundamentals figés par ticker)."""
    import json
    path = api_core.UNIVERSE_QUANTAMENTAL_PATH
    if not path.exists():
        raise SystemExit(
            f"[bootstrap] {path} introuvable — lancer universe_engine d'abord."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    tickers_map = (raw or {}).get("tickers") or {}
    if not tickers_map:
        raise SystemExit("[bootstrap] universe.json vide — abort.")
    return sorted(tickers_map.keys()), dict(tickers_map)


def _download_prices(
    tickers: list[str], start: date, end: date,
) -> dict[str, pd.Series]:
    """Téléchargement prix Adj Close 5 ans en bundles. Fail-open par bundle.

    Returns: {ticker: pd.Series indexée par date, valeur = prix ajusté}.
    """
    import yfinance as yf

    out: dict[str, pd.Series] = {}
    n_bundles = (len(tickers) + _YF_BUNDLE_SIZE - 1) // _YF_BUNDLE_SIZE
    logger.info(
        f"[bootstrap] DL prices {start.isoformat()} → {end.isoformat()} "
        f"({len(tickers)} tickers, {n_bundles} bundles)"
    )
    for i in range(0, len(tickers), _YF_BUNDLE_SIZE):
        bundle = tickers[i:i + _YF_BUNDLE_SIZE]
        bnum = i // _YF_BUNDLE_SIZE + 1
        print(f"[bootstrap] bundle {bnum}/{n_bundles} ({len(bundle)} tickers)…", flush=True)
        try:
            df = yf.download(
                bundle,
                start=start.strftime("%Y-%m-%d"),
                end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                interval="1d",
                progress=False,
                auto_adjust=True,
                threads=True,
                group_by="ticker",
            )
        except Exception as e:
            logger.warning(f"[bootstrap] bundle {bnum} failed: {e}")
            continue
        if df is None or df.empty:
            continue

        for t in bundle:
            try:
                if hasattr(df.columns, "get_level_values") and t in df.columns.get_level_values(0):
                    series = df[t]["Close"].dropna()
                elif "Close" in df.columns:
                    series = df["Close"].dropna()
                else:
                    continue
                if len(series) >= _MOMENTUM_HISTORY_DAYS // 2:
                    out[t] = series
            except Exception as e:
                logger.debug(f"[bootstrap] {t} extract close failed: {e}")
                continue
        # Petit sleep entre bundles pour rester gentil avec yfinance.
        time.sleep(1.0)
    logger.info(f"[bootstrap] DL OK → {len(out)}/{len(tickers)} tickers récupérés")
    return out


def _build_sample_dates(
    earliest: pd.Timestamp, end: date, step_days: int,
) -> list[date]:
    """Liste les dates de snapshot à matérialiser (calendaires).
    On veut au moins `_MOMENTUM_HISTORY_DAYS` jours d'historique avant le
    1er sample.
    """
    first_sample = (earliest + pd.Timedelta(days=_HISTORY_BUFFER_DAYS)).date()
    if first_sample > end:
        return []
    samples: list[date] = []
    d = first_sample
    while d <= end:
        samples.append(d)
        d += timedelta(days=step_days)
    return samples


def _slice_for_momentum(series: pd.Series, as_of: date) -> pd.Series:
    """Retourne la fenêtre des `_MOMENTUM_HISTORY_DAYS` derniers jours
    précédant ou égaux à `as_of`. Vide si série trop courte.
    """
    cutoff = pd.Timestamp(as_of)
    sub = series[series.index <= cutoff]
    if len(sub) < 30:
        return pd.Series(dtype=float)
    return sub.tail(_MOMENTUM_HISTORY_DAYS)


def _build_snapshot_row(
    ticker: str,
    base_fundamentals: dict[str, Any],
    series: pd.Series,
    as_of: date,
) -> dict[str, Any] | None:
    """Reconstruit un row façon universe.json pour `as_of`.

    Returns None si la série est trop courte pour calculer un momentum valide
    OU si le prix as_of est manquant (jour férié pile sur un sample → on skip).
    """
    window = _slice_for_momentum(series, as_of)
    if window.empty:
        return None

    try:
        last_px = float(window.iloc[-1])
    except Exception:
        return None
    if not math.isfinite(last_px) or last_px <= 0:
        return None

    stats = _compute_momentum_stats(window)
    row = dict(base_fundamentals)
    row["current_price"] = last_px
    row["momentum_return_pct"] = stats.get("return_pct")
    row["momentum_volatility_pct"] = stats.get("volatility_pct")
    row["momentum_risk_adjusted"] = stats.get("risk_adjusted")
    row["momentum_high_52w_ratio"] = stats.get("high_52w_ratio")
    # Tag traçabilité : ce row vient du bootstrap (lookahead fundamentals).
    row["_bootstrap_lookahead"] = True
    return row


def _materialize_snapshot(
    as_of: date,
    universe_tickers: list[str],
    fundamentals_today: dict[str, dict[str, Any]],
    prices: dict[str, pd.Series],
) -> int:
    """Matérialise un snapshot pour `as_of` et le persiste. Retourne le
    nombre de tickers retenus dans le snapshot.
    """
    from modules.sector_metrics._scoring import _score_universe

    ticker_rows: dict[str, dict[str, Any]] = {}
    for t in universe_tickers:
        series = prices.get(t)
        if series is None:
            continue
        base = fundamentals_today.get(t) or {}
        row = _build_snapshot_row(t, base, series, as_of)
        if row is not None:
            ticker_rows[t] = row

    if not ticker_rows:
        return 0

    # Phase 1 audit (2026-05-06) — propage as_of=snapshot_date pour activer le
    # gate Piotroski Y-1 strict (refuse Y-1 si publish_date > snapshot_date).
    scored = _score_universe(ticker_rows, as_of=as_of)
    if not scored:
        return 0

    universe_history.write_snapshot(
        scored,
        universe_updated_at=None,
        macro={"_bootstrap": True},
        snapshot_date=as_of,
    )
    return len(scored)


def bootstrap(
    *,
    years: int = 5,
    frequency: str = "weekly",
    max_tickers: int = 0,
    end: date | None = None,
    skip_existing: bool = True,
) -> dict[str, Any]:
    """Pipeline complet bootstrap → snapshots persistés dans HISTORY_DIR.

    Args:
        years: profondeur d'historique (5 par défaut).
        frequency: 'daily' | 'weekly' | 'monthly'.
        max_tickers: cap pour debug (0 = tous).
        end: date de fin, défaut today.
        skip_existing: si True, ne re-matérialise pas un snapshot déjà présent
                       (utile pour reprises incrémentales sans re-DL prix).

    Returns: dict d'audit {n_tickers_dl, n_snapshots_written, n_snapshots_skipped, …}
    """
    end = end or datetime.utcnow().date()
    start = end - timedelta(days=365 * years + _HISTORY_BUFFER_DAYS)
    step_days = _frequency_step_days(frequency)

    tickers, fundamentals_today = _load_universe_tickers()
    if max_tickers and max_tickers > 0:
        tickers = tickers[:max_tickers]
    logger.info(
        f"[bootstrap] start years={years} freq={frequency} step={step_days}d "
        f"tickers={len(tickers)} window=[{start.isoformat()}, {end.isoformat()}]"
    )

    t0 = time.time()
    prices = _download_prices(tickers, start, end)
    if not prices:
        raise SystemExit("[bootstrap] aucun prix téléchargé — abort.")

    earliest_ts: pd.Timestamp = min(s.index.min() for s in prices.values())
    samples = _build_sample_dates(earliest_ts, end, step_days)
    logger.info(f"[bootstrap] {len(samples)} dates de snapshot à matérialiser")

    existing = set(universe_history.list_snapshots())
    n_written = 0
    n_skipped = 0
    n_empty = 0
    for i, d in enumerate(samples, 1):
        if skip_existing and d in existing:
            n_skipped += 1
            continue
        n_kept = _materialize_snapshot(d, tickers, fundamentals_today, prices)
        if n_kept > 0:
            n_written += 1
        else:
            n_empty += 1
        if i % 10 == 0 or i == len(samples):
            print(
                f"[bootstrap] snapshots {i}/{len(samples)} "
                f"(written={n_written}, skipped={n_skipped}, empty={n_empty})",
                flush=True,
            )

    elapsed = time.time() - t0
    audit = {
        "years":              years,
        "frequency":          frequency,
        "step_days":          step_days,
        "n_tickers_input":    len(tickers),
        "n_tickers_dl":       len(prices),
        "n_samples":          len(samples),
        "n_snapshots_written": n_written,
        "n_snapshots_skipped": n_skipped,
        "n_snapshots_empty":  n_empty,
        "window_start":       start.isoformat(),
        "window_end":         end.isoformat(),
        "elapsed_seconds":    round(elapsed, 1),
    }
    logger.info(f"[bootstrap] DONE — {audit}")
    return audit


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def _main() -> int:
    parser = argparse.ArgumentParser(
        prog="universe_history_bootstrap",
        description="Bootstrap rétroactif de universe_history depuis yfinance 5y.",
    )
    parser.add_argument("--years", type=int, default=5,
                        help="Profondeur d'historique (défaut 5).")
    parser.add_argument("--frequency", default="weekly",
                        choices=["daily", "weekly", "monthly"],
                        help="Fréquence des snapshots (défaut weekly).")
    parser.add_argument("--max-tickers", type=int, default=0,
                        help="Cap pour debug (0 = tous).")
    parser.add_argument("--no-skip-existing", action="store_true",
                        help="Re-matérialise même si un snapshot existe déjà.")
    args = parser.parse_args()

    audit = bootstrap(
        years=args.years,
        frequency=args.frequency,
        max_tickers=args.max_tickers,
        skip_existing=not args.no_skip_existing,
    )
    print(f"[bootstrap] DONE — {audit}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
