"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  PROVIDER — yfinance (fallback)                                              ║
║                                                                              ║
║  Implémente à la fois FundamentalProviderBase et MarketDataProviderBase.    ║
║  Sert de fallback si FMP_API_KEY / MARKET_DATA_API_KEY sont absents — et   ║
║  de référence comportementale pour les autres providers.                    ║
║                                                                              ║
║  Conserve toute la robustesse existante :                                    ║
║    • Circuit-breaker yf_breaker (détection 429 / IP ban, fail-fast global).  ║
║    • Retry exponentiel borné (max 4 s).                                      ║
║    • Batch history() MultiIndex-aware.                                       ║
║                                                                              ║
║  Traduit les erreurs yfinance → ProviderQuotaExceeded / ProviderUnavailable  ║
║  pour respecter le contrat base.py.                                          ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any

import pandas as pd
import yfinance as yf

from modules.log import logger
from modules.yf_circuit_breaker import RateLimitTripped, yf_breaker

from .base import (
    FinancialRatios,
    FundamentalProviderBase,
    LatestPrice,
    MarketDataProviderBase,
    ProviderQuotaExceeded,
)

# yfinance retourne debtToEquity multiplié par 100 — on normalise en ratio brut
# pour respecter le contrat FinancialRatios.
_YF_DEBT_SCALE = 0.01

_MOMENTUM_CHUNK = 150

# Bornes de plausibilité yfinance. Si la valeur brute dépasse, on tente une
# reconstruction depuis d'autres champs avant de rendre None. Évite que le
# sanitize aval rejette des tickers dont yfinance renvoie juste *un* champ buggué.
_YF_DIV_YIELD_MAX = 0.25   # 25 %
_YF_MCAP_TOL = 0.05        # 5 % divergence mcap vs price×shares


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> int | None:
    f = _safe_float(v)
    return int(f) if f is not None else None


def _prev_year_col(df: Any) -> Any | None:
    """Retourne la colonne 'année précédente' d'un DataFrame annuel yfinance.

    yfinance renvoie `tk.financials` / `.balance_sheet` / `.cashflow` sous
    forme de DataFrame où les colonnes sont des pd.Timestamp d'arrêtés annuels
    (les plus récentes d'abord). Année la plus récente = [:, 0], Y-1 = [:, 1].

    Retourne None si le DF est vide ou < 2 colonnes.
    """
    if df is None:
        return None
    try:
        if not hasattr(df, "columns") or df.empty:
            return None
        if df.shape[1] < 2:
            return None
        return df.iloc[:, 1]  # Y-1
    except Exception:
        return None


def _prev_year_ratios(tk: "yf.Ticker") -> dict[str, float | None]:
    """Extrait les ratios Piotroski Y-1 depuis les annuels yfinance.

    Les 5 champs nécessaires pour F3/F5/F6/F8/F9 :
      - return_on_assets_prev_year : Net Income Y-1 / Total Assets Y-1
      - debt_to_equity_prev_year   : Total Debt Y-1 / Total Stockholder Equity Y-1
      - current_ratio_prev_year    : Current Assets Y-1 / Current Liabilities Y-1
      - shares_outstanding_prev_year : Ordinary Shares Number Y-1
      - gross_margin_prev_year     : Gross Profit Y-1 / Total Revenue Y-1

    Fail-open : si un appel yfinance pète, on renvoie {} (scoring retombe
    sur le cas 4 critères absolus — pas de régression).
    """
    out: dict[str, float | None] = {
        "return_on_assets_prev_year":   None,
        "debt_to_equity_prev_year":     None,
        "current_ratio_prev_year":      None,
        "shares_outstanding_prev_year": None,
        "gross_margin_prev_year":       None,
    }
    try:
        bs = tk.balance_sheet            # DataFrame annuel (cols = years desc)
        fin = tk.financials              # P&L annuel
    except Exception as e:
        logger.debug(f"[YF prev_year] fetch failed: {e}")
        return out

    bs_prev = _prev_year_col(bs)
    fin_prev = _prev_year_col(fin)
    if bs_prev is None and fin_prev is None:
        return out

    def _get(series: Any | None, *keys: str) -> float | None:
        """Premier champ présent parmi plusieurs alias yfinance."""
        if series is None:
            return None
        for k in keys:
            if k in series.index:
                v = _safe_float(series.get(k))
                if v is not None:
                    return v
        return None

    # Net Income & Total Assets → ROA
    ni_prev = _get(fin_prev, "Net Income", "Net Income Common Stockholders")
    ta_prev = _get(bs_prev, "Total Assets")
    if ni_prev is not None and ta_prev is not None and ta_prev != 0:
        out["return_on_assets_prev_year"] = ni_prev / ta_prev

    # Total Debt & Equity → D/E
    td_prev = _get(
        bs_prev, "Total Debt", "Long Term Debt", "Total Non Current Liabilities Net Minority Interest",
    )
    eq_prev = _get(
        bs_prev,
        "Common Stock Equity", "Stockholders Equity",
        "Total Equity Gross Minority Interest",
    )
    if td_prev is not None and eq_prev is not None and eq_prev != 0:
        # yfinance balance_sheet est en USD absolu (pas ×100) — contrairement à .info
        out["debt_to_equity_prev_year"] = td_prev / eq_prev

    # Current Assets / Current Liabilities → Current Ratio
    ca_prev = _get(bs_prev, "Current Assets", "Total Current Assets")
    cl_prev = _get(bs_prev, "Current Liabilities", "Total Current Liabilities")
    if ca_prev is not None and cl_prev is not None and cl_prev != 0:
        out["current_ratio_prev_year"] = ca_prev / cl_prev

    # Shares Outstanding → F8 (pas de dilution Y/Y)
    so_prev = _get(bs_prev, "Ordinary Shares Number", "Share Issued")
    if so_prev is not None:
        out["shares_outstanding_prev_year"] = so_prev

    # Gross Profit & Revenue → Gross Margin
    gp_prev = _get(fin_prev, "Gross Profit")
    rev_prev = _get(fin_prev, "Total Revenue", "Operating Revenue")
    if gp_prev is not None and rev_prev is not None and rev_prev != 0:
        out["gross_margin_prev_year"] = gp_prev / rev_prev

    return out


def _recover_dividend_yield(info: dict[str, Any], price: float | None) -> float | None:
    """Recovery robuste du dividend yield — yfinance bogue régulièrement sur ce champ.

    Stratégie (du plus fiable au moins fiable) :
      1. `dividendYield` brut si dans [0, 25 %]
      2. `dividendRate / currentPrice` si `dividendYield` hors bornes
      3. `trailingAnnualDividendYield` en dernier recours
      4. None

    Observé en prod sur 268 tickers : yfinance retourne `info["dividendYield"] = 1.95`
    (le dividend annuel en $) au lieu du ratio 0.0195 attendu.
    """
    raw = _safe_float(info.get("dividendYield"))
    if raw is not None and 0.0 <= raw <= _YF_DIV_YIELD_MAX:
        return raw

    # Recovery via dividendRate (dividend annuel en $) / price
    rate = _safe_float(
        info.get("dividendRate") or info.get("trailingAnnualDividendRate")
    )
    if rate is not None and rate >= 0 and price is not None and price > 0:
        computed = rate / price
        if 0.0 <= computed <= _YF_DIV_YIELD_MAX:
            return computed

    # Fallback : trailingAnnualDividendYield
    trailing = _safe_float(info.get("trailingAnnualDividendYield"))
    if trailing is not None and 0.0 <= trailing <= _YF_DIV_YIELD_MAX:
        return trailing

    return None


def _recover_market_cap(
    info: dict[str, Any], price: float | None, shares: float | None,
) -> float | None:
    """Market cap avec cross-check. Si mcap reporté diverge de price×shares > 5 %,
    on préfère price×shares (plus atomique : pas de latence de mise à jour multi-jour).

    Edge case : Class A/B shares non comptées dans sharesOutstanding — rare,
    et le sanitize aval va juste flagger mcap_mismatch sans modifier.
    """
    reported = _safe_float(info.get("marketCap"))
    if reported is None or reported <= 0:
        # Fallback pur : compute depuis price×shares.
        if price is not None and shares is not None and price > 0 and shares > 0:
            return price * shares
        return None

    if price is not None and shares is not None and price > 0 and shares > 0:
        computed = price * shares
        rel = abs(reported - computed) / max(reported, computed)
        if rel > _YF_MCAP_TOL:
            # Divergence importante → on trust price×shares.
            return computed
    return reported


def _extract_close_series(df: Any, ticker: str) -> pd.Series | None:
    """MultiIndex-aware extraction — même logique que sector_metrics._extract_close_series."""
    try:
        if hasattr(df.columns, "get_level_values") and ticker in df.columns.get_level_values(0):
            return df[ticker]["Close"].dropna()
        if "Close" in df.columns:
            return df["Close"].dropna()
    except Exception:
        return None
    return None


def _days_to_period(days: int) -> str:
    """Convertit un horizon en jours vers le paramètre `period` yfinance.
    Choisit la plus petite période couvrant `days` pour limiter la bande passante."""
    if days <= 7:
        return "7d"
    if days <= 30:
        return "1mo"
    if days <= 90:
        return "3mo"
    if days <= 180:
        return "6mo"
    if days <= 270:
        return "9mo"
    if days <= 365:
        return "1y"
    if days <= 730:
        return "2y"
    if days <= 1825:
        return "5y"
    return "10y"


# ─────────────────────────────────────────────────────────────────────────────
# PROVIDER
# ─────────────────────────────────────────────────────────────────────────────

class YFinanceProvider(FundamentalProviderBase, MarketDataProviderBase):
    """Double rôle : fallback quand ni FMP ni Polygon ne sont configurés."""

    name = "yfinance"

    def __init__(self, max_retries: int = 2) -> None:
        self._max_retries = max_retries

    # ── FundamentalProviderBase ─────────────────────────────────────────

    def get_financial_ratios(self, ticker: str) -> FinancialRatios:
        # Fast-fail si le breaker est déjà ouvert — évite d'émettre la requête.
        try:
            yf_breaker.ensure_closed()
        except RateLimitTripped as e:
            raise ProviderQuotaExceeded(str(e)[:200]) from e

        info: dict[str, Any] = {}
        tk: yf.Ticker | None = None
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                tk = yf.Ticker(ticker)
                raw = tk.info or {}
                if isinstance(raw, dict) and raw:
                    info = raw
                    break
            except Exception as e:
                if yf_breaker.record(e):
                    raise ProviderQuotaExceeded(str(e)[:200]) from e
                last_exc = e
            if attempt < self._max_retries:
                time.sleep(min(2 ** attempt * 0.5, 4.0))

        fetched_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        if not info:
            return FinancialRatios(
                ticker=ticker,
                source_provider=self.name,
                fetched_at=fetched_at,
                error=(str(last_exc)[:200] if last_exc else "yfinance info empty"),
            )

        d2e_raw = _safe_float(info.get("debtToEquity"))
        debt_to_equity = d2e_raw * _YF_DEBT_SCALE if d2e_raw is not None else None

        # Price + shares résolus d'abord : les recovery heuristics en dépendent.
        current_price = _safe_float(
            info.get("currentPrice")
            or info.get("regularMarketPrice")
            or info.get("previousClose")
        )
        shares_outstanding = _safe_float(info.get("sharesOutstanding"))

        # Piotroski Y-1 : un seul appel tk.balance_sheet + tk.financials qui
        # hit yfinance 1× de plus. Fail-open → pas de régression si les
        # annuels sont absents (ex: nouvelles IPOs < 1 an).
        prev = _prev_year_ratios(tk) if tk is not None else {}

        return FinancialRatios(
            ticker=ticker,
            name=info.get("longName") or info.get("shortName"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            country=info.get("country"),
            currency=info.get("currency"),
            exchange=info.get("exchange") or info.get("fullExchangeName"),
            market_cap=_recover_market_cap(info, current_price, shares_outstanding),
            current_price=current_price,
            forward_pe=_safe_float(info.get("forwardPE")),
            trailing_pe=_safe_float(info.get("trailingPE")),
            peg_ratio=_safe_float(info.get("pegRatio") or info.get("trailingPegRatio")),
            price_to_book=_safe_float(info.get("priceToBook")),
            dividend_yield=_recover_dividend_yield(info, current_price),
            beta=_safe_float(info.get("beta")),
            price_target_mean=_safe_float(info.get("targetMeanPrice")),
            price_target_high=_safe_float(info.get("targetHighPrice")),
            price_target_low=_safe_float(info.get("targetLowPrice")),
            recommendation_mean=_safe_float(info.get("recommendationMean")),
            recommendation_key=info.get("recommendationKey"),
            num_analysts=_safe_int(info.get("numberOfAnalystOpinions")),
            return_on_equity=_safe_float(info.get("returnOnEquity")),
            operating_margin=_safe_float(info.get("operatingMargins")),
            profit_margin=_safe_float(info.get("profitMargins")),
            # Lot 8 — Piotroski F-Score components.
            # netIncomeToCommon = TTM net income absolu (USD).
            # sharesOutstanding réservé pour F8 (issuance Y/Y) avec Lot 6 history.
            return_on_assets=_safe_float(info.get("returnOnAssets")),
            gross_margin=_safe_float(info.get("grossMargins")),
            net_income=_safe_float(info.get("netIncomeToCommon")),
            shares_outstanding=shares_outstanding,
            ev_to_ebitda=_safe_float(info.get("enterpriseToEbitda")),
            ev_to_revenue=_safe_float(info.get("enterpriseToRevenue")),
            free_cash_flow=_safe_float(info.get("freeCashflow")),
            operating_cash_flow=_safe_float(info.get("operatingCashflow")),
            debt_to_equity=debt_to_equity,
            current_ratio=_safe_float(info.get("currentRatio")),
            quick_ratio=_safe_float(info.get("quickRatio")),
            # Growth fields (Lot 12 — pilier Growth)
            revenue_growth=_safe_float(info.get("revenueGrowth")),
            earnings_growth=_safe_float(info.get("earningsGrowth")),
            earnings_quarterly_growth=_safe_float(info.get("earningsQuarterlyGrowth")),
            # Piotroski Y-1 (Lot 14 — active F3/F5/F6/F8/F9 sans attendre 1 an
            # d'universe_history). None si tk.balance_sheet / .financials vide.
            return_on_assets_prev_year=prev.get("return_on_assets_prev_year"),
            debt_to_equity_prev_year=prev.get("debt_to_equity_prev_year"),
            current_ratio_prev_year=prev.get("current_ratio_prev_year"),
            shares_outstanding_prev_year=prev.get("shares_outstanding_prev_year"),
            gross_margin_prev_year=prev.get("gross_margin_prev_year"),
            source_provider=self.name,
            fetched_at=fetched_at,
        )

    # ── MarketDataProviderBase ──────────────────────────────────────────

    def get_daily_history(self, ticker: str, days: int) -> pd.Series | None:
        result = self.get_daily_history_batch([ticker], days)
        return result.get(ticker)

    def get_daily_history_batch(
        self, tickers: list[str], days: int,
    ) -> dict[str, pd.Series | None]:
        out: dict[str, pd.Series | None] = {t: None for t in tickers}
        if not tickers:
            return out

        period = _days_to_period(days)
        chunks = [tickers[i:i + _MOMENTUM_CHUNK]
                  for i in range(0, len(tickers), _MOMENTUM_CHUNK)]

        for idx, chunk in enumerate(chunks, start=1):
            try:
                yf_breaker.ensure_closed()
                df = yf.download(
                    tickers=chunk,
                    period=period,
                    interval="1d",
                    progress=False,
                    auto_adjust=True,
                    threads=True,
                    group_by="ticker",
                )
            except RateLimitTripped as e:
                # Breaker OPEN — on propage pour que le moteur arrête la pipeline.
                raise ProviderQuotaExceeded(str(e)[:200]) from e
            except Exception as e:
                if yf_breaker.record(e):
                    raise ProviderQuotaExceeded(str(e)[:200]) from e
                logger.warning(
                    f"[YFinanceProvider] history chunk {idx}/{len(chunks)} failed: {e}"
                )
                continue

            if df is None or df.empty:
                logger.warning(
                    f"[YFinanceProvider] history chunk {idx}/{len(chunks)} empty"
                )
                continue

            for t in chunk:
                try:
                    out[t] = _extract_close_series(df, t)
                except Exception as e:
                    logger.debug(f"[YFinanceProvider] {t} parse fail: {e}")

        return out

    def get_latest_prices(
        self, tickers: list[str],
    ) -> dict[str, LatestPrice]:
        """
        Snapshot live via yf.download(period='2d') chunké + auto_adjust.
        Retourne le dernier close disponible (≤ 24h de décalage en weekend,
        ~15 min en intraday). Quota-friendly : 1 call HTTP par chunk de 150.
        """
        now_epoch = time.time()
        out: dict[str, LatestPrice] = {
            t: LatestPrice(ticker=t, price=None, fetched_at_epoch=now_epoch,
                           source=self.name, as_of=None)
            for t in tickers
        }
        if not tickers:
            return out

        chunks = [tickers[i:i + _MOMENTUM_CHUNK]
                  for i in range(0, len(tickers), _MOMENTUM_CHUNK)]

        for idx, chunk in enumerate(chunks, start=1):
            try:
                yf_breaker.ensure_closed()
                df = yf.download(
                    tickers=chunk,
                    period="2d",
                    interval="1d",
                    progress=False,
                    auto_adjust=True,
                    threads=True,
                    group_by="ticker",
                )
            except RateLimitTripped as e:
                raise ProviderQuotaExceeded(str(e)[:200]) from e
            except Exception as e:
                if yf_breaker.record(e):
                    raise ProviderQuotaExceeded(str(e)[:200]) from e
                logger.warning(
                    f"[YFinanceProvider] latest_prices chunk {idx}/{len(chunks)} failed: {e}"
                )
                continue

            if df is None or df.empty:
                continue

            for t in chunk:
                try:
                    series = _extract_close_series(df, t)
                    if series is None or len(series) == 0:
                        continue
                    price = float(series.iloc[-1])
                    if not math.isfinite(price) or price <= 0:
                        continue
                    as_of_iso = str(series.index[-1])
                    out[t] = LatestPrice(
                        ticker=t, price=price, fetched_at_epoch=now_epoch,
                        source=self.name, as_of=as_of_iso,
                    )
                except Exception as e:
                    logger.debug(f"[YFinanceProvider] latest_prices {t} parse fail: {e}")

        return out
