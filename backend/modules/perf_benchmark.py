"""Performance vs benchmarks — compte réel vs SPY vs panier TITAN théorique.

Audit 2026-09-17 (Lot 6) : « mesurer avant de toucher au scoring ». Le
compte paper était à −1 % pendant que SPY faisait +7,5 % et que le panier
top-20 TITAN (équipondéré, rebalance hebdo, 10 bps) faisait +18 % — et
personne ne le voyait. Ce module aligne les trois séries sur les mêmes
dates de bourse, les rebase à 100 et calcule un résumé.

Sources :
  • compte     : Alpaca `get_portfolio_history` (equity quotidienne, source de
                 vérité — inclut cash, frais, dividendes) ; fallback vide.
  • SPY        : yfinance (close ajusté) ; fallback vide.
  • panier     : `modules.backtest.run_titan_top_n` restreint aux snapshots
                 live (≥ LIVE_START) — les snapshots bootstrappés ont un
                 look-ahead et ne sont jamais utilisés ici.

Coûts : le backtest prend ~20–30 s → cache disque `data/.perf_benchmark_cache.json`
(TTL 12 h, invalidé par le nombre de snapshots) + cache RAM. `run_titan.sh`
peut chauffer le cache en appelant l'endpoint.
"""
from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from modules.log import logger

_BACKEND = Path(__file__).resolve().parents[1]
CACHE_PATH = _BACKEND / "data" / ".perf_benchmark_cache.json"

# Premier snapshot live du scoring (avant : bootstrap rétroactif, look-ahead).
LIVE_START = date(2026, 4, 22)
BASKET_TOP_N = 20
BASKET_SLIPPAGE_BPS = 10.0
BASKET_CACHE_TTL_SEC = 12 * 3600
_RAM_TTL_SEC = 15 * 60

_lock = threading.Lock()
_ram: dict[str, tuple[float, Any]] = {}


def _ram_get(key: str):
    hit = _ram.get(key)
    if hit and time.time() - hit[0] < _RAM_TTL_SEC:
        return hit[1]
    return None


def _ram_set(key: str, value: Any) -> None:
    _ram[key] = (time.time(), value)


# ─────────────────────────────────────────────────────────────────
# Séries brutes
# ─────────────────────────────────────────────────────────────────

def account_equity_series(months: int = 6) -> list[tuple[str, float]]:
    """[(YYYY-MM-DD, equity)] depuis Alpaca. [] si broker indisponible."""
    key = f"acct:{months}"
    cached = _ram_get(key)
    if cached is not None:
        return cached
    out: list[tuple[str, float]] = []
    try:
        import config
        if str(getattr(config, "BROKER_MODE", "paper")).lower() != "alpaca":
            return out
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        client = TradingClient(
            config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY,
            paper="paper" in str(config.ALPACA_BASE_URL),
        )
        period = f"{max(1, min(months, 12))}M"
        hist = client.get_portfolio_history(
            GetPortfolioHistoryRequest(period=period, timeframe="1D", extended_hours=False)
        )
        for ts, eq in zip(hist.timestamp or [], hist.equity or [], strict=False):
            try:
                v = float(eq or 0)
            except (TypeError, ValueError):
                continue
            if v <= 0:
                continue
            d = datetime.fromtimestamp(int(ts), tz=UTC).date().isoformat()
            out.append((d, v))
    except Exception as exc:
        logger.warning(f"[PerfBenchmark] Alpaca portfolio history indisponible : {exc}")
        return []
    _ram_set(key, out)
    return out


def spy_series(start: date, end: date | None = None, ticker: str = "SPY") -> list[tuple[str, float]]:
    """[(YYYY-MM-DD, close)] via yfinance. [] si indisponible."""
    end = end or date.today()
    key = f"spy:{ticker}:{start}:{end}"
    cached = _ram_get(key)
    if cached is not None:
        return cached
    out: list[tuple[str, float]] = []
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(
            start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), timeout=15,
        )
        for idx, row in hist.iterrows():
            try:
                out.append((idx.date().isoformat(), float(row["Close"])))
            except Exception:
                continue
    except Exception as exc:
        logger.warning(f"[PerfBenchmark] {ticker} indisponible : {exc}")
        return []
    _ram_set(key, out)
    return out


def _snapshot_count() -> int:
    try:
        from modules import universe_history
        return len(list(Path(universe_history.HISTORY_DIR).glob("snapshot_*.json.gz")))
    except Exception:
        return -1


def titan_basket_series(top_n: int = BASKET_TOP_N, force: bool = False) -> dict[str, Any]:
    """Panier top-N théorique sur la fenêtre live : {"points": [(date, index)],
    "periods": n, "hit_rate": x, "computed_at": iso, "top_n": n}.

    Index rebasé à 100 au premier signal live ; chaque période contribue
    (1 + return net) à la date `next_date`.
    """
    n_snap = _snapshot_count()
    cache_key = f"basket:{top_n}"
    if not force:
        cached = _ram_get(cache_key)
        if cached is not None:
            return cached
        try:
            if CACHE_PATH.exists():
                disk = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                entry = (disk.get("baskets") or {}).get(str(top_n))
                if entry:
                    age = time.time() - float(entry.get("computed_epoch", 0))
                    if age < BASKET_CACHE_TTL_SEC and entry.get("n_snapshots") == n_snap:
                        _ram_set(cache_key, entry)
                        return entry
        except Exception as exc:
            logger.debug(f"[PerfBenchmark] cache disque illisible : {exc}")

    with _lock:
        cached = _ram_get(cache_key)
        if cached is not None and not force:
            return cached
        try:
            from modules.backtest import run_titan_top_n
            res = run_titan_top_n(
                top_n=top_n, benchmark=None, slippage_bps=BASKET_SLIPPAGE_BPS,
                publication_lag_days=5,
            )
            d = res.to_dict() if hasattr(res, "to_dict") else res
            periods = [p for p in (d.get("periods") or []) if str(p.get("signal_date", "")) >= LIVE_START.isoformat()]
        except Exception as exc:
            logger.warning(f"[PerfBenchmark] backtest panier échoué : {exc}")
            periods = []
        points: list[tuple[str, float]] = []
        idx = 100.0
        wins = 0
        if periods:
            points.append((str(periods[0]["signal_date"]), idx))
        for p in periods:
            r = float(p.get("portfolio_return") or 0.0)
            idx *= 1.0 + r
            wins += 1 if r > 0 else 0
            points.append((str(p.get("next_date")), round(idx, 4)))
        entry = {
            "top_n": top_n,
            "points": points,
            "periods": len(periods),
            "hit_rate": round(wins / len(periods), 3) if periods else None,
            "slippage_bps": BASKET_SLIPPAGE_BPS,
            "n_snapshots": n_snap,
            "computed_epoch": time.time(),
            "computed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        _ram_set(cache_key, entry)
        try:
            disk = {}
            if CACHE_PATH.exists():
                disk = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            disk.setdefault("baskets", {})[str(top_n)] = entry
            tmp = CACHE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(disk), encoding="utf-8")
            tmp.replace(CACHE_PATH)
        except Exception as exc:
            logger.debug(f"[PerfBenchmark] cache disque non écrit : {exc}")
        return entry


# ─────────────────────────────────────────────────────────────────
# Alignement + résumé
# ─────────────────────────────────────────────────────────────────

@dataclass
class BenchmarkSummary:
    start: str | None = None
    end: str | None = None
    n_days: int = 0
    account_return_pct: float | None = None
    spy_return_pct: float | None = None
    basket_return_pct: float | None = None
    alpha_vs_spy_pct: float | None = None
    alpha_vs_basket_pct: float | None = None
    account_max_drawdown_pct: float | None = None
    spy_max_drawdown_pct: float | None = None
    basket_hit_rate: float | None = None
    basket_periods: int = 0
    notes: list[str] = field(default_factory=list)


def _max_drawdown(values: list[float]) -> float | None:
    if not values:
        return None
    peak = values[0]
    mdd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, (v - peak) / peak)
    return round(mdd * 100.0, 2)


def _step_series(points: list[tuple[str, float]], dates: list[str]) -> list[float | None]:
    """Aligne une série éparse (ex. panier hebdo) sur des dates : dernière valeur connue."""
    out: list[float | None] = []
    j = 0
    last: float | None = None
    pts = sorted(points)
    for d in dates:
        while j < len(pts) and pts[j][0] <= d:
            last = pts[j][1]
            j += 1
        out.append(last)
    return out


def build_benchmark(months: int = 6, top_n: int = BASKET_TOP_N, start: date | None = None) -> dict[str, Any]:
    """Aligne compte / SPY / panier, rebase à 100, résume."""
    acct = account_equity_series(months)
    basket = titan_basket_series(top_n)
    basket_pts = [(d, v) for d, v in basket.get("points", [])]

    candidates = [d for d, _ in acct] + [d for d, _ in basket_pts]
    if start is None:
        if acct:
            start = max(date.fromisoformat(acct[0][0]), LIVE_START)
        elif basket_pts:
            start = date.fromisoformat(basket_pts[0][0])
        else:
            start = LIVE_START
    end = date.today()
    spy = spy_series(start, end)

    dates = sorted({d for d, _ in spy if d >= start.isoformat()})
    if not dates:
        dates = sorted({d for d in candidates if d >= start.isoformat()})
    summary = BenchmarkSummary()
    if not dates:
        summary.notes.append("Aucune date commune — sources indisponibles")
        return {"dates": [], "series": {}, "summary": summary.__dict__, "basket_meta": basket}

    acct_map = dict(acct)
    spy_map = dict(spy)
    acct_vals = _step_series([(d, v) for d, v in acct if d >= start.isoformat()], dates)
    spy_vals = [spy_map.get(d) for d in dates]
    basket_vals = _step_series(basket_pts, dates)

    def _rebase(vals: list[float | None]) -> list[float | None]:
        base = next((v for v in vals if v), None)
        if not base:
            return [None] * len(vals)
        return [round(v / base * 100.0, 3) if v else None for v in vals]

    series = {
        "account": _rebase(acct_vals),
        "spy": _rebase(spy_vals),
        "basket": _rebase(basket_vals),
    }

    def _ret(vals: list[float | None]) -> float | None:
        clean = [v for v in vals if v]
        if len(clean) < 2:
            return None
        return round((clean[-1] / clean[0] - 1.0) * 100.0, 2)

    summary.start = dates[0]
    summary.end = dates[-1]
    summary.n_days = len(dates)
    summary.account_return_pct = _ret(acct_vals)
    summary.spy_return_pct = _ret(spy_vals)
    summary.basket_return_pct = _ret(basket_vals)
    if summary.account_return_pct is not None and summary.spy_return_pct is not None:
        summary.alpha_vs_spy_pct = round(summary.account_return_pct - summary.spy_return_pct, 2)
    if summary.account_return_pct is not None and summary.basket_return_pct is not None:
        summary.alpha_vs_basket_pct = round(summary.account_return_pct - summary.basket_return_pct, 2)
    summary.account_max_drawdown_pct = _max_drawdown([v for v in acct_vals if v])
    summary.spy_max_drawdown_pct = _max_drawdown([v for v in spy_vals if v])
    summary.basket_hit_rate = basket.get("hit_rate")
    summary.basket_periods = int(basket.get("periods") or 0)
    if not acct:
        summary.notes.append("Equity compte indisponible (broker)")
    if not spy:
        summary.notes.append("SPY indisponible (yfinance)")
    if not basket_pts:
        summary.notes.append("Panier TITAN indisponible (backtest)")
    if summary.basket_periods and summary.basket_periods < 26:
        summary.notes.append(
            f"Panier : {summary.basket_periods} périodes hebdo seulement — indicatif, pas significatif"
        )

    return {
        "dates": dates,
        "series": series,
        "raw": {"account_equity": [acct_map.get(d) for d in dates]},
        "summary": {k: v for k, v in summary.__dict__.items()},
        "basket_meta": {k: v for k, v in basket.items() if k != "points"},
        "params": {"months": months, "top_n": top_n, "live_start": LIVE_START.isoformat()},
    }


def invested_fraction(open_positions: list[dict[str, Any]], equity: float) -> float | None:
    """Part du capital investie (Σ size × prix courant / equity)."""
    if not equity or equity <= 0:
        return None
    total = 0.0
    for p in open_positions:
        try:
            px = float(p.get("current_price") or p.get("entry") or 0)
            total += px * float(p.get("size") or 0)
        except (TypeError, ValueError):
            continue
    return round(min(1.0, total / equity), 4) if math.isfinite(total) else None
