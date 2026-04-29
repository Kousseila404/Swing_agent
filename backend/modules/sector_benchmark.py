"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE SECTOR_BENCHMARK — performance position vs ETF sectoriel ║
║                                                                  ║
║  Pour chaque position OPEN, calcule :                            ║
║   - return absolu depuis Date d'entrée                           ║
║   - return de l'ETF sectoriel SPDR sur la même fenêtre           ║
║   - alpha (delta position − ETF)                                 ║
║                                                                  ║
║  Utilise yfinance (déjà installé) avec cache disque 1h.          ║
║  Fail-open : si l'ETF est indispo, on retourne juste le return   ║
║  position sans alpha.                                            ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from modules.log import logger

# Mapping GICS Sector → ETF SPDR Select Sector. Couverture 11 secteurs GICS.
# XLC + XLRE ajoutés en 2018 (séparation Telecom + REITs).
GICS_TO_ETF: dict[str, str] = {
    "Technology":             "XLK",
    "Financial Services":     "XLF",
    "Financials":             "XLF",
    "Healthcare":             "XLV",
    "Health Care":            "XLV",
    "Consumer Cyclical":      "XLY",
    "Consumer Discretionary": "XLY",
    "Consumer Defensive":     "XLP",
    "Consumer Staples":       "XLP",
    "Industrials":            "XLI",
    "Energy":                 "XLE",
    "Utilities":              "XLU",
    "Basic Materials":        "XLB",
    "Materials":              "XLB",
    "Real Estate":            "XLRE",
    "Communication Services": "XLC",
    "Communication":          "XLC",
}

_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "sector_benchmark_cache"
_CACHE_TTL = 60 * 60   # 1h


def _cache_path(ticker: str) -> Path:
    return _CACHE_DIR / f"{ticker.upper()}.json"


def _read_cache(ticker: str) -> dict[str, Any] | None:
    p = _cache_path(ticker)
    if not p.exists():
        return None
    if time.time() - p.stat().st_mtime > _CACHE_TTL:
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(ticker: str, payload: dict[str, Any]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_cache_path(ticker), "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception as exc:
        logger.warning(f"[sector_benchmark] cache write fail: {exc}")


def _fetch_history_close(ticker: str, start: date) -> dict[str, float] | None:
    """Retourne {iso_date: close} pour ticker depuis start (inclus). None si KO."""
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        tk = yf.Ticker(ticker)
        df = tk.history(start=start.isoformat(), auto_adjust=True, actions=False)
        if df is None or df.empty:
            return None
        out: dict[str, float] = {}
        for idx, row in df.iterrows():
            try:
                d = idx.strftime("%Y-%m-%d")
                c = float(row["Close"])
                if c > 0:
                    out[d] = c
            except Exception:
                continue
        return out if out else None
    except Exception as exc:
        logger.warning(f"[sector_benchmark] yfinance {ticker} fail: {exc}")
        return None


def _series_return_pct(series: dict[str, float], start_iso: str) -> float | None:
    """Return % entre start (1ère date ≥ start_iso) et dernière date dispo."""
    if not series:
        return None
    keys = sorted(series.keys())
    first = next((k for k in keys if k >= start_iso), None)
    if first is None:
        return None
    last = keys[-1]
    if first == last:
        return 0.0
    p0, p1 = series[first], series[last]
    if not p0:
        return None
    return (p1 / p0 - 1.0) * 100.0


def benchmark_for_position(
    ticker: str, sector: str | None, entry_date: str,
) -> dict[str, Any]:
    """Retourne dict {ticker, sector, etf, return_pct, etf_return_pct,
    alpha_pct, days_held, error?}. Cache disque 1h par ticker.
    """
    t = (ticker or "").upper().strip()
    if not t:
        return {"ticker": "", "error": "empty ticker"}

    # Validate entry_date
    try:
        entry_d = date.fromisoformat(str(entry_date)[:10])
    except (ValueError, TypeError):
        return {"ticker": t, "error": "invalid entry_date"}

    cached = _read_cache(t)
    if cached and cached.get("entry_date") == entry_d.isoformat():
        return cached

    today = date.today()
    days_held = (today - entry_d).days

    # Resolve ETF
    etf = None
    if sector:
        etf = GICS_TO_ETF.get(str(sector).strip())

    # Fetch position history
    pos_series = _fetch_history_close(t, entry_d)
    pos_return = _series_return_pct(pos_series or {}, entry_d.isoformat())

    etf_return = None
    if etf:
        etf_series = _fetch_history_close(etf, entry_d)
        etf_return = _series_return_pct(etf_series or {}, entry_d.isoformat())

    alpha = None
    if pos_return is not None and etf_return is not None:
        alpha = pos_return - etf_return

    out: dict[str, Any] = {
        "ticker":        t,
        "sector":        sector,
        "etf":           etf,
        "entry_date":    entry_d.isoformat(),
        "days_held":     days_held,
        "return_pct":    pos_return,
        "etf_return_pct": etf_return,
        "alpha_pct":     alpha,
        "error":         None if pos_return is not None else "no_position_data",
    }
    _write_cache(t, out)
    return out


def benchmark_portfolio(positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Pour un lot {ticker, sector, entry_date}, retourne tous les benchmarks.

    items = [{ticker, sector, entry_date}]
    """
    results = []
    for p in positions:
        ticker = p.get("ticker") or p.get("Ticker")
        sector = p.get("sector") or p.get("Sector")
        entry_date = p.get("entry_date") or p.get("Date")
        if not ticker or not entry_date:
            continue
        results.append(benchmark_for_position(ticker, sector, entry_date))
    # Aggregates
    alphas = [r["alpha_pct"] for r in results if isinstance(r.get("alpha_pct"), (int, float))]
    avg_alpha = (sum(alphas) / len(alphas)) if alphas else None
    n_outperform = sum(1 for a in alphas if a > 0)
    return {
        "items":        results,
        "n_items":      len(results),
        "n_with_alpha": len(alphas),
        "avg_alpha_pct": avg_alpha,
        "n_outperform": n_outperform,
    }
