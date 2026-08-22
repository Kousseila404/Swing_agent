"""Table horaires multi-place — book `my_portfolio` (Upgrade 4, incrément 1).

Généralisation additive de `modules.tracker.market.is_market_hours()` (qui
ne connaît que NYSE/NASDAQ) à 3 places : US, Euronext Paris, HKEX. N'existe
que pour le diagnostic a posteriori du journal d'exécution (Upgrade 4) — ne
touche pas et ne remplace pas `is_market_hours()`, utilisé ailleurs sur le
chemin critique TITAN/tracker.

HKEX a une pause déjeuner (09:30-12:00 puis 13:00-16:00 heure de Hong Kong)
qu'il faut modéliser comme deux sessions distinctes plutôt qu'un seul
intervalle continu, sous peine de classer un fill de 12h15 HKT comme
"marché ouvert" à tort (cas limite explicite de la spec).

DST : chaque place est ancrée sur son propre fuseau IANA (`zoneinfo`, même
pattern que `is_market_hours()`) — les horaires d'ouverture/fermeture sont
définis en heure LOCALE de la place, donc les transitions DST US/EU/HK
(non synchronisées entre elles) sont gérées nativement par la conversion
de fuseau, sans table de dates spéciales à maintenir.

Ne gère pas les jours fériés (mêmes limites documentées que
`is_market_hours()` — hors scope de ce diagnostic de saisie manuelle).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

# Place boursière -> fuseau IANA de cotation.
EXCHANGE_TIMEZONES: dict[str, ZoneInfo] = {
    "US": ZoneInfo("America/New_York"),
    "EURONEXT_PARIS": ZoneInfo("Europe/Paris"),
    "HKEX": ZoneInfo("Asia/Hong_Kong"),
}

# Place boursière -> liste de sessions (minutes depuis minuit, heure locale
# de la place, [start, end)). Plusieurs sessions par place = pause déjeuner.
_SESSIONS_MINUTES: dict[str, list[tuple[int, int]]] = {
    "US": [(9 * 60 + 30, 16 * 60)],
    "EURONEXT_PARIS": [(9 * 60, 17 * 60 + 30)],
    "HKEX": [(9 * 60 + 30, 12 * 60), (13 * 60, 16 * 60)],
}

EXCHANGES: frozenset[str] = frozenset(_SESSIONS_MINUTES)


def is_open(exchange: str, at: datetime) -> bool:
    """True si `exchange` est en séance à l'instant `at`.

    `at` doit être timezone-aware (offset explicite) — un datetime naïf est
    une source d'erreur silencieuse connue pour ce diagnostic (voir cas
    limite "erreur de fuseau à la saisie" de la spec), donc rejeté plutôt
    que supposé UTC ou local.
    """
    if exchange not in _SESSIONS_MINUTES:
        raise ValueError(f"Place boursière inconnue : {exchange!r} (attendu : {sorted(EXCHANGES)})")
    if at.tzinfo is None:
        raise ValueError("`at` doit être timezone-aware (offset explicite requis)")

    local = at.astimezone(EXCHANGE_TIMEZONES[exchange])
    if local.weekday() >= 5:
        return False

    minutes_since_midnight = local.hour * 60 + local.minute
    return any(
        start <= minutes_since_midnight < end
        for start, end in _SESSIONS_MINUTES[exchange]
    )
