"""PortfolioManager — coordinateur pipeline (stateless, déterministe).

Pipeline :
  1. Live momentum refresh (si provider)
  2. Filter BULLISH (momentum_6m > 0)
  3. Filter TOP-N par titan_composite_score
  4. Drop sans current_price (impossible de sizer)
  5. Risk parity weights + imputation σ sectorielle
  6. Sector cap 30 %
  7. Fetch live prices
  8. Sizing : amount_usd = W × (total_capital × regime_multiplier)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from modules.log import logger

from ._caches import (
    _MOMENTUM_LIVE_TTL_SECONDS as MOMENTUM_LIVE_TTL_SECONDS,  # noqa: F401 (re-export)
)
from ._caches import (
    fetch_latest_prices_cached,
    refresh_momentum_live_cached,
)
from ._filters import filter_bullish, filter_top_by_score
from ._risk_parity import compute_weights
from ._sector_cap import DEFAULT_SECTOR_CAP, apply_sector_cap
from ._utils import _age_days_from_iso, _safe_float
from ._vol_target import (
    apply_vol_target,
)

if TYPE_CHECKING:
    from data_providers.base import MarketDataProviderBase

DEFAULT_MAX_HOLDINGS = 20

# ── Staleness thresholds fondamentaux ─────────────────────────────
# Les fondamentaux (PE, ROE, margin…) tournent lentement — 14 j sans refresh
# est acceptable, au-delà on flag warning. 30 j = severe, data n'est plus
# actionnable.
FUNDAMENTAL_STALE_DAYS_WARNING = 14
FUNDAMENTAL_STALE_DAYS_SEVERE = 30

# On refresh les top-K où K = max_holdings × multiplier. Après refresh, certains
# candidats perdront le filtre bullish — la marge évite de finir avec moins de
# `max_holdings` sélections.
_MOMENTUM_CANDIDATE_MULTIPLIER = 3


@dataclass(frozen=True)
class AllocationResult:
    """Structure retournée par PortfolioManager.calculate_allocations()."""
    allocations: dict[str, dict[str, Any]]
    total_capital: float
    invested_usd: float
    cash_remaining_usd: float
    n_candidates: int
    n_bullish: int
    n_kept: int
    equal_weight_fallback: bool
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allocations":            self.allocations,
            "total_capital":          self.total_capital,
            "invested_usd":           round(self.invested_usd, 2),
            "cash_remaining_usd":     round(self.cash_remaining_usd, 2),
            "n_candidates":           self.n_candidates,
            "n_bullish":              self.n_bullish,
            "n_kept":                 self.n_kept,
            "equal_weight_fallback":  self.equal_weight_fallback,
            "diagnostics":            self.diagnostics,
        }


class PortfolioManager:
    """Moteur d'allocation — stateless, déterministe.

    Requiert :
      • scored_tickers : dict {TICKER: {..., titan_composite_score,
                                         current_price, momentum_return_pct,
                                         momentum_volatility_pct}}
        typiquement produit par `sector_metrics._score_universe()`.

    Exemple :
        pm = PortfolioManager(scored_tickers)
        plan = pm.calculate_allocations(
            target_tickers=list(scored_tickers.keys()),
            total_capital=100_000, max_holdings=20,
        )
    """

    def __init__(
        self,
        scored_tickers: dict[str, dict[str, Any]],
        *,
        market_provider: MarketDataProviderBase | None = None,
        momentum_provider: MarketDataProviderBase | None = None,
        sector_cap: float | None = DEFAULT_SECTOR_CAP,
        target_vol_pct: float | None = None,
    ) -> None:
        self._scored = scored_tickers
        self._market_provider = market_provider
        # Pour le refresh momentum live, on préfère un provider batch-friendly
        # (yfinance `yf.download` en 1 call) à un Polygon free tier qui ferait
        # N calls séquentiels rate-limités. L'API layer injecte la bonne pair.
        self._momentum_provider = momentum_provider
        self._sector_cap = sector_cap
        # target_vol_pct=None → désactive le vol-targeting (default fine-grained).
        # Les callers prod (routers, auto_proposer) passent explicitement
        # DEFAULT_TARGET_VOL_PCT ; les tests historiques peuvent omettre.
        self._target_vol_pct = target_vol_pct

    def _build_scored_overlay(
        self, tickers_to_refresh: list[str],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """Retourne (scored_live, diag) où `scored_live` est self._scored avec
        les champs momentum_return_pct/momentum_volatility_pct remplacés par
        les valeurs live pour les tickers où le refresh a abouti.
        """
        if not tickers_to_refresh or not self._momentum_provider:
            return self._scored, {"momentum_refreshed": False,
                                  "n_momentum_live": 0,
                                  "momentum_provider": None}

        fresh = refresh_momentum_live_cached(
            self._momentum_provider, tickers_to_refresh,
        )
        if not fresh:
            return self._scored, {"momentum_refreshed": False,
                                  "n_momentum_live": 0,
                                  "momentum_provider": self._momentum_provider.name}

        # Shallow-copy des lignes modifiées uniquement — évite un deepcopy 500×.
        overlay = dict(self._scored)
        for t, stats in fresh.items():
            if t not in overlay:
                continue
            base = overlay[t]
            overlay[t] = {
                **base,
                "momentum_return_pct":     stats.get("return_pct"),
                "momentum_volatility_pct": stats.get("volatility_pct"),
            }
        return overlay, {
            "momentum_refreshed":  True,
            "n_momentum_live":     len(fresh),
            "n_momentum_stale":    len(tickers_to_refresh) - len(fresh),
            "momentum_provider":   self._momentum_provider.name,
        }

    def calculate_allocations(
        self,
        target_tickers: list[str],
        total_capital: float,
        *,
        max_holdings: int = DEFAULT_MAX_HOLDINGS,
        allow_fractional_shares: bool = False,
        regime_multiplier: float = 1.0,
        regime_label: str | None = None,
    ) -> AllocationResult:
        """Pipeline complet : voir docstring module."""
        if total_capital <= 0:
            raise ValueError("total_capital doit être > 0")
        if max_holdings <= 0:
            raise ValueError("max_holdings doit être > 0")
        if regime_multiplier < 0 or regime_multiplier > 1.0:
            raise ValueError("regime_multiplier doit être dans [0, 1]")

        # Court-circuit : CRASH_PANIC (multiplier=0) → retour vide explicite,
        # inutile de scorer/filtrer dans cet état.
        if regime_multiplier == 0.0:
            logger.warning(
                "[PortfolioManager] Régime macro bloquant "
                f"({regime_label or 'multiplier=0'}) — allocations vides."
            )
            return AllocationResult(
                allocations={}, total_capital=total_capital,
                invested_usd=0.0, cash_remaining_usd=total_capital,
                n_candidates=0, n_bullish=0, n_kept=0,
                equal_weight_fallback=False,
                diagnostics={
                    "reason": "macro_regime_blocks_new_entries",
                    "regime_label": regime_label,
                    "regime_multiplier": regime_multiplier,
                    "effective_capital": 0.0,
                },
            )

        effective_capital = total_capital * regime_multiplier

        # 1. Universe de départ (dedupe + existence)
        candidates = sorted({t for t in target_tickers if t in self._scored})
        n_candidates = len(candidates)

        # 2. Live momentum refresh — sur top-K AVANT filtre bullish. Un ticker
        #    qui s'effondre post-earnings peut passer `momentum_return_pct > 0`
        #    sur des stats T-24h ; on force le recalcul sur 180 jours frais.
        #    Le pool ×3 garantit qu'on retrouve max_holdings candidats même
        #    si certains basculent bear après refresh.
        refresh_pool = filter_top_by_score(
            candidates, max_holdings * _MOMENTUM_CANDIDATE_MULTIPLIER,
            scored=self._scored,
        )
        scored_view, momentum_diag = self._build_scored_overlay(refresh_pool)

        # 3. Filtres (momentum frais si overlay appliqué)
        bullish = filter_bullish(candidates, scored=scored_view)
        n_bullish = len(bullish)
        top = filter_top_by_score(bullish, max_holdings, scored=scored_view)

        if not top:
            logger.warning(
                "[PortfolioManager] Aucun ticker passe les filtres "
                f"(candidates={n_candidates}, bullish={n_bullish})."
            )
            return AllocationResult(
                allocations={}, total_capital=total_capital,
                invested_usd=0.0, cash_remaining_usd=total_capital,
                n_candidates=n_candidates, n_bullish=n_bullish, n_kept=0,
                equal_weight_fallback=False,
                diagnostics={"reason": "no_candidates_post_filter",
                             **momentum_diag},
            )

        # 4. Drop tickers sans prix (impossible de sizer)
        priced = [t for t in top if _safe_float(
            (scored_view.get(t) or {}).get("current_price")
        )]
        dropped_no_price = [t for t in top if t not in priced]
        if dropped_no_price:
            logger.info(
                f"[PortfolioManager] Drop {len(dropped_no_price)} tickers sans "
                f"current_price: {dropped_no_price[:5]}..."
            )
        if not priced:
            return AllocationResult(
                allocations={}, total_capital=total_capital,
                invested_usd=0.0, cash_remaining_usd=total_capital,
                n_candidates=n_candidates, n_bullish=n_bullish, n_kept=0,
                equal_weight_fallback=False,
                diagnostics={"reason": "all_dropped_no_price",
                             "dropped": dropped_no_price,
                             **momentum_diag},
            )

        # 5. Weights (risk parity + imputation sectorielle)
        weights, vol_diag, eq_fallback = compute_weights(priced, scored=scored_view)

        # 6. Sector cap 30 % — après risk parity, avant vol-targeting.
        sector_by_ticker = {
            t: (scored_view.get(t) or {}).get("sector") or "Unknown"
            for t in priced
        }
        if self._sector_cap is not None and self._sector_cap > 0:
            weights, sector_cap_diag = apply_sector_cap(
                weights, sector_by_ticker, cap=self._sector_cap,
            )
        else:
            sector_cap_diag = {"cap_applied": False, "reason": "disabled"}

        # 6b. Vol-targeting — scaler le livre global pour que σ_p s'approche de
        # la cible. En régime HIGH-VOL, leverage < 1 → cash_residual ↑ (alloué
        # à cash_remaining_usd). Permet au drawdown de rester maîtrisé même
        # quand le circuit breaker n'a pas encore réagi.
        if self._target_vol_pct is not None and self._target_vol_pct > 0:
            weights, vol_target_diag = apply_vol_target(
                weights, scored_view, target_vol_pct=self._target_vol_pct,
            )
        else:
            vol_target_diag = {"applied": False, "reason": "disabled"}

        # 7. Fetch live prices AVANT sizing. Le current_price scoré peut avoir
        #    6-24h de retard → écart 2-5 % suffit à fausser la cible $.
        latest_prices = fetch_latest_prices_cached(self._market_provider, priced)
        now_epoch = time.time()
        n_price_live = 0
        n_price_stale = 0

        # 8. Sizing $ + shares
        allocations: dict[str, dict[str, Any]] = {}
        dropped_too_small: list[dict[str, Any]] = []
        invested = 0.0
        now_ts = time.time()
        n_stale_fundamentals = 0
        n_severe_fundamentals = 0
        oldest_fundamental_days: float | None = None
        for t in priced:
            row = scored_view.get(t) or {}
            w = weights[t]
            # effective_capital = total_capital × regime_multiplier.
            # En BULL (mult=1.0) ça revient à total_capital ; en BEAR (0.5) on
            # ne déploie que la moitié — le reste reste en cash_remaining_usd.
            amount = w * effective_capital
            stale_price = float(row["current_price"])

            lp = latest_prices.get(t)
            if lp is not None and lp.price is not None and lp.price > 0:
                price = float(lp.price)
                price_source = lp.source
                price_as_of = lp.as_of
                price_age_seconds = round(
                    max(0.0, now_epoch - lp.fetched_at_epoch), 2
                )
                price_live = True
                n_price_live += 1
            else:
                price = stale_price
                price_source = "scored_universe"
                price_as_of = None
                price_age_seconds = None
                price_live = False
                n_price_stale += 1

            if allow_fractional_shares:
                shares: float = round(amount / price, 4) if price > 0 else 0.0
                notional = shares * price
            else:
                shares_int = int(amount // price) if price > 0 else 0
                shares = float(shares_int)
                notional = shares_int * price

            # Filtre les allocations dégénérées : un ticker trop cher vs son
            # weight reçoit shares=0 notional=0. Plutôt qu'exposer une ligne
            # "achetez 0 ASML" au frontend, on la drop et on trace dans les
            # diagnostics pour audit.
            if shares <= 0 or notional <= 0:
                dropped_too_small.append({
                    "ticker":  t,
                    "weight_pct": round(w * 100.0, 4),
                    "price":   round(price, 4),
                    "target_usd": round(amount, 2),
                    "reason":  "insufficient_capital_for_one_share",
                })
                logger.debug(
                    f"[PortfolioManager] drop {t} : weight {w*100:.2f}% × "
                    f"effective_capital ${effective_capital:,.0f} = ${amount:.0f} < "
                    f"price ${price:.2f}"
                )
                continue

            # Flag per-ticker si le momentum a été rafraîchi — utile pour auditer
            # "la sélection vient-elle de data stale ou live ?".
            original_row = self._scored.get(t) or {}
            momentum_live = (
                row.get("momentum_return_pct") != original_row.get("momentum_return_pct")
                or row.get("momentum_volatility_pct") != original_row.get("momentum_volatility_pct")
            )
            vd = vol_diag[t]
            # Staleness fondamentaux. `fetched_at` vient du provider. None =
            # âge inconnu (pas un bug : le provider peut ne pas l'exposer).
            fetched_at = original_row.get("fetched_at")
            fund_age = _age_days_from_iso(fetched_at, now=now_ts)
            fund_stale = fund_age is not None and fund_age >= FUNDAMENTAL_STALE_DAYS_WARNING
            fund_severe = fund_age is not None and fund_age >= FUNDAMENTAL_STALE_DAYS_SEVERE
            if fund_stale:
                n_stale_fundamentals += 1
            if fund_severe:
                n_severe_fundamentals += 1
            if fund_age is not None and (oldest_fundamental_days is None or fund_age > oldest_fundamental_days):
                oldest_fundamental_days = fund_age
            allocations[t] = {
                "weight_pct":     round(w * 100.0, 4),
                "amount_usd":     round(notional, 2),
                "shares":         shares,
                "price":          round(price, 4),
                "price_live":     price_live,
                "price_source":   price_source,
                "price_as_of":    price_as_of,
                "price_age_seconds": price_age_seconds,
                "price_stale_reference": round(stale_price, 4),
                "target_usd":     round(amount, 2),
                "cash_residual_usd": round(amount - notional, 2),
                "titan_score":    _safe_float(row.get("titan_composite_score")),
                "momentum_pct":   _safe_float(row.get("momentum_return_pct")),
                "momentum_live":  momentum_live,
                "volatility_pct": _safe_float(row.get("momentum_volatility_pct")),
                "imputed_vol":    vd["imputed_vol"],
                "imputation_method": vd.get("method"),
                "sector":         row.get("sector"),
                "name":           row.get("name"),
                "fundamentals_fetched_at": fetched_at,
                "fundamentals_age_days":   round(fund_age, 2) if fund_age is not None else None,
                "fundamentals_stale":      fund_stale,
                "fundamentals_severe":     fund_severe,
                # Lot 15 — piliers + flags pour la capture d'entrée (auto_proposer
                # les embarque dans proposal.context, approve_batch les persiste
                # dans trade_journal via _entry_scores_dict).
                "quality_score":   _safe_float(row.get("quality_score")),
                "value_score":     _safe_float(row.get("value_score")),
                "risk_score":      _safe_float(row.get("risk_score")),
                "momentum_score":  _safe_float(row.get("momentum_score")),
                "piotroski_score": _safe_float(row.get("piotroski_score")),
                "growth_score":    _safe_float(row.get("growth_score")),
                "f_score":         row.get("f_score"),
                "f_score_max":     row.get("f_score_max"),
                "titan_tilt_flags":  row.get("titan_tilt_flags") or [],
                "titan_tilt_adjust": _safe_float(row.get("titan_tilt_adjust")),
            }
            invested += notional

        sector_weights_pct: dict[str, float] = {}
        for alloc in allocations.values():
            sec = alloc["sector"] or "Unknown"
            sector_weights_pct[sec] = sector_weights_pct.get(sec, 0.0) + alloc["weight_pct"]
        sector_weights_pct = {
            s: round(v, 4) for s, v in sorted(
                sector_weights_pct.items(), key=lambda kv: -kv[1],
            )
        }

        diagnostics = {
            "dropped_no_price":       dropped_no_price,
            "weight_method":          "equal_weight_fallback" if eq_fallback else "risk_parity",
            "n_imputed_vols":         sum(1 for d in vol_diag.values() if d["imputed_vol"]),
            "n_sector_imputed":       sum(
                1 for d in vol_diag.values()
                if d.get("method") == "sector_median_impute"
            ),
            "n_portfolio_imputed":    sum(
                1 for d in vol_diag.values()
                if d.get("method") == "portfolio_median_impute"
            ),
            "sector_cap":             sector_cap_diag,
            "vol_target":             vol_target_diag,
            "dropped_too_small":      dropped_too_small,
            "sector_weights_pct":     sector_weights_pct,
            "n_price_live":           n_price_live,
            "n_price_stale":          n_price_stale,
            "price_provider":         (self._market_provider.name
                                       if self._market_provider else None),
            "regime_label":           regime_label,
            "regime_multiplier":      regime_multiplier,
            "effective_capital":      round(effective_capital, 2),
            "n_stale_fundamentals":   n_stale_fundamentals,
            "n_severe_fundamentals":  n_severe_fundamentals,
            "oldest_fundamental_days": (round(oldest_fundamental_days, 2)
                                        if oldest_fundamental_days is not None else None),
            "fundamentals_warning_threshold_days": FUNDAMENTAL_STALE_DAYS_WARNING,
            "fundamentals_severe_threshold_days":  FUNDAMENTAL_STALE_DAYS_SEVERE,
            **momentum_diag,
            "allow_fractional_shares": allow_fractional_shares,
        }

        return AllocationResult(
            allocations=allocations,
            total_capital=total_capital,
            invested_usd=invested,
            cash_remaining_usd=total_capital - invested,
            n_candidates=n_candidates,
            n_bullish=n_bullish,
            n_kept=len(priced),
            equal_weight_fallback=eq_fallback,
            diagnostics=diagnostics,
        )
