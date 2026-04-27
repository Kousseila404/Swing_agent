"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  PROVIDER — Financial Modeling Prep (fondamental)                            ║
║                                                                              ║
║  Source canonique pour les ratios comptables (ROE, Debt, PE, Growth, FCF). ║
║  Endpoints consommés (API "stable", remplace /api/v3 déprécié aug-2025) :  ║
║    • /profile?symbol=X          → identité + market cap + beta + prix       ║
║    • /ratios-ttm?symbol=X       → marges, D/E, current/quick, P/B, P/E      ║
║    • /key-metrics-ttm?symbol=X  → ROE, EV/EBITDA, EV/Sales, FCF             ║
║    • /price-target-consensus    → price targets (optionnel)                 ║
║                                                                              ║
║  Tier gratuit : 250 requêtes / jour. Stratégie :                             ║
║    1. Compteur de quota en mémoire (process-wide, thread-safe).             ║
║    2. Dès qu'on franchit 240 appels (coussin 10), on lève                   ║
║       ProviderQuotaExceeded pour que le caller arrête la run proprement.   ║
║    3. En cas de 429 HTTP on lève aussi ProviderQuotaExceeded.               ║
║                                                                              ║
║  ⚠️  FMP ne sert PAS le market data (batch OHLCV 500 tickers trop cher).    ║
║      Utiliser PolygonProvider ou YFinanceProvider pour le momentum.         ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

import requests

from modules.log import logger

from .base import (
    FinancialRatios,
    FundamentalProviderBase,
    ProviderQuotaExceeded,
    ProviderUnavailable,
)

_FMP_BASE = "https://financialmodelingprep.com/stable"
_FMP_TIMEOUT = 12.0

# Coussin sous les 250/jour : on arrête à 240 pour laisser de la marge si
# plusieurs jobs tournent en parallèle (alerter, sector metrics, rebuild).
_DEFAULT_DAILY_QUOTA = 240


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> int | None:
    f = _safe_float(v)
    return int(f) if f is not None else None


class FMPProvider(FundamentalProviderBase):
    """Ratios comptables via Financial Modeling Prep."""

    name = "fmp"

    def __init__(
        self,
        api_key: str,
        daily_quota: int = _DEFAULT_DAILY_QUOTA,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("FMPProvider: api_key requis (FMP_API_KEY manquante).")
        self._api_key = api_key
        self._daily_quota = daily_quota
        self._session = session or requests.Session()

        # Compteur thread-safe, réinitialisé chaque jour UTC (FMP reset à minuit UTC).
        self._lock = threading.Lock()
        self._counter_day: str | None = None
        self._counter_n: int = 0

        # Paths qui ont renvoyé 402 (premium tier requis) — skippés ensuite
        # pour éviter de gaspiller le quota et polluer les logs. Réinitialisé
        # par instance (une nouvelle instance = nouveau run = re-test).
        self._premium_blocked: set[str] = set()

    # ── Quota tracking ──────────────────────────────────────────────────

    def _reserve_call(self) -> None:
        """Incrémente le compteur ; lève ProviderQuotaExceeded si le cap est atteint."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with self._lock:
            if self._counter_day != today:
                self._counter_day = today
                self._counter_n = 0
            if self._counter_n >= self._daily_quota:
                raise ProviderQuotaExceeded(
                    f"[FMP] Quota journalier atteint ({self._counter_n}/{self._daily_quota})"
                )
            self._counter_n += 1

    @property
    def calls_today(self) -> int:
        with self._lock:
            return self._counter_n if self._counter_day == datetime.utcnow().strftime("%Y-%m-%d") else 0

    # ── HTTP helper ─────────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET avec gestion des erreurs → Provider exceptions normalisées."""
        # Short-circuit : si on a déjà reçu 402 sur ce path pendant la session,
        # on ne gaspille ni quota ni log (free tier n'y accédera pas d'ici minuit).
        if path in self._premium_blocked:
            return None

        self._reserve_call()
        url = f"{_FMP_BASE}{path}"
        merged = {"apikey": self._api_key, **(params or {})}
        try:
            r = self._session.get(url, params=merged, timeout=_FMP_TIMEOUT)
        except requests.RequestException as e:
            raise ProviderUnavailable(f"[FMP] network: {e}") from e

        if r.status_code == 429:
            raise ProviderQuotaExceeded(f"[FMP] HTTP 429 sur {path}")
        if r.status_code == 401 or r.status_code == 403:
            # Clé invalide → unavailable plutôt que quota (sinon le caller pense à attendre minuit).
            raise ProviderUnavailable(f"[FMP] HTTP {r.status_code} sur {path} — clé invalide ?")
        if r.status_code == 402:
            # Endpoint premium non couvert par l'abonnement — on flag le path
            # pour skip les tickers suivants. Log une seule fois par path.
            if path not in self._premium_blocked:
                self._premium_blocked.add(path)
                logger.warning(
                    f"[FMP] Endpoint {path} requiert un tier premium (402) — "
                    f"skippé pour le reste de la session."
                )
            return None
        if r.status_code >= 500:
            raise ProviderUnavailable(f"[FMP] HTTP {r.status_code} sur {path}")
        if r.status_code != 200:
            logger.warning(f"[FMP] HTTP {r.status_code} sur {path}: {r.text[:150]}")
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def _first(self, payload: Any) -> dict[str, Any]:
        """FMP retourne souvent une liste d'un seul élément — on l'aplatit."""
        if isinstance(payload, list) and payload:
            first = payload[0]
            return first if isinstance(first, dict) else {}
        if isinstance(payload, dict):
            return payload
        return {}

    # ── API publique ────────────────────────────────────────────────────

    def get_financial_ratios(self, ticker: str) -> FinancialRatios:
        fetched_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        # Les ProviderQuotaExceeded et ProviderUnavailable remontent — le caller
        # doit savoir qu'il faut arrêter la pipeline (vs. erreur ticker-local).
        try:
            profile = self._first(self._get("/profile",         {"symbol": ticker}))
            ratios  = self._first(self._get("/ratios-ttm",      {"symbol": ticker}))
            metrics = self._first(self._get("/key-metrics-ttm", {"symbol": ticker}))
        except ProviderQuotaExceeded:
            raise
        except ProviderUnavailable as e:
            return FinancialRatios(
                ticker=ticker,
                source_provider=self.name,
                fetched_at=fetched_at,
                error=f"unavailable: {str(e)[:180]}",
            )

        if not profile and not ratios and not metrics:
            return FinancialRatios(
                ticker=ticker,
                source_provider=self.name,
                fetched_at=fetched_at,
                error="FMP empty payload",
            )

        # FCF absolu = FCF/share × shares_out.
        # FMP /profile ne donne pas shares directement ; on la dérive via
        # shares ≈ mktCap / price (robuste si les deux sont présents).
        # FCF/OCF per share vivent dans /ratios-ttm sur l'API stable ; le cash
        # flow absolu se reconstitue via shares ≈ marketCap / price.
        fcf_ps = _safe_float(ratios.get("freeCashFlowPerShareTTM"))
        ocf_ps = _safe_float(ratios.get("operatingCashFlowPerShareTTM"))
        mkt_cap = _safe_float(profile.get("marketCap"))
        price   = _safe_float(profile.get("price"))
        shares_out = (mkt_cap / price) if (mkt_cap and price and price > 0) else None
        fcf_abs = (fcf_ps * shares_out) if (fcf_ps is not None and shares_out) else None
        ocf_abs = (ocf_ps * shares_out) if (ocf_ps is not None and shares_out) else None

        # Lot 8 — Net income absolu = netIncomePerShareTTM × shares_out
        # (FMP stable expose que des ratios per-share, pas l'absolu directement).
        ni_ps = _safe_float(ratios.get("netIncomePerShareTTM"))
        net_income_abs = (ni_ps * shares_out) if (ni_ps is not None and shares_out) else None

        trailing_pe = _safe_float(ratios.get("priceToEarningsRatioTTM"))

        return FinancialRatios(
            ticker=ticker,
            # Identité + taille — depuis /profile
            name=profile.get("companyName"),
            sector=profile.get("sector"),
            industry=profile.get("industry"),
            country=profile.get("country"),
            currency=profile.get("currency"),
            exchange=profile.get("exchange"),
            market_cap=mkt_cap,
            current_price=price,
            beta=_safe_float(profile.get("beta")),

            # Valuation — FMP stable ne publie PAS forward P/E (endpoint payant
            # /analyst-estimates). On laisse None : le pilier Value bascule sur
            # EV/EBITDA (source plus fiable pour cycliques et growth que
            # trailing_pe qui explose en bas de cycle ou en croissance rapide).
            forward_pe=None,
            trailing_pe=trailing_pe,
            peg_ratio=_safe_float(ratios.get("priceToEarningsGrowthRatioTTM")),
            price_to_book=_safe_float(ratios.get("priceToBookRatioTTM")),
            dividend_yield=_safe_float(ratios.get("dividendYieldTTM")),

            # Quality — ROE vit maintenant dans /key-metrics-ttm sur l'API stable
            return_on_equity=_safe_float(metrics.get("returnOnEquityTTM")),
            operating_margin=_safe_float(ratios.get("operatingProfitMarginTTM")),
            profit_margin=_safe_float(ratios.get("netProfitMarginTTM")),

            # Lot 8 — Piotroski F-Score components.
            # FMP stable : returnOnAssetsTTM dans /key-metrics-ttm (peut aussi
            # exister dans /ratios-ttm comme returnOnAssetsTTM — on essaie les 2).
            return_on_assets=(
                _safe_float(metrics.get("returnOnAssetsTTM"))
                or _safe_float(ratios.get("returnOnAssetsTTM"))
            ),
            gross_margin=_safe_float(ratios.get("grossProfitMarginTTM")),
            net_income=net_income_abs,
            shares_outstanding=shares_out,

            # Value — renommages stable : enterpriseValueOverEBITDA → evToEBITDA
            ev_to_ebitda=_safe_float(metrics.get("evToEBITDATTM")),
            ev_to_revenue=_safe_float(metrics.get("evToSalesTTM")),
            free_cash_flow=fcf_abs,
            operating_cash_flow=ocf_abs,

            # Risk — renommage stable : debtEquityRatio → debtToEquityRatio
            debt_to_equity=_safe_float(ratios.get("debtToEquityRatioTTM")),
            current_ratio=_safe_float(ratios.get("currentRatioTTM")),
            quick_ratio=_safe_float(ratios.get("quickRatioTTM")),

            # Analystes — non couverts par ces endpoints : laissés à None.
            # Pour les targets / recos, consommer /price-target-consensus en
            # endpoint dédié (coûteux en quota, optionnel).

            source_provider=self.name,
            fetched_at=fetched_at,
        )
