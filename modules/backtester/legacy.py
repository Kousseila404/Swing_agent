"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE — BACKTESTER V4 (LONG / SHORT — MACRO-AWARE)            ║
║  Moteur de simulation institutionnel — zéro look-ahead bias.    ║
║                                                                  ║
║  Stratégies supportées :                                         ║
║    1. mean_reversion  : RSI sur-vendu (Long) / sur-acheté (Short)║
║    2. trend_following : Croisement MACD haussier/baissier       ║
║    3. breakout        : Cassure Bollinger Upper / Lower          ║
║                                                                  ║
║  Nouveautés V4 :                                                 ║
║    - Filtre Macro via macro_engine.get_market_regime()           ║
║    - Signal -1 (Short) / +1 (Long) — zéro = pas de signal       ║
║    - Stop Loss SHORT au-dessus du prix d'entrée                 ║
║    - Take Profit SHORT en dessous du prix d'entrée              ║
║    - PnL inversé pour SHORT : profit si prix baisse             ║
║    - TradeResult.direction = "LONG" | "SHORT"                   ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

import config
from modules.log import logger


# ─────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────

@dataclass
class BacktestParams:
    """Paramètres configurables des stratégies V4."""
    # Sélection de la stratégie
    strategy_name: str = "mean_reversion"  # mean_reversion | trend_following | breakout

    # Paramètres communs & gestion du risque
    volume_spike: float = 1.5
    stop_method: str = "atr_1.5x"   # atr_1.5x | atr_2x | atr_3x | support | percent_5
    tp_ratio: float = 2.0
    trailing_stop: bool = True
    atr_period: int = 14
    ema_trend: int = 200             # EMA 200 jours (Daily V8 — filtre de tendance macro)

    # Paramètres Mean Reversion (RSI)
    rsi_oversold: int = 30
    rsi_period: int = 14

    # Paramètres Trend Following (MACD)
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # Paramètres Breakout (Bollinger)
    bb_length: int = 20
    bb_std: float = 2.0

    # Paramètres Trend Breakout — Daily V8 (N-bar High/Low + EMA crossover)
    # Périodes exprimées en BOUGIES DAILY (End-of-Day V8)
    breakout_period: int = 20   # Lookback N bougies Daily (Donchian Canal)
    ema_fast: int = 20          # EMA rapide ≈ 20 jours (1 mois)
    ema_slow: int = 50          # EMA lente  ≈ 50 jours (2.5 mois)

    # Paramètres Chandelier Exit — V8 Momentum
    chandelier_period: int = 22      # Lookback pour le plus haut roulant
    chandelier_atr_mult: float = 3.0 # Multiplicateur ATR (Stop = High22 - 3×ATR22)

    def to_dict(self) -> dict:
        return {
            "strategy_name":  self.strategy_name,
            "volume_spike":   self.volume_spike,
            "stop_method":    self.stop_method,
            "tp_ratio":       self.tp_ratio,
            "trailing_stop":  self.trailing_stop,
            "atr_period":     self.atr_period,
            "ema_trend":      self.ema_trend,
            "rsi_oversold":   self.rsi_oversold,
            "rsi_period":     self.rsi_period,
            "macd_fast":      self.macd_fast,
            "macd_slow":      self.macd_slow,
            "macd_signal":    self.macd_signal,
            "bb_length":      self.bb_length,
            "bb_std":         self.bb_std,
            # V8 — Trend Breakout / Chandelier
            "breakout_period":      self.breakout_period,
            "ema_fast":             self.ema_fast,
            "ema_slow":             self.ema_slow,
            "chandelier_period":    self.chandelier_period,
            "chandelier_atr_mult":  self.chandelier_atr_mult,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BacktestParams":
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in fields})

    @classmethod
    def from_config(cls) -> "BacktestParams":
        return cls(
            rsi_oversold=int(config.RSI_OVERSOLD),
            volume_spike=float(config.VOLUME_SPIKE_RATIO),
        )


@dataclass
class TradeResult:
    ticker: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    exit_reason: str
    pnl_pct: float
    pnl_eur: float
    position_size_eur: float
    days_held: int
    win: bool
    direction: str = "LONG"   # "LONG" | "SHORT"  ← Nouveau V4


@dataclass
class BacktestStats:
    total_trades: int = 0
    win_rate: float = 0.0
    win_rate_ci_low: float = 0.0
    win_rate_ci_high: float = 0.0
    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_duration_days: int = 0
    expectancy_pct: float = 0.0
    expectancy_eur: float = 0.0
    market_exposure_pct: float = 0.0
    max_win_streak: int = 0
    max_loss_streak: int = 0
    benchmark_return_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_duration_days: float = 0.0

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class BacktestResult:
    ticker: str
    params: BacktestParams
    trades: list[TradeResult] = field(default_factory=list)
    stats: Optional[BacktestStats] = None
    equity_curve: Optional[pd.Series] = None
    benchmark_curve: Optional[pd.Series] = None
    period_start: Optional[pd.Timestamp] = None
    period_end: Optional[pd.Timestamp] = None
    period_years: float = 0.0
    error: Optional[str] = None
    regime: str = "UNKNOWN"   # Régime macro au moment du lancement


# ─────────────────────────────────────────────────────────────────
# TÉLÉCHARGEMENT & PRÉPARATION DES DONNÉES
# ─────────────────────────────────────────────────────────────────

def download_data(ticker: str, years: int = 5) -> Optional[pd.DataFrame]:
    """
    Télécharge les données historiques en Daily (End-of-Day — TITAN V8).

    Passage à l'interval="1d" pour un backtest End-of-Day institutionnel :
      - 5 ans d'historique (~1260 barres) → validité statistique maximale
      - EMA 200 calculable dès le 200ème jour
      - Pas de limite Yahoo Finance (contrairement au 1H limité à 730j)

    Anti-biais : les bougies de week-ends et jours fériés sont exclues via
    dropna() et Volume > 0 (yfinance ne renvoie que les jours ouvrés).
    """
    try:
        end   = datetime.now()
        start = end - timedelta(days=years * 365 + 90)  # +90j de marge pour EMA200

        df = yf.Ticker(ticker).history(
            start=start, end=end, interval="1d", auto_adjust=True
        )

        if df is None or len(df) == 0:
            logger.warning(f"[{ticker}] Aucune donnée yfinance Daily")
            return None

        # Normalisation timezone
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        else:
            df.index = pd.to_datetime(df.index).tz_localize(None)

        # Nettoyage standard
        df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
        df = df[df["Volume"] > 0]
        df = df[df["Close"] > 0]
        return df

    except Exception as e:
        logger.error(f"[{ticker}] Erreur téléchargement Daily : {e}")
        return None


def _compute_vwap_weekly(df: pd.DataFrame) -> pd.Series:
    """
    Calcule le VWAP ancré à la semaine ISO (reset chaque lundi).

    Formule : VWAP = cumsum(typical_price × volume) / cumsum(volume)
    Le cumsum est réinitialisé à chaque début de semaine ISO.

    Gère nativement les Overnight Gaps (index discontinu) car le groupby
    opère sur le numéro de semaine, pas sur la continuité temporelle.

    Returns:
        pd.Series avec le VWAP pour chaque bougie.
    """
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    tp_vol        = typical_price * df["Volume"]

    # Clé unique par semaine : AAAA × 100 + numéro_semaine_ISO
    week_key = pd.Series(
        df.index.year * 100 + df.index.isocalendar().week.values,
        index=df.index,
        dtype=int,
    )

    tmp = pd.DataFrame({
        "tp_vol": tp_vol.values,
        "volume": df["Volume"].values,
        "week":   week_key.values,
    }, index=df.index)

    cum_tp_vol = tmp.groupby("week")["tp_vol"].cumsum()
    cum_vol    = tmp.groupby("week")["volume"].cumsum().replace(0, np.nan)

    return (cum_tp_vol / cum_vol).rename("VWAP")


# ─────────────────────────────────────────────────────────────────
# INDICATEURS NATIFS — PANDAS / NUMPY UNIQUEMENT (zéro pandas_ta)
# ─────────────────────────────────────────────────────────────────

def _native_ema(series: pd.Series, period: int) -> pd.Series:
    """EMA lissage Wilder (alpha = 1/period, adjust=False)."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def _native_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Average True Range — lissage Wilder (alpha = 1/period, adjust=False)."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def _native_rsi(series: pd.Series, period: int) -> pd.Series:
    """RSI méthode Wilder (alpha = 1/period, adjust=False)."""
    delta     = series.diff()
    gain      = delta.clip(lower=0.0)
    loss      = (-delta).clip(lower=0.0)
    avg_gain  = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss  = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs        = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _native_macd_hist(series: pd.Series, fast: int, slow: int, signal: int) -> pd.Series:
    """
    Histogramme MACD (EMA span standard, adjust=False).
    Retourne directement la colonne MACDh = MACD − Signal.
    """
    ema_fast    = series.ewm(span=fast,   adjust=False).mean()
    ema_slow    = series.ewm(span=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line - signal_line


def _native_bbands(series: pd.Series, length: int, std: float):
    """
    Bollinger Bands — retourne (lower, upper).
    Utilise std échantillon (ddof=1) comme pandas_ta.
    """
    mid   = series.rolling(length).mean()
    sigma = series.rolling(length).std(ddof=1)
    lower = mid - std * sigma
    upper = mid + std * sigma
    return lower, upper


def _native_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """ADX méthode Wilder (alpha = 1/period, adjust=False)."""
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    up_move   = high - prev_high
    down_move = prev_low - low
    plus_dm   = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index,
    )
    minus_dm  = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )
    alpha      = 1.0 / period
    tr_s       = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di    = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / tr_s.replace(0, np.nan)
    minus_di   = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / tr_s.replace(0, np.nan)
    dx         = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False).mean()


def compute_indicators(df: pd.DataFrame, params: BacktestParams) -> pd.DataFrame:
    """
    Calcule l'ensemble des indicateurs techniques — Daily V8.

    Ajouts V8 End-of-Day :
      - ATR22 + Chandelier_Stop (Stop = High_22 − 3 × ATR22) pour Momentum
      - Donchian High/Low sur vrais High/Low (pas les clôtures)
      - Suppression du VWAP hebdomadaire (non pertinent en Daily)
    """
    df = df.copy()

    # 1. ATR & Volumes
    try:
        df["ATR"] = _native_atr(df["High"], df["Low"], df["Close"], params.atr_period)
    except Exception:
        df["ATR"] = df["Close"].rolling(params.atr_period).std()

    # ADX — Profilage comportemental du ticker (TITAN V6 — Asset Profiling)
    try:
        df["ADX"] = _native_adx(df["High"], df["Low"], df["Close"], params.atr_period)
    except Exception:
        df["ADX"] = np.nan

    df["AvgVol20"]    = df["Volume"].rolling(20).mean()
    df["AvgVol5"]     = df["Volume"].rolling(5).mean()
    df["VolumeRatio"] = df["Volume"] / df["AvgVol20"].replace(0, np.nan)

    # Niveaux de support (LONG) et résistance (SHORT)
    df["Low10"]  = df["Low"].rolling(10).min()
    df["High10"] = df["High"].rolling(10).max()   # ← Nouveau V4 (pour SHORT stop)

    df["Close_prev"] = df["Close"].shift(1)

    # 2. EMA 200 (Filtre de tendance global)
    df["EMA200"] = _native_ema(df["Close"], params.ema_trend)

    # 3. RSI (Mean Reversion)
    df["RSI"] = _native_rsi(df["Close"], params.rsi_period)

    # 4. MACD (Trend Following) — histogramme = MACD − Signal
    try:
        df["MACD_hist"] = _native_macd_hist(
            df["Close"], params.macd_fast, params.macd_slow, params.macd_signal
        )
        df["MACD_hist_prev"] = df["MACD_hist"].shift(1)
    except Exception:
        df["MACD_hist"] = df["MACD_hist_prev"] = np.nan

    # 5. Bandes de Bollinger (Breakout) — Upper ET Lower
    try:
        bbl, bbu        = _native_bbands(df["Close"], params.bb_length, params.bb_std)
        df["BBU"]       = bbu
        df["BBU_prev"]  = bbu.shift(1)
        df["BBL"]       = bbl
        df["BBL_prev"]  = bbl.shift(1)
    except Exception:
        df["BBU"] = df["BBU_prev"] = df["BBL"] = df["BBL_prev"] = np.nan

    # 6. Indicateurs Trend Breakout — V8 Daily ───────────────────────────
    # Donchian Canal : rolling N-day High/Low sur les vrais prix (anti-lookahead)
    # Utilise df["High"] et df["Low"] (pas les clôtures) pour un vrai canal de Donchian
    # shift(1) : le signal du jour i est basé sur les données jusqu'à i-1
    for period in [10, 15, 20, 30]:
        df[f"Low_{period}d"]  = df["Low"].rolling(period).min().shift(1)
        df[f"High_{period}d"] = df["High"].rolling(period).max().shift(1)

    # EMA rapide et lente pour golden/death cross
    # En Daily V8 : ema_fast=20 (1 mois), ema_slow=50 (2.5 mois)
    df["EMA20"]      = _native_ema(df["Close"], params.ema_fast)
    df["EMA50"]      = _native_ema(df["Close"], params.ema_slow)
    df["EMA20_prev"] = df["EMA20"].shift(1)
    df["EMA50_prev"] = df["EMA50"].shift(1)

    # 7. Chandelier Exit — V8 Momentum ────────────────────────────────
    # Stop = Highest High(22) − chandelier_atr_mult × ATR(22)
    # Ce stop ne fait que monter (trailing haussier) — jamais redescendre.
    # Utilisation : stop initial + trailing dans _simulate_trade_loop via chandelier_stops.
    try:
        chand_period = params.chandelier_period
        chand_mult   = params.chandelier_atr_mult
        df["ATR22"]          = _native_atr(df["High"], df["Low"], df["Close"], chand_period)
        df["High22_max"]     = df["High"].rolling(chand_period).max()
        df["Chandelier_Stop"] = df["High22_max"] - chand_mult * df["ATR22"]
    except Exception as e:
        logger.warning(f"[compute_indicators] Chandelier indisponible : {e}")
        df["ATR22"]           = np.nan
        df["High22_max"]      = np.nan
        df["Chandelier_Stop"] = np.nan

    return df


# ─────────────────────────────────────────────────────────────────
# GÉNÉRATION DES SIGNAUX LONG (+1) / SHORT (-1)
# ─────────────────────────────────────────────────────────────────

def generate_signal_mask(df: pd.DataFrame, params: BacktestParams) -> np.ndarray:
    """
    Génère les signaux directionnels vectorisés :
      +1 = signal LONG
      -1 = signal SHORT
       0 = pas de signal

    Logique V4 :
      mean_reversion  : RSI<seuil + close>EMA200 → Long
                        RSI>100-seuil + close<EMA200 → Short
      trend_following : MACD hist↑ + close>EMA200 → Long
                        MACD hist↓ + close<EMA200 → Short
      breakout        : Clôture > BBU → Long
                        Clôture < BBL → Short

    Note : les shorts ne sont générés que si config.ALLOW_SHORTS = True.
    Le filtre de régime macro est appliqué dans run_backtest().
    """
    vol_r  = df["VolumeRatio"].values
    vol5   = df["AvgVol5"].values
    atr    = df["ATR"].values
    close  = df["Close"].values

    # Filtre de liquidité commun (anti-biais microstructure)
    base_valid = ~np.isnan(atr) & ~np.isnan(vol_r) & ~np.isnan(vol5)
    base_cond  = base_valid & (vol_r > params.volume_spike) & (vol5 > config.MIN_VOLUME_FILTER)

    sig = np.zeros(len(df), dtype=int)

    # ── Stratégie 1 : Mean Reversion (RSI) ────────────────────────
    if params.strategy_name == "mean_reversion":
        rsi    = df["RSI"].values
        ema200 = df["EMA200"].values
        valid  = base_cond & ~np.isnan(rsi) & ~np.isnan(ema200)

        # Long : RSI sur-vendu + prix au-dessus de la tendance longue
        long_mask  = valid & (rsi < params.rsi_oversold) & (close > ema200)
        # Short : RSI sur-acheté + prix sous la tendance longue
        short_mask = (
            valid
            & (rsi > (100 - params.rsi_oversold))
            & (close < ema200)
            & config.ALLOW_SHORTS
        )
        sig[long_mask]  = 1
        sig[short_mask] = np.where(sig[short_mask] == 0, -1, sig[short_mask])

    # ── Stratégie 2 : Trend Following (MACD) ──────────────────────
    elif params.strategy_name == "trend_following":
        macd_h      = df["MACD_hist"].values
        macd_h_prev = df["MACD_hist_prev"].values
        ema200      = df["EMA200"].values
        valid       = base_cond & ~np.isnan(macd_h) & ~np.isnan(macd_h_prev) & ~np.isnan(ema200)

        # Long : croisement MACD histogramme vers le haut + au-dessus EMA200
        long_mask  = valid & (macd_h > 0) & (macd_h_prev <= 0) & (close > ema200)
        # Short : croisement MACD histogramme vers le bas + en-dessous EMA200
        short_mask = (
            valid
            & (macd_h < 0)
            & (macd_h_prev >= 0)
            & (close < ema200)
            & config.ALLOW_SHORTS
        )
        sig[long_mask]  = 1
        sig[short_mask] = np.where(sig[short_mask] == 0, -1, sig[short_mask])

    # ── Stratégie 3 : Breakout (Bollinger) ────────────────────────
    elif params.strategy_name == "breakout":
        bbu        = df["BBU"].values
        bbu_prev   = df["BBU_prev"].values
        bbl        = df["BBL"].values if "BBL" in df.columns else np.full(len(df), np.nan)
        bbl_prev   = df["BBL_prev"].values if "BBL_prev" in df.columns else np.full(len(df), np.nan)
        close_prev = df["Close_prev"].values

        valid_long  = base_cond & ~np.isnan(bbu) & ~np.isnan(bbu_prev) & ~np.isnan(close_prev)
        valid_short = base_cond & ~np.isnan(bbl) & ~np.isnan(bbl_prev) & ~np.isnan(close_prev)

        # Long : clôture casse au-dessus de la bande Bollinger supérieure
        long_mask  = valid_long  & (close > bbu) & (close_prev <= bbu_prev)
        # Short : clôture casse en-dessous de la bande Bollinger inférieure
        short_mask = (
            valid_short
            & (close < bbl)
            & (close_prev >= bbl_prev)
            & config.ALLOW_SHORTS
        )
        sig[long_mask]  = 1
        sig[short_mask] = np.where(sig[short_mask] == 0, -1, sig[short_mask])

    # ── Stratégie 4 : Trend Breakout (N-day High/Low + EMA Cross) ─
    elif params.strategy_name == "trend_breakout":
        period      = params.breakout_period
        low_nd_col  = f"Low_{period}d"
        high_nd_col = f"High_{period}d"

        # Si les colonnes ne sont pas encore calculées (df passé sans compute_indicators V6)
        # → on retourne un masque vide pour éviter un crash
        if low_nd_col not in df.columns or high_nd_col not in df.columns:
            logger.warning(
                f"[trend_breakout] Colonnes {low_nd_col}/{high_nd_col} absentes — "
                "relancez compute_indicators() avec les params V6."
            )
            return sig

        low_nd     = df[low_nd_col].values
        high_nd    = df[high_nd_col].values
        ema200     = df["EMA200"].values
        ema20      = df["EMA20"].values      if "EMA20"      in df.columns else np.full(len(df), np.nan)
        ema50      = df["EMA50"].values      if "EMA50"      in df.columns else np.full(len(df), np.nan)
        ema20_prev = df["EMA20_prev"].values if "EMA20_prev" in df.columns else np.full(len(df), np.nan)
        ema50_prev = df["EMA50_prev"].values if "EMA50_prev" in df.columns else np.full(len(df), np.nan)

        # Validité de base + indicateurs rolling disponibles
        valid      = base_cond & ~np.isnan(low_nd) & ~np.isnan(high_nd) & ~np.isnan(ema200)
        valid_ema  = valid & ~np.isnan(ema20) & ~np.isnan(ema50) & ~np.isnan(ema20_prev) & ~np.isnan(ema50_prev)

        # ── SHORT : 2 déclencheurs (union logique) ─────────────────
        # Signal 1 — Rupture de support : le prix casse sous le plus bas
        #            des N derniers jours ET reste sous l'EMA200 (tendance baissière confirmée)
        price_breakdown   = valid & (close < low_nd) & (close < ema200)

        # Signal 2 — Death cross court terme : EMA20 croise sous EMA50
        #            (momentum baissier accéléré) + prix sous EMA200
        ema_bearish_cross = valid_ema & (ema20 < ema50) & (ema20_prev >= ema50_prev) & (close < ema200)

        # ── LONG : miroir haussier ──────────────────────────────────
        # Signal 1 — Rupture de résistance : le prix casse au-dessus du plus
        #            haut des N derniers jours + au-dessus EMA200
        price_breakup     = valid & (close > high_nd) & (close > ema200)

        # Signal 2 — Golden cross court terme : EMA20 croise au-dessus EMA50
        ema_bullish_cross = valid_ema & (ema20 > ema50) & (ema20_prev <= ema50_prev) & (close > ema200)

        short_mask = (price_breakdown | ema_bearish_cross) & config.ALLOW_SHORTS
        long_mask  = (price_breakup | ema_bullish_cross)

        sig[long_mask]  = 1
        sig[short_mask] = np.where(sig[short_mask] == 0, -1, sig[short_mask])

    # ── Stratégie 5 : Chandelier Momentum (Donchian Breakout Daily) ─
    elif params.strategy_name == "chandelier_momentum":
        ema200   = df["EMA200"].values
        high_20d = df["High_20d"].values if "High_20d" in df.columns else np.full(len(df), np.nan)
        low_20d  = df["Low_20d"].values  if "Low_20d"  in df.columns else np.full(len(df), np.nan)

        valid_long  = base_cond & ~np.isnan(ema200) & ~np.isnan(high_20d)
        valid_short = base_cond & ~np.isnan(ema200) & ~np.isnan(low_20d)

        # LONG : cassure du plus haut des 20 derniers jours + prix > EMA 200
        long_mask  = valid_long & (close > high_20d) & (close > ema200)
        # SHORT : cassure du plus bas des 20 derniers jours + prix < EMA 200
        short_mask = valid_short & (close < low_20d) & (close < ema200) & config.ALLOW_SHORTS

        sig[long_mask]  = 1
        sig[short_mask] = np.where(sig[short_mask] == 0, -1, sig[short_mask])

    return sig


# ─────────────────────────────────────────────────────────────────
# CALCUL DES STOPS INITIAUX (LONG & SHORT)
# ─────────────────────────────────────────────────────────────────

def _compute_initial_stop_long(
    entry_price: float, atr: float, low10: float, method: str
) -> float:
    """Stop Loss LONG : en DESSOUS du prix d'entrée."""
    if method == "atr_1.5x":
        stop = entry_price - 1.5 * atr
    elif method == "atr_2x":
        stop = entry_price - 2.0 * atr
    elif method == "atr_3x":
        stop = entry_price - 3.0 * atr
    elif method == "support":
        stop = low10 * 0.995 if (not np.isnan(low10) and low10 > 0) else entry_price - 1.5 * atr
    elif method == "percent_5":
        stop = entry_price * 0.95
    else:
        stop = entry_price - 1.5 * atr
    return max(stop, entry_price * 0.85)   # Floor : jamais plus de 15% de perte


def _compute_initial_stop_short(
    entry_price: float, atr: float, high10: float, method: str
) -> float:
    """Stop Loss SHORT : AU-DESSUS du prix d'entrée."""
    if method == "atr_1.5x":
        stop = entry_price + 1.5 * atr
    elif method == "atr_2x":
        stop = entry_price + 2.0 * atr
    elif method == "atr_3x":
        stop = entry_price + 3.0 * atr
    elif method == "support":
        stop = high10 * 1.005 if (not np.isnan(high10) and high10 > 0) else entry_price + 1.5 * atr
    elif method == "percent_5":
        stop = entry_price * 1.05
    else:
        stop = entry_price + 1.5 * atr
    return min(stop, entry_price * 1.15)   # Ceil : jamais plus de 15% de perte


# ─────────────────────────────────────────────────────────────────
# BOUCLE DE SIMULATION BARRE-PAR-BARRE (LONG & SHORT UNIFIÉS)
# ─────────────────────────────────────────────────────────────────

def _simulate_trade_loop(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    atrs: np.ndarray,
    entry_idx: int,
    entry_price: float,
    initial_stop: float,
    target: float,
    risk_per_share: float,
    trailing_stop_enabled: bool,
    max_bars: int,
    n: int,
    direction: str = "LONG",
    time_stop_bars: int = 10,
    time_stop_min_pnl_pct: float = 0.5,
    chandelier_stops: Optional[np.ndarray] = None,
) -> tuple[int, float, str]:
    """
    Simule le cycle de vie d'un trade barre par barre.

    LONG :
      - Stop déclenché si bar_low  ≤ current_stop  (prix descend)
      - TP   déclenché si bar_high ≥ target         (prix monte)
      - Trailing stop : Chandelier Exit si chandelier_stops fourni,
        sinon trailing ATR classique.

    SHORT :
      - Stop déclenché si bar_high ≥ current_stop  (prix monte contre nous)
      - TP   déclenché si bar_low  ≤ target         (prix descend comme prévu)
      - Trailing stop descend à mesure que le prix descend (lock profits)

    Chandelier Exit (LONG uniquement) :
      - chandelier_stops[j] = High_22.shift(1)[j] − 3 × ATR(22).shift(1)[j]
      - current_stop = max(current_stop, chandelier_stops[j])
      - Le stop ne redescend jamais → protection des profits accumulés.

    Time-Based Exit (TIME_STOP) :
      - Si le trade est ouvert depuis ≥ time_stop_bars bougies ET que le PnL
        courant est < time_stop_min_pnl_pct (0.5%), fermeture immédiate.
      - En Daily V8 : time_stop_bars=10 (2 semaines de trading).

    Gestion du gap (réalisme) :
      - LONG stop  : exit = bar_open si gap down, sinon stop_price
      - SHORT stop : exit = bar_open si gap up,   sinon stop_price
      - LONG TP    : exit = bar_open si gap up,   sinon target
      - SHORT TP   : exit = bar_open si gap down, sinon target

    Returns:
        Tuple (exit_bar_idx, exit_price, exit_reason)
    """
    current_stop = initial_stop
    end_idx = min(entry_idx + max_bars - 1, n - 1)

    for j in range(entry_idx, end_idx + 1):
        bar_low  = lows[j]
        bar_high = highs[j]
        bar_open = opens[j]
        bar_atr  = atrs[j] if not np.isnan(atrs[j]) else risk_per_share

        if np.isnan(bar_low) or np.isnan(bar_high) or np.isnan(bar_open):
            continue

        bars_open = j - entry_idx   # Nombre de bougies depuis l'entrée

        if direction == "LONG":
            # ── Chandelier Exit : met à jour le stop (monte, jamais redescend)
            if chandelier_stops is not None and j < len(chandelier_stops):
                chand_val = chandelier_stops[j]
                if not np.isnan(chand_val) and chand_val > 0 and chand_val > current_stop:
                    current_stop = chand_val

            # ── Stop Loss LONG ────────────────────────────────────
            if bar_low <= current_stop:
                # Gap down → on sort à l'open (pire que le stop)
                exit_price = bar_open if bar_open < current_stop else current_stop
                reason = "TRAILING" if current_stop > initial_stop else "STOP"
                return j, exit_price, reason

            # ── Take Profit LONG ──────────────────────────────────
            if bar_high >= target:
                # Gap up → on sort à l'open (mieux que le TP)
                exit_price = bar_open if bar_open > target else target
                return j, exit_price, "TARGET"

            # ── Time-Based Exit LONG (trade mort sans momentum) ───
            if bars_open >= time_stop_bars:
                current_close = closes[j] if not np.isnan(closes[j]) else entry_price
                current_pnl_pct = (current_close - entry_price) / entry_price * 100
                if current_pnl_pct < time_stop_min_pnl_pct:
                    return j, current_close, "TIME_STOP"

            if j == end_idx:
                exit_price = closes[j] if not np.isnan(closes[j]) else entry_price
                return j, exit_price, "TIMEOUT"

            # ── Trailing Stop LONG ATR (uniquement si pas de Chandelier) ─
            if trailing_stop_enabled and chandelier_stops is None:
                intraday_profit = bar_high - entry_price
                if intraday_profit >= risk_per_share:
                    candidate    = max(entry_price, bar_high - bar_atr)
                    current_stop = max(current_stop, candidate)

        else:  # direction == "SHORT"
            # ── Stop Loss SHORT (prix monte contre nous) ──────────
            if bar_high >= current_stop:
                # Gap up → on sort à l'open (pire que le stop)
                exit_price = bar_open if bar_open > current_stop else current_stop
                reason = "TRAILING" if current_stop < initial_stop else "STOP"
                return j, exit_price, reason

            # ── Take Profit SHORT (prix descend comme prévu) ──────
            if bar_low <= target:
                # Gap down → on sort à l'open (mieux que le TP pour le short)
                exit_price = bar_open if bar_open < target else target
                return j, exit_price, "TARGET"

            # ── Time-Based Exit SHORT (trade mort sans momentum) ──
            if bars_open >= time_stop_bars:
                current_close = closes[j] if not np.isnan(closes[j]) else entry_price
                current_pnl_pct = (entry_price - current_close) / entry_price * 100
                if current_pnl_pct < time_stop_min_pnl_pct:
                    return j, current_close, "TIME_STOP"

            if j == end_idx:
                exit_price = closes[j] if not np.isnan(closes[j]) else entry_price
                return j, exit_price, "TIMEOUT"

            # ── Trailing Stop SHORT (descend avec le prix) ────────
            if trailing_stop_enabled:
                intraday_drop = entry_price - bar_low
                if intraday_drop >= risk_per_share:
                    candidate    = min(entry_price, bar_low + bar_atr)
                    current_stop = min(current_stop, candidate)

    # Sécurité : sortie au dernier bar si boucle non interrompue
    j = end_idx
    exit_price = closes[j] if not np.isnan(closes[j]) else entry_price
    return j, exit_price, "TIMEOUT"


# ─────────────────────────────────────────────────────────────────
# MÉTRIQUES STATISTIQUES & COURBE D'ÉQUITÉ
# ─────────────────────────────────────────────────────────────────

def _wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p      = wins / n
    denom  = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _compute_stats(
    trades: list[TradeResult],
    equity_curve: pd.Series,
    benchmark_return_pct: float,
    period_years: float,
    initial_capital: float,
    total_calendar_days: int,
) -> BacktestStats:
    stats = BacktestStats()
    n = len(trades)
    if n == 0:
        stats.benchmark_return_pct = benchmark_return_pct
        return stats

    wins   = [t for t in trades if t.win]
    losses = [t for t in trades if not t.win]

    stats.total_trades      = n
    stats.win_rate          = len(wins) / n
    stats.win_rate_ci_low, stats.win_rate_ci_high = _wilson_ci(len(wins), n)
    stats.avg_win_pct       = float(np.mean([t.pnl_pct for t in wins]))   if wins   else 0.0
    stats.avg_loss_pct      = float(np.mean([t.pnl_pct for t in losses])) if losses else 0.0
    stats.avg_duration_days = float(np.mean([t.days_held for t in trades]))

    gross_profit = sum(t.pnl_eur for t in wins)
    gross_loss   = abs(sum(t.pnl_eur for t in losses))
    if gross_loss > 0:
        stats.profit_factor = round(gross_profit / gross_loss, 3)
    elif gross_profit > 0:
        stats.profit_factor = float("inf")
    else:
        stats.profit_factor = 0.0

    final_capital = equity_curve.iloc[-1] if len(equity_curve) > 0 else initial_capital
    stats.total_return_pct = (final_capital / initial_capital - 1) * 100
    if period_years > 0 and initial_capital > 0:
        stats.cagr_pct = ((final_capital / initial_capital) ** (1 / period_years) - 1) * 100

    if len(equity_curve) > 1:
        roll_max     = equity_curve.cummax()
        drawdown_abs = (equity_curve - roll_max) / roll_max
        stats.max_drawdown_pct = abs(float(drawdown_abs.min())) * 100
        in_dd = drawdown_abs < -0.001
        if in_dd.any():
            dd_groups = (in_dd != in_dd.shift()).cumsum()
            durations = []
            for _, grp in drawdown_abs[in_dd].groupby(dd_groups[in_dd]):
                if len(grp) >= 2:
                    durations.append((grp.index[-1] - grp.index[0]).days)
            stats.max_drawdown_duration_days = max(durations) if durations else 0

    if len(equity_curve) > 2:
        daily_ret = equity_curve.pct_change().dropna()
        rf_daily  = config.RISK_FREE_RATE / 252   # Défini une seule fois, toujours disponible

        if len(daily_ret) > 1 and daily_ret.std() > 1e-9:
            excess = daily_ret - rf_daily
            stats.sharpe_ratio = float(excess.mean() / excess.std() * math.sqrt(252))

        neg_ret = daily_ret[daily_ret < rf_daily]
        if len(neg_ret) > 1 and neg_ret.std() > 1e-9:
            downside_std = float(neg_ret.std() * math.sqrt(252))
            annual_ret   = stats.cagr_pct / 100
            stats.sortino_ratio = (annual_ret - config.RISK_FREE_RATE) / downside_std

    if stats.max_drawdown_pct > 0:
        stats.calmar_ratio = round(stats.cagr_pct / stats.max_drawdown_pct, 3)

    loss_rate = 1.0 - stats.win_rate
    stats.expectancy_pct = (stats.win_rate * stats.avg_win_pct + loss_rate * stats.avg_loss_pct)
    avg_pos = float(np.mean([t.position_size_eur for t in trades]))
    stats.expectancy_eur = stats.expectancy_pct / 100 * avg_pos

    total_days_in = sum(t.days_held for t in trades)
    stats.market_exposure_pct = min(100.0, total_days_in / max(1, total_calendar_days) * 100)

    ordered  = sorted(trades, key=lambda t: t.entry_date)
    sequence = [1 if t.win else -1 for t in ordered]
    max_win  = max_loss = cur_win = cur_loss = 0
    for r in sequence:
        if r == 1:
            cur_win  += 1; cur_loss  = 0; max_win  = max(max_win, cur_win)
        else:
            cur_loss += 1; cur_win   = 0; max_loss = max(max_loss, cur_loss)
    stats.max_win_streak  = max_win
    stats.max_loss_streak = max_loss

    stats.benchmark_return_pct = benchmark_return_pct
    return stats


def _build_equity_curve(
    trades: list[TradeResult],
    initial_capital: float,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
) -> pd.Series:
    dates  = pd.date_range(period_start, period_end, freq="B")
    equity = pd.Series(index=dates, dtype=float)
    pnl_by_date: dict[pd.Timestamp, float] = {}
    for t in trades:
        d = pd.Timestamp(t.exit_date).normalize()
        pnl_by_date[d] = pnl_by_date.get(d, 0.0) + t.pnl_eur
    running = initial_capital
    for d in dates:
        if d in pnl_by_date:
            running += pnl_by_date[d]
        equity[d] = running
    return equity


# ─────────────────────────────────────────────────────────────────
# POINT D'ENTRÉE PRINCIPAL
# ─────────────────────────────────────────────────────────────────

def run_backtest(
    ticker: str,
    params: Optional[BacktestParams] = None,
    years: int = config.BACKTEST_YEARS,
    df: Optional[pd.DataFrame] = None,
    keep_df: bool = False,
) -> BacktestResult:
    """
    Exécute un backtest complet avec filtre macro-économique V4.

    Flux :
      1. Détermination du régime macro via MacroEngine
         → CRASH_PANIC : arrêt immédiat (protection capital)
         → BULL_MARKET : seulement signaux Long (+1)
         → BEAR_MARKET : seulement signaux Short (-1) si ALLOW_SHORTS
      2. Téléchargement & calcul des indicateurs
      3. Génération des signaux directionnels (-1 / 0 / +1)
      4. Filtrage des signaux selon le régime
      5. Simulation barre-par-barre avec mécanique LONG ou SHORT
      6. Calcul des statistiques & courbe d'équité

    PnL selon direction :
      LONG  : (exit - entry) * n_shares − commissions
      SHORT : (entry - exit) * n_shares − commissions
              (profitable si le prix baisse)
    """
    if params is None:
        params = BacktestParams.from_config()

    result = BacktestResult(ticker=ticker, params=params)

    # ── ÉTAPE 1 : Régime Macro ─────────────────────────────────────
    try:
        from modules.macro_engine import get_market_regime
        regime = get_market_regime()
        result.regime = regime
        logger.info(f"[{ticker}] Régime macro actuel : {regime}")

        if regime == "CRASH_PANIC":
            result.error = (
                "CRASH_PANIC — Marché en panique totale (VIX≥35). "
                "Tous les trades sont bloqués par le Moteur Macro."
            )
            return result

        # Directions autorisées selon le régime et la configuration
        if not config.ALLOW_SHORTS:
            allowed_signals = {1}           # Longs uniquement (ALLOW_SHORTS désactivé)
        elif regime == "BULL_MARKET":
            allowed_signals = {1}           # Bull Market → Longs uniquement
        else:   # BEAR_MARKET
            allowed_signals = {-1}          # Bear Market → Shorts uniquement

    except ImportError:
        logger.warning(f"[{ticker}] MacroEngine introuvable — signaux Long+Short activés")
        allowed_signals = {1, -1} if config.ALLOW_SHORTS else {1}
        regime = "UNKNOWN"
        result.regime = regime
    except Exception as e:
        logger.warning(f"[{ticker}] MacroEngine erreur ({e}) — signaux Long+Short activés")
        allowed_signals = {1, -1} if config.ALLOW_SHORTS else {1}
        result.regime = "UNKNOWN"

    try:
        # ── ÉTAPE 2 : Données ──────────────────────────────────────
        if df is None:
            raw = download_data(ticker, years)
            if raw is None or len(raw) < 60:
                result.error = "Données insuffisantes"
                return result
        else:
            raw = df.copy()

        if "MACD_hist" not in raw.columns:
            df_work = compute_indicators(raw, params)
        else:
            df_work = raw.copy()

        if df is None:
            cutoff  = df_work.index[-1] - pd.DateOffset(years=years)
            df_work = df_work[df_work.index >= cutoff].copy()

        if len(df_work) < 30:
            result.error = "Période effective trop courte"
            return result

        result.period_start = df_work.index[0]
        result.period_end   = df_work.index[-1]
        result.period_years = (result.period_end - result.period_start).days / 365.25

        opens  = df_work["Open"].values
        highs  = df_work["High"].values
        lows   = df_work["Low"].values
        closes = df_work["Close"].values
        atrs   = df_work["ATR"].values    if "ATR"    in df_work.columns else np.full(len(df_work), np.nan)
        low10s = df_work["Low10"].values  if "Low10"  in df_work.columns else np.full(len(df_work), np.nan)
        high10s= df_work["High10"].values if "High10" in df_work.columns else np.full(len(df_work), np.nan)
        n      = len(df_work)

        # ── Chandelier Exit : pré-calcul du tableau de stops ──────
        # chandelier_stops[j] = High_22 - 3×ATR22 au jour j−1 (shift=1, anti-lookahead)
        # Ce tableau est passé à _simulate_trade_loop pour la stratégie chandelier_momentum.
        chandelier_stops_arr: Optional[np.ndarray] = None
        if params.strategy_name == "chandelier_momentum" and "Chandelier_Stop" in df_work.columns:
            raw_chand = df_work["Chandelier_Stop"].values
            # Shift de 1 barre : à la barre j, on utilise la valeur calculée à j-1
            chandelier_stops_arr = np.concatenate([[np.nan], raw_chand[:-1]])

        # ── ÉTAPE 3 & 4 : Signaux + Filtrage Régime ───────────────
        raw_signal_mask = generate_signal_mask(df_work, params)

        # Applique le filtre de régime : garde uniquement les directions autorisées
        filtered_mask = np.zeros(n, dtype=int)
        for allowed in allowed_signals:
            filtered_mask[raw_signal_mask == allowed] = allowed

        signal_idxs = np.where(filtered_mask != 0)[0]
        signal_idxs = signal_idxs[(signal_idxs >= 20) & (signal_idxs < n - 1)]

        # ── ÉTAPE 5 : Simulation Trade-par-Trade ──────────────────
        trades: list[TradeResult] = []
        last_exit_idx   = -1
        initial_capital = float(config.CAPITAL)

        for sig_idx in signal_idxs:
            if sig_idx <= last_exit_idx:
                continue

            entry_idx = sig_idx + 1
            if entry_idx >= n:
                break

            raw_open = opens[entry_idx]
            if np.isnan(raw_open) or raw_open <= 0:
                continue

            direction   = "LONG" if filtered_mask[sig_idx] == 1 else "SHORT"
            atr_sig     = atrs[sig_idx]
            low10_sig   = low10s[sig_idx]
            high10_sig  = high10s[sig_idx]

            if np.isnan(atr_sig) or atr_sig <= 0:
                continue

            # ── Calcul de l'entrée & du stop selon la direction ──
            if direction == "LONG":
                # Slippage défavorable LONG : on achète légèrement au-dessus de l'open
                entry_price = raw_open * (1.0 + config.SLIPPAGE_PCT)

                # Chandelier Momentum : stop initial = chandelier à l'entrée
                if (params.strategy_name == "chandelier_momentum"
                        and chandelier_stops_arr is not None
                        and entry_idx < len(chandelier_stops_arr)):
                    chand_entry = chandelier_stops_arr[entry_idx]
                    if not np.isnan(chand_entry) and chand_entry > 0 and chand_entry < entry_price:
                        stop_price = chand_entry
                    else:
                        stop_price = _compute_initial_stop_long(entry_price, atr_sig, low10_sig, params.stop_method)
                else:
                    stop_price = _compute_initial_stop_long(entry_price, atr_sig, low10_sig, params.stop_method)

                risk_per_share = entry_price - stop_price

            else:  # SHORT
                # Slippage défavorable SHORT : on vend légèrement en-dessous de l'open
                entry_price = raw_open * (1.0 - config.SLIPPAGE_PCT)
                stop_price  = _compute_initial_stop_short(entry_price, atr_sig, high10_sig, params.stop_method)
                risk_per_share = stop_price - entry_price   # Stop EST au-dessus → toujours > 0

            if risk_per_share <= 0:
                continue

            # ── Risk Parity V8 : (Capital × Risk%) / Distance_Stop ─
            # position_size = (capital × 1%) / distance_stop
            risk_capital      = initial_capital * config.MAX_RISK_PER_TRADE
            n_shares          = risk_capital / risk_per_share
            position_size_eur = n_shares * entry_price
            commission_in     = position_size_eur * config.COMMISSION_PCT

            # ── Target Price selon la direction ──────────────────
            if direction == "LONG":
                target_price = entry_price + params.tp_ratio * risk_per_share
            else:  # SHORT : cible en dessous du prix d'entrée
                target_price = entry_price - params.tp_ratio * risk_per_share

            # ── Simulation barre-par-barre ────────────────────────
            # max_bars=252 ≈ 1 an de trading Daily (laisse le Chandelier jouer)
            # TIME_STOP à 10 bougies (2 semaines) si PnL < 0.5%
            # Pour chandelier_momentum, chandelier_stops_arr gère le trailing
            exit_idx, raw_exit_price, exit_reason = _simulate_trade_loop(
                opens, highs, lows, closes, atrs,
                entry_idx, entry_price, stop_price, target_price,
                risk_per_share, params.trailing_stop, 252, n,
                direction=direction,
                time_stop_bars=10,
                time_stop_min_pnl_pct=0.5,
                chandelier_stops=chandelier_stops_arr if direction == "LONG" else None,
            )

            # ── Calcul du PnL avec slippage et commissions ────────
            if direction == "LONG":
                # Exit : on vend légèrement en-dessous du prix de sortie
                exit_price_net = raw_exit_price * (1.0 - config.SLIPPAGE_PCT)
                pnl_eur        = (exit_price_net - entry_price) * n_shares
            else:  # SHORT
                # Exit (rachat) : on achète légèrement au-dessus du prix de sortie
                exit_price_net = raw_exit_price * (1.0 + config.SLIPPAGE_PCT)
                pnl_eur        = (entry_price - exit_price_net) * n_shares   # Profit si prix baisse

            commission_out = n_shares * raw_exit_price * config.COMMISSION_PCT
            pnl_eur       -= commission_in + commission_out
            pnl_pct        = pnl_eur / position_size_eur * 100

            entry_date = df_work.index[entry_idx]
            exit_date  = df_work.index[exit_idx]
            days_held  = max(1, (exit_date - entry_date).days)

            trade = TradeResult(
                ticker            = ticker,
                entry_date        = entry_date,
                exit_date         = exit_date,
                entry_price       = round(entry_price, 4),
                exit_price        = round(exit_price_net, 4),
                stop_price        = round(stop_price, 4),
                target_price      = round(target_price, 4),
                exit_reason       = exit_reason,
                pnl_pct           = round(pnl_pct, 4),
                pnl_eur           = round(pnl_eur, 2),
                position_size_eur = round(position_size_eur, 2),
                days_held         = days_held,
                win               = pnl_eur > 0,
                direction         = direction,
            )
            trades.append(trade)
            last_exit_idx = exit_idx

        # ── ÉTAPE 6 : Statistiques & Équité ───────────────────────
        result.trades = trades

        bh_entry  = float(closes[0])
        bh_exit   = float(closes[-1])
        bh_return = (bh_exit / bh_entry - 1) * 100 if bh_entry > 0 else 0.0

        equity_curve = _build_equity_curve(trades, initial_capital, result.period_start, result.period_end)
        result.equity_curve = equity_curve

        close_series = df_work["Close"].reindex(equity_curve.index, method="ffill")
        result.benchmark_curve = initial_capital * close_series / bh_entry

        total_cal_days = (result.period_end - result.period_start).days
        result.stats = _compute_stats(
            trades, equity_curve, bh_return, result.period_years, initial_capital, total_cal_days
        )

    except Exception as e:
        logger.error(f"[{ticker}] Erreur backtest : {e}", exc_info=True)
        result.error = str(e)

    return result
