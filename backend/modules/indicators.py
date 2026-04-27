"""
╔══════════════════════════════════════════════════════════════════════════╗
║  MODULE INDICATORS — Indicateurs techniques centralisés                  ║
║  Source unique de vérité importée par tous les modules                   ║
║  (scanner.py, tracker.py, api.py, backtester/legacy.py)                  ║
╚══════════════════════════════════════════════════════════════════════════╝

Conventions :
  - Lissage EMA/RSI/ATR/ADX : méthode Wilder (alpha = 1/period, adjust=False)
  - Lissage MACD            : convention standard (ewm span, alpha = 2/(span+1))
  - Bollinger Bands         : ddof=1 (écart-type échantillon — identique à
                              pandas_ta et TradingView)
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────
# HELPERS PRIVÉS
# ─────────────────────────────────────────────────────────────────

def _true_range(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
) -> pd.Series:
    """True Range vectorisé — max(H−L, |H−Cprev|, |L−Cprev|)."""
    prev_close = close.shift(1)
    return pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)


# ─────────────────────────────────────────────────────────────────
# EMA
# ─────────────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    """EMA avec lissage Wilder (alpha = 1/period, adjust=False)."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


# ─────────────────────────────────────────────────────────────────
# RSI
# ─────────────────────────────────────────────────────────────────

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI méthode Wilder (alpha = 1/period, adjust=False)."""
    delta    = series.diff()
    gain     = delta.clip(lower=0.0)
    loss     = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


# ─────────────────────────────────────────────────────────────────
# ATR — deux variantes de signature
# ─────────────────────────────────────────────────────────────────

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    ATR (Average True Range) série complète — méthode Wilder.

    Args:
        df:     DataFrame avec colonnes High, Low, Close.
        period: Période (défaut 14).
    """
    tr = _true_range(df["High"], df["Low"], df["Close"])
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def atr_series(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    ATR à partir de séries individuelles — méthode Wilder.

    Variante de `atr(df, period)` pour les contextes où les colonnes
    OHLCV sont déjà extraites en séries séparées (ex : backtester).
    """
    tr = _true_range(high, low, close)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def atr_value(df: pd.DataFrame, period: int = 14) -> float:
    """ATR de la dernière barre en float. Retourne nan si impossible."""
    try:
        val = float(atr(df, period).iloc[-1])
        return val if not math.isnan(val) else float("nan")
    except Exception:
        return float("nan")


# ─────────────────────────────────────────────────────────────────
# ADX
# ─────────────────────────────────────────────────────────────────

def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    ADX (Average Directional Index) — méthode Wilder.
    Retourne la série ADX uniquement (pas +DI/−DI).
    """
    tr        = _true_range(high, low, close)
    prev_high = high.shift(1)
    prev_low  = low.shift(1)

    up_move   = high - prev_high
    down_move = prev_low - low

    plus_dm  = pd.Series(
        np.where((up_move > down_move) & (up_move > 0),   up_move,   0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )

    alpha    = 1.0 / period
    tr_s     = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di  = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / tr_s.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / tr_s.replace(0, np.nan)
    dx       = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False).mean()


# ─────────────────────────────────────────────────────────────────
# MACD HISTOGRAM
# ─────────────────────────────────────────────────────────────────

def macd_hist(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.Series:
    """
    Histogramme MACD — retourne MACD_line − Signal_line.

    Utilise ewm(span=..., adjust=False) — convention standard MACD
    (alpha = 2/(span+1)), distincte de la convention Wilder (alpha = 1/period).
    """
    ema_fast    = series.ewm(span=fast,   adjust=False).mean()
    ema_slow    = series.ewm(span=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line - signal_line


# ─────────────────────────────────────────────────────────────────
# BOLLINGER BANDS
# ─────────────────────────────────────────────────────────────────

def bbands(
    series: pd.Series,
    length: int = 20,
    std: float = 2.0,
) -> tuple[pd.Series, pd.Series]:
    """
    Bollinger Bands — retourne (lower, upper).

    ddof=1 : écart-type échantillon — identique à pandas_ta et TradingView.
    """
    mid   = series.rolling(length).mean()
    sigma = series.rolling(length).std(ddof=1)
    return mid - std * sigma, mid + std * sigma
