"""Fonctions pures d'analytics pour le dashboard (pas de streamlit).

Extrait de dashboard.py : compute_metrics (stats agrégées du journal),
badges macro, helpers de parsing de logs.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

# Capital par défaut pour les runs où l'appelant n'en passe pas un explicite.
# Les appelants doivent privilégier la lecture depuis config.ACCOUNT_SIZE ou
# depuis le premier equity_state connu — la constante ici n'est qu'un ultime fallback.
DEFAULT_INITIAL_CAPITAL = 100_000.0
PROP_FIRM_FLOOR         = 95_000.0


def compute_metrics(
    df: pd.DataFrame,
    initial_capital: float | None = None,
) -> dict:
    """Reconstruit PnL réalisé depuis Entry/Exit_Price/Size/Direction.

    Args:
        df: Journal de trades (CSV / DuckDB).
        initial_capital: Capital de référence pour le calcul d'équité / ratios.
            Si None, lit config.ACCOUNT_SIZE, sinon tombe sur DEFAULT_INITIAL_CAPITAL.
    """
    if initial_capital is None:
        try:
            import config as _config
            initial_capital = float(getattr(_config, "ACCOUNT_SIZE", DEFAULT_INITIAL_CAPITAL))
        except Exception:
            initial_capital = DEFAULT_INITIAL_CAPITAL
    INITIAL_CAPITAL = float(initial_capital)

    empty = {
        "capital": INITIAL_CAPITAL, "pnl_total": 0.0,
        "margin": INITIAL_CAPITAL - PROP_FIRM_FLOOR,
        "wins": 0, "losses": 0, "total_closed": 0,
        "win_rate": 0.0, "profit_factor": 0.0,
        "open_count": 0, "pnl_series": [],
        "equity_by_date": [],
    }
    if df.empty or "Status" not in df.columns:
        return empty

    closed = df[df["Status"].isin(["WIN", "LOSS"])].copy()
    opens  = df[df["Status"] == "OPEN"]

    if closed.empty:
        return {**empty, "open_count": len(opens)}

    for col in ("Entry", "Exit_Price", "Size"):
        closed[col] = pd.to_numeric(closed[col], errors="coerce").fillna(0.0)

    direction = (
        closed["Direction"].str.upper().fillna("LONG")
        if "Direction" in closed.columns
        else pd.Series("LONG", index=closed.index)
    )
    is_short = direction == "SHORT"

    closed["_pnl"] = 0.0
    closed.loc[~is_short, "_pnl"] = (
        (closed.loc[~is_short, "Exit_Price"] - closed.loc[~is_short, "Entry"])
        * closed.loc[~is_short, "Size"]
    )
    closed.loc[is_short, "_pnl"] = (
        (closed.loc[is_short, "Entry"] - closed.loc[is_short, "Exit_Price"])
        * closed.loc[is_short, "Size"]
    )

    pnl      = closed["_pnl"]
    capital  = INITIAL_CAPITAL + pnl.sum()
    pnl_tot  = capital - INITIAL_CAPITAL
    wins     = int((pnl > 0).sum())
    losses   = int((pnl < 0).sum())
    total    = wins + losses
    win_rate = (wins / total * 100) if total > 0 else 0.0
    gross_w  = pnl[pnl > 0].sum()
    gross_l  = abs(pnl[pnl < 0].sum())
    pf       = round(gross_w / gross_l, 2) if gross_l > 0 else 0.0

    equity_by_date = []
    if "Exit_Date" in closed.columns:
        ts = closed.copy()
        ts["_exit_dt"] = pd.to_datetime(ts["Exit_Date"], errors="coerce")
        ts = ts.dropna(subset=["_exit_dt"]).sort_values("_exit_dt")
        cum = INITIAL_CAPITAL
        for _, row in ts.iterrows():
            cum += row["_pnl"]
            equity_by_date.append((row["_exit_dt"].isoformat(), round(cum, 2)))

    avg_win  = float(pnl[pnl > 0].mean()) if wins  > 0 else 0.0
    avg_loss = float(pnl[pnl < 0].mean()) if losses > 0 else 0.0
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

    avg_r = 0.0
    if "RR" in closed.columns:
        rr_vals = pd.to_numeric(closed["RR"], errors="coerce").dropna()
        if not rr_vals.empty:
            avg_r = float(rr_vals.mean())

    # Sharpe annualisé — CORRECT : on agrège d'abord les PnL par JOUR CALENDAIRE,
    # puis on calcule mean/std sur la série daily, puis on annualise par √252.
    # L'ancienne version utilisait `pnl / capital` par trade et annualisait
    # directement via √252 — faux si N trades/jour variable (≈ gonflait le Sharpe
    # d'un facteur √n_trades_per_day). Observé : Sharpe 7+ sur 20 trades/mois,
    # pur artefact de comptage.
    sharpe = 0.0
    if "Exit_Date" in closed.columns and len(closed) >= 5:
        ts2 = closed.copy()
        ts2["_exit_dt"] = pd.to_datetime(ts2["Exit_Date"], errors="coerce")
        ts2 = ts2.dropna(subset=["_exit_dt"])
        if len(ts2) >= 5:
            # Agrégation par jour calendaire d'exit (1 obs/jour, pas 1 obs/trade).
            ts2["_day"] = ts2["_exit_dt"].dt.date
            daily_pnl_series = ts2.groupby("_day")["_pnl"].sum()
            if len(daily_pnl_series) >= 5:
                daily_ret = daily_pnl_series / INITIAL_CAPITAL
                mean_r = daily_ret.mean()
                std_r  = daily_ret.std()
                sharpe = float((mean_r / std_r) * (252 ** 0.5)) if std_r > 0 else 0.0

    max_drawdown_pct = 0.0
    if equity_by_date:
        eq_vals = [v for _, v in equity_by_date]
        peak = INITIAL_CAPITAL
        for v in eq_vals:
            if v > peak:
                peak = v
            dd = (v - peak) / peak * 100
            if dd < max_drawdown_pct:
                max_drawdown_pct = dd

    # Sortino : même logique — agréger d'abord par jour, puis stdev sur les
    # journées perdantes seulement.
    sortino = 0.0
    if len(closed) >= 5 and "Exit_Date" in closed.columns:
        ts3 = closed.copy()
        ts3["_exit_dt"] = pd.to_datetime(ts3["Exit_Date"], errors="coerce")
        ts3 = ts3.dropna(subset=["_exit_dt"])
        if len(ts3) >= 5:
            ts3["_day"] = ts3["_exit_dt"].dt.date
            daily_pnl3 = ts3.groupby("_day")["_pnl"].sum()
            if len(daily_pnl3) >= 5:
                daily_ret3   = daily_pnl3 / INITIAL_CAPITAL
                neg_ret      = daily_ret3[daily_ret3 < 0]
                downside_dev = float(neg_ret.std()) if len(neg_ret) >= 2 else 0.0
                mean_r3      = float(daily_ret3.mean())
                sortino = (
                    float(mean_r3 / downside_dev * (252 ** 0.5))
                    if downside_dev > 0 else 0.0
                )

    calmar = 0.0
    if (
        max_drawdown_pct < 0
        and "Exit_Date" in closed.columns
        and len(closed) >= 3
    ):
        try:
            ts4 = closed.copy()
            ts4["_exit_dt"] = pd.to_datetime(ts4["Exit_Date"], errors="coerce")
            ts4 = ts4.dropna(subset=["_exit_dt"])
            if len(ts4) >= 2:
                n_days  = max(
                    1.0,
                    (ts4["_exit_dt"].max() - ts4["_exit_dt"].min()).days,
                )
                n_years = n_days / 365.25
                cagr_   = (
                    (capital / INITIAL_CAPITAL) ** (1.0 / max(n_years, 0.1))
                    - 1.0
                ) * 100
                calmar  = abs(cagr_ / max_drawdown_pct)
        except Exception:
            pass

    streak = 0
    if "Exit_Date" in closed.columns and not closed.empty:
        ts5 = closed.copy()
        ts5["_exit_dt"] = pd.to_datetime(ts5["Exit_Date"], errors="coerce")
        ts5 = ts5.dropna(subset=["_exit_dt", "_pnl"]).sort_values("_exit_dt")
        if not ts5.empty:
            last_sign = 1 if ts5["_pnl"].iloc[-1] > 0 else -1
            streak = last_sign
            for v in reversed(ts5["_pnl"].values[:-1]):
                s = 1 if v > 0 else -1
                if s == last_sign:
                    streak += last_sign
                else:
                    break

    avg_holding_days = 0.0
    if (
        "Date" in closed.columns
        and "Exit_Date" in closed.columns
        and not closed.empty
    ):
        try:
            ts6 = closed.copy()
            ts6["_entry_dt"] = pd.to_datetime(ts6["Date"],      errors="coerce")
            ts6["_exit_dt"]  = pd.to_datetime(ts6["Exit_Date"], errors="coerce")
            ts6 = ts6.dropna(subset=["_entry_dt", "_exit_dt"])
            if not ts6.empty:
                diffs = (
                    ts6["_exit_dt"] - ts6["_entry_dt"]
                ).dt.total_seconds() / 86400
                avg_holding_days = float(diffs.clip(lower=0).mean())
        except Exception:
            pass

    per_ticker_pnl: dict = {}
    if "Ticker" in closed.columns and not closed.empty:
        for tkr, grp in closed.groupby("Ticker"):
            grp_pnl = grp["_pnl"]
            per_ticker_pnl[str(tkr)] = {
                "total_pnl": round(float(grp_pnl.sum()), 2),
                "wins":      int((grp_pnl > 0).sum()),
                "losses":    int((grp_pnl < 0).sum()),
                "avg_pnl":   round(float(grp_pnl.mean()), 2),
            }

    daily_pnl: dict = {}
    if "Exit_Date" in closed.columns and not closed.empty:
        ts7 = closed.copy()
        ts7["_exit_dt"] = pd.to_datetime(ts7["Exit_Date"], errors="coerce")
        ts7 = ts7.dropna(subset=["_exit_dt"])
        ts7["_day"] = ts7["_exit_dt"].dt.date.astype(str)
        for day, grp in ts7.groupby("_day"):
            daily_pnl[str(day)] = round(float(grp["_pnl"].sum()), 2)

    return {
        "capital": capital, "pnl_total": pnl_tot,
        "margin": capital - PROP_FIRM_FLOOR,
        "wins": wins, "losses": losses, "total_closed": total,
        "win_rate": win_rate, "profit_factor": pf,
        "open_count": len(opens),
        "pnl_series": pnl.cumsum().tolist(),
        "equity_by_date": equity_by_date,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "expectancy": expectancy, "avg_r": avg_r,
        "sharpe": sharpe, "sortino": sortino, "calmar": calmar,
        "max_drawdown_pct": max_drawdown_pct,
        "pnl_raw": pnl.tolist(),
        "streak": streak,
        "avg_holding_days": avg_holding_days,
        "per_ticker_pnl": per_ticker_pnl,
        "daily_pnl": daily_pnl,
    }


def macro_badge(agent_lines: list[str]) -> str:
    for ln in reversed(agent_lines):
        if "BEAR_MARKET" in ln or "BEAR MARKET" in ln:
            return '<span class="macro-bear">🔴 BEAR MARKET</span>'
        if "BULL_MARKET" in ln or "BULL MARKET" in ln:
            return '<span class="macro-bull">🟢 BULL MARKET</span>'
        if "NEUTRAL" in ln:
            return '<span class="macro-na">⚪ NEUTRAL</span>'
    return '<span class="macro-na">— N/A</span>'


def last_tickers_scanned(agent_lines: list[str]) -> list[str]:
    for ln in reversed(agent_lines):
        if "retenus" in ln and ":" in ln:
            part = ln.split(":", 1)[-1].strip()
            return [t.strip() for t in part.split(",") if t.strip()][:12]
    return []


def snapshot_staleness(last_update_str: str | None) -> int | None:
    """Retourne l'âge du snapshot en minutes, ou None si inconnu."""
    if not last_update_str:
        return None
    try:
        lu = datetime.strptime(last_update_str, "%Y-%m-%d %H:%M:%S")
        return int((datetime.now() - lu).total_seconds() / 60)
    except Exception:
        return None


def macro_badge_from_state(macro_state: dict) -> str:
    """Génère un badge HTML à partir de macro_state.json."""
    regime = macro_state.get("confirmed_regime", "")
    last   = macro_state.get("last_update", "?")
    ts_style = (
        'style="color:#45475A;font-size:0.65rem;'
        'font-family:Courier New,monospace"'
    )
    if "BULL" in regime:
        return (
            f'<span class="macro-badge-bull">🟢 BULL MARKET</span> '
            f'<span {ts_style}>{last}</span>'
        )
    if "BEAR" in regime:
        return (
            f'<span class="macro-badge-bear">🔴 BEAR MARKET</span> '
            f'<span {ts_style}>{last}</span>'
        )
    if "PANIC" in regime or "CRASH" in regime:
        return (
            f'<span class="macro-badge-panic">🚨 CRASH/PANIC</span> '
            f'<span {ts_style}>{last}</span>'
        )
    return '<span class="macro-badge-na">— N/A</span>'
