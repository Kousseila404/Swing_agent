"""Composite market provider : primary + fallback pour `get_latest_prices`.

Motivation : Polygon free tier bloque `/v2/snapshot/...` (403). Le call ne
raise pas — il retourne `price=None` pour tous les tickers, et le
PortfolioManager retombe silencieusement sur `current_price` scoré (jusqu'à
plusieurs jours stale). Résultat : entries et SL calibrés sur des prix
périmés → stop-outs mécaniques à la prochaine évaluation tracker.

Stratégie :
  1. Appel `primary.get_latest_prices()` → dict {ticker: LatestPrice}.
  2. Sélectionne les tickers dont `price is None` → retry via `fallback`.
  3. Merge : primary gagne quand il a une valeur ; fallback comble les None.

Les autres méthodes (get_daily_history, get_daily_history_batch,
get_ohlcv_adjusted) restent déléguées au primary — Polygon daily fonctionne
en free tier donc pas de raison d'ajouter un coût yfinance.

Audit 2026-04-23 : en prod Polygon renvoie 403 à 100 % sur le snapshot. On
ajoute `use_primary_for_snapshot` (default False côté factory) pour court-
circuiter l'appel Polygon inutile et aller direct sur le fallback. Gardable
True si on upgrade la clé un jour.
"""
from __future__ import annotations

import pandas as pd

from modules.log import logger

from .base import (
    LatestPrice,
    MarketDataProviderBase,
    ProviderError,
)


class FallbackMarketProvider(MarketDataProviderBase):
    """Wrapper qui ne fallback QUE pour `get_latest_prices`.

    Usage : `FallbackMarketProvider(PolygonProvider(...), YFinanceProvider())`.
    Si le primary répond avec tous les prix remplis, le fallback n'est pas
    appelé. Sinon, on fetch YF uniquement pour les tickers manquants.
    """

    def __init__(
        self,
        primary: MarketDataProviderBase,
        fallback: MarketDataProviderBase,
        *,
        use_primary_for_snapshot: bool = True,
        use_primary_for_daily_batch: bool = True,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._use_primary_for_snapshot = use_primary_for_snapshot
        self._use_primary_for_daily_batch = use_primary_for_daily_batch
        self.name = f"{primary.name}+{fallback.name}"

    # ── Pass-through pur (Polygon daily OK en free tier) ────────────────

    def get_daily_history(self, ticker: str, days: int) -> pd.Series | None:
        return self._primary.get_daily_history(ticker, days)

    def get_daily_history_batch(
        self, tickers: list[str], days: int,
    ) -> dict[str, pd.Series | None]:
        """Route batch daily history avec vraie cascade bidirectionnelle.

        • `use_primary_for_daily_batch=True` : Polygon first (slow), YF fallback
          sur les holes. Sémantique historique, garde si quelqu'un a tier payant.
        • `use_primary_for_daily_batch=False` (default) : YF first (batch rapide),
          **Polygon fallback** si YF échoue ou retourne des holes. Résilient au
          circuit-breaker yfinance.
        """
        if not tickers:
            return {}

        if self._use_primary_for_daily_batch:
            fast, slow = self._primary, self._fallback
        else:
            fast, slow = self._fallback, self._primary

        # 1. Tentative source rapide.
        try:
            fast_out = fast.get_daily_history_batch(tickers, days)
        except ProviderError as e:
            logger.warning(
                f"[{self.name}] batch {fast.name} crash ({e}) "
                f"→ fallback complet sur {slow.name}"
            )
            fast_out = {}
        except Exception as e:
            logger.warning(
                f"[{self.name}] batch {fast.name} crash inattendu: {e}"
            )
            fast_out = {}

        # 2. Identifie les holes (None ou série vide).
        def _is_hole(s: pd.Series | None) -> bool:
            if s is None:
                return True
            try:
                return len(s) == 0
            except TypeError:
                return True

        missing = [t for t in tickers if _is_hole(fast_out.get(t))]
        if not missing:
            return fast_out

        # 3. Fallback ciblé sur les holes seulement.
        logger.info(
            f"[{self.name}] batch {fast.name} manque {len(missing)}/{len(tickers)} "
            f"séries → retry via {slow.name}"
        )
        try:
            slow_out = slow.get_daily_history_batch(missing, days)
        except ProviderError as e:
            logger.warning(
                f"[{self.name}] fallback batch {slow.name} crash: {e}"
            )
            return fast_out
        except Exception as e:
            logger.warning(
                f"[{self.name}] fallback batch {slow.name} crash inattendu: {e}"
            )
            return fast_out

        merged = dict(fast_out)
        n_filled = 0
        for t in missing:
            s = slow_out.get(t)
            if not _is_hole(s):
                merged[t] = s
                n_filled += 1

        if n_filled:
            logger.info(
                f"[{self.name}] fallback batch {slow.name} a comblé "
                f"{n_filled}/{len(missing)} séries manquantes."
            )
        return merged

    # ── Avec fallback ──────────────────────────────────────────────────

    def get_latest_prices(
        self, tickers: list[str],
    ) -> dict[str, LatestPrice]:
        """Prix live avec cascade bidirectionnelle selon `use_primary_for_snapshot`.

        • True : Polygon first (snapshot tier payant), YF fallback holes.
        • False (default) : YF first (instant), **Polygon fallback** si YF
          échoue. Même si Polygon free retourne 403 sur /v2/snapshot,
          l'appel ne crash pas — il retourne price=None → l'ultime dernier
          recours est un vrai snapshot Polygon tier payant si dispo.
        """
        if not tickers:
            return {}

        if self._use_primary_for_snapshot:
            fast, slow = self._primary, self._fallback
        else:
            fast, slow = self._fallback, self._primary

        # 1. Tentative source rapide.
        try:
            fast_out = fast.get_latest_prices(tickers)
        except ProviderError as e:
            logger.warning(
                f"[{self.name}] {fast.name} get_latest_prices crash: {e} "
                f"→ fallback {slow.name}"
            )
            fast_out = {}
        except Exception as e:
            logger.warning(
                f"[{self.name}] {fast.name} crash inattendu: {e}"
            )
            fast_out = {}

        missing = [
            t for t in tickers
            if fast_out.get(t) is None or fast_out[t].price is None
        ]

        if not missing:
            return fast_out

        # Détection signal faible : si Polygon répond 200 avec dict vide (cas
        # free-tier /v2/snapshot bloqué 403 ou clé expirée), tous les prix sont
        # None. Log explicite pour distinguer du bruit "1 ticker introuvable".
        if tickers and len(missing) == len(tickers) and fast_out:
            logger.warning(
                f"[{self.name}] {fast.name} a répondu mais TOUS les prix sont None "
                f"({len(tickers)} tickers) — probable endpoint bloqué / clé expirée"
            )

        logger.info(
            f"[{self.name}] {len(missing)}/{len(tickers)} prix None côté "
            f"{fast.name} → fallback {slow.name} (exemples : {missing[:5]})"
        )

        try:
            slow_out = slow.get_latest_prices(missing)
        except ProviderError as e:
            logger.warning(
                f"[{self.name}] fallback {slow.name} crash: {e}"
            )
            return fast_out
        except Exception as e:
            logger.warning(
                f"[{self.name}] fallback {slow.name} crash inattendu: {e}"
            )
            return fast_out

        merged = dict(fast_out)
        n_filled = 0
        for t in missing:
            fb = slow_out.get(t)
            if fb is not None and fb.price is not None and fb.price > 0:
                merged[t] = fb
                n_filled += 1

        if n_filled:
            logger.info(
                f"[{self.name}] fallback {slow.name} a comblé "
                f"{n_filled}/{len(missing)} prix manquants."
            )

        return merged
