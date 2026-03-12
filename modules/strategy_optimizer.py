"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE — STRATEGY OPTIMIZER V3 (MULTI-STRATEGIES)               ║
║  Walk-Forward anti-overfitting institutionnel + multiprocessing. ║
║                                                                  ║
║  Protocole :                                                     ║
║    • Découpage rolling en N fenêtres (Train→Valid chronologique) ║
║    • Met en compétition 3 stratégies : Mean Reversion, Trend     ║
║      Following, et Breakout.                                     ║
║    • Parallélisation CPU via ProcessPoolExecutor                 ║
║    • Score de confiance capé 0–100% avec règles d'or            ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import itertools
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from tqdm import tqdm

import config
from modules.backtester import (
    BacktestParams,
    BacktestStats,
    compute_indicators,
    download_data,
    run_backtest,
)
from modules.log import logger

# ─────────────────────────────────────────────────────────────────
# GRILLES DE PARAMÈTRES PAR STRATÉGIE (La Bataille Royale V3)
# ─────────────────────────────────────────────────────────────────

STRATEGY_GRIDS: dict[str, dict[str, list]] = {
    # 1. MEAN REVERSION — RSI élargi pour plus de fréquence en 1H
    # rsi_oversold élargi (35→45) : génère plus de signaux sur timeframe horaire
    # → 3 × 3 × 3 × 3 × 2 × 1 = 162 combinaisons
    "mean_reversion": {
        "rsi_oversold":  [35, 40, 45],           # Élargi (vs 30-40 en daily)
        "volume_spike":  [1.0, 1.2, 1.5],
        "stop_method":   ["atr_1.5x", "atr_2x", "support"],
        "tp_ratio":      [1.5, 2.0, 3.0],
        "trailing_stop": [True, False],
        "ema_trend":     [1400],                  # ≈ 200 jours × 7 bougies/jour 1H
    },

    # 2. TREND FOLLOWING — Cibles géantes pour les mega-trends
    # → 1 × 1 × 1 × 2 × 2 × 3 × 1 × 1 = 12 combinaisons
    "trend_following": {
        "macd_fast":     [12],
        "macd_slow":     [26],
        "macd_signal":   [9],
        "volume_spike":  [1.0, 1.2],
        "stop_method":   ["atr_1.5x", "atr_2x"],
        "tp_ratio":      [3.0, 5.0, 10.0],       # Cibles asymétriques maintenues
        "trailing_stop": [True],
        "ema_trend":     [1400],
    },

    # 3. BREAKOUT (Bollinger)
    # → 1 × 2 × 2 × 2 × 2 × 1 = 16 combinaisons
    "breakout": {
        "bb_length":     [20],
        "bb_std":        [2.0, 2.5],
        "volume_spike":  [1.5, 2.0],
        "stop_method":   ["atr_1.5x", "percent_5"],
        "tp_ratio":      [2.0, 4.0],
        "trailing_stop": [True, False],
        "ema_trend":     [1400],
    },

    # 4. TREND BREAKOUT — V6 High Frequency (bougies horaires)
    # breakout_period = nombre de BOUGIES 1H (10h ≈ 1.5j, 15h ≈ 2j, 20h ≈ 3j)
    # Plus réactif en 1H qu'en daily pour détecter les cassures intraday.
    # → 3 × 3 × 2 × 3 × 1 × 1 = 54 combinaisons
    "trend_breakout": {
        "breakout_period": [10, 15, 20],          # Bougies horaires (retrait du 30)
        "volume_spike":    [1.0, 1.2, 1.5],
        "stop_method":     ["atr_2x", "atr_3x"],  # Stops larges (volatilité US)
        "tp_ratio":        [2.0, 3.0, 5.0],
        "trailing_stop":   [True],
        "ema_trend":       [1400],
    },
}

# Nombre de workers (CPUs - 1, minimum 1)
_MAX_WORKERS = max(1, os.cpu_count() - 1) if os.cpu_count() else 4


def _build_param_grid(strategies: list[str] | None = None) -> list[BacktestParams]:
    """
    Génère la liste des combinaisons pour les stratégies spécifiées.

    Args:
        strategies: Sous-liste de STRATEGY_GRIDS à tester.
                    Si None → toutes les stratégies (comportement V5 inchangé).

    Returns:
        Liste de BacktestParams couvrant le produit cartésien des grilles.
    """
    target = strategies if strategies is not None else list(STRATEGY_GRIDS.keys())
    combos = []

    for strategy_name in target:
        if strategy_name not in STRATEGY_GRIDS:
            logger.warning(f"[build_param_grid] Stratégie inconnue ignorée : {strategy_name!r}")
            continue
        grid = STRATEGY_GRIDS[strategy_name]
        keys = list(grid.keys())
        for values in itertools.product(*grid.values()):
            combo_dict = dict(zip(keys, values))
            combo_dict["strategy_name"] = strategy_name
            combos.append(BacktestParams(**combo_dict))

    return combos


# ─────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────

@dataclass
class WindowResult:
    window_idx: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    val_start: pd.Timestamp
    val_end: pd.Timestamp
    best_params: BacktestParams
    train_stats: BacktestStats
    val_stats: BacktestStats
    train_score: float
    val_score: float
    val_train_ratio: float


@dataclass
class ConfigResult:
    params: BacktestParams
    avg_val_score: float = 0.0
    val_trades: int = 0
    composite_valid: float = 0.0

    def to_dict(self) -> dict:
        return {
            "params":          self.params.to_dict(),
            "avg_val_score":   round(self.avg_val_score, 3),
            "val_trades":      self.val_trades,
            "composite_valid": round(self.composite_valid, 4),
        }


@dataclass
class OptimizationResult:
    ticker: str
    windows: list[WindowResult] = field(default_factory=list)
    top_configs: list[ConfigResult] = field(default_factory=list)
    total_combos_tested: int = 0
    overfitting_detected: bool = False
    reliability_status: str = "NON FIABLE"
    confidence_score: int = 0
    avg_val_stats: Optional[BacktestStats] = None
    # Profil comportemental ADX (Asset Profiling — TITAN V7)
    ticker_profile: str = "UNKNOWN"   # "MOMENTUM" | "MEAN_REVERSION" | "UNKNOWN"
    avg_adx: float = 0.0              # ADX moyen sur les 50 dernières bougies
    error: Optional[str] = None

    def best_config(self) -> Optional[ConfigResult]:
        return self.top_configs[0] if self.top_configs else None

    def to_dict(self) -> dict:
        best = self.best_config()
        return {
            "ticker":               self.ticker,
            "confidence_score":     self.confidence_score,
            "reliability_status":   self.reliability_status,
            "overfitting_detected": self.overfitting_detected,
            "total_combos_tested":  self.total_combos_tested,
            "n_windows":            len(self.windows),
            "ticker_profile":       self.ticker_profile,
            "avg_adx":              round(self.avg_adx, 1),
            "avg_val_stats":        self.avg_val_stats.to_dict() if self.avg_val_stats else {},
            "best_params":          best.params.to_dict() if best else {},
            "top_configs":          [c.to_dict() for c in self.top_configs[:3]],
            "windows": [
                {
                    "window":          w.window_idx,
                    "train_score":     round(w.train_score, 2),
                    "val_score":       round(w.val_score, 2),
                    "val_train_ratio": round(w.val_train_ratio, 3),
                    "val_trades":      w.val_stats.total_trades,
                    "val_wr":          round(w.val_stats.win_rate, 3),
                    "val_return":      round(w.val_stats.total_return_pct, 2),
                    "val_dd":          round(w.val_stats.max_drawdown_pct, 2),
                }
                for w in self.windows
            ],
        }


# ─────────────────────────────────────────────────────────────────
# SCORE DE CONFIANCE
# ─────────────────────────────────────────────────────────────────

def _training_score(stats: Optional[BacktestStats]) -> float:
    if stats is None or stats.total_trades < 5:
        return 0.0

    pf    = min(stats.profit_factor, 10.0)
    score = 0.0

    if pf > 1.5:                       score += 20.0
    if stats.win_rate > 0.50:          score += 15.0
    if stats.total_trades > 15:        score += 10.0
    if stats.max_drawdown_pct < 15.0:  score += 10.0
    if stats.sharpe_ratio > 1.0:       score += 10.0
    if stats.expectancy_pct > 0:       score +=  5.0

    return score


def compute_confidence_score(val_stats: BacktestStats, val_train_ratio: float, inter_window_cv: float) -> tuple[int, str]:
    n_val = val_stats.total_trades if val_stats else 0

    if n_val < config.MIN_TRADES_FOR_CONFIDENCE:
        return 0, "DONNÉES INSUFFISANTES"

    pf    = min(val_stats.profit_factor if val_stats else 0.0, 10.0)
    score = 0

    if pf > 1.5:                           score += 20
    if val_stats.win_rate > 0.50:          score += 15
    if n_val > 25:                         score += 15
    if val_train_ratio > 0.50:             score += 15
    if inter_window_cv < 0.30:             score += 15
    if val_stats.max_drawdown_pct < 15.0:  score += 10
    if val_stats.sharpe_ratio > 1.0:       score += 10

    if val_train_ratio < 0.30 or inter_window_cv > 0.30:
        return score, "NON FIABLE"

    if score < 40:
        return score, "NON FIABLE"

    return score, "FIABLE"


# ─────────────────────────────────────────────────────────────────
# FENÊTRES WALK-FORWARD
# ─────────────────────────────────────────────────────────────────

@dataclass
class _Window:
    idx: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    val_start: pd.Timestamp
    val_end: pd.Timestamp


def _create_windows(df: pd.DataFrame, n_windows: int) -> list[_Window]:
    # Fenêtres calibrées pour le 1H (730 jours max disponibles) :
    #   train = 270 jours (≈ 9 mois, ~1755 bougies 1H)
    #   val   = 120 jours (≈ 4 mois,  ~780 bougies 1H)
    #   step  = 120 jours (→ 3 fenêtres = 630 jours, compatible 730j)
    val_days   = 120
    train_days = 270
    step_days  = val_days

    df_end   = df.index[-1]
    df_start = df.index[0]
    windows: list[_Window] = []

    for i in range(n_windows - 1, -1, -1):
        val_end   = df_end   - pd.Timedelta(days=i * step_days)
        val_start = val_end  - pd.Timedelta(days=val_days)
        tr_end    = val_start - pd.Timedelta(days=1)
        tr_start  = tr_end   - pd.Timedelta(days=train_days)

        if tr_start < df_start:
            continue

        idx_gte_trstart = df.index[df.index >= tr_start]
        idx_lte_trend   = df.index[df.index <= tr_end]
        idx_gte_valstart= df.index[df.index >= val_start]
        idx_lte_valend  = df.index[df.index <= val_end]

        if len(idx_gte_trstart) == 0 or len(idx_lte_trend) == 0 \
                or len(idx_gte_valstart) == 0 or len(idx_lte_valend) == 0:
            continue

        windows.append(_Window(
            idx=len(windows) + 1,
            train_start=idx_gte_trstart[0],
            train_end=idx_lte_trend[-1],
            val_start=idx_gte_valstart[0],
            val_end=idx_lte_valend[-1],
        ))

    return windows


# ─────────────────────────────────────────────────────────────────
# AGRÉGATION DES STATS DE VALIDATION
# ─────────────────────────────────────────────────────────────────

def _aggregate_val_stats(window_results: list[WindowResult]) -> BacktestStats:
    valid_stats = [w.val_stats for w in window_results if w.val_stats.total_trades > 0]
    if not valid_stats:
        return BacktestStats()

    total = sum(s.total_trades for s in valid_stats)
    weights = [s.total_trades / max(1, total) for s in valid_stats]

    def wavg(attr: str) -> float:
        vals = [getattr(s, attr) for s in valid_stats]
        return float(sum(v * w for v, w in zip(vals, weights)))

    agg = BacktestStats()
    agg.total_trades        = total
    agg.win_rate            = wavg("win_rate")
    agg.profit_factor       = wavg("profit_factor")
    agg.total_return_pct    = sum(s.total_return_pct for s in valid_stats)
    agg.cagr_pct            = wavg("cagr_pct")
    agg.sharpe_ratio        = wavg("sharpe_ratio")
    agg.sortino_ratio       = wavg("sortino_ratio")
    agg.max_drawdown_pct    = max((s.max_drawdown_pct for s in valid_stats), default=0.0)
    agg.calmar_ratio        = wavg("calmar_ratio")
    agg.expectancy_pct      = wavg("expectancy_pct")
    agg.avg_win_pct         = wavg("avg_win_pct")
    agg.avg_loss_pct        = wavg("avg_loss_pct")
    agg.benchmark_return_pct= wavg("benchmark_return_pct")

    return agg


# ─────────────────────────────────────────────────────────────────
# WORKER FUNCTIONS (exécutées dans les process enfants)
# ─────────────────────────────────────────────────────────────────

def _worker_train(ticker: str, params_dict: dict, df_slice: pd.DataFrame) -> tuple[dict, float]:
    try:
        params = BacktestParams.from_dict(params_dict)
        r  = run_backtest(ticker=ticker, params=params, df=df_slice)
        sc = _training_score(r.stats)
        return params_dict, sc
    except Exception:
        return params_dict, 0.0


def _worker_val(ticker: str, params_dict: dict, val_slices: list[pd.DataFrame]) -> tuple[dict, float, int]:
    try:
        params   = BacktestParams.from_dict(params_dict)
        sc_sum   = 0.0
        n_trades = 0
        for val_df in val_slices:
            if len(val_df) < 200:   # Minimum 200 bougies 1H par fenêtre val
                continue
            r  = run_backtest(ticker=ticker, params=params, df=val_df)
            sc = _training_score(r.stats)
            nt = r.stats.total_trades if r.stats else 0
            sc_sum   += sc * nt
            n_trades += nt
        avg = (sc_sum / n_trades) if n_trades > 0 else 0.0
        return params_dict, avg, n_trades
    except Exception:
        return params_dict, 0.0, 0


# ─────────────────────────────────────────────────────────────────
# POINT D'ENTRÉE PRINCIPAL
# ─────────────────────────────────────────────────────────────────

def optimize_ticker(ticker: str, verbose: bool = False) -> OptimizationResult:
    result = OptimizationResult(ticker=ticker)

    try:
        # ── 1. Téléchargement unique (process principal) ────────────
        logger.info(f"[{ticker}] Téléchargement {config.BACKTEST_YEARS + 1} ans...")
        raw_df = download_data(ticker, years=config.BACKTEST_YEARS + 1)

        # Seuil 1H : 1500 bougies ≈ 230 jours de trading (minimum pour walk-forward)
        if raw_df is None or len(raw_df) < 1500:
            result.error             = "Historique insuffisant (<1500 bougies 1H)"
            result.reliability_status = "DONNÉES INSUFFISANTES"
            return result

        # ── 2. Calcul unique des indicateurs ────────────────────────
        base_params = BacktestParams() # Pour les params par défaut de compute_indicators
        full_df     = compute_indicators(raw_df, base_params)

        logger.info(
            f"[{ticker}] {len(full_df)} barres | "
            f"{full_df.index[0].date()} → {full_df.index[-1].date()}"
        )

        # ── 3. Fenêtres walk-forward ────────────────────────────────
        windows = _create_windows(full_df, config.WALK_FORWARD_WINDOWS)

        if len(windows) < 2:
            result.error             = "Historique trop court pour walk-forward"
            result.reliability_status = "DONNÉES INSUFFISANTES"
            return result

        # ── 4. Sélection intelligente des stratégies (V7 — ADX Asset Profiling) ──
        selected_strats: list[str] | None = None
        try:
            from modules.macro_engine import get_market_regime
            from modules.strategy_factory import StrategyFactory
            macro_regime    = get_market_regime(use_cache=True)
            selected_strats = StrategyFactory.select_strategies(ticker, full_df, macro_regime)

            # Stocker le profil ADX dans le résultat (pour l'IA Advisor)
            adx_profile, avg_adx_val = StrategyFactory.get_ticker_profile(full_df)
            result.ticker_profile = adx_profile
            result.avg_adx = float(avg_adx_val) if not np.isnan(avg_adx_val) else 0.0

            if not selected_strats:
                # CRASH_PANIC ou liste vide → fallback toutes stratégies
                selected_strats = None
        except Exception as e:
            logger.warning(
                f"[{ticker}] StrategyFactory indisponible ({e}) "
                "→ toutes les stratégies testées (mode V5)"
            )

        # ── 5. Grille de paramètres filtrée par régime ───────────────
        all_params               = _build_param_grid(strategies=selected_strats)
        all_params_dicts         = [p.to_dict() for p in all_params]
        result.total_combos_tested = len(all_params) * len(windows)

        logger.info(
            f"[{ticker}] Stratégies testées : {selected_strats or 'TOUTES'} | "
            f"{len(windows)} fenêtres × {len(all_params)} combos = "
            f"{result.total_combos_tested} backtests | "
            f"{_MAX_WORKERS} workers CPU"
        )

        # ── 5. Pré-découpage des DataFrames ─────────────────────────
        train_slices: list[pd.DataFrame] = []
        val_slices:   list[pd.DataFrame] = []
        valid_windows: list[_Window]     = []

        for win in windows:
            mask_train = (full_df.index >= win.train_start) & (full_df.index <= win.train_end)
            mask_val   = (full_df.index >= win.val_start)   & (full_df.index <= win.val_end)
            train_df   = full_df.loc[mask_train].copy()
            val_df     = full_df.loc[mask_val].copy()

            # Minimums en bougies 1H : train≥500 (≈77j), val≥200 (≈31j)
            if len(train_df) < 500 or len(val_df) < 200:
                continue

            train_slices.append(train_df)
            val_slices.append(val_df)
            valid_windows.append(win)

        if not valid_windows:
            result.error             = "Aucune fenêtre valide"
            result.reliability_status = "DONNÉES INSUFFISANTES"
            return result

        # ── 6. Optimisation fenêtre par fenêtre (PARALLÈLE) ────────
        window_results: list[WindowResult] = []

        for w_idx, (win, train_df, val_df) in enumerate(zip(valid_windows, train_slices, val_slices)):
            if verbose:
                logger.info(
                    f"[{ticker}] ── Fenêtre {win.idx}/{len(windows)} "
                    f"Train {win.train_start.date()}→{win.train_end.date()} "
                    f"| Valid {win.val_start.date()}→{win.val_end.date()}"
                )

            best_params_dict = all_params_dicts[0]
            best_score       = -1.0

            with ProcessPoolExecutor(max_workers=_MAX_WORKERS) as executor:
                futures = {executor.submit(_worker_train, ticker, pd_dict, train_df): pd_dict for pd_dict in all_params_dicts}

                desc = f"[{ticker}] Train F{win.idx}"
                for future in tqdm(as_completed(futures), total=len(futures), desc=desc, leave=False, disable=not verbose):
                    p_dict, sc = future.result()
                    if sc > best_score:
                        best_score       = sc
                        best_params_dict = p_dict

            best_params  = BacktestParams.from_dict(best_params_dict)
            best_train_r = run_backtest(ticker=ticker, params=best_params, df=train_df)
            best_train_s = best_train_r.stats or BacktestStats()

            try:
                val_r     = run_backtest(ticker=ticker, params=best_params, df=val_df)
                val_s     = val_r.stats or BacktestStats()
                val_score = _training_score(val_s)
            except Exception as e:
                val_s     = BacktestStats()
                val_score = 0.0

            vt_ratio = (val_score / best_score) if best_score > 1e-6 else 0.0

            if verbose:
                logger.info(
                    f"[{ticker}] Fenêtre {win.idx} : "
                    f"[{best_params.strategy_name}] Train {best_score:.0f}pts | Valid {val_score:.0f}pts "
                    f"| V/T {vt_ratio:.2f} | {val_s.total_trades} trades val"
                )

            window_results.append(WindowResult(
                window_idx=win.idx, train_start=win.train_start, train_end=win.train_end,
                val_start=win.val_start, val_end=win.val_end, best_params=best_params,
                train_stats=best_train_s, val_stats=val_s, train_score=best_score,
                val_score=val_score, val_train_ratio=vt_ratio,
            ))

        result.windows = window_results

        if not window_results:
            result.error = "Aucune fenêtre valide"
            result.reliability_status = "DONNÉES INSUFFISANTES"
            return result

        # ── 7. Agrégation & confiance ───────────────────────────────
        agg_val              = _aggregate_val_stats(window_results)
        result.avg_val_stats = agg_val

        val_scores  = [w.val_score for w in window_results]
        avg_val_sc  = float(np.mean(val_scores))
        cv_val      = (float(np.std(val_scores)) / max(1e-6, abs(avg_val_sc)))
        avg_vt      = float(np.mean([w.val_train_ratio for w in window_results]))

        conf, reliability          = compute_confidence_score(agg_val, avg_vt, cv_val)
        result.confidence_score    = conf
        result.reliability_status  = reliability
        result.overfitting_detected = (avg_vt < 0.30 or cv_val > 0.30)

        logger.info(
            f"[{ticker}] Score : {conf}% | {reliability} | "
            f"V/T {avg_vt:.2f} | CV {cv_val:.2f} | "
            f"{agg_val.total_trades} trades val"
        )

        # ── 8. Top 5 configs par validation score moyen ─────────────
        logger.info(f"[{ticker}] Ranking top configs sur {len(val_slices)} fenêtres val...")

        combo_scores: list[tuple[float, int, dict]] = []

        with ProcessPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            futures = {executor.submit(_worker_val, ticker, pd_dict, val_slices): pd_dict for pd_dict in all_params_dicts}

            for future in tqdm(as_completed(futures), total=len(futures), desc=f"[{ticker}] Ranking", leave=False, disable=not verbose):
                p_dict, avg_sc, n_trades = future.result()
                # Pénalise les configs avec moins de 50 trades : validité statistique insuffisante
                if n_trades >= 50:
                    combo_scores.append((avg_sc, n_trades, p_dict))

        combo_scores.sort(key=lambda x: x[0], reverse=True)
        result.top_configs = [
            ConfigResult(
                params=BacktestParams.from_dict(p_dict),
                avg_val_score=round(sc, 2),
                val_trades=nt,
                composite_valid=round(sc / 100.0, 4),
            )
            for sc, nt, p_dict in combo_scores[:5]
        ]

    except KeyboardInterrupt:
        raise
    except Exception as e:
        logger.error(f"[{ticker}] Erreur optimisation : {e}", exc_info=True)
        result.error             = str(e)
        result.reliability_status = "NON FIABLE"

    return result