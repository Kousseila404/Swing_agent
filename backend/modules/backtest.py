"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  MINIMAL BACKTEST — Top-N par TITAN score, equal-weight, daily rebalance    ║
║                                                                              ║
║  Source : snapshots universe_history (modules/universe_history.py).          ║
║                                                                              ║
║  Pour chaque paire consécutive de dates (t, t+1) :                           ║
║    1. Ranker tickers du snapshot t par `titan_composite_score` desc          ║
║    2. Prendre top N (défaut 20), allocation equal-weight                     ║
║    3. Return période = moyenne arithmétique des (price_t+1 / price_t - 1)    ║
║    4. Agréger en equity curve (initial = 1.0)                                ║
║                                                                              ║
║  CLI : `python -m modules.backtest [--top-n 20] [--benchmark SPY]`           ║
║                                                                              ║
║  Limitation actuelle : 3 snapshots = 2 périodes. Significance statistique    ║
║  nulle. L'objectif de ce module est de VALIDER LE PIPELINE en attendant      ║
║  que l'historique s'étoffe (6-12 mois = ~250 périodes utiles).               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from modules import universe_history
from modules.log import logger


@dataclass
class PeriodResult:
    """Un rebalance : date de signal + return net + top-N tickers."""
    signal_date: str            # date du score utilisé pour ranker
    next_date: str              # date du prix de sortie
    top_tickers: list[str]
    returns: dict[str, float]   # par ticker, décimal (0.02 = +2%)
    portfolio_return: float     # moyenne arithmétique equal-weight
    n_valid: int                # tickers avec price_t et price_t+1 non-None
    n_skipped: int              # tickers skippés (price manquant t ou t+1)


@dataclass
class BacktestResult:
    strategy:        str                           # "titan_top_n"
    top_n:           int
    periods:         list[PeriodResult]
    equity_curve:    list[tuple[str, float]]       # (date, equity)
    total_return:    float                         # cumulative, décimal
    avg_daily_return: float                        # moyenne arithmétique des périodes
    sharpe_daily:    float | None
    sharpe_annual:   float | None
    max_drawdown:    float                         # décimal, positif (0.05 = -5%)
    hit_rate:        float                         # % périodes positives
    benchmark_return: float | None = None          # return SPY sur la même période
    alpha:           float | None = None           # total_return - benchmark_return
    diagnostics:     dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "top_n": self.top_n,
            "n_periods": len(self.periods),
            "equity_curve": self.equity_curve,
            "total_return": round(self.total_return, 5),
            "avg_daily_return": round(self.avg_daily_return, 5),
            "sharpe_daily": round(self.sharpe_daily, 3) if self.sharpe_daily is not None else None,
            "sharpe_annual": round(self.sharpe_annual, 3) if self.sharpe_annual is not None else None,
            "max_drawdown": round(self.max_drawdown, 5),
            "hit_rate": round(self.hit_rate, 3),
            "benchmark_return": round(self.benchmark_return, 5) if self.benchmark_return is not None else None,
            "alpha": round(self.alpha, 5) if self.alpha is not None else None,
            "periods": [
                {
                    "signal_date":       p.signal_date,
                    "next_date":         p.next_date,
                    "portfolio_return":  round(p.portfolio_return, 5),
                    "n_valid":           p.n_valid,
                    "n_skipped":         p.n_skipped,
                    "top_tickers":       p.top_tickers[:10],  # cap pour le payload
                }
                for p in self.periods
            ],
            "diagnostics": self.diagnostics,
        }


def _rank_top_n(snapshot: dict[str, Any], top_n: int) -> list[tuple[str, float]]:
    """Retourne top-N tickers (ticker, score) par titan_composite_score desc."""
    tickers = snapshot.get("tickers") or {}
    candidates: list[tuple[str, float]] = []
    for t, row in tickers.items():
        score = row.get("titan_composite_score")
        if score is None or not isinstance(score, (int, float)):
            continue
        if not math.isfinite(float(score)):
            continue
        candidates.append((t, float(score)))
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:top_n]


def _extract_prices(snapshot: dict[str, Any], tickers: list[str]) -> dict[str, float]:
    """Extrait current_price pour chaque ticker du snapshot (None → exclu)."""
    out: dict[str, float] = {}
    snap_tickers = snapshot.get("tickers") or {}
    for t in tickers:
        row = snap_tickers.get(t) or {}
        px = row.get("current_price")
        if px is not None and isinstance(px, (int, float)) and math.isfinite(float(px)) and float(px) > 0:
            out[t] = float(px)
    return out


def _compute_period(
    snapshot_t: dict[str, Any],
    snapshot_t1: dict[str, Any],
    top_n: int,
) -> PeriodResult:
    """Simule un rebalance : rank top-N sur t, mesure return jusqu'à t+1."""
    top = _rank_top_n(snapshot_t, top_n)
    top_tickers = [t for t, _ in top]

    px_t  = _extract_prices(snapshot_t,  top_tickers)
    px_t1 = _extract_prices(snapshot_t1, top_tickers)

    returns: dict[str, float] = {}
    for t in top_tickers:
        if t in px_t and t in px_t1:
            returns[t] = (px_t1[t] / px_t[t]) - 1.0

    portfolio_return = statistics.mean(returns.values()) if returns else 0.0

    return PeriodResult(
        signal_date=str(snapshot_t.get("snapshot_date") or ""),
        next_date=str(snapshot_t1.get("snapshot_date") or ""),
        top_tickers=top_tickers,
        returns=returns,
        portfolio_return=portfolio_return,
        n_valid=len(returns),
        n_skipped=len(top_tickers) - len(returns),
    )


def _compute_stats(periods: list[PeriodResult]) -> dict[str, float]:
    """Agrège daily stats à partir des returns par période."""
    rets = [p.portfolio_return for p in periods]
    if not rets:
        return {
            "total_return": 0.0, "avg_daily_return": 0.0,
            "sharpe_daily": None, "sharpe_annual": None,
            "max_drawdown": 0.0, "hit_rate": 0.0,
        }

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in rets:
        equity *= (1.0 + r)
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    avg = statistics.mean(rets)
    hit = sum(1 for r in rets if r > 0) / len(rets)

    # Sharpe : σ > 0 requis (≥ 2 périodes).
    sharpe_daily: float | None = None
    if len(rets) >= 2:
        sigma = statistics.stdev(rets)
        if sigma > 0 and math.isfinite(sigma):
            sharpe_daily = avg / sigma
    sharpe_annual = sharpe_daily * math.sqrt(252) if sharpe_daily is not None else None

    return {
        "total_return":     equity - 1.0,
        "avg_daily_return": avg,
        "sharpe_daily":     sharpe_daily,
        "sharpe_annual":    sharpe_annual,
        "max_drawdown":     max_dd,
        "hit_rate":         hit,
    }


def _benchmark_return(start: date, end: date, ticker: str = "SPY") -> float | None:
    """Return buy-and-hold du benchmark entre start et end (yfinance).

    Fail-open : retourne None si yfinance KO (pas bloquant pour le backtest).
    """
    try:
        import yfinance as yf
        # +1 jour de buffer à la fin pour garantir le close disponible.
        from datetime import timedelta
        df = yf.download(
            ticker,
            start=start.isoformat(),
            end=(end + timedelta(days=3)).isoformat(),
            progress=False, auto_adjust=True, threads=False,
        )
        if df is None or df.empty or len(df) < 2:
            return None
        # MultiIndex flatten
        if hasattr(df.columns, "get_level_values"):
            closes = df["Close"]
            if hasattr(closes, "columns"):
                closes = closes.iloc[:, 0]
        else:
            closes = df["Close"]
        p0 = float(closes.iloc[0])
        p1 = float(closes.iloc[-1])
        if p0 <= 0:
            return None
        return (p1 / p0) - 1.0
    except Exception as e:
        logger.warning(f"[Backtest] benchmark {ticker} fetch failed: {e}")
        return None


def run_titan_top_n(
    top_n: int = 20,
    benchmark: str | None = "SPY",
) -> BacktestResult:
    """Backtest complet du pipeline TITAN sur l'historique disponible.

    Args:
        top_n: taille du portefeuille equal-weight (défaut 20).
        benchmark: ticker du benchmark (défaut SPY). None = pas de comparaison.
    """
    dates = universe_history.list_snapshots()
    if len(dates) < 2:
        raise ValueError(
            f"Au moins 2 snapshots requis pour 1 période de rebalance. "
            f"Disponibles : {[d.isoformat() for d in dates]}"
        )

    snapshots: list[tuple[date, dict[str, Any]]] = []
    for d in dates:
        snap = universe_history.read_snapshot(d)
        if snap:
            snapshots.append((d, snap))
    if len(snapshots) < 2:
        raise ValueError("Snapshots illisibles")

    periods: list[PeriodResult] = []
    for (d0, s0), (d1, s1) in zip(snapshots[:-1], snapshots[1:]):
        period = _compute_period(s0, s1, top_n)
        periods.append(period)

    stats = _compute_stats(periods)

    # Equity curve pour visualisation
    equity = 1.0
    curve: list[tuple[str, float]] = [(str(snapshots[0][0].isoformat()), 1.0)]
    for p in periods:
        equity *= (1.0 + p.portfolio_return)
        curve.append((p.next_date, round(equity, 5)))

    result = BacktestResult(
        strategy="titan_top_n",
        top_n=top_n,
        periods=periods,
        equity_curve=curve,
        total_return=stats["total_return"],
        avg_daily_return=stats["avg_daily_return"],
        sharpe_daily=stats["sharpe_daily"],
        sharpe_annual=stats["sharpe_annual"],
        max_drawdown=stats["max_drawdown"],
        hit_rate=stats["hit_rate"],
    )

    # Benchmark (SPY buy&hold sur la même période)
    if benchmark:
        bench_start = snapshots[0][0]
        bench_end   = snapshots[-1][0]
        bench_ret = _benchmark_return(bench_start, bench_end, benchmark)
        if bench_ret is not None:
            result.benchmark_return = bench_ret
            result.alpha = result.total_return - bench_ret

    result.diagnostics = {
        "n_snapshots": len(snapshots),
        "n_periods":   len(periods),
        "benchmark":   benchmark,
        "date_range": {
            "start": snapshots[0][0].isoformat(),
            "end":   snapshots[-1][0].isoformat(),
        },
    }
    return result


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def _main() -> int:
    import argparse
    import json
    p = argparse.ArgumentParser(prog="backtest",
        description="Backtest minimaliste du pipeline TITAN sur universe_history.")
    p.add_argument("--top-n", type=int, default=20,
                   help="Taille du portefeuille equal-weight (défaut 20)")
    p.add_argument("--benchmark", type=str, default="SPY",
                   help="Ticker benchmark (défaut SPY, 'none' pour skip)")
    p.add_argument("--json", action="store_true",
                   help="Sortie JSON brut (sinon : tableau lisible humain)")
    args = p.parse_args()

    bench = None if args.benchmark.lower() == "none" else args.benchmark.upper()
    try:
        result = run_titan_top_n(top_n=args.top_n, benchmark=bench)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return 0

    # Pretty print
    print(f"=== TITAN Backtest — Top {result.top_n} equal-weight, daily rebalance ===")
    print(f"Période : {result.diagnostics['date_range']['start']} → {result.diagnostics['date_range']['end']}")
    print(f"N snapshots : {result.diagnostics['n_snapshots']} | N rebalances : {result.diagnostics['n_periods']}")
    print()
    print(f"Total return      : {result.total_return*100:+.2f}%")
    print(f"Avg daily return  : {result.avg_daily_return*100:+.3f}%")
    if result.sharpe_daily is not None:
        print(f"Sharpe daily      : {result.sharpe_daily:.2f}  (annualized ~ {result.sharpe_annual:.2f})")
    else:
        print("Sharpe            : N/A (< 2 périodes ou σ=0)")
    print(f"Max drawdown      : {result.max_drawdown*100:.2f}%")
    print(f"Hit rate          : {result.hit_rate*100:.1f}%")
    if result.benchmark_return is not None:
        print(f"Benchmark {bench:<5}   : {result.benchmark_return*100:+.2f}%")
        print(f"Alpha             : {result.alpha*100:+.2f}% {'✅' if result.alpha > 0 else '🔴'}")
    print()
    print("=== Equity curve ===")
    for d, eq in result.equity_curve:
        bar = "█" * int((eq - 1.0) * 1000 + 0.5) if eq >= 1.0 else ""
        print(f"  {d}  {eq:7.4f}  {bar}")
    print()
    print("=== Périodes ===")
    for p_ in result.periods:
        top5 = ", ".join(p_.top_tickers[:5])
        print(f"  {p_.signal_date} → {p_.next_date}: {p_.portfolio_return*100:+.2f}% "
              f"({p_.n_valid}/{p_.n_valid + p_.n_skipped} valid) | top5: {top5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
