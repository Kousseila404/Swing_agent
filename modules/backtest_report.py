"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE — BACKTEST REPORT V2                                    ║
║  Rapports console (ANSI coloré + sparkline) et HTML interactif. ║
║                                                                  ║
║  Console :                                                       ║
║    • Tableau trié par confiance décroissante                    ║
║    • Colorisation ANSI (vert/jaune/rouge) honnête               ║
║    • Sparkline ASCII de la courbe d'équité                      ║
║  HTML :                                                          ║
║    • Courbe d'équité (Stratégie vs Buy & Hold)                  ║
║    • Heatmap mensuelle des rendements                           ║
║    • Distribution des P&L                                       ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import base64
import io
import os
from datetime import datetime
from typing import Optional

import matplotlib
matplotlib.use("Agg")          # Mode non-interactif (serveur/script)
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd

from modules.backtester import BacktestResult, BacktestStats, TradeResult
from modules.log import logger


# ─────────────────────────────────────────────────────────────────
# CODES COULEUR ANSI
# ─────────────────────────────────────────────────────────────────

class _C:
    """Codes ANSI pour la colorisation console."""
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    GRAY   = "\033[90m"
    WHITE  = "\033[97m"


def _color_value(value: float, good_threshold: float, bad_threshold: float, higher_is_better: bool = True) -> str:
    """Retourne le code couleur ANSI pour une valeur donnée."""
    if higher_is_better:
        if value >= good_threshold:
            return _C.GREEN
        if value <= bad_threshold:
            return _C.RED
        return _C.YELLOW
    else:  # Lower is better (ex: drawdown)
        if value <= good_threshold:
            return _C.GREEN
        if value >= bad_threshold:
            return _C.RED
        return _C.YELLOW


# ─────────────────────────────────────────────────────────────────
# SPARKLINE ASCII
# ─────────────────────────────────────────────────────────────────

_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(series: pd.Series, width: int = 10) -> str:
    """
    Génère une sparkline ASCII de largeur fixe.

    Args:
        series: Série de valeurs numériques.
        width:  Nombre de caractères.

    Returns:
        Chaîne de caractères représentant la tendance.
    """
    if series is None or len(series) < 2:
        return "─" * width

    data = series.dropna().values
    if len(data) < 2:
        return "─" * width

    # Rééchantillonner à la largeur cible
    indices = np.linspace(0, len(data) - 1, width).astype(int)
    sampled = data[indices]

    mn, mx = sampled.min(), sampled.max()
    if mx == mn:
        return "─" * width

    norm   = (sampled - mn) / (mx - mn)
    chars  = [_SPARK_CHARS[int(v * (len(_SPARK_CHARS) - 1))] for v in norm]
    return "".join(chars)


# ─────────────────────────────────────────────────────────────────
# RAPPORT CONSOLE — TICKER INDIVIDUEL
# ─────────────────────────────────────────────────────────────────

def print_ticker_report(result: BacktestResult, show_trades: bool = False) -> None:
    """
    Affiche un rapport détaillé pour un ticker individuel.

    Args:
        result:      Résultat de backtest.
        show_trades: Si True, liste les trades individuels.
    """
    s    = result.stats
    tick = result.ticker

    print(f"\n{'━' * 65}")
    print(f"  {_C.BOLD}{_C.CYAN}{tick}{_C.RESET}  "
          f"| Période : {result.period_start.date() if result.period_start else 'N/A'} "
          f"→ {result.period_end.date() if result.period_end else 'N/A'} "
          f"({result.period_years:.1f} ans)")
    print(f"{'━' * 65}")

    if result.error:
        print(f"  {_C.RED}ERREUR : {result.error}{_C.RESET}")
        return

    if s is None or s.total_trades == 0:
        print(f"  {_C.GRAY}Aucun trade généré sur cette période.{_C.RESET}")
        return

    # Couleur globale selon la rentabilité
    overall_color = _C.GREEN if s.total_return_pct > 0 else _C.RED

    # ── Métriques principales ──
    print(f"\n  {'RENDEMENTS':<30}")
    print(f"  {'Rendement total':<28} : "
          f"{overall_color}{s.total_return_pct:+.1f}%{_C.RESET}")
    print(f"  {'CAGR':<28} : "
          f"{_color_value(s.cagr_pct, 5, 0)}{s.cagr_pct:+.1f}%{_C.RESET}")
    print(f"  {'Buy & Hold (benchmark)':<28} : "
          f"{s.benchmark_return_pct:+.1f}%")
    alpha = s.total_return_pct - s.benchmark_return_pct
    ac    = _C.GREEN if alpha > 0 else _C.RED
    print(f"  {'Alpha vs B&H':<28} : {ac}{alpha:+.1f}%{_C.RESET}")

    print(f"\n  {'RATIOS DE PERFORMANCE':<30}")
    print(f"  {'Sharpe (annualisé)':<28} : "
          f"{_color_value(s.sharpe_ratio, 1.0, 0)}{s.sharpe_ratio:.2f}{_C.RESET}")
    print(f"  {'Sortino':<28} : "
          f"{_color_value(s.sortino_ratio, 1.0, 0)}{s.sortino_ratio:.2f}{_C.RESET}")
    print(f"  {'Calmar':<28} : "
          f"{_color_value(s.calmar_ratio, 0.5, 0)}{s.calmar_ratio:.2f}{_C.RESET}")
    print(f"  {'Profit Factor':<28} : "
          f"{_color_value(s.profit_factor, 1.5, 1.0)}{s.profit_factor:.2f}{_C.RESET}")

    print(f"\n  {'STATISTIQUES TRADES':<30}")
    wr_pct  = s.win_rate * 100
    ci_low  = s.win_rate_ci_low  * 100
    ci_high = s.win_rate_ci_high * 100
    print(f"  {'Trades totaux':<28} : {s.total_trades}")
    print(f"  {'Win rate':<28} : "
          f"{_color_value(wr_pct, 50, 40)}{wr_pct:.1f}%{_C.RESET} "
          f"{_C.GRAY}[IC95 : {ci_low:.1f}%–{ci_high:.1f}%]{_C.RESET}")
    print(f"  {'Avg win / avg loss':<28} : "
          f"{_C.GREEN}{s.avg_win_pct:+.2f}%{_C.RESET} / "
          f"{_C.RED}{s.avg_loss_pct:+.2f}%{_C.RESET}")
    print(f"  {'Expectancy':<28} : "
          f"{_color_value(s.expectancy_pct, 0.1, 0)}"
          f"{s.expectancy_pct:+.2f}%{_C.RESET} "
          f"({s.expectancy_eur:+.1f}€/trade)")
    print(f"  {'Max win streak':<28} : {_C.GREEN}{s.max_win_streak}{_C.RESET} trades")
    print(f"  {'Max loss streak':<28} : {_C.RED}{s.max_loss_streak}{_C.RESET} trades")

    print(f"\n  {'RISQUE':<30}")
    print(f"  {'Max Drawdown':<28} : "
          f"{_color_value(s.max_drawdown_pct, 10, 20, higher_is_better=False)}"
          f"{s.max_drawdown_pct:.1f}%{_C.RESET}")
    if s.max_drawdown_duration_days > 0:
        print(f"  {'Durée drawdown max':<28} : {s.max_drawdown_duration_days}j")
    print(f"  {'Exposition marché':<28} : {s.market_exposure_pct:.1f}%")
    print(f"  {'Durée moyenne trade':<28} : {s.avg_duration_days:.1f}j")

    # ── Sparkline ──
    if result.equity_curve is not None and len(result.equity_curve) > 0:
        spark = _sparkline(result.equity_curve, width=20)
        print(f"\n  Équité : {spark}")

    print(f"{'━' * 65}")

    # ── Détail trades ──
    if show_trades and result.trades:
        print(f"\n  {_C.BOLD}LISTE DES TRADES{_C.RESET}")
        print(f"  {'Date Entrée':<12} {'Entrée':>8} {'Stop':>8} {'Target':>8} "
              f"{'Sortie':<10} {'P&L%':>7} {'P&L€':>8} {'Raison':<10}")
        print(f"  {'─' * 78}")
        for t in sorted(result.trades, key=lambda x: x.entry_date):
            color = _C.GREEN if t.win else _C.RED
            print(
                f"  {t.entry_date.strftime('%Y-%m-%d'):<12} "
                f"{t.entry_price:>8.2f} "
                f"{t.stop_price:>8.2f} "
                f"{t.target_price:>8.2f} "
                f"{t.exit_reason:<10} "
                f"{color}{t.pnl_pct:>+7.2f}%{_C.RESET} "
                f"{color}{t.pnl_eur:>+8.2f}€{_C.RESET}"
            )


# ─────────────────────────────────────────────────────────────────
# RAPPORT CONSOLE — PORTEFEUILLE
# ─────────────────────────────────────────────────────────────────

def print_portfolio_report(results: list[BacktestResult]) -> None:
    """
    Affiche un tableau de synthèse pour tous les tickers, trié par rendement total.

    Args:
        results: Liste de BacktestResult.
    """
    valid = [r for r in results if r.stats and r.stats.total_trades > 0]
    valid.sort(key=lambda r: r.stats.total_return_pct, reverse=True)  # type: ignore[union-attr]

    print(f"\n{'═' * 110}")
    print(f"{_C.BOLD}  RAPPORT PORTEFEUILLE — {len(valid)} ticker(s) avec signaux{_C.RESET}")
    print(f"{'═' * 110}")

    header = (
        f"  {'Ticker':<10} {'Trades':>6} {'WR%':>6} "
        f"{'PF':>5} {'Ret%':>7} {'CAGR%':>6} "
        f"{'Sharpe':>7} {'DD%':>6} {'Expect%':>8} "
        f"{'B&H%':>7} {'Sparkline':<12}"
    )
    print(f"{_C.BOLD}{header}{_C.RESET}")
    print(f"  {'─' * 107}")

    for r in valid:
        s     = r.stats
        spark = _sparkline(r.equity_curve, width=10) if r.equity_curve is not None else "──────────"

        # Couleur globale
        if s.total_return_pct > 5 and s.profit_factor > 1.3:
            row_c = _C.GREEN
        elif s.total_return_pct < 0 or s.profit_factor < 1.0:
            row_c = _C.RED
        else:
            row_c = _C.YELLOW

        wr_pct = s.win_rate * 100
        print(
            f"  {row_c}{r.ticker:<10}{_C.RESET} "
            f"{s.total_trades:>6} "
            f"{wr_pct:>6.1f} "
            f"{min(s.profit_factor, 9.99):>5.2f} "
            f"{row_c}{s.total_return_pct:>+7.1f}{_C.RESET} "
            f"{row_c}{s.cagr_pct:>+6.1f}{_C.RESET} "
            f"{s.sharpe_ratio:>7.2f} "
            f"{s.max_drawdown_pct:>6.1f} "
            f"{s.expectancy_pct:>+8.2f} "
            f"{s.benchmark_return_pct:>+7.1f} "
            f"{_C.GRAY}{spark}{_C.RESET}"
        )

    # Tickers sans signal
    no_signal = [r for r in results if not r.stats or r.stats.total_trades == 0]
    if no_signal:
        print(f"\n  {_C.GRAY}Tickers sans signal : "
              f"{', '.join(r.ticker for r in no_signal)}{_C.RESET}")

    print(f"{'═' * 110}\n")


# ─────────────────────────────────────────────────────────────────
# RAPPORT CONSOLE — TIERS
# ─────────────────────────────────────────────────────────────────

def print_tiers_report() -> None:
    """
    Affiche le classement par Tiers de tous les tickers profilés.
    Importé depuis ticker_profiles pour ne pas créer de dépendance circulaire.
    """
    from modules.ticker_profiles import list_profiles_by_tier

    tiers = list_profiles_by_tier()
    tier_colors = {
        "A":          _C.GREEN,
        "B":          _C.CYAN,
        "C":          _C.YELLOW,
        "UNTRADABLE": _C.RED,
    }
    tier_labels = {
        "A":          "TIER A — Haute confiance (≥70%)",
        "B":          "TIER B — Confiance modérée (50–69%)",
        "C":          "TIER C — Confiance faible (30–49%) — Prudence",
        "UNTRADABLE": "NON TRADABLE (<30% ou données insuffisantes)",
    }

    print(f"\n{'═' * 95}")
    print(f"{_C.BOLD}  CLASSEMENT PAR TIERS — TICKERS PROFILÉS{_C.RESET}")
    print(f"{'═' * 95}")

    for tier in ("A", "B", "C", "UNTRADABLE"):
        profiles = tiers.get(tier, [])
        tc = tier_colors[tier]
        print(f"\n  {tc}{_C.BOLD}{tier_labels[tier]}{_C.RESET}"
              f"  {_C.GRAY}({len(profiles)} ticker(s)){_C.RESET}")

        if not profiles:
            print(f"    {_C.GRAY}— aucun —{_C.RESET}")
            continue

        print(f"  {'─' * 92}")
        print(f"  {'Ticker':<10} {'Score':>6} {'Type':<16} {'WR%':>6} {'PF':>5} "
              f"{'Ret%':>7} {'DD%':>6} {'Trades':>7} {'Optimisé':<12}")
        print(f"  {'─' * 92}")

        for p in profiles:
            wr_str = f"{p['val_wr'] * 100:.0f}%" if p['val_wr'] else " N/A"
            pf_str = f"{p['val_pf']:.2f}" if p['val_pf'] else "N/A"
            ret_str = f"{p['val_return']:+.1f}%" if p['val_return'] != 0 else " N/A"
            dd_str = f"{p['val_dd']:.1f}%" if p['val_dd'] else "N/A"

            warn_icon = " ⚠" if p.get("overfitting") else ""
            print(
                f"  {tc}{p['ticker']:<10}{_C.RESET} "
                f"{p['confidence']:>6}% "
                f"{p['strategy_type']:<16} "
                f"{wr_str:>6} "
                f"{pf_str:>5} "
                f"{ret_str:>7} "
                f"{dd_str:>6} "
                f"{p['val_trades']:>7} "
                f"{p['last_optimized']:<12}{warn_icon}"
            )

    print(f"\n{'═' * 95}\n")


# ─────────────────────────────────────────────────────────────────
# GRAPHIQUES MATPLOTLIB
# ─────────────────────────────────────────────────────────────────

def _fig_to_base64(fig: plt.Figure) -> str:
    """Convertit une figure matplotlib en base64 PNG."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def _plot_equity_curve(result: BacktestResult) -> str:
    """
    Génère le graphique de la courbe d'équité (stratégie vs B&H).

    Args:
        result: BacktestResult avec equity_curve et benchmark_curve.

    Returns:
        Image PNG encodée en base64.
    """
    fig, ax = plt.subplots(figsize=(12, 5), facecolor="#1a1a2e")
    ax.set_facecolor("#16213e")

    eq = result.equity_curve
    bh = result.benchmark_curve

    if eq is not None and len(eq) > 1:
        color_eq = "#00d4aa" if eq.iloc[-1] >= eq.iloc[0] else "#ff4757"
        ax.plot(eq.index, eq.values, color=color_eq, linewidth=1.8,
                label=f"Stratégie ({result.stats.total_return_pct:+.1f}%)")
        ax.fill_between(eq.index, eq.values, eq.iloc[0],
                        alpha=0.15, color=color_eq)

    if bh is not None and len(bh) > 1:
        ax.plot(bh.index, bh.values, color="#ffa502", linewidth=1.2,
                linestyle="--", label=f"Buy & Hold ({result.stats.benchmark_return_pct:+.1f}%)",
                alpha=0.8)

    ax.set_title(f"Courbe d'équité — {result.ticker}", color="white", fontsize=13, pad=10)
    ax.set_ylabel("Capital (€)", color="#aaaaaa")
    ax.tick_params(colors="#aaaaaa")
    ax.spines[:].set_color("#333355")
    ax.legend(facecolor="#16213e", edgecolor="#333355", labelcolor="white", fontsize=9)
    ax.grid(axis="y", color="#333355", alpha=0.5, linewidth=0.5)

    return _fig_to_base64(fig)


def _plot_monthly_heatmap(trades: list[TradeResult], ticker: str) -> str:
    """
    Génère une heatmap mensuelle des rendements.

    Args:
        trades: Liste des trades.
        ticker: Symbole boursier.

    Returns:
        Image PNG encodée en base64.
    """
    if not trades:
        fig, ax = plt.subplots(figsize=(10, 3), facecolor="#1a1a2e")
        ax.text(0.5, 0.5, "Aucune donnée", ha="center", va="center",
                color="white", transform=ax.transAxes)
        return _fig_to_base64(fig)

    # Construire la matrice mensuelle
    data: dict[tuple[int, int], float] = {}
    for t in trades:
        key = (t.exit_date.year, t.exit_date.month)
        data[key] = data.get(key, 0.0) + t.pnl_pct

    years  = sorted(set(k[0] for k in data))
    months = list(range(1, 13))

    matrix = np.full((len(years), 12), np.nan)
    for (yr, mo), pnl in data.items():
        if yr in years:
            matrix[years.index(yr)][mo - 1] = pnl

    # Normalisation symétrique
    vmax = max(abs(np.nanmax(matrix)), abs(np.nanmin(matrix)), 1.0)

    fig, ax = plt.subplots(figsize=(14, max(2.5, len(years) * 0.6 + 1.5)),
                           facecolor="#1a1a2e")
    ax.set_facecolor("#16213e")

    cmap = plt.colormaps.get_cmap("RdYlGn")
    im   = ax.imshow(matrix, cmap=cmap, vmin=-vmax, vmax=vmax, aspect="auto")

    # Annotations
    mon_labels = ["Jan", "Fév", "Mar", "Avr", "Mai", "Jun",
                  "Jul", "Aoû", "Sep", "Oct", "Nov", "Déc"]
    ax.set_xticks(range(12))
    ax.set_xticklabels(mon_labels, color="#aaaaaa", fontsize=9)
    ax.set_yticks(range(len(years)))
    ax.set_yticklabels(years, color="#aaaaaa", fontsize=9)

    for i in range(len(years)):
        for j in range(12):
            val = matrix[i, j]
            if not np.isnan(val):
                txt_color = "black" if abs(val) < vmax * 0.6 else "white"
                ax.text(j, i, f"{val:+.1f}%", ha="center", va="center",
                        color=txt_color, fontsize=7.5)

    plt.colorbar(im, ax=ax, label="P&L %", shrink=0.8)
    ax.set_title(f"Heatmap mensuelle — {ticker}", color="white", fontsize=12, pad=10)
    ax.tick_params(colors="#aaaaaa")
    ax.spines[:].set_color("#333355")

    return _fig_to_base64(fig)


def _plot_pnl_distribution(trades: list[TradeResult], ticker: str) -> str:
    """
    Génère la distribution des P&L des trades.

    Args:
        trades: Liste des trades.
        ticker: Symbole boursier.

    Returns:
        Image PNG encodée en base64.
    """
    if not trades:
        fig, ax = plt.subplots(figsize=(8, 4), facecolor="#1a1a2e")
        ax.text(0.5, 0.5, "Aucune donnée", ha="center", va="center",
                color="white", transform=ax.transAxes)
        return _fig_to_base64(fig)

    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p >= 0]
    loss = [p for p in pnls if p < 0]

    fig, ax = plt.subplots(figsize=(9, 4), facecolor="#1a1a2e")
    ax.set_facecolor("#16213e")

    bins = min(30, max(10, len(pnls) // 3))
    ax.hist(loss, bins=bins // 2, color="#ff4757", alpha=0.75, label="Pertes")
    ax.hist(wins, bins=bins // 2, color="#2ed573", alpha=0.75, label="Gains")

    ax.axvline(x=0, color="white", linewidth=0.8, alpha=0.5)
    avg = float(np.mean(pnls))
    ax.axvline(x=avg, color="#ffa502", linewidth=1.2, linestyle="--",
               label=f"Moyenne : {avg:+.2f}%")

    ax.set_title(f"Distribution des P&L — {ticker}", color="white", fontsize=12, pad=10)
    ax.set_xlabel("P&L (%)", color="#aaaaaa")
    ax.set_ylabel("Fréquence", color="#aaaaaa")
    ax.tick_params(colors="#aaaaaa")
    ax.spines[:].set_color("#333355")
    ax.legend(facecolor="#16213e", edgecolor="#333355", labelcolor="white", fontsize=9)
    ax.grid(axis="y", color="#333355", alpha=0.5, linewidth=0.5)

    return _fig_to_base64(fig)


# ─────────────────────────────────────────────────────────────────
# RAPPORT HTML
# ─────────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Swing Agent — Rapport Backtest V2</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f0f23; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; padding: 20px; }}
  h1   {{ color: #00d4aa; margin: 20px 0 10px; font-size: 1.6em; }}
  h2   {{ color: #7ecfff; margin: 25px 0 8px; font-size: 1.2em; border-bottom: 1px solid #333355; padding-bottom: 5px; }}
  h3   {{ color: #aaaaaa; margin: 15px 0 6px; font-size: 1em; }}
  .ticker-section {{ background: #16213e; border-radius: 8px; padding: 20px; margin: 20px 0;
                     border-left: 4px solid {border_color}; }}
  .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin: 12px 0; }}
  .metric {{ background: #1a1a2e; border-radius: 6px; padding: 10px 14px; }}
  .metric-label {{ font-size: 0.75em; color: #888; margin-bottom: 3px; }}
  .metric-value {{ font-size: 1.1em; font-weight: bold; }}
  .green  {{ color: #2ed573; }}  .red   {{ color: #ff4757; }}
  .yellow {{ color: #ffa502; }}  .gray  {{ color: #666; }}
  .chart-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin: 15px 0; }}
  .chart-full {{ margin: 15px 0; }}
  img {{ width: 100%; border-radius: 6px; }}
  .badge {{ display: inline-block; padding: 2px 10px; border-radius: 20px; font-size: 0.8em;
            font-weight: bold; margin-left: 8px; }}
  .badge-green  {{ background: #1a4d2e; color: #2ed573; border: 1px solid #2ed573; }}
  .badge-red    {{ background: #4d1a1a; color: #ff4757; border: 1px solid #ff4757; }}
  .badge-yellow {{ background: #4d3a1a; color: #ffa502; border: 1px solid #ffa502; }}
  .summary-table {{ width: 100%; border-collapse: collapse; margin: 15px 0; font-size: 0.9em; }}
  .summary-table th {{ background: #1a1a2e; padding: 8px 12px; text-align: left; color: #aaa; }}
  .summary-table td {{ padding: 7px 12px; border-bottom: 1px solid #222; }}
  .summary-table tr:hover {{ background: #1a2540; }}
  footer {{ text-align: center; color: #444; margin-top: 40px; font-size: 0.8em; }}
</style>
</head>
<body>
<h1>Swing Agent — Rapport Backtest V2</h1>
<p style="color:#666; margin-bottom:20px;">Généré le {generated_at} | {n_tickers} ticker(s) analysés</p>

{portfolio_table}

{ticker_sections}

<footer>Swing Trading Agent V2 — Rapport généré automatiquement</footer>
</body>
</html>
"""

_TICKER_SECTION_TEMPLATE = """
<div class="ticker-section" style="border-left-color: {border_color}">
  <h2>{ticker} <span class="badge {badge_class}">{badge_text}</span></h2>
  <div class="metrics-grid">
    <div class="metric"><div class="metric-label">Rendement Total</div>
      <div class="metric-value {ret_class}">{total_return:+.1f}%</div></div>
    <div class="metric"><div class="metric-label">Buy &amp; Hold</div>
      <div class="metric-value">{bh:+.1f}%</div></div>
    <div class="metric"><div class="metric-label">CAGR</div>
      <div class="metric-value {ret_class}">{cagr:+.1f}%</div></div>
    <div class="metric"><div class="metric-label">Win Rate</div>
      <div class="metric-value {wr_class}">{wr:.1f}% <span style="font-size:0.75em;color:#666">[{ci_low:.1f}–{ci_high:.1f}]</span></div></div>
    <div class="metric"><div class="metric-label">Profit Factor</div>
      <div class="metric-value {pf_class}">{pf:.2f}</div></div>
    <div class="metric"><div class="metric-label">Sharpe</div>
      <div class="metric-value {sh_class}">{sharpe:.2f}</div></div>
    <div class="metric"><div class="metric-label">Sortino</div>
      <div class="metric-value">{sortino:.2f}</div></div>
    <div class="metric"><div class="metric-label">Max Drawdown</div>
      <div class="metric-value {dd_class}">{dd:.1f}%</div></div>
    <div class="metric"><div class="metric-label">Calmar</div>
      <div class="metric-value">{calmar:.2f}</div></div>
    <div class="metric"><div class="metric-label">Expectancy</div>
      <div class="metric-value {exp_class}">{expectancy:+.2f}% ({exp_eur:+.1f}€)</div></div>
    <div class="metric"><div class="metric-label">Trades</div>
      <div class="metric-value">{trades}</div></div>
    <div class="metric"><div class="metric-label">Exposition marché</div>
      <div class="metric-value">{exposure:.1f}%</div></div>
  </div>
  <div class="chart-full">
    <img src="data:image/png;base64,{equity_b64}" alt="Courbe équité" />
  </div>
  <div class="chart-row">
    <img src="data:image/png;base64,{heatmap_b64}" alt="Heatmap mensuelle" />
    <img src="data:image/png;base64,{distrib_b64}" alt="Distribution P&L" />
  </div>
</div>
"""


def _css_class(value: float, good: float, bad: float, higher_better: bool = True) -> str:
    if higher_better:
        if value >= good:  return "green"
        if value <= bad:   return "red"
        return "yellow"
    else:
        if value <= good:  return "green"
        if value >= bad:   return "red"
        return "yellow"


def generate_html_report(
    results: list[BacktestResult],
    output_dir: str = "data",
) -> str:
    """
    Génère un rapport HTML interactif complet.

    Inclut pour chaque ticker :
        - Tableau de métriques colorisé
        - Courbe d'équité (Stratégie vs Buy & Hold)
        - Heatmap mensuelle des rendements
        - Distribution des P&L

    Args:
        results:    Liste de BacktestResult.
        output_dir: Répertoire de sortie du fichier HTML.

    Returns:
        Chemin absolu du fichier HTML généré.
    """
    os.makedirs(output_dir, exist_ok=True)

    valid   = [r for r in results if r.stats and r.stats.total_trades > 0]
    invalid = [r for r in results if not r.stats or r.stats.total_trades == 0]
    valid.sort(key=lambda r: r.stats.total_return_pct, reverse=True)  # type: ignore[union-attr]

    # ── Tableau de synthèse ──
    rows = []
    for r in valid:
        s   = r.stats
        rc  = "green"  if s.total_return_pct > 5  else ("red" if s.total_return_pct < 0 else "yellow")
        rows.append(
            f"<tr><td><b>{r.ticker}</b></td>"
            f"<td>{s.total_trades}</td>"
            f"<td class='{rc}'>{s.total_return_pct:+.1f}%</td>"
            f"<td class='{rc}'>{s.cagr_pct:+.1f}%</td>"
            f"<td>{s.win_rate * 100:.1f}%</td>"
            f"<td>{min(s.profit_factor, 9.99):.2f}</td>"
            f"<td>{s.sharpe_ratio:.2f}</td>"
            f"<td>{s.max_drawdown_pct:.1f}%</td>"
            f"<td>{s.benchmark_return_pct:+.1f}%</td></tr>"
        )

    if invalid:
        for r in invalid:
            rows.append(
                f"<tr><td class='gray'>{r.ticker}</td>"
                f"<td colspan='8' class='gray'>Aucun signal ou erreur : {r.error or 'N/A'}</td></tr>"
            )

    portfolio_table = (
        "<h2>Synthèse portefeuille</h2>"
        "<table class='summary-table'>"
        "<tr><th>Ticker</th><th>Trades</th><th>Rendement</th><th>CAGR</th>"
        "<th>Win Rate</th><th>PF</th><th>Sharpe</th><th>Max DD</th><th>B&H</th></tr>"
        + "".join(rows) + "</table>"
    )

    # ── Sections par ticker ──
    sections = []
    for r in valid:
        s   = r.stats
        pos = s.total_return_pct > 0

        try:
            eq_b64  = _plot_equity_curve(r)
            hm_b64  = _plot_monthly_heatmap(r.trades, r.ticker)
            dis_b64 = _plot_pnl_distribution(r.trades, r.ticker)
        except Exception as e:
            logger.warning(f"[{r.ticker}] Erreur génération graphique : {e}")
            eq_b64 = hm_b64 = dis_b64 = ""

        border = "#2ed573" if pos else ("#ff4757" if s.total_return_pct < -10 else "#ffa502")
        badge_text  = "PROFITABLE" if pos else "PERDANTE"
        badge_class = "badge-green" if pos else "badge-red"

        sections.append(_TICKER_SECTION_TEMPLATE.format(
            ticker=r.ticker,
            border_color=border,
            badge_class=badge_class,
            badge_text=badge_text,
            total_return=s.total_return_pct,
            ret_class=_css_class(s.total_return_pct, 5, 0),
            bh=s.benchmark_return_pct,
            cagr=s.cagr_pct,
            wr=s.win_rate * 100,
            wr_class=_css_class(s.win_rate * 100, 50, 40),
            ci_low=s.win_rate_ci_low * 100,
            ci_high=s.win_rate_ci_high * 100,
            pf=min(s.profit_factor, 9.99),
            pf_class=_css_class(s.profit_factor, 1.5, 1.0),
            sharpe=s.sharpe_ratio,
            sh_class=_css_class(s.sharpe_ratio, 1.0, 0),
            sortino=s.sortino_ratio,
            dd=s.max_drawdown_pct,
            dd_class=_css_class(s.max_drawdown_pct, 10, 20, higher_better=False),
            calmar=s.calmar_ratio,
            expectancy=s.expectancy_pct,
            exp_class=_css_class(s.expectancy_pct, 0.1, 0),
            exp_eur=s.expectancy_eur,
            trades=s.total_trades,
            exposure=s.market_exposure_pct,
            equity_b64=eq_b64,
            heatmap_b64=hm_b64,
            distrib_b64=dis_b64,
        ))

    html = _HTML_TEMPLATE.format(
        border_color="#00d4aa",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_tickers=len(results),
        portfolio_table=portfolio_table,
        ticker_sections="\n".join(sections),
    )

    ts      = datetime.now().strftime("%Y%m%d_%H%M")
    outpath = os.path.join(output_dir, f"backtest_report_{ts}.html")

    with open(outpath, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(f"Rapport HTML généré : {outpath}")
    return outpath
