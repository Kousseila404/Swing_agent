"""
modules/backtester/vector_engine.py
=====================================
Moteur de Backtest Vectorisé V11 — Stratégie MOMENTUM_DIP
==========================================================

Architecture strictement séparée de la logique Live.
Aucun import des modules Live (scanner, alerter, ia_brain, etc.).

Stratégie MOMENTUM_DIP :
  Entry  : Close > EMA(param_ema)  (macro haussier, période dynamique)
           AND ADX > param_adx     (force directionnelle)
           AND RSI < param_rsi     (pullback / dip)
  Exit   : Time Stop après param_time_stop barres depuis l'entrée
           + SL Trailing param_sl% depuis le pic (vectorbt)
           + TP param_tp% (vectorbt)

Anti-Look-Ahead Bias :
  Tous les signaux sont DÉCALÉS de 1 barre (shift(1)) afin de simuler
  une exécution en ouverture du lendemain (J+1). Aucune donnée future
  n'est utilisée pour générer un signal à l'instant t.

Optimisation 6D (Optuna — God Fitness) :
  param_ema        ∈ [50, 200]   — Période EMA de tendance (dynamique)
  param_adx        ∈ [10, 35]    — Seuil ADX (force directionnelle)
  param_rsi        ∈ [20, 60]    — Seuil RSI (condition de dip)
  param_sl         ∈ [0.05, 0.20] — Stop Loss trailing (%)
  param_tp         ∈ [0.10, 0.60] — Take Profit (%)
  param_time_stop  ∈ [5, 40]     — Time Stop (barres depuis entrée)

  God Fitness = CAGR × (CAGR / |MaxDD|)
  Filtres durs : MaxDD < -15% → -1.0 | n_trades < 100 → -1.0

Efficacité mémoire :
  ADX et RSI sont pré-calculés UNE SEULE FOIS avant la boucle Optuna.
  Seule l'EMA (période variable) est recalculée à chaque trial.

Reporting :
  QuantStats est utilisé pour toutes les métriques de performance et la
  génération de rapports HTML. PyFolio n'est PAS utilisé (incompatible
  Python 3.12). vectorbt est utilisé uniquement pour l'exécution des
  signaux (simulation de portefeuille).

Dépendances :
  vectorbt >= 0.26 | optuna >= 3.0 | quantstats >= 0.0.62 | numpy | pandas
"""

from __future__ import annotations

import json
import os
import pickle
import warnings
from datetime import date
from typing import Any, Optional

import numpy as np
import pandas as pd
import vectorbt as vbt
import optuna
import quantstats as qs

# Supprime les logs verbeux de vectorbt, optuna et quantstats
optuna.logging.set_verbosity(optuna.logging.WARNING)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# ─────────────────────────────────────────────────────────────────────────────
# CHEMINS & CONSTANTES
# ─────────────────────────────────────────────────────────────────────────────

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CACHE_DIR            = os.path.join(_PROJECT_ROOT, "data", "market_cache")
BEST_STRATEGY_PATH   = os.path.join(_PROJECT_ROOT, "data", "best_strategy.json")

# Indicateurs techniques — paramètres fixes (utilisés comme défauts)
EMA_TREND_PERIOD: int = 200
EMA_EXIT_PERIOD: int  = 50
RSI_PERIOD: int       = 14
ADX_PERIOD: int       = 14

# Paramètres de simulation
FEES: float           = 0.001   # 0.1 % par exécution
TRAILING_STOP: float  = 0.07    # 7 % trailing stop (défaut backward-compat)

# Filtre qualité des données
MIN_BARS: int = 250


# ─────────────────────────────────────────────────────────────────────────────
# INDICATEURS — HELPERS SINGLE-TICKER (utilisés par les fonctions matricielles)
# ─────────────────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    """EMA classique via pandas ewm (span = period). Causale."""
    return series.ewm(span=period, adjust=False).mean()


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """
    RSI via lissage Wilder. ewm(com=period-1) ↔ alpha=1/period.
    Zéro look-ahead : .diff() n'utilise que les données passées.
    """
    delta    = close.diff()
    gain     = delta.clip(lower=0.0)
    loss     = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(com=period - 1, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False, min_periods=period).mean()
    rs  = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def _adx(
    high:   pd.Series,
    low:    pd.Series,
    close:  pd.Series,
    period: int = ADX_PERIOD,
) -> pd.Series:
    """
    ADX via lissage Wilder (J. Welles Wilder Jr.).
    Zéro look-ahead : shift(1) pour la barre précédente.
    """
    prev_close = close.shift(1)
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)

    tr = pd.concat(
        [high - low,
         (high - prev_close).abs(),
         (low  - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    up_move   = high - prev_high
    down_move = prev_low - low

    plus_dm  = pd.Series(
        np.where((up_move > down_move)   & (up_move  > 0), up_move,   0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move)   & (down_move > 0), down_move, 0.0),
        index=high.index,
    )

    alpha = 1.0 / period
    atr_s      = tr.ewm(alpha=alpha,       adjust=False, min_periods=period).mean()
    plus_dm_s  = plus_dm.ewm(alpha=alpha,  adjust=False, min_periods=period).mean()
    minus_dm_s = minus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    plus_di  = 100.0 * (plus_dm_s  / atr_s.replace(0.0, np.nan))
    minus_di = 100.0 * (minus_dm_s / atr_s.replace(0.0, np.nan))

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx     = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx    = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    return adx


def _safe(fn, *args, default: float = float("nan"), **kwargs) -> float:
    """Appelle fn(*args, **kwargs); retourne default si exception ou NaN/inf."""
    try:
        v = float(fn(*args, **kwargs))
        return v if np.isfinite(v) else default
    except Exception:
        return default


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_cache_data(
    cache_dir: str = CACHE_DIR,
    min_bars:  int = MIN_BARS,
) -> dict[str, pd.DataFrame]:
    """
    Charge et valide tous les fichiers .pkl du market_cache.

    Filtres :
      - Colonnes OHLCV obligatoires présentes
      - Au moins min_bars barres daily
      - Moins de 5 % de NaN sur Close
      - Index trié chronologiquement

    Returns:
        Dict {ticker: DataFrame OHLCV} — tickers valides uniquement.
    """
    if not os.path.isdir(cache_dir):
        raise FileNotFoundError(
            f"Dossier market_cache introuvable : {cache_dir}\n"
            "Lancez d'abord : python main.py --scan-sp500"
        )

    pkl_files = sorted(f for f in os.listdir(cache_dir) if f.endswith(".pkl"))
    print(f"  [V11] Chargement de {len(pkl_files)} fichiers depuis {cache_dir} ...")

    required_cols = {"Close", "High", "Low", "Open", "Volume"}
    data: dict[str, pd.DataFrame] = {}

    for fname in pkl_files:
        ticker = fname[:-4]
        fpath  = os.path.join(cache_dir, fname)
        try:
            with open(fpath, "rb") as fh:
                df = pickle.load(fh)
            if not required_cols.issubset(df.columns):
                continue
            if len(df) < min_bars:
                continue
            if df["Close"].isna().mean() > 0.05:
                continue
            data[ticker] = df.sort_index()
        except Exception:
            continue

    print(
        f"  [V11] {len(data)} tickers valides "
        f"(filtre ≥ {min_bars} barres, NaN < 5%)"
    )
    return data


# ─────────────────────────────────────────────────────────────────────────────
# SPX MACRO FILTER — CHARGEMENT DE L'INDICE DE RÉFÉRENCE
# ─────────────────────────────────────────────────────────────────────────────

def _load_spx_close(
    common_idx: pd.DatetimeIndex,
    cache_dir:  str = CACHE_DIR,
    spx_ticker: str = "^GSPC",
) -> Optional[pd.Series]:
    """
    Charge le cours de clôture du S&P 500 aligné sur l'index commun.

    Stratégie de chargement (ordre de priorité) :
      1. Cache local  : {cache_dir}/{spx_ticker}.pkl  (ou GSPC.pkl / SPY.pkl)
      2. yfinance     : téléchargement automatique (nécessite une connexion)
      3. None         : si aucune source disponible → filtre macro désactivé

    Le résultat est forward-filled + backward-filled pour couvrir les jours
    de fermeture absents de l'index commun (différence US/intl).

    Précautions d'alignement :
      - Timezone normalisée à tz-naive (tz_localize(None))
      - reindex(common_idx) garantit une correspondance exacte d'index
      - fill_value=None sur reindex → ffill/bfill comblent les trous

    Returns:
        pd.Series (float) de clôtures SPX indexée sur common_idx.
        None si les données sont indisponibles (filtre macro désactivé).
    """
    spx: Optional[pd.Series] = None

    # ── 1. Essai depuis le cache local ──────────────────────────────
    for fname in (f"{spx_ticker}.pkl", "GSPC.pkl", "SPY.pkl"):
        fpath = os.path.join(cache_dir, fname)
        if os.path.isfile(fpath):
            try:
                with open(fpath, "rb") as fh:
                    df_cached = pickle.load(fh)
                if "Close" in df_cached.columns and len(df_cached) >= 50:
                    spx = df_cached["Close"]
                    break
            except Exception:
                continue

    # ── 2. Téléchargement yfinance si absent du cache ────────────────
    if spx is None:
        try:
            import yfinance as yf  # lazy import — isolé du module live
            print(f"  [V11] Téléchargement {spx_ticker} depuis yfinance ...")
            df_spx = yf.download(
                spx_ticker, period="10y", interval="1d",
                progress=False, auto_adjust=True,
            )
            if isinstance(df_spx.columns, pd.MultiIndex):
                df_spx.columns = df_spx.columns.get_level_values(0)
            if "Close" in df_spx.columns and len(df_spx) >= 50:
                spx = df_spx["Close"]
        except Exception as exc:
            print(f"  [V11] ⚠️  Téléchargement {spx_ticker} échoué ({exc})")

    # ── 3. Aucune source disponible ──────────────────────────────────
    if spx is None or len(spx) == 0:
        print("  [V11] ⚠️  SPX indisponible — filtre macro DÉSACTIVÉ")
        return None

    # ── 4. Normalisation timezone → tz-naive ────────────────────────
    if hasattr(spx.index, "tz") and spx.index.tz is not None:
        spx.index = spx.index.tz_localize(None)
    else:
        spx.index = pd.to_datetime(spx.index).tz_localize(None)

    # ── 5. Alignement sur common_idx + remplissage des trous ────────
    # reindex → NaN pour dates absentes, ffill/bfill couvrent les jours fériés
    spx_aligned = spx.reindex(common_idx).ffill().bfill()

    if spx_aligned.isna().all():
        print("  [V11] ⚠️  SPX : aucune date commune — filtre macro DÉSACTIVÉ")
        return None

    return spx_aligned


# ─────────────────────────────────────────────────────────────────────────────
# MATRICES DE DONNÉES BRUTES (pré-calcul — index commun)
# ─────────────────────────────────────────────────────────────────────────────

def _build_raw_matrices(
    data: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Aligne tous les tickers sur un index commun et retourne les matrices
    Close, High, Low en tant que DataFrames (dates × tickers).

    L'intersection garantit qu'aucun NaN ne subsiste pour des raisons
    d'IPO / delisting. Les colonnes entièrement NaN sont supprimées.
    Les NaN résiduels (jours fériés) sont remplis par forward-fill.

    Returns:
        close_mx, high_mx, low_mx — DataFrames alignés, même shape.
    """
    # ── Index commun ────────────────────────────────────────────────
    common_idx: Optional[pd.DatetimeIndex] = None
    for df in data.values():
        idx = df.index
        common_idx = idx if common_idx is None else common_idx.intersection(idx)

    if common_idx is None or len(common_idx) == 0:
        raise ValueError(
            "Aucune date commune entre les tickers. "
            "Vérifiez l'intégrité du market_cache."
        )

    # ── Construction des DataFrames ─────────────────────────────────
    close_cols: dict[str, pd.Series] = {}
    high_cols:  dict[str, pd.Series] = {}
    low_cols:   dict[str, pd.Series] = {}

    for ticker, df in data.items():
        df_a = df.reindex(common_idx)
        close_cols[ticker] = df_a["Close"]
        high_cols[ticker]  = df_a["High"]
        low_cols[ticker]   = df_a["Low"]

    close_mx = pd.DataFrame(close_cols)
    high_mx  = pd.DataFrame(high_cols)
    low_mx   = pd.DataFrame(low_cols)

    # ── Nettoyage ───────────────────────────────────────────────────
    valid_cols = close_mx.dropna(how="all", axis=1).columns
    close_mx   = close_mx[valid_cols].ffill().dropna(how="all")
    high_mx    = high_mx[valid_cols].ffill().reindex(close_mx.index)
    low_mx     = low_mx[valid_cols].ffill().reindex(close_mx.index)

    return close_mx, high_mx, low_mx


# ─────────────────────────────────────────────────────────────────────────────
# MATRICES D'INDICATEURS (pré-calcul unique avant boucle Optuna)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_rsi_matrix(
    close_mx: pd.DataFrame,
    period:   int = RSI_PERIOD,
) -> pd.DataFrame:
    """
    Calcule le RSI pour tous les tickers en une seule passe vectorisée.
    Applique _rsi() colonne par colonne (pandas ewm natif).

    Returns:
        DataFrame RSI (dates × tickers), même shape que close_mx.
    """
    return close_mx.apply(lambda col: _rsi(col, period))


def _compute_adx_matrix(
    high_mx:  pd.DataFrame,
    low_mx:   pd.DataFrame,
    close_mx: pd.DataFrame,
    period:   int = ADX_PERIOD,
) -> pd.DataFrame:
    """
    Calcule l'ADX pour tous les tickers en une seule passe vectorisée.
    Applique _adx() colonne par colonne.

    Returns:
        DataFrame ADX (dates × tickers), même shape que close_mx.
    """
    return pd.DataFrame(
        {
            col: _adx(high_mx[col], low_mx[col], close_mx[col], period)
            for col in close_mx.columns
        },
        index=close_mx.index,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GOD FITNESS FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def _god_fitness(cagr: float, max_drawdown: float, n_trades: int) -> float:
    """
    God Fitness : CAGR × (CAGR / |MaxDD|)

    Récompense exponentiellement les combinaisons fort CAGR / faible drawdown.

    Filtres durs (retournent -1.0) :
      - n_trades  < 100    : résultat statistiquement non fiable
      - CAGR      ≤ 0      : stratégie non rentable
      - |MaxDD|   ≥ 15%    : drawdown excessif (> -15%)
      - Valeurs non finies  : NaN ou inf

    Returns:
        Score ∈ (-1.0, +∞) — maximiser via Optuna.
    """
    if n_trades < 100:
        return -1.0
    if not np.isfinite(cagr) or cagr <= 0.0:
        return -1.0
    if not np.isfinite(max_drawdown) or max_drawdown >= 0.0:
        return -1.0
    if max_drawdown < -0.15:           # drawdown > 15% → éliminé
        return -1.0
    return float(cagr * (cagr / abs(max_drawdown)))


# ─────────────────────────────────────────────────────────────────────────────
# EXÉCUTION D'UN TRIAL (7 hyperparamètres — Cash Sharing)
# ─────────────────────────────────────────────────────────────────────────────

def _run_trial(
    close_mx:        pd.DataFrame,
    adx_mx:          pd.DataFrame,
    rsi_mx:          pd.DataFrame,
    param_ema:       int,
    param_adx:       float,
    param_rsi:       float,
    param_sl:        float,
    param_tp:        float,
    param_time_stop: int,
    param_pos_size:  float,              # ← fraction du cash par trade
    fees:            float = FEES,
    macro_bull_flag: "pd.Series | None" = None,  # ← NEW : filtre macro SPX
) -> dict[str, Any]:
    """
    Exécute un backtest complet avec les 7 hyperparamètres donnés.

    Seule l'EMA est recalculée (période dynamique param_ema).
    ADX et RSI sont reçus pré-calculés → zéro recomputation inutile.

    Stratégie :
      Entry  : Close > EMA(param_ema) AND ADX > param_adx AND RSI < param_rsi
               → décalée de +1 barre (anti-look-ahead)
      Exit   : Time Stop = entries décalées de param_time_stop barres
               + SL Trailing param_sl% (sl_trail=True, vectorbt)
               + TP param_tp% (vectorbt)

    Cash Sharing (anti-cash-drag) :
      group_by=True + cash_sharing=True → un seul pool de cash global.
      size=param_pos_size, size_type='percent' → chaque trade consomme
      param_pos_size% du cash disponible au moment de l'exécution.
      call_seq='auto' → sorties prioritaires, puis entrées triées.

    Market Regime Filter (anti-krach) :
      Si macro_bull_flag est fourni (pd.Series bool, index = common_idx) :
      Les signaux d'entrée sont annulés sur les barres où S&P 500 < EMA200.
      macro_bull_flag est pré-calculé UNE SEULE FOIS avant la boucle Optuna.
      Application : raw_entries &= macro_bull_flag via numpy broadcasting.

    Returns:
        Dict de métriques incluant cagr, max_drawdown, n_trades, sharpe, etc.
        + 'portfolio_returns' (pd.Series) pour les rapports QuantStats.
    """
    # ── Recompute EMA seulement (seule dimension variable par trial) ─
    ema_mx = close_mx.ewm(span=param_ema, adjust=False).mean()

    # ── Signal d'entrée brut à la clôture de t ──────────────────────
    raw_entries = (
        (close_mx > ema_mx)       &   # Filtre macro : tendance haussière
        (adx_mx   > param_adx)    &   # Force : marché directionnel
        (rsi_mx   < param_rsi)        # Trigger : pullback / dip
    )

    # ── Market Regime Filter : S&P 500 > EMA200 ─────────────────────
    # Annule toutes les entrées sur les barres où l'indice est en bear market.
    # Protection contre les krachs majeurs (2020, 2022, etc.)
    # Alignement : macro_bull_flag est indexé sur common_idx = close_mx.index
    # Broadcasting : reindex → (n,1) numpy array → & sur tout le DataFrame (n,m)
    if macro_bull_flag is not None:
        _mbf = (
            macro_bull_flag
            .reindex(close_mx.index, fill_value=False)
            .astype(bool)
        )
        # Reshape en vecteur colonne (n, 1) pour broadcast numpy sur (n, m)
        raw_entries = raw_entries & _mbf.to_numpy().reshape(-1, 1)

    # ── Anti-Look-Ahead : exécution simulée à t+1 ───────────────────
    entries_mx = raw_entries.shift(1).fillna(False).astype(bool)

    # ── Time Stop : sortie param_time_stop barres après l'entrée ────
    # entries_mx[i]=True → exits_mx[i+param_time_stop]=True
    exits_mx = entries_mx.shift(param_time_stop).fillna(False).astype(bool)

    # Ré-aligner sur l'index close_mx (shift peut légèrement décaler)
    entries_mx = entries_mx.reindex(close_mx.index).fillna(False)
    exits_mx   = exits_mx.reindex(close_mx.index).fillna(False)

    # ── Simulation vectorbt — Cash Sharing ──────────────────────────
    # group_by=True     : toutes les colonnes forment UN seul portefeuille
    # cash_sharing=True : pool de cash global unique (élimine le cash drag)
    # size / size_type  : param_pos_size% du cash disponible par trade
    # call_seq='auto'   : sorties prioritaires, puis entrées triées
    # upon_long_conflict: entry gagne si entry ET exit le même jour (même ticker)
    pf = vbt.Portfolio.from_signals(
        close              = close_mx,
        entries            = entries_mx,
        exits              = exits_mx,
        group_by           = True,           # 1 groupe = 1 portefeuille global
        cash_sharing       = True,           # pool de cash partagé → no cash drag
        size               = param_pos_size, # fraction du cash dispo par trade
        size_type          = "percent",      # % du cash disponible au moment T
        call_seq           = "auto",         # sorties avant entrées, tri intelligent
        fees               = fees,
        sl_stop            = param_sl,       # Stop Loss trailing depuis le pic
        sl_trail           = True,           # Trailing (monte, jamais redescend)
        tp_stop            = param_tp,       # Take Profit fixe
        upon_long_conflict = 1,              # ConflictMode.Entry → entrée prioritaire
        freq               = "1D",
    )

    # ── Rendements du portefeuille global (cash sharing → 1 groupe) ─
    # pf.returns(group_by=True) → DataFrame 1 colonne ou Series selon vbt
    # squeeze() → Series de rendements daily du portefeuille consolidé
    raw_ret = pf.returns(group_by=True)
    if isinstance(raw_ret, pd.DataFrame):
        portfolio_returns: pd.Series = raw_ret.squeeze()
    elif isinstance(raw_ret, pd.Series):
        portfolio_returns = raw_ret
    else:
        portfolio_returns = pd.Series(dtype=float)

    prices = portfolio_returns.add(1.0).cumprod()

    # ── Métriques QuantStats ─────────────────────────────────────────
    sharpe        = _safe(qs.stats.sharpe,        portfolio_returns, periods=252)
    sortino       = _safe(qs.stats.sortino,       portfolio_returns, periods=252)
    cagr          = _safe(qs.stats.cagr,          portfolio_returns, periods=252)
    max_drawdown  = _safe(qs.stats.max_drawdown,  prices)
    calmar        = _safe(qs.stats.calmar,         portfolio_returns, periods=252)
    profit_factor = _safe(qs.stats.profit_factor, portfolio_returns)
    volatility    = _safe(qs.stats.volatility,    portfolio_returns, periods=252)
    omega         = _safe(qs.stats.omega,         portfolio_returns, periods=252)
    win_rate      = _safe(qs.stats.win_rate,      portfolio_returns)

    # ── Trade metrics vectorbt ───────────────────────────────────────
    # records_readable reste disponible par ticker même avec cash_sharing
    try:
        trades   = pf.trades.records_readable
        n_trades = int(len(trades))
        trade_wr = float((trades["PnL"] > 0).mean()) if n_trades > 0 else 0.0
    except Exception:
        n_trades = 0
        trade_wr = float("nan")

    return {
        # QuantStats metrics
        "sharpe":        sharpe,
        "sortino":       sortino,
        "cagr":          cagr,
        "max_drawdown":  max_drawdown,
        "calmar":        calmar,
        "profit_factor": profit_factor,
        "volatility":    volatility,
        "omega":         omega,
        "win_rate":      win_rate,
        "total_return":  float(prices.iloc[-1] - 1.0) if len(prices) > 0 else float("nan"),
        # vectorbt trade metrics
        "n_tickers":     int(close_mx.shape[1]),
        "n_trades":      n_trades,
        "trade_wr":      trade_wr,
        # Hyperparamètres (7D)
        "param_ema":       param_ema,
        "param_adx":       param_adx,
        "param_rsi":       param_rsi,
        "param_sl":        param_sl,
        "param_tp":        param_tp,
        "param_time_stop": param_time_stop,
        "param_pos_size":  param_pos_size,
        # Série retours pour QuantStats reports
        "portfolio_returns": portfolio_returns,
    }


# ─────────────────────────────────────────────────────────────────────────────
# JSON AUTOPILOT — PERSISTANCE DE LA MEILLEURE STRATÉGIE
# ─────────────────────────────────────────────────────────────────────────────

def save_best_strategy(
    params:   dict[str, Any],
    metrics:  dict[str, Any],
    n_trials: int,
) -> str:
    """
    Sauvegarde les 6 meilleurs hyperparamètres + métriques dans un fichier JSON.

    Chemin : data/best_strategy.json (créé si inexistant).

    Format JSON :
    {
      "param_ema": 150, "param_adx": 22.5, "param_rsi": 35.0,
      "param_sl": 0.07, "param_tp": 0.25, "param_time_stop": 15,
      "param_pos_size": 0.10,
      "cagr_estimated": 0.18, "sharpe": 1.2, "max_drawdown": -0.08,
      "profit_factor": 1.6, "n_trades": 1523,
      "n_trials": 50, "tested_at": "2026-03-11",
      "strategy": "MOMENTUM_DIP_V11"
    }

    Returns:
        Chemin absolu du fichier JSON sauvegardé.
    """
    os.makedirs(os.path.dirname(BEST_STRATEGY_PATH), exist_ok=True)

    def _f(x: Any) -> Any:
        """Convertit les types numpy en types Python natifs pour JSON."""
        if isinstance(x, (np.integer,)):
            return int(x)
        if isinstance(x, (np.floating,)):
            return float(x)
        return x

    payload = {
        "param_ema":       int(_f(params.get("param_ema",        EMA_TREND_PERIOD))),
        "param_adx":       float(_f(params.get("param_adx",      20.0))),
        "param_rsi":       float(_f(params.get("param_rsi",      40.0))),
        "param_sl":        float(_f(params.get("param_sl",       TRAILING_STOP))),
        "param_tp":        float(_f(params.get("param_tp",       0.30))),
        "param_time_stop": int(_f(params.get("param_time_stop",  20))),
        "param_pos_size":  float(_f(params.get("param_pos_size", 0.10))),  # ← NEW
        "cagr_estimated":  float(_f(metrics.get("cagr",           float("nan")))),
        "sharpe":          float(_f(metrics.get("sharpe",          float("nan")))),
        "max_drawdown":    float(_f(metrics.get("max_drawdown",    float("nan")))),
        "profit_factor":   float(_f(metrics.get("profit_factor",   float("nan")))),
        "n_trades":        int(_f(metrics.get("n_trades",          0))),
        "n_trials":        int(n_trials),
        "tested_at":       date.today().isoformat(),
        "strategy":        "MOMENTUM_DIP_V11",
    }

    with open(BEST_STRATEGY_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, allow_nan=False,
                  default=lambda o: None)

    return BEST_STRATEGY_PATH


# ─────────────────────────────────────────────────────────────────────────────
# HYPER-OPTIMISATION OPTUNA — ESPACE 6D + GOD FITNESS
# ─────────────────────────────────────────────────────────────────────────────

def run_optimization(
    data:     dict[str, pd.DataFrame],
    n_trials: int   = 20,
    fees:     float = FEES,
) -> tuple[dict[str, Any], "optuna.Study"]:
    """
    Optimise les 6 hyperparamètres MOMENTUM_DIP via Optuna (TPE Sampler).

    Espace de recherche 7D :
      param_ema        ∈ [50, 200]    — Période EMA de tendance
      param_adx        ∈ [10.0, 35.0] — Seuil ADX
      param_rsi        ∈ [20.0, 60.0] — Seuil RSI (dip)
      param_sl         ∈ [0.05, 0.20] — Stop Loss trailing
      param_tp         ∈ [0.10, 0.60] — Take Profit
      param_time_stop  ∈ [5, 40]      — Time Stop (barres)
      param_pos_size   ∈ [0.05, 0.33] — Taille position (% cash par trade)

    Objectif : Maximiser la God Fitness = CAGR × (CAGR / |MaxDD|).
    Filtres  : MaxDD < -15% → -1.0 | n_trades < 100 → -1.0

    Cash Sharing :
      group_by=True + cash_sharing=True → pool de cash unique pour les 462
      tickers. Élimine le cash drag : le capital inactif est redéployé sur
      chaque nouveau signal.

    Efficacité :
      ADX et RSI sont pré-calculés AVANT la boucle → O(1) par trial.
      Seule l'EMA (span=param_ema) est recalculée à chaque trial.

    Args:
        data:     Données OHLCV {ticker: DataFrame} (load_cache_data)
        n_trials: Nombre d'essais Optuna
        fees:     Frais de courtage par ordre

    Returns:
        (best_metrics, optuna_study)
    """
    # ── 1. Construction des matrices brutes ─────────────────────────
    print(f"\n  [V11] Construction des matrices de données ({len(data)} tickers)...")
    close_mx, high_mx, low_mx = _build_raw_matrices(data)
    print(f"  [V11] Index commun : {len(close_mx)} barres × {close_mx.shape[1]} tickers")

    # ── 2. Pré-calcul ADX + RSI (une seule fois) ────────────────────
    print("  [V11] Pré-calcul ADX (Wilder, 14j)  ...")
    adx_mx = _compute_adx_matrix(high_mx, low_mx, close_mx)

    print("  [V11] Pré-calcul RSI (Wilder, 14j)  ...")
    rsi_mx = _compute_rsi_matrix(close_mx)

    # ── 3. Filtre Macro S&P 500 (pré-calculé une fois — EMA200 fixe) ─
    # L'EMA200 du SPX est toujours à 200 barres (pas param_ema).
    # macro_bull_flag est une bool Series : True = S&P 500 en bull market.
    print("  [V11] Calcul filtre macro S&P 500 (EMA 200) ...")
    _spx_close = _load_spx_close(close_mx.index)
    if _spx_close is not None:
        _spx_ema200     = _spx_close.ewm(span=200, adjust=False).mean()
        macro_bull_flag = (_spx_close > _spx_ema200).astype(bool)
        _bull_pct       = macro_bull_flag.mean()
        print(
            f"  [V11] Filtre macro : {macro_bull_flag.sum()} barres BULL "
            f"/ {len(macro_bull_flag)} total ({_bull_pct:.0%} du temps)"
        )
    else:
        macro_bull_flag = None
        print("  [V11] Filtre macro : DÉSACTIVÉ (SPX indisponible)")

    print(
        f"\n  [V11] Démarrage Optuna — {n_trials} trials "
        f"| 7D : EMA∈[50,200] ADX∈[10,35] RSI∈[20,60] "
        f"SL∈[5%,20%] TP∈[10%,60%] T∈[5,40] POS∈[5%,33%]\n"
        f"       Objectif : God Fitness = CAGR × (CAGR / |MaxDD|)\n"
        f"       Cash Sharing activé → pool unique, no cash drag\n"
    )

    # ── 3. Fonction objectif ─────────────────────────────────────────
    def objective(trial: optuna.Trial) -> float:
        param_ema       = trial.suggest_int(  "param_ema",       50,  200)
        param_adx       = trial.suggest_float("param_adx",       10.0, 35.0)
        param_rsi       = trial.suggest_float("param_rsi",       20.0, 60.0)
        param_sl        = trial.suggest_float("param_sl",        0.05, 0.20)
        param_tp        = trial.suggest_float("param_tp",        0.10, 0.60)
        param_time_stop = trial.suggest_int(  "param_time_stop", 5,    40)
        param_pos_size  = trial.suggest_float("param_pos_size",  0.05, 0.33)  # ← NEW

        try:
            metrics = _run_trial(
                close_mx, adx_mx, rsi_mx,
                param_ema, param_adx, param_rsi,
                param_sl, param_tp, param_time_stop,
                param_pos_size, fees,
                macro_bull_flag = macro_bull_flag,  # ← filtre macro SPX
            )

            fitness = _god_fitness(
                metrics["cagr"],
                metrics["max_drawdown"],
                metrics["n_trades"],
            )

            # Stocke les métriques clés dans le trial pour le callback
            trial.set_user_attr("cagr",         metrics["cagr"])
            trial.set_user_attr("max_drawdown",  metrics["max_drawdown"])
            trial.set_user_attr("n_trades",      metrics["n_trades"])
            trial.set_user_attr("sharpe",        metrics["sharpe"])

            return fitness

        except Exception as exc:
            print(f"    [Trial {trial.number}] Exception : {exc}")
            return -1.0

    # ── 4. Callback de progression (1 ligne par trial) ───────────────
    def _progress_cb(study: optuna.Study, trial: optuna.FrozenTrial) -> None:
        best = study.best_value if study.best_trial else float("nan")
        cagr = trial.user_attrs.get("cagr",        float("nan"))
        dd   = trial.user_attrs.get("max_drawdown", float("nan"))
        n    = trial.user_attrs.get("n_trades",     0)
        p    = trial.params
        fit  = trial.value if trial.value is not None else float("nan")

        # Formatage compact
        cagr_s = f"{cagr*100:+.1f}%" if np.isfinite(cagr) else "  N/A "
        dd_s   = f"{dd*100:.1f}%"    if np.isfinite(dd)   else "  N/A"
        fit_s  = f"{fit:.4f}"        if np.isfinite(fit)  else "-1.0000"
        best_s = f"{best:.4f}"       if np.isfinite(best) else "-1.0000"

        print(
            f"    Trial {trial.number:>3} | "
            f"EMA={p.get('param_ema', 0):>3} "
            f"ADX={p.get('param_adx', 0):>5.1f} "
            f"RSI={p.get('param_rsi', 0):>5.1f} "
            f"SL={p.get('param_sl', 0):.2f} "
            f"TP={p.get('param_tp', 0):.2f} "
            f"T={p.get('param_time_stop', 0):>2} "
            f"POS={p.get('param_pos_size', 0):.2f} | "   # ← NEW
            f"CAGR={cagr_s} DD={dd_s} N={n:>5} | "
            f"Fit={fit_s} Best={best_s}"
        )

    # ── 5. Lancement de l'étude ──────────────────────────────────────
    study = optuna.create_study(
        direction  = "maximize",
        sampler    = optuna.samplers.TPESampler(seed=42),
        study_name = "MOMENTUM_DIP_V11_6D",
    )
    study.optimize(objective, n_trials=n_trials, callbacks=[_progress_cb])

    # ── 6. Backtest final avec les meilleurs paramètres ──────────────
    best_p = study.best_params
    print(
        f"\n  [V11] Optimisation terminée. Meilleurs paramètres :\n"
        f"        EMA={best_p['param_ema']} | "
        f"ADX={best_p['param_adx']:.2f} | "
        f"RSI={best_p['param_rsi']:.2f} | "
        f"SL={best_p['param_sl']:.2f} | "
        f"TP={best_p['param_tp']:.2f} | "
        f"T={best_p['param_time_stop']} | "
        f"POS={best_p['param_pos_size']:.2f}"   # ← NEW
    )

    best_metrics = _run_trial(
        close_mx, adx_mx, rsi_mx,
        int(best_p["param_ema"]),
        float(best_p["param_adx"]),
        float(best_p["param_rsi"]),
        float(best_p["param_sl"]),
        float(best_p["param_tp"]),
        int(best_p["param_time_stop"]),
        float(best_p["param_pos_size"]),
        fees,
        macro_bull_flag = macro_bull_flag,  # ← filtre macro SPX
    )

    # ── 7. Sauvegarde JSON ───────────────────────────────────────────
    json_path = save_best_strategy(best_p, best_metrics, n_trials)
    print(f"  [V11] Stratégie sauvegardée → {json_path}")

    return best_metrics, study


# ─────────────────────────────────────────────────────────────────────────────
# RÉTROCOMPATIBILITÉ — RUN_VECTORBT_BACKTEST (V8 API)
# ─────────────────────────────────────────────────────────────────────────────

def run_vectorbt_backtest(
    data:           dict[str, pd.DataFrame],
    param_adx:      float = 20.0,
    param_rsi:      float = 40.0,
    fees:           float = FEES,
    trailing_stop:  float = TRAILING_STOP,
    param_pos_size: float = 0.10,           # ← NEW : 10% du cash par trade
) -> dict[str, Any]:
    """
    Wrapper de rétrocompatibilité V8.

    Utilise les nouveaux internals V11 (_build_raw_matrices, _run_trial)
    avec des paramètres fixes pour EMA, TP et Time Stop.
    Cash Sharing activé par défaut (param_pos_size=10%).

    Paramètres V8 → V11 :
      param_adx     → param_adx     (inchangé)
      param_rsi     → param_rsi     (inchangé)
      trailing_stop → param_sl      (SL trailing, comportement identique)
      param_pos_size → param_pos_size (position sizing, défaut 10%)
      EMA_TREND_PERIOD (200) → param_ema
      0.30 fixe              → param_tp
      20 barres fixe         → param_time_stop

    Args:
        data:           Données OHLCV {ticker: DataFrame}
        param_adx:      Seuil ADX (défaut 20.0)
        param_rsi:      Seuil RSI (défaut 40.0)
        fees:           Frais de courtage
        trailing_stop:  % de trailing stop loss (défaut 7%)
        param_pos_size: Fraction du cash par trade (défaut 10%)

    Returns:
        Dict de métriques (même format que _run_trial).
    """
    close_mx, high_mx, low_mx = _build_raw_matrices(data)
    adx_mx = _compute_adx_matrix(high_mx, low_mx, close_mx)
    rsi_mx = _compute_rsi_matrix(close_mx)

    # ── Filtre macro SPX (cohérent avec run_optimization) ────────────
    _spx_close = _load_spx_close(close_mx.index)
    if _spx_close is not None:
        _spx_ema200     = _spx_close.ewm(span=200, adjust=False).mean()
        macro_bull_flag = (_spx_close > _spx_ema200).astype(bool)
    else:
        macro_bull_flag = None

    return _run_trial(
        close_mx        = close_mx,
        adx_mx          = adx_mx,
        rsi_mx          = rsi_mx,
        param_ema       = EMA_TREND_PERIOD,  # 200 — fixe pour compat V8
        param_adx       = param_adx,
        param_rsi       = param_rsi,
        param_sl        = trailing_stop,     # trailing SL = ancien trailing_stop
        param_tp        = 0.30,              # TP 30% fixe
        param_time_stop = 20,                # Time Stop 20 barres fixe
        param_pos_size  = param_pos_size,
        fees            = fees,
        macro_bull_flag = macro_bull_flag,   # ← filtre macro SPX
    )
