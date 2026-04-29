"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  MODULE — DATA PROVIDER CONTRACTS                                            ║
║                                                                              ║
║  Définit deux interfaces abstraites consommées par le moteur TITAN :        ║
║                                                                              ║
║    • FundamentalProviderBase — ratios comptables (ROE, Debt, PE, Growth,   ║
║      marges, market cap, analystes).                                        ║
║    • MarketDataProviderBase  — séries OHLCV ajustées pour momentum / SMA /  ║
║      RSI (ticker unique ou batch).                                          ║
║                                                                              ║
║  Objectif : découpler universe_engine + sector_metrics du provider concret  ║
║  (yfinance / FMP / Polygon / Alpaca). Toute nouvelle source est une         ║
║  implémentation de ces deux classes, rien d'autre à toucher.                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# EXCEPTIONS MÉTIER
# ─────────────────────────────────────────────────────────────────────────────

class ProviderError(Exception):
    """Base pour toutes les erreurs provider."""


class ProviderQuotaExceeded(ProviderError):
    """Quota journalier / rate-limit atteint — le caller doit abandonner la run
    (PAS retry en boucle sinon on dépasse encore plus)."""


class ProviderUnavailable(ProviderError):
    """Réseau down, API en panne, 5xx répété — distinct du quota pour que le
    caller puisse envisager un fallback vers un autre provider."""


# ─────────────────────────────────────────────────────────────────────────────
# DATACLASSES DE TRANSPORT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class FinancialRatios:
    """
    Snapshot normalisé des ratios comptables d'un ticker, indépendant du
    provider. Toutes les métriques tolèrent None — chaque source a ses trous.

    Unités / conventions :
      - return_on_equity / operating_margin / profit_margin : décimal (0.25 = 25 %)
      - dividend_yield  : décimal (0.04 = 4 %)
      - market_cap / free_cash_flow / operating_cash_flow : USD absolu
      - debt_to_equity  : ratio brut (1.0 = dette = equity), pas de × 100

    Les providers sont responsables de la normalisation aux unités ci-dessus.
    """
    ticker: str

    # ── Identité ─────────────────────────────────────────────────────────
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    currency: str | None = None
    exchange: str | None = None

    # ── Taille & prix courant ────────────────────────────────────────────
    market_cap: float | None = None
    current_price: float | None = None
    # Audit S3.x (2026-04-27) — ADTV (Average Daily Trading Volume) en nb
    # d'actions sur 3M (yfinance `info.averageVolume`). Utilisé par
    # `apply_adtv_cap()` (sizing live) et le modèle d'impact backtest.
    # None si provider ne l'expose pas — backtest fallback sur market_cap.
    avg_volume_3m: float | None = None

    # ── Valuation ────────────────────────────────────────────────────────
    forward_pe: float | None = None
    trailing_pe: float | None = None
    peg_ratio: float | None = None
    price_to_book: float | None = None
    dividend_yield: float | None = None
    beta: float | None = None

    # ── Analystes ────────────────────────────────────────────────────────
    price_target_mean: float | None = None
    price_target_high: float | None = None
    price_target_low: float | None = None
    recommendation_mean: float | None = None   # 1 = Strong Buy, 5 = Strong Sell
    recommendation_key: str | None = None
    num_analysts: int | None = None

    # ── Quality ──────────────────────────────────────────────────────────
    return_on_equity: float | None = None
    operating_margin: float | None = None
    profit_margin: float | None = None
    # Lot 8 — Piotroski F-Score : ROA, gross margin et net income TTM permettent
    # 4 critères absolus immédiats (F1: ROA>0, F2: OCF>0, F4: OCF>NI, F7: CR>1).
    # Les 5 critères Y/Y restants attendent ≥ 2 ans d'historique (Lot 6 storage).
    return_on_assets: float | None = None
    gross_margin: float | None = None
    net_income: float | None = None             # TTM, USD absolute
    shares_outstanding: float | None = None     # nb d'actions, dérivable mktcap/price

    # ── Growth (Lot 12 — pilier Growth) ───────────────────────────────────
    # Toutes en décimal YoY (0.15 = +15 %). Ces champs sont renseignés par
    # yfinance (.info) quand disponibles ; FMP free tier ne les expose pas
    # directement, le fallback YF comble.
    revenue_growth: float | None = None          # revenue_TTM / revenue_TTM-1 - 1
    earnings_growth: float | None = None         # earnings TTM YoY
    earnings_quarterly_growth: float | None = None  # earnings Q/Q YoY (plus volatile)

    # ── Revisions / Earnings Surprise (Lot 16 — pilier Revisions) ─────────
    # Capture la dynamique des révisions analyste et la qualité des earnings.
    # Source yfinance : `tk.recommendations` (rating history), `tk.earnings_history`
    # (surprise %), `tk.analyst_price_targets` (snapshot mean/high/low actuel).
    # Tous None si scraping KO ou pas d'analyse couverte.
    upgrades_30d:                 int   | None = None  # nombre d'upgrades 30j
    downgrades_30d:               int   | None = None
    upgrades_90d:                 int   | None = None
    downgrades_90d:               int   | None = None
    revisions_net_score:          float | None = None  # (up-down)/(up+down) sur 90j ∈ [-1, 1]
    earnings_surprise_pct_last:   float | None = None  # surprise dernier Q en %
    earnings_surprise_avg_4q:     float | None = None  # moyenne surprise sur 4 trimestres
    earnings_beat_rate_8q:        float | None = None  # taux de beat sur 8Q (0-1)
    next_earnings_date:           str   | None = None  # ISO YYYY-MM-DD du prochain earnings

    # ── Dividend Safety (Lot 16 — scorecard dividende) ────────────────────
    # Permet de calculer un Dividend Safety Score 0-100 à la SeekingAlpha :
    #   axis 1 : payout_ratio ≤ 60 % = sain
    #   axis 2 : FCF cover (FCF / dividends_paid) ≥ 1.5
    #   axis 3 : dividend_growth_5y ≥ 0
    #   axis 4 : years_of_consecutive_dividends ≥ 5
    payout_ratio:                 float | None = None  # décimal (0.45 = 45 %)
    dividends_paid:               float | None = None  # USD absolute (TTM, négatif yfinance)
    five_year_avg_dividend_yield: float | None = None  # décimal moyen 5Y

    # ── Value ────────────────────────────────────────────────────────────
    ev_to_ebitda: float | None = None
    ev_to_revenue: float | None = None
    free_cash_flow: float | None = None
    operating_cash_flow: float | None = None

    # ── Risk / bilan ─────────────────────────────────────────────────────
    debt_to_equity: float | None = None
    current_ratio: float | None = None
    quick_ratio: float | None = None

    # ── Piotroski Y-1 (snapshot annuel précédent) ─────────────────────────
    # Données extraites de yfinance `financials` / `balance_sheet` / `cashflow`
    # (4 années d'annuels). Permet d'activer F3/F5/F6/F8/F9 sans attendre que
    # universe_history accumule 1 an. None = scraping échoué ou provider
    # absent (FMP free tier n'expose pas les annuels ; yfinance les a presque
    # tous quand tk.financials n'est pas vide).
    return_on_assets_prev_year:    float | None = None
    debt_to_equity_prev_year:      float | None = None
    current_ratio_prev_year:       float | None = None
    shares_outstanding_prev_year:  float | None = None
    gross_margin_prev_year:        float | None = None
    # Audit S3.x rigoureux (2026-04-27) — dates de fin de période fiscale
    # extraites de tk.balance_sheet.columns / tk.financials.columns. ISO
    # YYYY-MM-DD. Permet à _lookup_yoy_snapshot d'appliquer un lag de
    # publication par-ticker (period_end + 90j) au lieu du lag global.
    fundamentals_period_end:       str | None = None  # Y0
    fundamentals_period_end_y1:    str | None = None  # Y-1

    # ── Métadonnées provider (traçabilité) ───────────────────────────────
    source_provider: str | None = None
    fetched_at: str | None = None
    error: str | None = None
    # Liste des champs comblés depuis un provider de fallback (ex: ["return_on_equity",
    # "ev_to_ebitda"] si FMP a manqué ces champs et YF les a fournis). None = pas
    # de fallback déclenché, [] = fallback déclenché mais rien à combler.
    backfill_fields: list[str] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# INTERFACE — FUNDAMENTAL PROVIDER
# ─────────────────────────────────────────────────────────────────────────────

class FundamentalProviderBase(ABC):
    """
    Contrat pour toute source de ratios comptables.

    Conventions :
      - Retourne TOUJOURS un FinancialRatios (même si vide, avec .error set)
        → le moteur TITAN ne crashe jamais sur un ticker individuel.
      - Lève ProviderQuotaExceeded si l'API coupe l'accès au process
        → le caller doit interrompre la pipeline, pas retry.
      - Lève ProviderUnavailable pour un 5xx répété → le caller peut fallback.
    """

    #: Nom court utilisé dans les logs et dans FinancialRatios.source_provider.
    name: str = "abstract"

    @abstractmethod
    def get_financial_ratios(self, ticker: str) -> FinancialRatios:
        """Récupère les ratios d'un seul ticker. Jamais-crash sauf Quota/Unavailable."""
        raise NotImplementedError

    def get_financial_ratios_batch(
        self, tickers: list[str]
    ) -> dict[str, FinancialRatios]:
        """
        Fallback naïf en boucle. Les providers qui exposent un endpoint batch
        (ex: FMP `/quote?symbols=...`) peuvent surcharger pour diviser les
        quotas par N.
        """
        return {t: self.get_financial_ratios(t) for t in tickers}


# ─────────────────────────────────────────────────────────────────────────────
# INTERFACE — MARKET DATA PROVIDER
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class LatestPrice:
    """
    Snapshot prix le plus récent disponible pour un ticker.

    - price : prix (float) ou None si indisponible (on degrade silencieux).
    - fetched_at_epoch : timestamp UTC du fetch (pour calcul d'âge côté caller).
    - source : nom du provider ayant servi la donnée ("polygon", "yfinance").
    - as_of : horodatage ISO du prix lui-même quand disponible
              (ex: timestamp du dernier trade Polygon), None sinon.
    """
    ticker: str
    price: float | None
    fetched_at_epoch: float
    source: str
    as_of: str | None = None


class MarketDataProviderBase(ABC):
    """
    Contrat pour toute source de prix ajustés.

    Le moteur TITAN ne consomme QUE des Close ajustés daily — pas de OHLC intraday,
    pas de volume. Cette restriction volontaire simplifie drastiquement les
    providers gratuits (FMP, Polygon free, yfinance).

    Conventions :
      - get_daily_history : renvoie une pd.Series indexée DatetimeIndex, de longueur
        ≤ `days`. Peut retourner None si le ticker est inconnu / data indisponible.
      - get_daily_history_batch : clé = ticker ; valeur = Series (ou None si échec
        individuel). Jamais-crash global ; un ticker fail ne tue pas les autres.
      - get_latest_prices : snapshot prix quasi-live pour sizing portfolio. L'impl
        par défaut dérive du dernier close daily (acceptable en fallback, mais
        les providers temps-réel-capables doivent surcharger).
    """

    name: str = "abstract"

    @abstractmethod
    def get_daily_history(self, ticker: str, days: int) -> pd.Series | None:
        """Close ajusté quotidien pour `ticker`, ≤ `days` points."""
        raise NotImplementedError

    @abstractmethod
    def get_daily_history_batch(
        self, tickers: list[str], days: int,
    ) -> dict[str, pd.Series | None]:
        """Version batch — un ticker fail n'empêche pas le reste. Recommandé
        pour les runs universe-wide (500+ tickers)."""
        raise NotImplementedError

    def get_latest_prices(
        self, tickers: list[str],
    ) -> dict[str, LatestPrice]:
        """
        Retourne {ticker: LatestPrice} pour sizing portfolio en quasi-temps-réel.

        Impl par défaut : dérive du dernier close daily via get_daily_history_batch
        (équivalent à un "yesterday's close"). Les providers avec endpoints snapshot
        ou intraday (Polygon /v2/snapshot, yfinance fast_info) DOIVENT surcharger
        pour réduire le décalage à quelques secondes.

        Jamais-crash : un ticker sans donnée retourne un LatestPrice avec price=None.
        """
        import time as _time
        now = _time.time()
        try:
            batch = self.get_daily_history_batch(tickers, days=3)
        except ProviderQuotaExceeded:
            raise
        except Exception:
            batch = {t: None for t in tickers}

        out: dict[str, LatestPrice] = {}
        for t in tickers:
            series = batch.get(t)
            price: float | None = None
            as_of: str | None = None
            if series is not None and len(series) > 0:
                try:
                    price = float(series.iloc[-1])
                    as_of = str(series.index[-1])
                except Exception:
                    price = None
            out[t] = LatestPrice(
                ticker=t, price=price, fetched_at_epoch=now,
                source=self.name, as_of=as_of,
            )
        return out
