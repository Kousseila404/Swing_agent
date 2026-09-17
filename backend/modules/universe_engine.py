"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  MODULE — UNIVERSE ENGINE (Quantamental Long-Term)                           ║
║                                                                              ║
║  Moteur dynamique de définition de l'univers d'investissement long-terme.   ║
║  Pivot stratégique : remplace la logique Swing (familles sectorielles ETF)  ║
║  par une sélection Smart-Beta / Rotation Sectorielle sur fondamentaux.      ║
║                                                                              ║
║  Pipeline :                                                                  ║
║    1. Fetch tickers S&P 500 + Nasdaq 100 (Wikipedia HTML scraping).         ║
║    2. Pour chaque ticker → yfinance .info (sector, industry, market cap,    ║
║       forward P/E, price targets, recommandations analystes…).              ║
║    3. Filtre institutionnel : market_cap > min_market_cap (défaut 10 B$).   ║
║    4. Persistance atomique dans data/universe.json (backup + FileLock).     ║
║                                                                              ║
║  Robustesse :                                                                ║
║    • Timeout + retry yfinance (ticker individuel ne crashe pas la run).     ║
║    • Parallélisation bornée (ThreadPoolExecutor, 8 workers).                ║
║    • Progress log lisible en subprocess (stdout unbuffered via print).      ║
║    • Écriture atomique tmp+rename, FileLock cross-process.                  ║
║                                                                              ║
║  CLI :                                                                       ║
║    python -m modules.universe_engine [--indices sp500,ndx100]                ║
║                                      [--min-cap 1e10] [--workers 8]          ║
║                                      [--max-retries 2] [--timeout 15]        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from filelock import FileLock, Timeout

from data_providers import (
    FundamentalProviderBase,
    MarketDataProviderBase,
    ProviderQuotaExceeded,
    get_providers,
)
from data_providers.base import FinancialRatios
from modules.log import logger

# ─────────────────────────────────────────────────────────────────────────────
# PATHS & CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

_PROJECT_ROOT  = Path(__file__).resolve().parent.parent
_UNIVERSE_PATH = _PROJECT_ROOT / "data" / "universe.json"
_LOCK_PATH     = _PROJECT_ROOT / "data" / "universe.json.lock"
_BACKUP_DIR    = _PROJECT_ROOT / "data" / ".universe_backups_quantamental"

_FILELOCK_TIMEOUT = 15.0
_BACKUP_RETENTION = 10

# Seuil institutionnel : large caps uniquement pour le long terme.
DEFAULT_MIN_MARKET_CAP = 10_000_000_000.0  # 10 B$

# Wikipedia est la source canonique et la plus à jour pour ces index.
_WIKI_SP500  = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_WIKI_NDX100 = "https://en.wikipedia.org/wiki/Nasdaq-100"
_USER_AGENT  = (
    "Mozilla/5.0 (compatible; SwingQuant-UniverseEngine/1.0; "
    "+https://github.com/yfinance)"
)
_HTTP_TIMEOUT = 20

# Regex ticker valide (yfinance US) : lettres, chiffres, tiret, point.
_TICKER_RE = re.compile(r"^[A-Z0-9\-\.]{1,10}$")


# ─────────────────────────────────────────────────────────────────────────────
# DATACLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TickerFundamentals:
    """Snapshot quantamental d'une action — toutes les métriques tolèrent None."""
    ticker: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    currency: str | None = None
    exchange: str | None = None
    market_cap: float | None = None
    current_price: float | None = None
    # Audit S3.x — ADTV 3M (yfinance averageVolume). Utilisé par le modèle
    # d'impact backtest et `apply_adtv_cap()` pour le sizing live.
    avg_volume_3m: float | None = None
    forward_pe: float | None = None
    trailing_pe: float | None = None
    peg_ratio: float | None = None
    price_to_book: float | None = None
    dividend_yield: float | None = None
    beta: float | None = None
    price_target_mean: float | None = None
    price_target_high: float | None = None
    price_target_low: float | None = None
    recommendation_mean: float | None = None  # 1=Strong Buy, 5=Strong Sell
    recommendation_key: str | None = None
    num_analysts: int | None = None
    # ── QUALITY (profitabilité & efficience capital) ─────────────────────────
    return_on_equity: float | None = None      # decimal (0.25 = 25%)
    operating_margin: float | None = None      # decimal
    profit_margin: float | None   = None       # decimal (net margin, bonus)
    # Lot 8 — Piotroski F-Score components (4 critères absolus dispo).
    return_on_assets: float | None = None      # decimal — F1: ROA > 0
    gross_margin: float | None = None          # decimal — réservé F9 (Y/Y)
    net_income: float | None = None            # TTM USD absolute — F4: OCF > NI
    shares_outstanding: float | None = None    # nb actions — réservé F8 (Y/Y)
    # ── VALUE (chèreté) ──────────────────────────────────────────────────────
    ev_to_ebitda: float | None = None          # ratio (<10 = cheap, >20 = rich)
    ev_to_revenue: float | None = None         # ratio
    free_cash_flow: float | None = None        # USD absolute, TTM
    operating_cash_flow: float | None = None   # USD absolute, TTM (fallback FCF)
    # ── RISK / BALANCE SHEET ─────────────────────────────────────────────────
    debt_to_equity: float | None = None        # % (yfinance renvoie souvent ×100)
    current_ratio: float | None = None         # >1 = liquide
    quick_ratio: float | None = None           # acid-test
    # ── GROWTH (Lot 12 — pilier Growth) ──────────────────────────────────────
    revenue_growth: float | None = None          # décimal YoY
    earnings_growth: float | None = None         # décimal YoY
    earnings_quarterly_growth: float | None = None
    # ── PIOTROSKI Y-1 (Lot 14 — active F3/F5/F6/F8/F9 sans attendre 1 an
    # d'universe_history). Source : yfinance tk.balance_sheet + .financials
    # (DataFrames annuels, Y-1 = col index 1). None si scraping KO.
    return_on_assets_prev_year:    float | None = None
    debt_to_equity_prev_year:      float | None = None
    current_ratio_prev_year:       float | None = None
    shares_outstanding_prev_year:  float | None = None
    gross_margin_prev_year:        float | None = None
    # Audit S3.x rigoureux — dates fiscales (cf. base.FinancialRatios).
    fundamentals_period_end:       str | None = None
    fundamentals_period_end_y1:    str | None = None
    # ── REVISIONS / EARNINGS SURPRISE (Lot 16 — pilier Revisions) ────────────
    upgrades_30d:                 int   | None = None
    downgrades_30d:               int   | None = None
    upgrades_90d:                 int   | None = None
    downgrades_90d:               int   | None = None
    revisions_net_score:          float | None = None  # ∈ [-1, 1]
    earnings_surprise_pct_last:   float | None = None
    earnings_surprise_avg_4q:     float | None = None
    earnings_beat_rate_8q:        float | None = None
    next_earnings_date:           str   | None = None
    # ── DIVIDEND SAFETY (Lot 16) ─────────────────────────────────────────────
    payout_ratio:                 float | None = None
    dividends_paid:               float | None = None
    five_year_avg_dividend_yield: float | None = None
    # ── MOMENTUM 12M-1M (Jegadeesh-Titman, injecté après enrich fundamentaux) ─
    # Malgré le nom legacy "6M", le pipeline calcule le 12M-1M (skip last 21j)
    # via _MOMENTUM_HISTORY_DAYS=272 dans _momentum.py. C'est l'anomalie momentum
    # la mieux documentée académiquement (Jegadeesh-Titman 1993, Asness 1994).
    momentum_return_pct: float | None = None     # % rendement 12M-1M
    momentum_volatility_pct: float | None = None # σ annualisée (%)
    momentum_risk_adjusted: float | None = None  # approx Sharpe 12M-1M (ret/σ)
    # Lot 14 — 52-week high ratio (last_close / max(close 272d)). Proche 1.0
    # = stock en tendance forte, anti-reversal bullish.
    momentum_high_52w_ratio: float | None = None
    source_indices: list[str] = field(default_factory=list)
    fetched_at: str | None = None
    error: str | None = None
    # Traçabilité (Lot 11 audit) — qui a vraiment fourni les données ?
    # Valeurs : "fmp", "yfinance", "fmp+yfinance" (FallbackFundamentalProvider).
    source_provider: str | None = None
    backfill_fields: list[str] | None = None
    # Bug #20 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-20) — divergences cross-provider
    # propagées du FallbackFundamentalProvider jusqu'à universe.json/scoring.
    cross_provider_divergence: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# TICKER DISCOVERY — Wikipedia HTML scraping
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_html(url: str) -> str | None:
    """GET avec UA custom (Wikipedia bloque les requests nus) + timeout."""
    try:
        r = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.error(f"[UniverseEngine] HTTP GET failed {url}: {e}")
        return None


def _normalize_ticker(raw: str) -> str | None:
    """yfinance attend les tickers avec '-' (ex: BRK.B → BRK-B)."""
    if not isinstance(raw, str):
        return None
    t = raw.strip().upper().replace(".", "-")
    if not t or not _TICKER_RE.match(t):
        return None
    return t


def fetch_sp500_tickers() -> list[str]:
    """Scrape la table officielle S&P 500 sur Wikipedia. Retourne [] si échec."""
    html = _fetch_html(_WIKI_SP500)
    if html is None:
        return []
    try:
        tables = pd.read_html(html)
    except Exception as e:
        logger.error(f"[UniverseEngine] S&P500 parse failed: {e}")
        return []
    for tbl in tables:
        cols = [str(c).strip().lower() for c in tbl.columns]
        if "symbol" in cols:
            col = tbl.columns[cols.index("symbol")]
            tickers = [_normalize_ticker(t) for t in tbl[col].tolist()]
            return sorted({t for t in tickers if t})
    logger.error("[UniverseEngine] S&P500 table 'Symbol' introuvable")
    return []


_NDX100_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / ".ndx100_cache.json"


def _ndx100_fallback() -> list[str]:
    """Liste Nasdaq-100 de secours : dernier scrape réussi (cache), sinon les
    tickers marqués `ndx100` dans universe.json (audit 2026-09-17, P2-5 : le
    scrape Wikipedia échouait chaque jour depuis juillet — 138 erreurs — sans
    que personne ne sache d'où venait la liste effectivement utilisée)."""
    try:
        if _NDX100_CACHE_PATH.exists():
            data = json.loads(_NDX100_CACHE_PATH.read_text(encoding="utf-8"))
            tickers = sorted({str(t) for t in data.get("tickers", []) if t})
            if 80 <= len(tickers) <= 120:
                logger.warning(
                    f"[UniverseEngine] NDX100 : fallback cache {data.get('fetched_at', '?')} ({len(tickers)} tickers)"
                )
                return tickers
    except Exception as exc:
        logger.debug(f"[UniverseEngine] NDX100 cache illisible : {exc}")
    try:
        upath = Path(__file__).resolve().parents[1] / "data" / "universe.json"
        u = json.loads(upath.read_text(encoding="utf-8"))
        rows = u.get("tickers") or {}
        tickers = sorted(t for t, r in rows.items() if "ndx100" in (r.get("source_indices") or []))
        if 80 <= len(tickers) <= 120:
            logger.warning(f"[UniverseEngine] NDX100 : fallback universe.json ({len(tickers)} tickers)")
            return tickers
    except Exception as exc:
        logger.debug(f"[UniverseEngine] NDX100 fallback universe.json : {exc}")
    return []


def fetch_nasdaq100_tickers() -> list[str]:
    """Scrape la table officielle Nasdaq-100 sur Wikipedia ; fallback cache/univers."""
    html = _fetch_html(_WIKI_NDX100)
    tables = []
    if html is not None:
        try:
            tables = pd.read_html(io.StringIO(html))
        except Exception as e:
            logger.error(f"[UniverseEngine] NDX100 parse failed: {e}")
    # La table "Components" varie d'emplacement — cherche par colonne.
    for tbl in tables:
        cols = [str(c).strip().lower() for c in tbl.columns]
        for candidate in ("ticker", "symbol"):
            if candidate in cols:
                col = tbl.columns[cols.index(candidate)]
                tickers = [_normalize_ticker(t) for t in tbl[col].tolist()]
                kept = sorted({t for t in tickers if t})
                # Sanity check : Nasdaq-100 ≈ 100 tickers, tolère ±15
                if 80 <= len(kept) <= 120:
                    try:
                        _NDX100_CACHE_PATH.write_text(json.dumps({
                            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                            "tickers": kept,
                        }), encoding="utf-8")
                    except Exception:
                        pass
                    return kept
    logger.warning("[UniverseEngine] NDX100 table Components introuvable sur Wikipedia — fallback")
    return _ndx100_fallback()


def _collect_tickers(indices: Sequence[str]) -> dict[str, list[str]]:
    """
    Retourne {ticker: [index_sources]} union des index demandés.
    Indices supportés : 'sp500', 'ndx100'.
    """
    sources: dict[str, list[str]] = {}

    if "sp500" in indices:
        tickers = fetch_sp500_tickers()
        logger.info(f"[UniverseEngine] S&P500 : {len(tickers)} tickers")
        for t in tickers:
            sources.setdefault(t, []).append("sp500")

    if "ndx100" in indices:
        tickers = fetch_nasdaq100_tickers()
        logger.info(f"[UniverseEngine] NDX100 : {len(tickers)} tickers")
        for t in tickers:
            sources.setdefault(t, []).append("ndx100")

    return sources


# ─────────────────────────────────────────────────────────────────────────────
# ENRICHMENT — via FundamentalProviderBase (cf. data_providers/)
# ─────────────────────────────────────────────────────────────────────────────

# Historique demandé au market provider pour le momentum 6M : on prend ~220
# jours de trading (≈ 10.5 mois calendaire) pour couvrir 6 mois + buffer σ.
_MOMENTUM_HISTORY_DAYS = 220


def _ratios_to_fundamentals(
    ratios: FinancialRatios,
    source_indices: list[str],
) -> TickerFundamentals:
    """Adapte FinancialRatios (transport provider) → TickerFundamentals (domaine).
    La seule différence : source_indices + champs momentum injectés plus tard.

    Lot bug-fix : `source_provider` et `backfill_fields` étaient perdus avant
    Lot 11 (audit data fetch a montré universe.json avec source_provider=None
    pour 501/501 tickers). Désormais propagés → traçabilité dans le payload.
    """
    return TickerFundamentals(
        ticker=ratios.ticker,
        name=ratios.name,
        sector=ratios.sector,
        industry=ratios.industry,
        country=ratios.country,
        currency=ratios.currency,
        exchange=ratios.exchange,
        market_cap=ratios.market_cap,
        current_price=ratios.current_price,
        avg_volume_3m=ratios.avg_volume_3m,
        forward_pe=ratios.forward_pe,
        trailing_pe=ratios.trailing_pe,
        peg_ratio=ratios.peg_ratio,
        price_to_book=ratios.price_to_book,
        dividend_yield=ratios.dividend_yield,
        beta=ratios.beta,
        price_target_mean=ratios.price_target_mean,
        price_target_high=ratios.price_target_high,
        price_target_low=ratios.price_target_low,
        recommendation_mean=ratios.recommendation_mean,
        recommendation_key=ratios.recommendation_key,
        num_analysts=ratios.num_analysts,
        return_on_equity=ratios.return_on_equity,
        operating_margin=ratios.operating_margin,
        profit_margin=ratios.profit_margin,
        return_on_assets=ratios.return_on_assets,
        gross_margin=ratios.gross_margin,
        net_income=ratios.net_income,
        shares_outstanding=ratios.shares_outstanding,
        ev_to_ebitda=ratios.ev_to_ebitda,
        ev_to_revenue=ratios.ev_to_revenue,
        free_cash_flow=ratios.free_cash_flow,
        operating_cash_flow=ratios.operating_cash_flow,
        debt_to_equity=ratios.debt_to_equity,
        current_ratio=ratios.current_ratio,
        quick_ratio=ratios.quick_ratio,
        revenue_growth=ratios.revenue_growth,
        earnings_growth=ratios.earnings_growth,
        earnings_quarterly_growth=ratios.earnings_quarterly_growth,
        return_on_assets_prev_year=ratios.return_on_assets_prev_year,
        debt_to_equity_prev_year=ratios.debt_to_equity_prev_year,
        current_ratio_prev_year=ratios.current_ratio_prev_year,
        shares_outstanding_prev_year=ratios.shares_outstanding_prev_year,
        gross_margin_prev_year=ratios.gross_margin_prev_year,
        fundamentals_period_end=ratios.fundamentals_period_end,
        fundamentals_period_end_y1=ratios.fundamentals_period_end_y1,
        upgrades_30d=ratios.upgrades_30d,
        downgrades_30d=ratios.downgrades_30d,
        upgrades_90d=ratios.upgrades_90d,
        downgrades_90d=ratios.downgrades_90d,
        revisions_net_score=ratios.revisions_net_score,
        earnings_surprise_pct_last=ratios.earnings_surprise_pct_last,
        earnings_surprise_avg_4q=ratios.earnings_surprise_avg_4q,
        earnings_beat_rate_8q=ratios.earnings_beat_rate_8q,
        next_earnings_date=ratios.next_earnings_date,
        payout_ratio=ratios.payout_ratio,
        dividends_paid=ratios.dividends_paid,
        five_year_avg_dividend_yield=ratios.five_year_avg_dividend_yield,
        source_indices=source_indices,
        fetched_at=ratios.fetched_at,
        error=ratios.error,
        source_provider=ratios.source_provider,
        backfill_fields=ratios.backfill_fields,
        cross_provider_divergence=ratios.cross_provider_divergence,
    )


def _enrich_ticker_via_provider(
    ticker: str,
    source_indices: list[str],
    fundamental_provider: FundamentalProviderBase,
) -> TickerFundamentals:
    """Enrichit un ticker via le provider. Jamais-crash sauf Quota (propagé)."""
    try:
        ratios = fundamental_provider.get_financial_ratios(ticker)
        return _ratios_to_fundamentals(ratios, source_indices)
    except ProviderQuotaExceeded:
        # Quota API OPEN — on laisse remonter jusqu'à build_universe()
        # pour aborter la pipeline entière au lieu de continuer à l'aveugle.
        raise
    except Exception as e:
        logger.warning(f"[UniverseEngine] {ticker} enrich crashed: {e}")
        return TickerFundamentals(
            ticker=ticker,
            source_indices=source_indices,
            fetched_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            error=str(e)[:200],
        )


def _enrich_momentum_via_provider(
    tickers: list[str],
    market_provider: MarketDataProviderBase,
) -> dict[str, dict[str, float | None]]:
    """
    Calcule {return_pct, volatility_pct, risk_adjusted} via le market_provider.
    Réutilise les helpers de sector_metrics pour garder la formule Sharpe-like
    (ret/σann) strictement identique entre secteur et ticker.
    """
    # Import paresseux pour éviter un cycle si sector_metrics évolue.
    from modules.sector_metrics import _EMPTY_MOMENTUM, _compute_momentum_stats

    out: dict[str, dict[str, float | None]] = {
        t: dict(_EMPTY_MOMENTUM) for t in tickers
    }
    if not tickers:
        return out

    logger.info(f"[UniverseEngine] Momentum batch : {len(tickers)} tickers via {market_provider.name}")
    try:
        series_by_ticker = market_provider.get_daily_history_batch(
            tickers, days=_MOMENTUM_HISTORY_DAYS,
        )
    except ProviderQuotaExceeded as e:
        logger.critical(f"[UniverseEngine] Momentum batch quota exceeded: {e}")
        return out
    except Exception as e:
        logger.error(f"[UniverseEngine] Momentum batch failed: {e}")
        return out

    for t, close in series_by_ticker.items():
        try:
            out[t] = _compute_momentum_stats(close)
        except Exception as e:
            logger.debug(f"[UniverseEngine] {t} momentum parse fail: {e}")

    return out


# ─────────────────────────────────────────────────────────────────────────────
# BUILD PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

class UniverseEngine:
    """
    Moteur de construction de l'univers quantamental — injecté avec ses deux
    providers (fondamental + market data). Ne parle plus à yfinance directement.

    Usage :
        from data_providers import get_providers
        fundamental, market = get_providers()
        engine = UniverseEngine(fundamental_provider=fundamental,
                                market_provider=market)
        payload = engine.build(indices=("sp500", "ndx100"))
    """

    def __init__(
        self,
        fundamental_provider: FundamentalProviderBase,
        market_provider: MarketDataProviderBase,
    ) -> None:
        self._fundamental = fundamental_provider
        self._market = market_provider

    def build(
        self,
        indices: Sequence[str] = ("sp500", "ndx100"),
        min_market_cap: float = DEFAULT_MIN_MARKET_CAP,
        workers: int = 8,
        progress_every: int = 25,
    ) -> dict[str, Any]:
        """Construit le payload universe (sans persister)."""
        return _build_universe_impl(
            indices=indices,
            min_market_cap=min_market_cap,
            workers=workers,
            progress_every=progress_every,
            fundamental_provider=self._fundamental,
            market_provider=self._market,
        )


def build_universe(
    indices: Sequence[str] = ("sp500", "ndx100"),
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP,
    workers: int = 8,
    progress_every: int = 25,
    fundamental_provider: FundamentalProviderBase | None = None,
    market_provider: MarketDataProviderBase | None = None,
) -> dict[str, Any]:
    """
    Wrapper fonctionnel conservé pour les callers existants (CLI, API).
    Résout les providers via get_providers() si non injectés.
    """
    if fundamental_provider is None or market_provider is None:
        f, m = get_providers()
        fundamental_provider = fundamental_provider or f
        market_provider = market_provider or m
    return _build_universe_impl(
        indices=indices,
        min_market_cap=min_market_cap,
        workers=workers,
        progress_every=progress_every,
        fundamental_provider=fundamental_provider,
        market_provider=market_provider,
    )


def _build_universe_impl(
    *,
    indices: Sequence[str],
    min_market_cap: float,
    workers: int,
    progress_every: int,
    fundamental_provider: FundamentalProviderBase,
    market_provider: MarketDataProviderBase,
) -> dict[str, Any]:
    """Implémentation unique de la pipeline — partagée UniverseEngine + build_universe."""
    t0 = time.time()
    sources = _collect_tickers(indices)
    if not sources:
        raise RuntimeError(
            f"[UniverseEngine] Aucun ticker récupéré depuis {list(indices)} — "
            f"échec de scraping Wikipedia (réseau ou layout changé)."
        )

    tickers = sorted(sources.keys())
    n_total = len(tickers)
    logger.info(
        f"[UniverseEngine] Union ({indices}) : {n_total} tickers uniques à enrichir "
        f"(fundamental={fundamental_provider.name}, market={market_provider.name})"
    )
    print(
        f"[UniverseEngine] Scanning {n_total} tickers "
        f"(workers={workers}, fund={fundamental_provider.name})...",
        flush=True,
    )

    results: dict[str, TickerFundamentals] = {}
    n_done = 0
    n_errors = 0

    aborted_quota = False
    quota_msg: str | None = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _enrich_ticker_via_provider, t, sources[t], fundamental_provider,
            ): t for t in tickers
        }
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                fund = fut.result()
            except ProviderQuotaExceeded as e:
                # Quota API OPEN — on ne peut plus récupérer de données fiables.
                # Les futures restantes continuent (chacune relèvera à son tour),
                # mais on retient le fait qu'on doit tuer la pipeline avant le momentum.
                aborted_quota = True
                quota_msg = str(e)[:160]
                logger.critical(
                    f"[UniverseEngine] Quota provider sur {t} — pipeline avortée : {e}"
                )
                fund = TickerFundamentals(
                    ticker=t,
                    source_indices=sources[t],
                    error=f"provider_quota_exceeded: {quota_msg}",
                )
            except Exception as e:
                fund = TickerFundamentals(
                    ticker=t,
                    source_indices=sources[t],
                    error=f"executor: {e}"[:200],
                )
            results[t] = fund
            n_done += 1
            if fund.error:
                n_errors += 1
            if n_done % progress_every == 0 or n_done == n_total:
                print(
                    f"[UniverseEngine] Progress {n_done}/{n_total} "
                    f"(errors={n_errors})",
                    flush=True,
                )

    if aborted_quota:
        raise ProviderQuotaExceeded(
            "[UniverseEngine] Build avorté — quota provider atteint "
            f"({quota_msg or 'n/a'}). Réessayez plus tard."
        )

    # ── Filtre institutionnel ───────────────────────────────────────────────
    kept: dict[str, dict[str, Any]] = {}
    rejected_low_cap = 0
    rejected_no_cap = 0
    for t in tickers:
        fund = results[t]
        mc = fund.market_cap
        if mc is None:
            rejected_no_cap += 1
            continue
        if mc < min_market_cap:
            rejected_low_cap += 1
            continue
        kept[t] = fund.to_dict()

    # ── Momentum 6M risk-adjusted par ticker (batch market provider, post-filtre) ──
    # On ne charge l'historique que des tickers retenus — évite ~40% de trafic.
    momentum = _enrich_momentum_via_provider(sorted(kept.keys()), market_provider)
    for t, stats in momentum.items():
        if t in kept:
            kept[t]["momentum_return_pct"]     = stats.get("return_pct")
            kept[t]["momentum_volatility_pct"] = stats.get("volatility_pct")
            kept[t]["momentum_risk_adjusted"]  = stats.get("risk_adjusted")
            kept[t]["momentum_high_52w_ratio"] = stats.get("high_52w_ratio")

    # ── Agrégation sectorielle (index inversé pour rotation sector-aware) ───
    sectors: dict[str, list[str]] = {}
    for t, f in kept.items():
        sec = f.get("sector") or "Unknown"
        sectors.setdefault(sec, []).append(t)
    for sec in sectors:
        sectors[sec].sort()

    elapsed = time.time() - t0
    logger.info(
        f"[UniverseEngine] Built in {elapsed:.1f}s — "
        f"kept {len(kept)}/{n_total} "
        f"(rejected: low_cap={rejected_low_cap}, no_cap={rejected_no_cap}, errors={n_errors})"
    )

    return {
        "version":          1,
        "updated_at":       datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_indices":   list(indices),
        "filter": {
            "min_market_cap_usd": min_market_cap,
        },
        "stats": {
            "n_scanned":         n_total,
            "n_kept":            len(kept),
            "n_rejected_low_cap": rejected_low_cap,
            "n_rejected_no_cap":  rejected_no_cap,
            "n_errors":          n_errors,
            "elapsed_seconds":   round(elapsed, 1),
            "workers":           workers,
        },
        "sectors":          sectors,
        "tickers":          kept,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PERSISTENCE — atomic write + rotating backups + FileLock
# ─────────────────────────────────────────────────────────────────────────────

def save_universe(payload: dict[str, Any], reason: str = "rebuild") -> Path:
    """
    Écrit atomiquement `payload` dans data/universe.json. Protégé par FileLock
    cross-process ; backup horodaté du fichier précédent (rotation N=10).
    """
    _UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    filelock = FileLock(str(_LOCK_PATH), timeout=_FILELOCK_TIMEOUT)
    try:
        filelock.acquire()
    except Timeout as e:
        raise RuntimeError(
            f"[UniverseEngine] FileLock timeout ({_FILELOCK_TIMEOUT}s) — "
            f"un autre rebuild est probablement en cours."
        ) from e

    try:
        # Audit S1.1 (2026-04-27) — point-in-time / survivorship.
        # On lit l'ancien payload AVANT de le backup-er pour pouvoir diff
        # contre le nouveau. Best-effort : si le load échoue on ne bloque pas.
        prev_tickers: dict[str, Any] | None = None
        if _UNIVERSE_PATH.exists():
            try:
                prev_payload = json.loads(_UNIVERSE_PATH.read_text(encoding="utf-8"))
                prev_tickers = prev_payload.get("tickers") if isinstance(prev_payload, dict) else None
            except (OSError, ValueError) as e:
                logger.warning(f"[UniverseEngine] prev payload unreadable for diff: {e}")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = _BACKUP_DIR / f"universe_{ts}.json"
            try:
                backup_path.write_bytes(_UNIVERSE_PATH.read_bytes())
            except Exception as e:
                logger.warning(f"[UniverseEngine] Backup failed: {e}")

            try:
                backups = sorted(_BACKUP_DIR.glob("universe_*.json"))
                for old in backups[:-_BACKUP_RETENTION]:
                    old.unlink()
            except Exception:
                pass

        # `as_of_date` explicite — facilite le filtrage point-in-time côté
        # backtest (la date du build, pas la date courante de lecture).
        as_of = datetime.now().strftime("%Y-%m-%d")
        payload = {**payload, "last_change": reason, "as_of_date": as_of}

        tmp = _UNIVERSE_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        tmp.replace(_UNIVERSE_PATH)

        # Diff prev → new vers le registry delisted (best-effort, fail-open).
        try:
            from modules import delisted as _delisted
            new_tickers = payload.get("tickers") if isinstance(payload, dict) else None
            if isinstance(new_tickers, dict):
                _delisted.record_diff(prev_tickers, new_tickers, today=as_of)
        except Exception as e:
            logger.warning(f"[UniverseEngine] delisted diff failed: {e}")
    finally:
        filelock.release()

    logger.info(
        f"[UniverseEngine] Persisté {_UNIVERSE_PATH} — "
        f"{payload.get('stats', {}).get('n_kept', '?')} tickers — reason: {reason}"
    )
    return _UNIVERSE_PATH


def load_universe() -> dict[str, Any]:
    """
    Retourne le payload universe depuis disque, ou un squelette vide si absent/corrompu.
    """
    if not _UNIVERSE_PATH.exists():
        return {"version": 0, "tickers": {}, "sectors": {}, "stats": {}}
    try:
        return json.loads(_UNIVERSE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"[UniverseEngine] Load failed: {e}")
        return {"version": 0, "tickers": {}, "sectors": {}, "stats": {}, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# CLI — consommé par le système de jobs (subprocess détaché)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_indices(raw: str) -> list[str]:
    wanted = [x.strip().lower() for x in raw.split(",") if x.strip()]
    allowed = {"sp500", "ndx100"}
    unknown = [x for x in wanted if x not in allowed]
    if unknown:
        raise SystemExit(
            f"[UniverseEngine] Indices inconnus: {unknown}. Autorisés: {sorted(allowed)}"
        )
    return wanted


def _main() -> int:
    parser = argparse.ArgumentParser(
        prog="universe_engine",
        description="Rebuild data/universe.json (quantamental long-term universe).",
    )
    parser.add_argument(
        "--indices", default="sp500,ndx100",
        help="Index sources séparés par virgule. Défaut: sp500,ndx100.",
    )
    parser.add_argument(
        "--min-cap", type=float, default=DEFAULT_MIN_MARKET_CAP,
        help=f"Seuil Market Cap min en USD. Défaut: {DEFAULT_MIN_MARKET_CAP:.0f}",
    )
    parser.add_argument(
        "--workers", type=int, default=8,
        help="Taille du ThreadPool d'enrichissement fondamental. Défaut: 8.",
    )
    parser.add_argument(
        "--reason", default="rebuild (cli)",
        help="Tag de changelog persisté dans universe.json.",
    )
    args = parser.parse_args()

    indices = _parse_indices(args.indices)
    print(
        f"[UniverseEngine] START indices={indices} min_cap={args.min_cap:.0f} "
        f"workers={args.workers}",
        flush=True,
    )

    try:
        payload = build_universe(
            indices=indices,
            min_market_cap=args.min_cap,
            workers=args.workers,
        )
    except Exception as e:
        logger.error(f"[UniverseEngine] FATAL: {e}", exc_info=True)
        print(f"[UniverseEngine] FATAL: {e}", flush=True)
        return 2

    try:
        save_universe(payload, reason=args.reason)
    except Exception as e:
        logger.error(f"[UniverseEngine] PERSIST FAILED: {e}", exc_info=True)
        print(f"[UniverseEngine] PERSIST FAILED: {e}", flush=True)
        return 3

    stats = payload.get("stats", {})
    print(
        f"[UniverseEngine] DONE kept={stats.get('n_kept')}/"
        f"{stats.get('n_scanned')} in {stats.get('elapsed_seconds')}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
