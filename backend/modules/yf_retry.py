"""yfinance retry helper — backoff exponentiel sur erreurs transitoires.

Pattern d'usage côté caller :

    from modules.yf_retry import yf_safe_call, YFNonRetriable

    try:
        df = yf_safe_call(
            lambda: yf.download(ticker, period="14mo", interval="1d",
                                progress=False, auto_adjust=True, threads=False),
            label=f"download {ticker}",
        )
    except YFNonRetriable:
        # breaker OPEN ou erreur non retriable → on log et on passe à autre chose
        df = None

Différences vs un retry naïf :
 • Le circuit breaker yfinance (yf_breaker) est respecté avant chaque tentative
   ET les rate-limits 429/IP-ban font tripper le breaker au lieu d'être
   retentés (un retry sur 429 aggrave le ban).
 • Le backoff est exponentiel (2s → 4s → 8s) avec jitter ±25 % pour éviter
   le thundering-herd entre workers.
 • La dernière exception est propagée si toutes les tentatives échouent —
   pas de silent return None.

Pourquoi pas tenacity ? Dépendance supplémentaire pour 60 LOC. On garde
l'implémentation minimale et auditeable.
"""
from __future__ import annotations

import random
import time
from collections.abc import Callable

from modules.log import logger
from modules.yf_circuit_breaker import RateLimitTripped, yf_breaker


class YFNonRetriable(RuntimeError):
    """Erreur définitive — breaker OPEN ou tentatives épuisées.

    Le caller doit gérer ce cas (fallback vers cache local, skip de ticker,
    etc.) plutôt que de réessayer.
    """


# Exceptions considérées comme transitoires (network blip, DNS, timeout
# de lecture). Réessayer a du sens. PAS 429 — celles-là sont catchées par
# le breaker et propagent comme RateLimitTripped.
def _is_transient(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in {
        "ConnectionError", "Timeout", "ConnectTimeout", "ReadTimeout",
        "ChunkedEncodingError", "RemoteDisconnected", "ProtocolError",
        "IncompleteRead",
    }:
        return True
    # requests.exceptions.RequestException hiérarchie : on chope par nom pour
    # ne pas avoir à importer requests ici (yfinance peut l'importer en lazy).
    msg = str(exc).lower()
    if any(s in msg for s in ("temporarily unavailable", "connection reset",
                              "connection aborted", "timed out")):
        return True
    return False


def _backoff_delay(attempt: int, base: float, cap: float) -> float:
    """Exp backoff avec jitter ±25 %. attempt = 0-based."""
    raw = min(cap, base * (2 ** attempt))
    return raw * random.uniform(0.75, 1.25)


def yf_safe_call[T](
    fn: Callable[[], T],
    *,
    label: str = "yfinance call",
    retries: int = 2,
    backoff_base: float = 2.0,
    backoff_cap: float = 30.0,
) -> T:
    """Exécute `fn()` avec breaker + retries sur erreurs transitoires.

    Args:
        fn: Closure 0-arg encapsulant l'appel yfinance.
        label: Description courte pour les logs (ticker, fonction, etc.).
        retries: Nombre de tentatives SUPPLÉMENTAIRES après la 1re (donc
                 retries=2 = 3 tentatives au total).
        backoff_base: Délai initial en secondes (doublé à chaque retry).
        backoff_cap: Plafond du délai.

    Raises:
        YFNonRetriable: breaker OPEN ou tentatives épuisées.
        Exception: l'exception originale si elle n'est pas transitoire et
                   pas un rate-limit.
    """
    total_attempts = retries + 1
    last_exc: BaseException | None = None
    for attempt in range(total_attempts):
        try:
            yf_breaker.ensure_closed()
        except RateLimitTripped as e:
            logger.warning(
                "[yf_retry] %s skipped — breaker OPEN: %s", label, e,
            )
            raise YFNonRetriable(f"breaker open: {e}") from e
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — on classifie ci-dessous
            last_exc = e
            # Rate-limit → trip breaker, abandon immédiat (pas de retry).
            if yf_breaker.record(e):
                logger.warning(
                    "[yf_retry] %s tripped breaker (%s): %s",
                    label, type(e).__name__, e,
                )
                raise YFNonRetriable(f"rate-limit / ban: {e}") from e
            # Transitoire : retry si reste des tentatives, sinon tomber dans
            # le YFNonRetriable post-loop.
            if _is_transient(e):
                if attempt < total_attempts - 1:
                    delay = _backoff_delay(attempt, backoff_base, backoff_cap)
                    logger.info(
                        "[yf_retry] %s attempt %d/%d failed (%s), retry in %.1fs",
                        label, attempt + 1, total_attempts,
                        type(e).__name__, delay,
                    )
                    time.sleep(delay)
                    continue
                # Dernière tentative transitoire échouée → on sort de la boucle
                # pour wrapper en YFNonRetriable (signal "skip ce ticker").
                break
            # Erreur définitive (non transitoire, non rate-limit) → propage.
            raise
    # Tentatives épuisées sur erreur transitoire.
    logger.warning(
        "[yf_retry] %s exhausted %d attempts, last error: %s",
        label, total_attempts, last_exc,
    )
    raise YFNonRetriable(f"exhausted {total_attempts} attempts: {last_exc}") from last_exc
