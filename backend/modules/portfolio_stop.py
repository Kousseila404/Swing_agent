"""Stop de portefeuille — drawdown global de l'equity (2026-09-17).

Les stops par ligne protègent d'un accident sur un titre ; rien ne protégeait
d'une érosion lente et corrélée de tout le panier. Règle : si l'equity du
compte (Alpaca, quotidien) est ≤ `PORTFOLIO_STOP_DD_PCT` (−15 %) sous son
plus haut des `PORTFOLIO_STOP_WINDOW_D` (60) dernières séances → gel des
nouvelles entrées (même mécanisme que le killswitch, mode freeze), alerte
Telegram. Le relevage suit l'hysteresis existante (equity ≥ 96 % du peak).

Pure : `evaluate()`. IO : `run_portfolio_stop()` (CLI quotidien, run_titan.sh).
"""
from __future__ import annotations

import json
from typing import Any

import config
from modules.log import logger


def evaluate(equity: list[float], window: int, dd_threshold_pct: float) -> dict[str, Any]:
    """{peak, last, dd_pct, triggered} sur les `window` dernières lectures."""
    vals = [float(v) for v in equity if v and float(v) > 0][-window:]
    if len(vals) < 5:
        return {"peak": None, "last": None, "dd_pct": None, "triggered": False, "n": len(vals)}
    peak = max(vals)
    last = vals[-1]
    dd = (last / peak - 1.0) * 100.0
    return {"peak": round(peak, 2), "last": round(last, 2), "dd_pct": round(dd, 2),
            "triggered": dd <= dd_threshold_pct, "n": len(vals)}


def run_portfolio_stop() -> dict[str, Any]:
    from modules.perf_benchmark import account_equity_series
    series = account_equity_series(months=6)
    res = evaluate([v for _, v in series], int(getattr(config, "PORTFOLIO_STOP_WINDOW_D", 60)),
                   float(getattr(config, "PORTFOLIO_STOP_DD_PCT", -15.0)))
    res["threshold_pct"] = getattr(config, "PORTFOLIO_STOP_DD_PCT", -15.0)
    if not res["triggered"]:
        return res
    try:
        from modules.tracker.killswitch import _set_trading_blocked, is_trading_allowed
        if is_trading_allowed():
            _set_trading_blocked(True, peak_equity=res["peak"])
            res["action"] = "entries_frozen"
            logger.critical(f"[PortfolioStop] drawdown 60 j {res['dd_pct']:.1f} % ≤ {res['threshold_pct']} % → entrées gelées")
            try:
                from modules.alerter import _send_telegram_message
                _send_telegram_message(
                    "🧊 <b>Stop de portefeuille</b>\n"
                    f"Equity {res['last']:,.0f} $ = {res['dd_pct']:.1f} % sous le plus haut 60 j ({res['peak']:,.0f} $). "
                    "Nouvelles entrées gelées ; positions conservées avec leurs stops."
                )
            except Exception:
                pass
        else:
            res["action"] = "already_frozen"
    except Exception as exc:
        logger.error(f"[PortfolioStop] {exc}")
        res["action"] = f"error: {exc}"
    return res


if __name__ == "__main__":
    print(json.dumps(run_portfolio_stop(), indent=1))
