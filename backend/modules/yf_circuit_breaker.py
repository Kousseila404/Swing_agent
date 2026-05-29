"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  MODULE — yfinance Circuit-Breaker                                           ║
║                                                                              ║
║  Intercepte les erreurs de type HTTP 429 ("Too Many Requests") et les bans  ║
║  IP renvoyés par Yahoo Finance. Dès qu'une première détection est          ║
║  confirmée, le breaker bascule en état OPEN : toute tentative ultérieure    ║
║  échoue immédiatement avec `RateLimitTripped` au lieu de s'acharner en      ║
║  boucle — ce qui aggraverait le ban côté Yahoo.                             ║
║                                                                              ║
║  Utilisation :                                                               ║
║    from modules.yf_circuit_breaker import yf_breaker, RateLimitTripped       ║
║                                                                              ║
║    try:                                                                      ║
║        yf_breaker.ensure_closed()                                            ║
║        df = yf.download(...)                                                 ║
║    except RateLimitTripped:                                                  ║
║        # Abort proprement, pas de retry.                                    ║
║        ...                                                                   ║
║    except Exception as e:                                                    ║
║        yf_breaker.record(e)        # Trip si 429, sinon no-op.              ║
║        raise                                                                 ║
║                                                                              ║
║  Partagé par `universe_engine` et `sector_metrics` — le ban IP est global    ║
║  à tout le process, donc un seul singleton module-level suffit.             ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

from modules.log import logger

# yfinance ≥ 0.2.x expose cette classe ; on l'importe paresseusement pour rester
# compatible avec les versions antérieures (fallback sur détection regex).
try:
    from yfinance.exceptions import YFRateLimitError
except Exception:  # pragma: no cover — defensive
    class YFRateLimitError(Exception):  # type: ignore[no-redef]
        """Shim si la classe n'existe pas dans la version installée."""


# Regex pour détecter un 429 / ban dans un message d'exception ou une réponse
# HTTP brute. Yahoo retourne parfois "Edge: Too Many Requests" (Akamai WAF),
# parfois "rate limit exceeded" — on couvre les deux formulations.
_RATE_LIMIT_PATTERNS = re.compile(
    r"(429|too\s*many\s*requests|rate\s*limit|ip.{0,20}ban|forbidden)",
    flags=re.IGNORECASE,
)


class RateLimitTripped(RuntimeError):
    """Le circuit-breaker yfinance est OPEN — abandon immédiat."""


@dataclass
class _BreakerState:
    """État interne du breaker. Accédé sous lock."""
    tripped: bool = False
    tripped_at: float | None = None
    reason: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


def _looks_like_rate_limit(exc: BaseException) -> bool:
    """
    True ssi l'exception ressemble à un 429 / IP ban yfinance.

    Trois heuristiques cumulatives :
      1. isinstance de YFRateLimitError (chemin canonique).
      2. Attribut response.status_code == 429 (requests.HTTPError).
      3. Pattern regex dans str(exc) (robustesse sur wrappings divers).
    """
    if isinstance(exc, YFRateLimitError):
        return True
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if status in (429, 403):
        return True
    msg = str(exc) or ""
    return bool(_RATE_LIMIT_PATTERNS.search(msg))


class YFCircuitBreaker:
    """
    Circuit-breaker process-wide pour yfinance.

    Lot 11 (audit data) — auto-reset après cooldown :
      • Avant : "tripped jusqu'à fin du process" → API long-running coincée
        plusieurs heures même si le ban Yahoo dure 10 min.
      • Maintenant : auto-close après `cooldown_seconds` (default 600s = 10min).
        Le caller suivant retest naturellement et le breaker re-trip si Yahoo
        est encore en ban.

    Logique : un ban IP yfinance dure typiquement 5-30 minutes (mesuré via
    plusieurs runs prod). 10 min = sweet spot entre récupération réaliste et
    pas marteler Yahoo trop tôt.
    """

    # Délai après lequel le breaker se réinitialise automatiquement.
    # Override possible via constructor pour tests.
    DEFAULT_COOLDOWN_SECONDS = 600

    def __init__(self, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS) -> None:
        self._state = _BreakerState()
        self._cooldown = cooldown_seconds

    def is_tripped(self) -> bool:
        with self._state.lock:
            return self._maybe_auto_reset_unlocked()

    def _maybe_auto_reset_unlocked(self) -> bool:
        """Appelé sous lock — auto-close si tripped depuis > cooldown.
        Retourne l'état tripped *après* éventuel reset."""
        if not self._state.tripped:
            return False
        if self._state.tripped_at is None:
            return True
        age = time.time() - self._state.tripped_at
        if age >= self._cooldown:
            logger.info(
                f"[YFBreaker] 🔄 Auto-reset après {age:.0f}s de cooldown "
                f"(seuil {self._cooldown}s). yfinance retry autorisé."
            )
            self._state.tripped = False
            self._state.tripped_at = None
            self._state.reason = ""
            return False
        return True

    def ensure_closed(self) -> None:
        """Raise si le breaker est OPEN — à appeler avant chaque requête yf.

        Effectue d'abord un check d'auto-reset basé sur la cooldown.
        """
        with self._state.lock:
            if self._maybe_auto_reset_unlocked():
                raise RateLimitTripped(
                    f"yfinance circuit-breaker OPEN (tripped at "
                    f"{self._format_tripped_at()}): {self._state.reason}"
                )

    def record(self, exc: BaseException) -> bool:
        """
        Inspecte une exception survenue pendant un call yfinance.

        Returns True si le breaker vient de tripper (ou était déjà tripped).
        Le caller doit alors propager/abandonner au lieu de réessayer.
        """
        if not _looks_like_rate_limit(exc):
            return False
        with self._state.lock:
            if not self._state.tripped:
                self._state.tripped = True
                self._state.tripped_at = time.time()
                self._state.reason = f"{type(exc).__name__}: {str(exc)[:200]}"
                logger.critical(
                    "[YFBreaker] 🚨 OPEN — yfinance rate-limit / IP ban détecté : "
                    f"{self._state.reason}. Toutes les requêtes yfinance "
                    "suivantes seront refusées immédiatement."
                )
        return True

    def reset(self) -> None:
        """Réinitialise le breaker (tests + retry manuel après backoff côté humain)."""
        with self._state.lock:
            self._state.tripped = False
            self._state.tripped_at = None
            self._state.reason = ""

    def snapshot(self) -> dict:
        with self._state.lock:
            now = time.time()
            tripped = self._state.tripped
            tripped_at = self._state.tripped_at
            return {
                "tripped":          tripped,
                "tripped_at":       tripped_at,
                "tripped_age_sec":  (round(now - tripped_at, 1)
                                     if tripped_at is not None else None),
                "cooldown_seconds": self._cooldown,
                "auto_reset_in_sec": (max(0, round(self._cooldown - (now - tripped_at), 1))
                                       if tripped and tripped_at is not None else None),
                "reason":           self._state.reason,
            }

    def _format_tripped_at(self) -> str:
        if self._state.tripped_at is None:
            return "?"
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._state.tripped_at))


yf_breaker = YFCircuitBreaker()
