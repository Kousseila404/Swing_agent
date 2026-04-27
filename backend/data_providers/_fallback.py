"""Composite fundamental provider : primary + fallback merge.

Raison d'être : FMP free tier bloque `/ratios-ttm` et `/key-metrics-ttm` (402
Premium) → tous les champs Quality (ROE, margins) et Value (EV/EBITDA, FCF,
D/E) reviennent None. On backfill via yfinance — le `/profile` FMP reste
source of truth (company name, market cap, price) car c'est ce que FMP fait
le mieux en free tier.

Stratégie :
  1. Call primary.get_financial_ratios() systématiquement.
  2. Si des champs critiques sont None (ou primary.error set) → appel fallback.
  3. Merge : primary gagne partout ; le fallback ne remplit QUE les None.
  4. Tracé via `source_provider="fmp+yf"` et `backfill_fields=[...]`.

Quota / perf :
  - +1 call fallback par ticker UNIQUEMENT si les critical_fields sont vides.
  - Le fallback partage le circuit-breaker yf_breaker (instance unique via
    get_providers) → pas de double compteur.
  - Les exceptions Quota/Unavailable du fallback ne tuent pas la run : on
    retourne le primary tel quel (il reste la source de vérité).
"""
from __future__ import annotations

from dataclasses import fields

from modules.log import logger

from .base import (
    FinancialRatios,
    FundamentalProviderBase,
    ProviderError,
)

# Champs que l'on considère "critiques" pour déclencher un backfill.
# Si TOUS sont None sur le primary → on appelle le fallback.
# Choix = Quality + Value (les piliers scoring TITAN) ; l'identité (name,
# sector, market_cap) est déjà fournie par FMP /profile donc non-critique.
_CRITICAL_FIELDS: tuple[str, ...] = (
    "return_on_equity",
    "operating_margin",
    "profit_margin",
    "ev_to_ebitda",
    "free_cash_flow",
    "debt_to_equity",
)


def _needs_backfill(r: FinancialRatios) -> bool:
    """True ssi le primary a renvoyé une erreur OU aucun critical field rempli."""
    if r.error:
        return True
    return all(getattr(r, f) is None for f in _CRITICAL_FIELDS)


def _merge(primary: FinancialRatios, fallback: FinancialRatios) -> FinancialRatios:
    """primary gagne, fallback comble les None. Retourne un nouveau FinancialRatios
    avec source_provider="primary+fallback" et backfill_fields listant ce qui a
    été comblé."""
    filled: list[str] = []
    merged_kwargs: dict = {}
    for f in fields(primary):
        name = f.name
        if name in ("source_provider", "fetched_at", "error", "backfill_fields"):
            # Métadonnées traitées séparément après le merge des champs data.
            continue
        p_val = getattr(primary, name)
        if p_val is None:
            fb_val = getattr(fallback, name)
            merged_kwargs[name] = fb_val
            if fb_val is not None:
                filled.append(name)
        else:
            merged_kwargs[name] = p_val

    tag = f"{primary.source_provider or 'primary'}+{fallback.source_provider or 'fallback'}"
    return FinancialRatios(
        **merged_kwargs,
        source_provider=tag if filled else primary.source_provider,
        fetched_at=primary.fetched_at,
        # L'erreur primary devient info secondaire si le fallback a complété.
        error=None if filled else primary.error,
        backfill_fields=filled if filled else None,
    )


class FallbackFundamentalProvider(FundamentalProviderBase):
    """Wrapper qui enchaîne deux FundamentalProviderBase et merge les résultats.

    Utilisation typique : `FallbackFundamentalProvider(FMPProvider(...), YFinanceProvider())`.
    Si le primary répond correctement (au moins un critical field rempli), le
    fallback n'est jamais appelé → pas de coût supplémentaire.
    """

    def __init__(
        self,
        primary: FundamentalProviderBase,
        fallback: FundamentalProviderBase,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self.name = f"{primary.name}+{fallback.name}"

    def get_financial_ratios(self, ticker: str) -> FinancialRatios:
        # 1. Primary — les exceptions (Quota/Unavailable) remontent intactes.
        r_primary = self._primary.get_financial_ratios(ticker)

        if not _needs_backfill(r_primary):
            return r_primary

        # 2. Fallback — ici on SWALLOW les ProviderError : le primary tient déjà
        # un résultat (éventuellement vide), on évite d'escalader un problème
        # YF (ex: breaker OPEN) en faillite de run.
        try:
            r_fallback = self._fallback.get_financial_ratios(ticker)
        except ProviderError as e:
            logger.warning(
                f"[{self.name}] fallback {self._fallback.name} indisponible "
                f"pour {ticker}: {e}"
            )
            return r_primary
        except Exception as e:
            logger.warning(
                f"[{self.name}] fallback {self._fallback.name} crash "
                f"pour {ticker}: {e}"
            )
            return r_primary

        # 3. Merge — primary gagne, fallback comble les None.
        return _merge(r_primary, r_fallback)
