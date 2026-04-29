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


def _col_date(df: Any | None, idx: int) -> str | None:
    """Retourne la date (ISO) de la colonne `idx` d'un DataFrame yfinance
    annuel. Les colonnes de `tk.balance_sheet` / `tk.financials` sont des
    Timestamps pandas correspondant à la date de fin de période fiscale.

    None si le DF est vide / hors bornes / colonne non datée.

    Audit S3.x rigoureux (2026-04-27) — permet de remplacer le lag fixe
    de 90 j par un check par-ticker `period_end + 90 j ≤ as_of`. Compagnies
    à FY décalée (Sept 30, Mar 31) ne sont plus jugées sur le calendrier
    civil mais sur leur vrai calendrier fiscal.
    """
    if df is None:
        return None
    try:
        if not hasattr(df, "columns") or df.empty:
            return None
        if df.shape[1] <= idx:
            return None
        col = df.columns[idx]
        # pandas Timestamp / datetime / np.datetime64 → strftime
        if hasattr(col, "strftime"):
            return col.strftime("%Y-%m-%d")
        return str(col)[:10]
    except Exception:
        return None


def _prev_year_ratios(tk: "yf.Ticker") -> dict[str, float | None]:
    """Extrait les ratios Piotroski Y-1 + dates de période fiscale depuis
    les annuels yfinance.

    Les 5 ratios nécessaires pour F3/F5/F6/F8/F9 :
      - return_on_assets_prev_year : Net Income Y-1 / Total Assets Y-1
      - debt_to_equity_prev_year   : Total Debt Y-1 / Total Stockholder Equity Y-1
      - current_ratio_prev_year    : Current Assets Y-1 / Current Liabilities Y-1
      - shares_outstanding_prev_year : Ordinary Shares Number Y-1
      - gross_margin_prev_year     : Gross Profit Y-1 / Total Revenue Y-1

    Plus 2 dates ISO :
      - fundamentals_period_end    : fin de période fiscale Y0 (la plus récente)
      - fundamentals_period_end_y1 : fin de période fiscale Y-1

    Permet à `_piotroski_score_pillar(as_of, publication_lag_days)` de
    refuser un Y-1 dont la période fiscale n'a pas encore été publiée à
    `as_of` (vrai check par-ticker, plus rigoureux que le lag 90j fixe).

    Fail-open : si un appel yfinance pète, on renvoie {} (scoring retombe
    sur le cas 4 critères absolus — pas de régression).
    """
    out: dict[str, float | str | None] = {
        "return_on_assets_prev_year":   None,
        "debt_to_equity_prev_year":     None,
        "current_ratio_prev_year":      None,
        "shares_outstanding_prev_year": None,
        "gross_margin_prev_year":       None,
        "fundamentals_period_end":      None,
        "fundamentals_period_end_y1":   None,
    }
    try:
        bs = tk.balance_sheet            # DataFrame annuel (cols = years desc)
        fin = tk.financials              # P&L annuel
    except Exception as e:
        logger.debug(f"[YF prev_year] fetch failed: {e}")
        return out

    bs_prev = _prev_year_col(bs)
    fin_prev = _prev_year_col(fin)
    # On extrait les dates même si une seule des deux sources marche : la
    # bs_prev est plus fiable que fin (P&L parfois absent en free yfinance).
    out["fundamentals_period_end"]    = _col_date(bs, 0) or _col_date(fin, 0)
    out["fundamentals_period_end_y1"] = _col_date(bs, 1) or _col_date(fin, 1)
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


def _scrape_revisions_and_earnings(tk: "yf.Ticker") -> dict[str, Any]:
    """Scrape les révisions analyste, l'earnings surprise history et le prochain
    earnings depuis yfinance. Tout fail-open — si une source pète, on retourne
    le dict avec None pour les fields concernés.

    Champs produits :
      • upgrades_30d / downgrades_30d / upgrades_90d / downgrades_90d
      • revisions_net_score ∈ [-1, 1]
      • earnings_surprise_pct_last / earnings_surprise_avg_4q / earnings_beat_rate_8q
      • next_earnings_date (ISO)
    """
    out: dict[str, Any] = {
        "upgrades_30d":                 None,
        "downgrades_30d":               None,
        "upgrades_90d":                 None,
        "downgrades_90d":               None,
        "revisions_net_score":          None,
        "earnings_surprise_pct_last":   None,
        "earnings_surprise_avg_4q":     None,
        "earnings_beat_rate_8q":        None,
        "next_earnings_date":           None,
    }

    # ── Recommendations history (upgrades / downgrades) ─────────────────
    # `tk.recommendations` retourne un DataFrame avec colonnes
    # [period, strongBuy, buy, hold, sell, strongSell] ou (legacy) un index
    # daté avec [Action, From Grade, To Grade, Firm]. On gère les deux schémas.
    try:
        rec = tk.recommendations
        if rec is not None and not rec.empty:
            up_30 = down_30 = up_90 = down_90 = 0
            now_ts = pd.Timestamp.utcnow().tz_localize(None)

            cols = set(rec.columns) if hasattr(rec, "columns") else set()
            if {"strongBuy", "buy", "sell", "strongSell"} & cols:
                # Schéma actuel yfinance : aggrégat par période ('0m','-1m'…).
                # On considère 0m+(-1m) pour 30d. On compare au snapshot deux
                # périodes plus tôt pour estimer net upgrades/downgrades :
                # une montée de strongBuy+buy = up, une montée sell+strongSell = down.
                df = rec.copy()
                if "period" in df.columns:
                    df = df.set_index("period")
                # Index attendu : '0m', '-1m', '-2m', '-3m'
                def _bull(r) -> int:
                    return int((r.get("strongBuy") or 0) + (r.get("buy") or 0))
                def _bear(r) -> int:
                    return int((r.get("sell") or 0) + (r.get("strongSell") or 0))
                if "0m" in df.index and "-1m" in df.index:
                    bull_now  = _bull(df.loc["0m"])
                    bear_now  = _bear(df.loc["0m"])
                    bull_1m   = _bull(df.loc["-1m"])
                    bear_1m   = _bear(df.loc["-1m"])
                    up_30   = max(0, bull_now - bull_1m)
                    down_30 = max(0, bear_now - bear_1m)
                if "0m" in df.index and "-3m" in df.index:
                    bull_now  = _bull(df.loc["0m"])
                    bear_now  = _bear(df.loc["0m"])
                    bull_3m   = _bull(df.loc["-3m"])
                    bear_3m   = _bear(df.loc["-3m"])
                    up_90   = max(0, bull_now - bull_3m)
                    down_90 = max(0, bear_now - bear_3m)
            else:
                # Schéma legacy daté : on classe les actions sur 30/90j.
                if not isinstance(rec.index, pd.DatetimeIndex):
                    try:
                        rec.index = pd.to_datetime(rec.index)
                    except Exception:
                        pass
                if isinstance(rec.index, pd.DatetimeIndex):
                    rec = rec.copy()
                    if rec.index.tz is not None:
                        rec.index = rec.index.tz_localize(None)
                    cutoff_30 = now_ts - pd.Timedelta(days=30)
                    cutoff_90 = now_ts - pd.Timedelta(days=90)
                    actions = rec.get("Action") if "Action" in rec.columns else None
                    if actions is not None:
                        a30 = actions[rec.index >= cutoff_30]
                        a90 = actions[rec.index >= cutoff_90]
                        # yfinance 'Action' ∈ {'main', 'reit', 'up', 'down', 'init'}
                        up_30   = int((a30 == "up").sum())
                        down_30 = int((a30 == "down").sum())
                        up_90   = int((a90 == "up").sum())
                        down_90 = int((a90 == "down").sum())

            out["upgrades_30d"]   = up_30
            out["downgrades_30d"] = down_30
            out["upgrades_90d"]   = up_90
            out["downgrades_90d"] = down_90
            denom = up_90 + down_90
            if denom > 0:
                out["revisions_net_score"] = (up_90 - down_90) / denom
    except Exception as e:
        logger.debug(f"[YF revisions] scrape failed: {e}")

    # ── Earnings surprise history (PEAD signal) ─────────────────────────
    try:
        eh = None
        # tk.earnings_history retourne DF avec colonnes
        # ['epsEstimate', 'epsActual', 'epsDifference', 'surprisePercent']
        if hasattr(tk, "earnings_history"):
            eh = tk.earnings_history
        if eh is not None and not eh.empty and "surprisePercent" in eh.columns:
            sp = pd.to_numeric(eh["surprisePercent"], errors="coerce").dropna()
            if len(sp):
                out["earnings_surprise_pct_last"] = float(sp.iloc[-1])
                if len(sp) >= 1:
                    out["earnings_surprise_avg_4q"] = float(sp.tail(4).mean())
                # Beat = surprisePercent > 0 (surprise positive = beat).
                last_n = sp.tail(8)
                if len(last_n) >= 4:
                    out["earnings_beat_rate_8q"] = float((last_n > 0).mean())
    except Exception as e:
        logger.debug(f"[YF earnings_history] scrape failed: {e}")

    # ── Next earnings date ──────────────────────────────────────────────
    # `tk.calendar` peut être un dict {Earnings Date: [Timestamp]} ou un DF.
    try:
        cal = tk.calendar
        next_date: Any = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date") or cal.get("earningsDate")
            if isinstance(ed, list) and ed:
                next_date = ed[0]
            elif ed is not None:
                next_date = ed
        elif cal is not None and hasattr(cal, "iloc") and not cal.empty:
            row_idx = "Earnings Date"
            if row_idx in cal.index:
                next_date = cal.loc[row_idx].iloc[0]
        if next_date is not None:
            if hasattr(next_date, "strftime"):
                out["next_earnings_date"] = next_date.strftime("%Y-%m-%d")
            else:
                out["next_earnings_date"] = str(next_date)[:10]
    except Exception as e:
        logger.debug(f"[YF calendar] scrape failed: {e}")

    return out


def _scrape_dividend_safety(tk: "yf.Ticker", info: dict[str, Any]) -> dict[str, Any]:
    """Champs pour le Dividend Safety Score. yfinance.info expose payoutRatio
    et fiveYearAvgDividendYield directement ; dividendsPaid vient du cashflow
    annuel (négatif chez yfinance par convention)."""
    out: dict[str, Any] = {
        "payout_ratio":                 _safe_float(info.get("payoutRatio")),
        "dividends_paid":               None,
        "five_year_avg_dividend_yield": None,
    }
    raw_fy = _safe_float(info.get("fiveYearAvgDividendYield"))
    if raw_fy is not None:
        # yfinance bug : peut renvoyer en % (4.2) au lieu de ratio (0.042).
        if raw_fy > 1.0:
            raw_fy = raw_fy / 100.0
        if 0.0 <= raw_fy <= 0.25:
            out["five_year_avg_dividend_yield"] = raw_fy

    try:
        cf = tk.cashflow
        if cf is not None and not cf.empty and cf.shape[1] >= 1:
            for k in ("Cash Dividends Paid", "Dividends Paid"):
                if k in cf.index:
                    v = _safe_float(cf.loc[k].iloc[0])
                    if v is not None:
                        # yfinance retourne dividendsPaid négatif → on prend |v|.
                        out["dividends_paid"] = abs(v)
                        break
    except Exception as e:
        logger.debug(f"[YF dividends_paid] scrape failed: {e}")
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
        # Lot 16 — Revisions / Earnings Surprise / Dividend Safety. Fail-open.
        rev_data = _scrape_revisions_and_earnings(tk) if tk is not None else {}
        div_safety = _scrape_dividend_safety(tk, info) if tk is not None else {}

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
            # Audit S3.x — ADTV 3M : yfinance expose `averageVolume` (3M) et
            # `averageVolume10days` (10j). On préfère le 3M, plus robuste.
            avg_volume_3m=_safe_float(
                info.get("averageVolume") or info.get("averageVolume10days")
            ),
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
            fundamentals_period_end=prev.get("fundamentals_period_end"),
            fundamentals_period_end_y1=prev.get("fundamentals_period_end_y1"),
            # Lot 16 — Revisions / Earnings Surprise / Dividend Safety.
            upgrades_30d=rev_data.get("upgrades_30d"),
            downgrades_30d=rev_data.get("downgrades_30d"),
            upgrades_90d=rev_data.get("upgrades_90d"),
            downgrades_90d=rev_data.get("downgrades_90d"),
            revisions_net_score=rev_data.get("revisions_net_score"),
            earnings_surprise_pct_last=rev_data.get("earnings_surprise_pct_last"),
            earnings_surprise_avg_4q=rev_data.get("earnings_surprise_avg_4q"),
            earnings_beat_rate_8q=rev_data.get("earnings_beat_rate_8q"),
            next_earnings_date=rev_data.get("next_earnings_date"),
            payout_ratio=div_safety.get("payout_ratio"),
            dividends_paid=div_safety.get("dividends_paid"),
            five_year_avg_dividend_yield=div_safety.get("five_year_avg_dividend_yield"),
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
