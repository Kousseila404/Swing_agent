"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE — SCANNER V14.1 (DAILY — ADX ROUTING — CHANDELIER EXIT) ║
║  Scanner End-of-Day avec profilage ADX et routage intelligent.  ║
║                                                                  ║
║  V14.1 : Zéro dépendance pandas_ta / vbt.                       ║
║           EMA / RSI / ADX / BB calculés en Pandas pur (Wilder). ║
║                                                                  ║
║  Flux :                                                          ║
║    1. Interroge le MacroEngine → régime + VIX actuel            ║
║    2. CRASH_PANIC → arrêt immédiat, alerte console              ║
║    3. BULL_MARKET → cherche signaux LONG uniquement             ║
║    4. BEAR_MARKET → cherche signaux SHORT uniquement            ║
║    5. Routage ADX par ticker :                                   ║
║       ADX > 25 → CHANDELIER_MOMENTUM (Donchian + EMA200)       ║
║               → MOMENTUM_DIP (pullback RSI<45 ou sous BBL)     ║
║       ADX ≤ 25 → MEAN_REVERSION (Bollinger Lower + EMA200)     ║
║    6. Chandelier Exit ATR × 3 (VIX ≤ 25) / × 4 (VIX > 25)    ║
║    7. Retourne les ScanResult avec leur direction               ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

import config
from modules.log import logger
from modules.ticker_profiles import get_backtest_params


# ─────────────────────────────────────────────────────────────────
# INDICATEURS NATIFS — PANDAS / NUMPY UNIQUEMENT (Wilder exact)
# ─────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    """EMA avec lissage Wilder (alpha = 1/period, adjust=False)."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI méthode Wilder (alpha = 1/period, adjust=False)."""
    delta     = series.diff()
    gain      = delta.clip(lower=0.0)
    loss      = (-delta).clip(lower=0.0)
    avg_gain  = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss  = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs        = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    ADX méthode Wilder (alpha = 1/period, adjust=False).
    Retourne la série ADX uniquement.
    """
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    # True Range
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    up_move   = high - prev_high
    down_move = prev_low - low

    plus_dm  = np.where((up_move > down_move) & (up_move > 0),   up_move,   0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm_s  = pd.Series(plus_dm,  index=high.index).ewm(alpha=1.0 / period, adjust=False).mean()
    minus_dm_s = pd.Series(minus_dm, index=high.index).ewm(alpha=1.0 / period, adjust=False).mean()
    tr_s       = tr.ewm(alpha=1.0 / period, adjust=False).mean()

    plus_di  = 100.0 * plus_dm_s  / tr_s.replace(0, np.nan)
    minus_di = 100.0 * minus_dm_s / tr_s.replace(0, np.nan)

    dx  = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1.0 / period, adjust=False).mean()
    return adx


def _bbands(series: pd.Series, length: int = 20, std: float = 2.0):
    """
    Bollinger Bands — retourne (lower, upper).
    Utilise la moyenne mobile simple et l'écart-type glissant.
    """
    mid   = series.rolling(length).mean()
    sigma = series.rolling(length).std(ddof=0)
    lower = mid - std * sigma
    upper = mid + std * sigma
    return lower, upper


# ─────────────────────────────────────────────────────────────────
# V17 — RISK MANAGEMENT (Prop Firm : FTMO / TopStep)
# ─────────────────────────────────────────────────────────────────

def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """
    Calcule l'ATR (Average True Range) sur `period` périodes via lissage Wilder.

    Méthode identique à _adx() mais retourne uniquement l'ATR (float) de la
    dernière barre complète — utilisable indépendamment du calcul directionnel.

    Args:
        df:     DataFrame Pandas avec colonnes High, Low, Close.
        period: Nombre de périodes (défaut 14, standard Wilder).

    Returns:
        Valeur ATR de la dernière barre (float), ou float('nan') si impossible.
    """
    try:
        prev_close = df["Close"].shift(1)
        tr = pd.concat(
            [
                df["High"] - df["Low"],
                (df["High"] - prev_close).abs(),
                (df["Low"]  - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr_series = tr.ewm(alpha=1.0 / period, adjust=False).mean()
        return float(atr_series.iloc[-1])
    except Exception as exc:
        logger.warning(f"[ATR] Calcul échoué : {exc}")
        return float("nan")


def calculate_trade_parameters(
    entry_price: float,
    atr_value: float,
    direction: str = "LONG",
) -> dict:
    """
    Calcule les métriques de dimensionnement du trade selon les règles Prop Firm (V17).

    Formules :
        LONG  → SL = Entry − 2×ATR  |  TP = Entry + 2×(Entry − SL) = Entry + 4×ATR
        SHORT → SL = Entry + 2×ATR  |  TP = Entry − 2×(SL − Entry) = Entry − 4×ATR
        R/R   = |TP − Entry| / |Entry − SL|          (cible ≥ 2.0)
        Size  = ⌊(Capital × Risk%) / risque_unitaire⌋  (arrondi inférieur)

    Args:
        entry_price: Prix d'entrée au marché.
        atr_value:   Valeur ATR(14) courante.
        direction:   "LONG" ou "SHORT".

    Returns:
        Dict : stop_loss, take_profit, rr_ratio, position_size,
               risk_amount ($), gain_amount ($).
        Toutes les valeurs sont NaN / 0 en cas d'erreur de calcul.
    """
    _nan_result = {
        "stop_loss":     float("nan"),
        "take_profit":   float("nan"),
        "rr_ratio":      float("nan"),
        "position_size": 0,
        "risk_amount":   0.0,
        "gain_amount":   0.0,
    }

    try:
        total_capital  = getattr(config, "TOTAL_CAPITAL",  100_000)
        risk_pct       = getattr(config, "RISK_PER_TRADE", 0.0025)

        if np.isnan(atr_value) or atr_value <= 0:
            raise ValueError(f"ATR invalide ({atr_value})")

        if direction == "LONG":
            stop_loss     = entry_price - (2.0 * atr_value)
            risk_per_unit = entry_price - stop_loss        # 2 × ATR
            take_profit   = entry_price + (2.0 * risk_per_unit)
        else:  # SHORT
            stop_loss     = entry_price + (2.0 * atr_value)
            risk_per_unit = stop_loss - entry_price        # 2 × ATR
            take_profit   = entry_price - (2.0 * risk_per_unit)

        if risk_per_unit <= 0:
            raise ValueError(f"risque_unitaire ≤ 0 ({risk_per_unit:.6f})")

        reward_per_unit = abs(take_profit - entry_price)
        rr_ratio        = reward_per_unit / risk_per_unit

        dollar_risk     = total_capital * risk_pct          # ex : 250 $
        position_size   = int(dollar_risk / risk_per_unit)  # arrondi inférieur

        return {
            "stop_loss":     round(stop_loss,   4),
            "take_profit":   round(take_profit, 4),
            "rr_ratio":      round(rr_ratio,    2),
            "position_size": position_size,
            "risk_amount":   round(position_size * risk_per_unit,   2),
            "gain_amount":   round(position_size * reward_per_unit, 2),
        }
    except Exception as exc:
        logger.warning(f"[TradeParams] Calcul échoué : {exc}")
        return _nan_result


# ─────────────────────────────────────────────────────────────────
# V18 — FILTRE EARNINGS (yfinance calendar)
# ─────────────────────────────────────────────────────────────────

def check_upcoming_earnings(ticker_symbol: str, days_threshold: int = 5) -> bool:
    """
    Vérifie si les prochains résultats financiers sont dans moins de `days_threshold` jours.

    Si la date est imminente, le signal est BLOQUÉ pour éviter le risque binaire
    (gap violent au lendemain d'une publication de bénéfices).

    Gestion défensive : toute exception yfinance → retourne False (fail-open).
    Le calendrier n'est pas toujours disponible via l'API yfinance.

    Args:
        ticker_symbol:  Symbole yfinance (ex : "NVDA", "META").
        days_threshold: Fenêtre de blocage en jours avant publication (défaut 5).

    Returns:
        True  → Earnings imminents (≤ threshold jours) → signal à IGNORER.
        False → Pas d'earnings proche ou date indisponible → signal VALIDE.
    """
    try:
        ticker_obj = yf.Ticker(ticker_symbol)
        cal = ticker_obj.calendar

        if cal is None:
            logger.debug(f"[{ticker_symbol}] Calendrier earnings vide — signal validé par défaut")
            return False

        # yfinance peut retourner un DataFrame ou un dict selon la version
        earnings_dates: "pd.Series | None" = None

        if isinstance(cal, pd.DataFrame):
            if not cal.empty:
                if "Earnings Date" in cal.columns:
                    earnings_dates = pd.to_datetime(cal["Earnings Date"], errors="coerce").dropna()
                elif "Earnings Date" in cal.index:
                    raw = cal.loc["Earnings Date"]
                    items = list(raw) if hasattr(raw, "__iter__") else [raw]
                    earnings_dates = pd.to_datetime(pd.Series(items), errors="coerce").dropna()
        elif isinstance(cal, dict):
            raw = cal.get("Earnings Date", [])
            if raw:
                items = list(raw) if hasattr(raw, "__iter__") else [raw]
                earnings_dates = pd.to_datetime(pd.Series(items), errors="coerce").dropna()

        if earnings_dates is None or len(earnings_dates) == 0:
            logger.debug(f"[{ticker_symbol}] Date earnings introuvable — signal validé par défaut")
            return False

        # Normalise le timestamp (supprime le timezone si présent)
        next_ts   = pd.Timestamp(earnings_dates.min())
        next_date = next_ts.tz_localize(None).date() if next_ts.tzinfo else next_ts.date()
        today     = datetime.now().date()
        days_until = (next_date - today).days

        if days_until < 0:
            return False   # Earnings déjà passés → pas de blocage

        if days_until <= days_threshold:
            logger.warning(
                f"[{ticker_symbol}] ⚠️ Earnings dans {days_until}j "
                f"({next_date}) — Signal BLOQUÉ (filtre V18)"
            )
            return True

        logger.debug(f"[{ticker_symbol}] Earnings dans {days_until}j — Signal autorisé")
        return False

    except Exception as exc:
        logger.warning(
            f"[{ticker_symbol}] check_upcoming_earnings erreur ({exc}) "
            "— signal validé par défaut (fail-open)"
        )
        return False


# ─────────────────────────────────────────────────────────────────
# PONT DE PRODUCTION — STRATÉGIE OPTIMISÉE PAR OPTUNA
# ─────────────────────────────────────────────────────────────────

# Valeurs de fallback si best_strategy.json est absent (comportement V8 pur)
_DEFAULT_STRATEGY: dict = {
    "param_ema":       200,    # EMA200 standard (identique à l'existant)
    "param_adx":       25.0,   # Seuil ADX routing existant
    "param_rsi":       45.0,   # Seuil RSI MOMENTUM_DIP existant
    "param_sl":        0.07,   # 7% trailing stop loss
    "param_tp":        0.30,   # 30% take profit
    "param_time_stop": 20,     # 20 barres time stop
    "param_pos_size":  0.10,   # 10% du capital par trade
}


def load_optimized_strategy() -> dict:
    """
    Charge les hyperparamètres optimisés depuis data/best_strategy.json.

    Ce fichier est généré automatiquement par l'optimiseur Optuna V11 après :
        python main.py --vector-backtest --trials 50

    Si le fichier est absent, illisible ou invalide, retourne les valeurs
    de fallback (_DEFAULT_STRATEGY) → comportement identique à la V8.
    Aucun crash, aucune exception propagée.

    Returns:
        Dict avec les clés : param_ema, param_adx, param_rsi,
        param_sl, param_tp, param_time_stop, param_pos_size.
    """
    # Résolution depuis la racine du projet (CWD) — robuste quel que soit
    # l'emplacement de ce fichier dans l'arborescence.
    path = os.path.join(os.getcwd(), "data", "best_strategy.json")

    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)

        strategy = {
            "param_ema":       int(raw.get("param_ema",       _DEFAULT_STRATEGY["param_ema"])),
            "param_adx":       float(raw.get("param_adx",     _DEFAULT_STRATEGY["param_adx"])),
            "param_rsi":       float(raw.get("param_rsi",     _DEFAULT_STRATEGY["param_rsi"])),
            "param_sl":        float(raw.get("param_sl",      _DEFAULT_STRATEGY["param_sl"])),
            "param_tp":        float(raw.get("param_tp",      _DEFAULT_STRATEGY["param_tp"])),
            "param_time_stop": int(raw.get("param_time_stop", _DEFAULT_STRATEGY["param_time_stop"])),
            "param_pos_size":  float(raw.get("param_pos_size",_DEFAULT_STRATEGY["param_pos_size"])),
        }
        logger.info(
            f"[Scanner] ✅ Stratégie Optuna chargée → "
            f"EMA={strategy['param_ema']} | ADX={strategy['param_adx']:.1f} | "
            f"RSI={strategy['param_rsi']:.1f} | POS={strategy['param_pos_size']:.0%}"
        )
        return strategy

    except FileNotFoundError:
        logger.warning(
            "[Scanner] ⚠️  best_strategy.json introuvable — "
            "Fallback valeurs V8. Lancez --vector-backtest pour optimiser."
        )
        return dict(_DEFAULT_STRATEGY)

    except Exception as exc:
        logger.error(f"[Scanner] Erreur lecture best_strategy.json ({exc}) — fallback V8")
        return dict(_DEFAULT_STRATEGY)


@dataclass
class ScanResult:
    """Structure de résultat pour un signal détecté."""
    ticker: str
    name: str
    price: float
    volume: int
    avg_volume: float
    volume_ratio: float
    rsi: float           # Gardé pour rétrocompatibilité
    signal: str          # MEAN_REVERSION | TREND_FOLLOWING | BREAKOUT
    change_pct: float    # Variation jour en %
    direction: str = "LONG"   # "LONG" | "SHORT"
    # V17 — Risk Management : paramètres calculés dans _analyze_ticker_inner
    atr:           float = float("nan")  # ATR(14) au moment du signal
    stop_loss:     float = float("nan")  # Entry ± 2×ATR
    take_profit:   float = float("nan")  # Entry ± 4×ATR
    rr_ratio:      float = float("nan")  # Risk/Reward (cible ≥ 2.0)
    position_size: int   = 0             # Nombre d'actions (arrondi inférieur)
    risk_amount:   float = 0.0           # Montant $ réellement risqué
    gain_amount:   float = 0.0           # Gain potentiel $ si TP atteint


# ─────────────────────────────────────────────────────────────────
# SCANNER PRINCIPAL (POINT D'ENTRÉE)
# ─────────────────────────────────────────────────────────────────

def scan_universe() -> list[ScanResult]:
    """
    Scanne tous les tickers de config.TICKERS_FLAT.

    Étape 1 : Interrogation du MacroEngine.
      - CRASH_PANIC → retourne liste vide + alerte, pas un seul trade.
      - BULL_MARKET → cherche uniquement des signaux LONG.
      - BEAR_MARKET → cherche uniquement des signaux SHORT.

    Étape 2 : Analyse ticker par ticker.

    Returns:
        Liste de ScanResult pour les tickers ayant passé les filtres.
    """
    # ── Étape 0 : Chargement de la stratégie optimisée ───────────────
    strategy = load_optimized_strategy()

    # ── Étape 1 : Vérification du Régime Macro ────────────────────
    regime = _get_regime_safe()

    total = len(config.TICKERS_FLAT)

    if regime == "CRASH_PANIC":
        _print_crash_alert()
        logger.critical(
            "🚨 [Scanner] CRASH_PANIC détecté — Scan annulé. "
            "Protection du capital activée. VIX ≥ 35."
        )
        return []

    allowed_direction = "LONG" if regime == "BULL_MARKET" else "SHORT"
    logger.info(
        f"📡 [Scanner] Régime : {regime} — "
        f"Direction autorisée : {allowed_direction} — "
        f"{total} tickers à analyser"
    )

    # ── Étape 2 : Scan des tickers ────────────────────────────────
    results: list[ScanResult] = []
    end_date = datetime.now()

    for ticker_symbol in config.TICKERS_FLAT:
        try:
            result = _analyze_ticker(ticker_symbol, end_date, regime=regime, strategy=strategy)
            if result is not None:
                results.append(result)
        except Exception as e:
            logger.warning(f"[{ticker_symbol}] Erreur ignorée : {e}")
            continue

    direction_label = f"{allowed_direction}S"
    logger.info(
        f"✅ [Scanner] {len(results)} signal(aux) {direction_label} "
        f"détecté(s) sur {total} tickers"
    )
    return results


# ─────────────────────────────────────────────────────────────────
# ANALYSE D'UN TICKER INDIVIDUEL
# ─────────────────────────────────────────────────────────────────

def _analyze_ticker(
    ticker_symbol: str,
    end_date: datetime,
    regime: str = "BULL_MARKET",
    df: pd.DataFrame | None = None,
    strategy: dict | None = None,
) -> ScanResult | None:
    """
    Analyse un ticker individuel (Daily V14.1) et retourne un ScanResult si un signal
    valide est détecté pour le régime macro courant.

    Tous les indicateurs sont calculés en Pandas/NumPy pur (zéro pandas_ta / vbt).

    Routage ADX :
      ADX > 25 (MOMENTUM)       → Cassure Donchian High(20) + EMA200 → CHANDELIER_MOMENTUM
                                → ou MOMENTUM_DIP (RSI < param_rsi / Close < BBL)
      ADX ≤ 25 (MEAN_REVERSION) → Toucher de la Bollinger inférieure + EMA200 → MEAN_REVERSION

    Args:
        ticker_symbol: Symbole yfinance (ex: "NVDA", "BTC-USD").
        end_date:      Date de fin pour le téléchargement des données.
        regime:        Régime macro actuel ("BULL_MARKET" | "BEAR_MARKET").
        df:            DataFrame pré-chargé (optionnel). Si fourni, le téléchargement
                       est ignoré → 0 requête redondante (Data Pass-Through V8.5).
        strategy:      Hyperparamètres Optuna. Chargés automatiquement si None.

    Returns:
        ScanResult si un signal est trouvé, None sinon.
    """
    try:
        return _analyze_ticker_inner(ticker_symbol, end_date, regime, df, strategy)
    except Exception as exc:
        logger.warning(f"[{ticker_symbol}] Calcul échoué, ticker ignoré : {exc}")
        return None


def _analyze_ticker_inner(
    ticker_symbol: str,
    end_date: datetime,
    regime: str,
    df: pd.DataFrame | None,
    strategy: dict | None,
) -> ScanResult | None:
    """Implémentation interne — les exceptions remontent vers _analyze_ticker."""

    # ── 0. Paramètres de stratégie ────────────────────────────────
    params = get_backtest_params(ticker_symbol)

    if strategy is None:
        strategy = load_optimized_strategy()
    _param_ema = int(strategy["param_ema"])
    _param_adx = float(strategy["param_adx"])
    _param_rsi = float(strategy["param_rsi"])

    # ── 1. Données Daily ──────────────────────────────────────────
    _df_provided = df is not None
    ticker_obj   = yf.Ticker(ticker_symbol)

    if not _df_provided:
        start_date = end_date - timedelta(days=5 * 365 + 90)
        df = ticker_obj.history(
            start=start_date, end=end_date, interval="1d", auto_adjust=True
        )
    else:
        if isinstance(df.columns, pd.MultiIndex):
            df = df.copy()
            df.columns = df.columns.get_level_values(0)

    if df is None or len(df) == 0:
        logger.debug(f"[{ticker_symbol}] Aucune donnée Daily yfinance")
        return None

    # Normalisation timezone
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    else:
        df.index = pd.to_datetime(df.index).tz_localize(None)

    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    df = df[df["Volume"] > 0]
    df = df[df["Close"] > 0]

    _min_bars = 30 if _df_provided else 220
    if len(df) < _min_bars:
        logger.debug(f"[{ticker_symbol}] Pas assez de barres Daily ({len(df)} < {_min_bars})")
        return None

    # ── 2. Calcul des indicateurs (Pandas/NumPy pur) ──────────────
    df["AvgVol20"]    = df["Volume"].rolling(20).mean()
    df["AvgVol5"]     = df["Volume"].rolling(5).mean()
    df["VolumeRatio"] = df["Volume"] / df["AvgVol20"].replace(0, np.nan)

    # EMA 200 — filtre tendance macro (fixe)
    df["EMA200"] = _ema(df["Close"], 200)

    # EMA dynamique Optuna — MOMENTUM_DIP
    if _param_ema == 200:
        df["EMA_OPT"] = df["EMA200"]
    else:
        df["EMA_OPT"] = _ema(df["Close"], _param_ema)

    # RSI — méthode Wilder
    df["RSI"] = _rsi(df["Close"], period=params.rsi_period)

    # Bollinger Bands
    bbl_s, bbu_s = _bbands(df["Close"], length=params.bb_length, std=params.bb_std)
    df["BBL"] = bbl_s
    df["BBU"] = bbu_s

    # ADX — méthode Wilder
    try:
        df["ADX"] = _adx(df["High"], df["Low"], df["Close"], period=14)
    except Exception:
        df["ADX"] = np.nan

    # Donchian — anti-lookahead via shift(1)
    df["Donchian_High_20"] = df["High"].rolling(20).max().shift(1)
    df["Donchian_Low_20"]  = df["Low"].rolling(20).min().shift(1)

    # ── 3. Extraction de la dernière barre ───────────────────────
    latest = df.iloc[-1]

    vol_r        = latest.get("VolumeRatio", 0)
    vol5         = latest.get("AvgVol5", 0)
    close        = float(latest["Close"])
    ema200       = float(latest.get("EMA200",  np.nan))
    ema_opt      = float(latest.get("EMA_OPT", ema200))
    rsi          = float(latest.get("RSI",     np.nan))
    bbl          = float(latest.get("BBL",     np.nan))
    bbl_prev     = float(df["BBL"].iloc[-2])  if len(df) >= 2 else bbl
    bbu          = float(latest.get("BBU",     np.nan))
    bbu_prev     = float(df["BBU"].iloc[-2])  if len(df) >= 2 else bbu
    donchian_high = float(latest.get("Donchian_High_20", np.nan))
    donchian_low  = float(latest.get("Donchian_Low_20",  np.nan))
    close_prev    = float(df["Close"].iloc[-2]) if len(df) >= 2 else close

    # ── VIX → Chandelier ATR multiplier adaptatif ────────────────
    try:
        from modules.macro_engine import get_regime_details
        _macro = get_regime_details()
        _vix   = float(_macro.get("vix") or 20.0)
    except Exception:
        _vix = 20.0
    chandelier_atr_mult = 4.0 if _vix > 25.0 else params.chandelier_atr_mult  # noqa: F841

    # ── Filtre global liquidité + volume ─────────────────────────
    if pd.isna(vol_r) or pd.isna(vol5) or vol5 < config.MIN_VOLUME_FILTER or vol_r < params.volume_spike:
        return None

    # ── ADX moyen sur 20 barres → profil comportemental ──────────
    adx_series  = df["ADX"].dropna()
    if len(adx_series) >= 5:
        avg_adx     = float(adx_series.iloc[-20:].mean())
        is_momentum = avg_adx > 25.0
    else:
        avg_adx     = np.nan
        is_momentum = False

    signal    = None
    direction = None

    # ── 4. Détection du signal selon le régime et le profil ADX ──
    if regime == "BULL_MARKET":
        direction = "LONG"

        if is_momentum:
            # MOMENTUM (ADX > 25) : Cassure Donchian High
            if not (pd.isna(donchian_high) or pd.isna(ema200)):
                if close > donchian_high and close > ema200:
                    signal = "CHANDELIER_MOMENTUM"

            # MOMENTUM_DIP (ADX > 25) : Pullback sur leader confirmé
            if signal is None and not pd.isna(ema_opt) and close > ema_opt:
                _adx_ok = pd.isna(avg_adx) or (avg_adx > _param_adx)
                dip_triggered = (
                    (not pd.isna(rsi) and rsi < _param_rsi)
                    or (not pd.isna(bbl) and close < bbl)
                )
                if _adx_ok and dip_triggered:
                    signal = "MOMENTUM_DIP"
        else:
            # MEAN_REVERSION (ADX ≤ 25) : Rebond sur Bollinger
            if not (pd.isna(bbl) or pd.isna(ema200)):
                if close_prev > bbl_prev and close <= bbl and close > ema200:
                    signal = "MEAN_REVERSION"
                elif not pd.isna(rsi) and rsi < params.rsi_oversold and close > ema200:
                    signal = "MEAN_REVERSION"

    else:   # regime == "BEAR_MARKET"
        if not config.ALLOW_SHORTS:
            return None

        direction = "SHORT"

        if is_momentum:
            # MOMENTUM SHORT : Cassure Donchian Low
            if not (pd.isna(donchian_low) or pd.isna(ema200)):
                if close < donchian_low and close < ema200:
                    signal = "TREND_BREAKOUT"
        else:
            # MEAN_REVERSION SHORT : Rejet sur Bollinger supérieure
            if not (pd.isna(bbu) or pd.isna(ema200)):
                if close_prev < bbu_prev and close >= bbu and close < ema200:
                    signal = "MEAN_REVERSION"
                elif not pd.isna(rsi) and rsi > (100 - params.rsi_oversold) and close < ema200:
                    signal = "MEAN_REVERSION"

    # ── 5. Aucun signal → on passe ────────────────────────────────
    if signal is None:
        return None

    # ── V17 : Calcul ATR(14) + paramètres de trade ────────────────
    atr_value    = calculate_atr(df, period=14)
    trade_params = calculate_trade_parameters(close, atr_value, direction=direction)

    # ── V18 : Filtre R/R minimum 2.0 ──────────────────────────────
    _MIN_RR = 2.0
    _rr = trade_params["rr_ratio"]
    if not np.isnan(_rr) and _rr < _MIN_RR:
        logger.info(
            f"[{ticker_symbol}] Signal filtré V18 : "
            f"R/R={_rr:.2f} < {_MIN_RR} requis — ignoré"
        )
        return None

    # ── V18 : Filtre Earnings imminents (< 5 jours) ───────────────
    if check_upcoming_earnings(ticker_symbol):
        return None

    # ── 6. Construction du résultat ───────────────────────────────
    change_pct = ((close - close_prev) / close_prev) * 100 if close_prev > 0 else 0.0

    try:
        info = ticker_obj.info
        name = info.get("shortName", ticker_symbol)
    except Exception:
        name = ticker_symbol

    adx_str = f"ADX={avg_adx:.1f}" if not pd.isna(avg_adx) else "ADX=N/A"
    result = ScanResult(
        ticker        = ticker_symbol,
        name          = name,
        price         = round(close, 4),
        volume        = int(latest["Volume"]),
        avg_volume    = round(float(latest.get("AvgVol20", 0))),
        volume_ratio  = round(float(vol_r), 2),
        rsi           = round(rsi if not pd.isna(rsi) else 0.0, 1),
        signal        = signal,
        change_pct    = round(change_pct, 2),
        direction     = direction,
        # V17 — Risk Management
        atr           = round(atr_value, 4) if not np.isnan(atr_value) else float("nan"),
        stop_loss     = trade_params["stop_loss"],
        take_profit   = trade_params["take_profit"],
        rr_ratio      = trade_params["rr_ratio"],
        position_size = trade_params["position_size"],
        risk_amount   = trade_params["risk_amount"],
        gain_amount   = trade_params["gain_amount"],
    )

    logger.info(
        f"{'🟢' if direction == 'LONG' else '🔴'} SIGNAL [{signal}] {direction} "
        f"{result.ticker} | Prix: {result.price} | {adx_str} | Vol: {result.volume_ratio}x"
    )
    return result


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _get_regime_safe() -> str:
    """
    Récupère le régime macro avec fallback si MacroEngine est indisponible.
    Ne lève jamais d'exception — retourne BEAR_MARKET en fail-safe.
    """
    try:
        from modules.macro_engine import get_market_regime
        return get_market_regime()
    except ImportError:
        logger.warning("[Scanner] MacroEngine introuvable — BULL_MARKET par défaut")
        return "BULL_MARKET"
    except Exception as e:
        logger.error(f"[Scanner] MacroEngine erreur ({e}) — BEAR_MARKET par défaut")
        return "BEAR_MARKET"


def _print_crash_alert() -> None:
    """Affiche une alerte visuelle CRASH_PANIC dans la console."""
    border = "🚨" * 35
    print(f"\n{border}")
    print("🚨  ALERTE CRITIQUE — CRASH / PANIC DÉTECTÉ                       🚨")
    print("🚨                                                                  🚨")
    print("🚨  VIX ≥ 35 : Panique extrême sur les marchés financiers.         🚨")
    print("🚨  Le Moteur Macro a BLOQUÉ tous les trades.                       🚨")
    print("🚨  AUCUN scan ni signal ne sera généré.                            🚨")
    print("🚨                                                                  🚨")
    print("🚨  ACTIONS RECOMMANDÉES :                                          🚨")
    print("🚨    1. Fermer toutes les positions ouvertes.                      🚨")
    print("🚨    2. Passer en cash ou actifs refuges (or, obligations).        🚨")
    print("🚨    3. Attendre que VIX < 35 pour reprendre le trading.           🚨")
    print(f"{border}\n")
