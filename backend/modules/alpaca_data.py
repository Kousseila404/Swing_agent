"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE — ALPACA DATA API                                        ║
║  Fournit des prix temps réel et des barres historiques via       ║
║  l'API Alpaca Markets. Fallback automatique yfinance si Alpaca   ║
║  n'est pas configuré ou si une erreur survient.                  ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

import config
from modules.log import logger

# ─────────────────────────────────────────────────────────────────
# SINGLETON CLIENT ALPACA (initialisé lazily)
# ─────────────────────────────────────────────────────────────────

_alpaca_client = None   # StockHistoricalDataClient | None


def _get_alpaca_client():
    """
    Retourne le client Alpaca (singleton lazy).
    Retourne None si les clés sont absentes ou si alpaca-py n'est pas installé.
    """
    global _alpaca_client

    if _alpaca_client is not None:
        return _alpaca_client

    api_key    = getattr(config, "ALPACA_API_KEY",    None)
    secret_key = getattr(config, "ALPACA_SECRET_KEY", None)

    if not api_key or not secret_key:
        logger.debug("[AlpacaData] Clés API absentes — mode yfinance uniquement")
        return None

    try:
        from alpaca.data.historical import StockHistoricalDataClient
        _alpaca_client = StockHistoricalDataClient(
            api_key=api_key, secret_key=secret_key
        )
        logger.debug("[AlpacaData] Client Alpaca initialisé")
        return _alpaca_client
    except ImportError:
        logger.warning("[AlpacaData] alpaca-py non installé — fallback yfinance")
        return None
    except Exception as exc:
        logger.warning(f"[AlpacaData] Impossible d'initialiser le client : {exc}")
        return None


# ─────────────────────────────────────────────────────────────────
# get_latest_price — prix temps réel (mid bid/ask)
# ─────────────────────────────────────────────────────────────────

def get_latest_price(ticker: str) -> float | None:
    """
    Retourne le prix temps réel via Alpaca (mid entre bid et ask).
    Fallback automatique yfinance si Alpaca non configuré ou erreur.

    Args:
        ticker: Symbole boursier (ex: "NVDA").

    Returns:
        Prix float ou None si toutes les tentatives échouent.
    """
    client = _get_alpaca_client()

    if client is not None:
        try:
            from alpaca.data.requests import StockLatestQuoteRequest
            req    = StockLatestQuoteRequest(symbol_or_symbols=[ticker])
            quotes = client.get_stock_latest_quote(req)
            quote  = quotes.get(ticker)
            if quote is not None:
                ask = float(quote.ask_price or 0)
                bid = float(quote.bid_price or 0)
                if ask > 0 and bid > 0:
                    mid = (ask + bid) / 2.0
                    logger.debug(f"[AlpacaData] {ticker} mid={mid:.4f} (bid={bid}, ask={ask})")
                    return mid
                elif ask > 0:
                    return ask
                elif bid > 0:
                    return bid
        except Exception as exc:
            logger.warning(f"[AlpacaData] get_latest_price({ticker}) erreur Alpaca : {exc} — fallback yfinance")

    # ── Fallback yfinance ──────────────────────────────────────────
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="1d", interval="1m")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
        hist = yf.Ticker(ticker).history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as exc:
        logger.warning(f"[AlpacaData] get_latest_price({ticker}) fallback yfinance échoué : {exc}")

    return None


# ─────────────────────────────────────────────────────────────────
# get_intraday_bars — barres horaires historiques
# ─────────────────────────────────────────────────────────────────

def get_intraday_bars(
    ticker: str,
    timeframe: str = "1Hour",
    days: int = 10,
) -> pd.DataFrame | None:
    """
    Retourne des barres intraday via Alpaca StockBarsRequest.

    Args:
        ticker:    Symbole boursier (ex: "NVDA").
        timeframe: Résolution temporelle (défaut "1Hour").
        days:      Nombre de jours d'historique (défaut 10).

    Returns:
        DataFrame avec colonnes Open/High/Low/Close/Volume, ou None si
        ALPACA_API_KEY absent, ou en cas d'erreur.
    """
    client = _get_alpaca_client()

    if client is None:
        return None

    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        # Résolution du timeframe
        _TF_MAP = {
            "1Hour":  TimeFrame(1, TimeFrameUnit.Hour),
            "1Min":   TimeFrame(1, TimeFrameUnit.Minute),
            "5Min":   TimeFrame(5, TimeFrameUnit.Minute),
            "15Min":  TimeFrame(15, TimeFrameUnit.Minute),
            "1Day":   TimeFrame(1, TimeFrameUnit.Day),
        }
        tf = _TF_MAP.get(timeframe, TimeFrame(1, TimeFrameUnit.Hour))

        start = datetime.now() - timedelta(days=days)
        req   = StockBarsRequest(
            symbol_or_symbols=[ticker],
            timeframe=tf,
            start=start,
        )
        bars = client.get_stock_bars(req)

        # BarSet.df retourne un MultiIndex (symbol, timestamp) — on filtre sur le ticker
        df_all = bars.df
        if df_all is None or df_all.empty:
            logger.debug(f"[AlpacaData] Aucune barre Alpaca pour {ticker}")
            return None

        # Si MultiIndex (symbol, timestamp) → slice le ticker
        if isinstance(df_all.index, pd.MultiIndex):
            if ticker in df_all.index.get_level_values(0):
                df = df_all.loc[ticker].copy()
            else:
                # Essai insensible à la casse
                sym_upper = ticker.upper()
                symbols = df_all.index.get_level_values(0).unique()
                match = [s for s in symbols if str(s).upper() == sym_upper]
                if match:
                    df = df_all.loc[match[0]].copy()
                else:
                    logger.debug(f"[AlpacaData] {ticker} absent du BarSet")
                    return None
        else:
            df = df_all.copy()
        if df is None or df.empty:
            return None

        # Normalise les noms de colonnes (Alpaca retourne des minuscules)
        df = df.rename(columns={
            "open":   "Open",
            "high":   "High",
            "low":    "Low",
            "close":  "Close",
            "volume": "Volume",
        })

        # Supprime le timezone de l'index
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        logger.debug(f"[AlpacaData] {ticker} {len(df)} barres {timeframe} chargées via Alpaca")
        return df

    except Exception as exc:
        logger.warning(f"[AlpacaData] get_intraday_bars({ticker}) erreur : {exc}")
        return None


# ─────────────────────────────────────────────────────────────────
# get_latest_prices_batch — prix batch multi-tickers
# ─────────────────────────────────────────────────────────────────

def get_latest_prices_batch(tickers: list[str]) -> dict[str, float]:
    """
    Retourne les prix les plus récents pour une liste de tickers.
    Utilise Alpaca en batch si disponible, sinon yfinance batch.

    Args:
        tickers: Liste de symboles boursiers.

    Returns:
        Dict {ticker: price} — les tickers sans prix sont absents du dict.
    """
    if not tickers:
        return {}

    client = _get_alpaca_client()

    if client is not None:
        try:
            from alpaca.data.requests import StockLatestQuoteRequest

            # Filtre les crypto (Alpaca Stocks API ne supporte pas BTC-USD etc.)
            stock_tickers = [t for t in tickers if not t.endswith("-USD")]
            result: dict[str, float] = {}

            if stock_tickers:
                req    = StockLatestQuoteRequest(symbol_or_symbols=stock_tickers)
                quotes = client.get_stock_latest_quote(req)
                for t in stock_tickers:
                    q = quotes.get(t)
                    if q is not None:
                        ask = float(q.ask_price or 0)
                        bid = float(q.bid_price or 0)
                        if ask > 0 and bid > 0:
                            result[t] = (ask + bid) / 2.0
                        elif ask > 0:
                            result[t] = ask
                        elif bid > 0:
                            result[t] = bid

            # Crypto via yfinance individuellement
            crypto_tickers = [t for t in tickers if t.endswith("-USD")]
            for t in crypto_tickers:
                price = get_latest_price(t)
                if price is not None:
                    result[t] = price

            if result:
                logger.debug(f"[AlpacaData] Batch prix Alpaca : {len(result)}/{len(tickers)} tickers")
                # Complète les manquants via yfinance
                missing = [t for t in tickers if t not in result]
                if missing:
                    yf_prices = _yfinance_batch_prices(missing)
                    result.update(yf_prices)
                return result

        except Exception as exc:
            logger.warning(f"[AlpacaData] get_latest_prices_batch erreur Alpaca : {exc} — fallback yfinance")

    # ── Fallback yfinance batch ────────────────────────────────────
    return _yfinance_batch_prices(tickers)


def _yfinance_batch_prices(tickers: list[str]) -> dict[str, float]:
    """Télécharge les prix batch via yfinance."""
    result: dict[str, float] = {}
    try:
        import yfinance as yf
        data = yf.download(
            tickers,
            period="1d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        if data.empty:
            return result
        close = data["Close"]
        if isinstance(close, pd.Series):
            # Un seul ticker
            if not close.empty and len(tickers) == 1:
                result[tickers[0]] = float(close.iloc[-1])
        else:
            for t in tickers:
                try:
                    val = float(close[t].dropna().iloc[-1])
                    result[t] = val
                except Exception:
                    pass
    except Exception as exc:
        logger.warning(f"[AlpacaData] _yfinance_batch_prices erreur : {exc}")
    return result


def get_latest_quote(ticker: str) -> dict | None:
    """{bid, ask, mid, spread_pct} — cotation NBBO Alpaca (IEX en plan gratuit).
    None si client absent ou quote incomplète. Utilisé par le gate de spread."""
    client = _get_alpaca_client()
    if client is None:
        return None
    try:
        from alpaca.data.requests import StockLatestQuoteRequest
        quotes = client.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=[ticker]))
        q = quotes.get(ticker)
        if q is None:
            return None
        bid = float(q.bid_price or 0)
        ask = float(q.ask_price or 0)
        if bid <= 0 or ask <= 0:
            return None
        mid = (bid + ask) / 2.0
        return {"bid": bid, "ask": ask, "mid": mid, "spread_pct": round((ask - bid) / mid * 100.0, 3)}
    except Exception as exc:
        logger.debug(f"[AlpacaData] get_latest_quote({ticker}) : {exc}")
        return None
