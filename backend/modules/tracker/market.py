"""Accès aux prix de marché pour le tracker.

Stratégie adaptative :
  - Si BROKER_MODE=alpaca : Alpaca Data API (temps réel, pas de délai).
  - Sinon pendant heures de marché US : yfinance intraday (1m, ~15 min retard).
  - Sinon : yfinance EOD (dernière clôture officielle).
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import yfinance as yf

import config

from .state import FETCH_TIMEOUT, logger

# Fenêtre au-delà de laquelle yfinance ne sert plus d'historique intraday
# (1m) — limite connue du fournisseur, pas une valeur choisie ici.
_INTRADAY_HISTORY_WINDOW_DAYS = 30

# Cache mémoire des taux de change (5 min) — évite de marteler yfinance à
# chaque requête dashboard pour une donnée qui ne bouge pas seconde par
# seconde. Contrairement aux prix actions (aucune couche de cache, voir
# get_current_price_detailed), un taux FX stale de quelques minutes est
# un compromis acceptable.
_FX_CACHE: dict[str, tuple[float, float]] = {}  # pair -> (rate, fetched_epoch)
_FX_CACHE_TTL_SECONDS = 300.0

# Cache de l'horloge Alpaca — is_market_hours() est appelé une fois PAR ticker
# et PAR cycle tracker (via get_current_price_detailed) ; sans cache, N
# positions = N appels réseau get_clock() pour une réponse qui ne change
# qu'à l'open/close. 60 s = granularité largement suffisante.
_ALPACA_CLOCK_CACHE: tuple[bool, float] | None = None  # (is_open, fetched_epoch)
_ALPACA_CLOCK_TTL_SECONDS = 60.0


def get_fx_rate(pair: str) -> float | None:
    """Taux de change live pour une paire yfinance (ex: "EURUSD=X", "USDHKD=X").

    Retourne le dernier taux connu (même expiré) si le fetch échoue, plutôt
    que None, pour éviter de casser l'affichage sur un simple hoquet réseau.
    """
    now = time.time()
    cached = _FX_CACHE.get(pair)
    if cached is not None and (now - cached[1]) < _FX_CACHE_TTL_SECONDS:
        return cached[0]

    try:
        hist = yf.Ticker(pair).history(period="1d", timeout=FETCH_TIMEOUT)
        if not hist.empty:
            rate = float(hist["Close"].iloc[-1])
            _FX_CACHE[pair] = (rate, now)
            return rate
        logger.warning(f"[FX] Aucune donnée pour {pair}.")
    except Exception as exc:
        logger.warning(f"[FX] Erreur taux {pair} : {exc}")

    return cached[0] if cached is not None else None


def is_market_hours() -> bool:
    """Retourne True si les marchés US sont actuellement ouverts.

    Phase 7 audit (2026-05-06) — robustesse DST + jours fériés US :
      1. Si AlpacaBroker disponible et configuré → délègue à `clock.is_open`
         (autorité officielle, gère DST + holidays + half-days).
      2. Sinon : heuristique zoneinfo (pas pytz, qui devine mal le DST gap).
         Lun-ven 09:30-16:00 NY. NOTE : ne gère PAS les jours fériés US, ne
         gère PAS les half-days (close 13:00 veille de Noël/Thanksgiving).
         Pour ces cas-là, configurer Alpaca.
    """
    # 1) Source de vérité : Alpaca clock si dispo (cachée 60 s)
    global _ALPACA_CLOCK_CACHE
    try:
        from modules.broker_gateway import AlpacaBroker, get_broker
        broker = get_broker()
        if isinstance(broker, AlpacaBroker):
            now_epoch = time.time()
            if (
                _ALPACA_CLOCK_CACHE is not None
                and (now_epoch - _ALPACA_CLOCK_CACHE[1]) < _ALPACA_CLOCK_TTL_SECONDS
            ):
                return _ALPACA_CLOCK_CACHE[0]
            client = broker._get_client()
            is_open = bool(client.get_clock().is_open)
            _ALPACA_CLOCK_CACHE = (is_open, now_epoch)
            return is_open
    except Exception:
        pass  # fallback heuristique

    # 2) Heuristique zoneinfo (Python 3.9+, gère DST natif)
    try:
        try:
            from zoneinfo import ZoneInfo  # py3.9+
        except ImportError:
            import pytz as _pytz
            ZoneInfo = _pytz.timezone  # type: ignore[misc,assignment]  # noqa: N806  (fallback py<3.9)

        ny = ZoneInfo("America/New_York")
        now = datetime.now(ny)
        if now.weekday() >= 5:
            return False
        # Open : 09:30 → close : 16:00 (heure NY)
        minutes_since_midnight = now.hour * 60 + now.minute
        return 9 * 60 + 30 <= minutes_since_midnight < 16 * 60
    except Exception:
        return False


def get_current_price(ticker: str) -> float | None:
    """Retourne le prix le plus récent disponible pour un ticker.

    Fallback automatique sur EOD si l'appel intraday échoue.
    Retourne None si toutes les tentatives échouent — trade reste OPEN.
    """
    price, _as_of, _fetched_at = get_current_price_detailed(ticker)
    return price


def get_current_price_detailed(
    ticker: str, use_alpaca: bool = True
) -> tuple[float | None, str | None, str]:
    """Retourne (prix, as_of, fetched_at).

    `fetched_at` : horodatage de cet appel (le chemin de fetch n'a AUCUNE
    couche de cache — chaque appel tape yfinance/Alpaca en direct).
    `as_of` : horodatage de la barre de prix elle-même, tel que renvoyé par
    la source. Une donnée "fraîchement fetchée" (fetched_at = maintenant)
    peut quand même porter un `as_of` vieux de plusieurs heures si la source
    elle-même sert un prix retardé/périmé — ce champ permet de distinguer
    les deux et de repérer les tickers qui ne se rafraîchissent pas
    correctement côté fournisseur, plutôt que de le découvrir en comparant
    manuellement avec un broker externe.

    `use_alpaca` : mettre à False pour forcer yfinance même si
    BROKER_MODE=alpaca. Audit 2026-08-21 : le plan Alpaca gratuit ne sert
    que le carnet IEX (une seule place), ce qui dérive de plusieurs % vs le
    NBBO consolidé sur les tickers peu liquides (FMX/HRTG constatés à
    ±7 %, PSX plus liquide à ±3 %) — confirmé en comparant les réponses
    Alpaca brutes à un broker externe. Sans intérêt pour des positions qui
    ne sont de toute façon pas exécutées via Alpaca (book personnel
    my_portfolio) : yfinance (consolidé, juste retardé ~15 min) colle mieux
    au prix affiché par un broker tiers que l'IEX temps réel.
    """
    fetched_at = datetime.now(UTC).isoformat()

    # BROKER_MODE=alpaca → Alpaca Data API (temps réel, pas de délai)
    if use_alpaca and getattr(config, "BROKER_MODE", "paper").lower() == "alpaca":
        try:
            from modules.alpaca_data import get_latest_price
            price = get_latest_price(ticker)
            if price is not None and price > 0:
                logger.debug(f"[{ticker}] Prix Alpaca : {price:.4f} fetched_at={fetched_at}")
                # Cotation "latest quote" Alpaca : pas de barre historique à
                # dater séparément, la donnée EST l'instant du fetch.
                return price, fetched_at, fetched_at
        except Exception as _ae:
            logger.debug(f"[{ticker}] Alpaca price fallback yfinance : {_ae}")

    intraday = is_market_hours()
    try:
        if intraday:
            hist = yf.Ticker(ticker).history(
                period="1d", interval="1m", timeout=FETCH_TIMEOUT
            )
        else:
            hist = yf.Ticker(ticker).history(period="1d", timeout=FETCH_TIMEOUT)

        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
            as_of = hist.index[-1]
            as_of_iso = as_of.isoformat()
            age_s = (datetime.now(as_of.tzinfo) - as_of).total_seconds()
            logger.info(
                f"[{ticker}] price={price:.4f} as_of={as_of_iso} "
                f"fetched_at={fetched_at} age={age_s:.0f}s"
            )
            return price, as_of_iso, fetched_at

        logger.warning(f"[{ticker}] Aucune donnée de prix disponible.")
        return None, None, fetched_at

    except Exception as exc:
        if intraday:
            try:
                hist = yf.Ticker(ticker).history(period="1d", timeout=FETCH_TIMEOUT)
                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])
                    as_of_iso = hist.index[-1].isoformat()
                    logger.debug(
                        f"[{ticker}] Fallback EOD après échec intraday, as_of={as_of_iso}"
                    )
                    return price, as_of_iso, fetched_at
            except Exception:
                pass
        logger.error(f"[{ticker}] Erreur yfinance : {exc}")
        return None, None, fetched_at


def _nearest_historical_bar(
    symbol: str, at_utc: datetime, *, start: datetime, end: datetime, interval: str | None = None
) -> tuple[float, datetime] | None:
    """Barre `Close` la plus proche de `at_utc` dans `yf.Ticker(symbol).history(start, end)`.

    Retourne None si la fenêtre ne contient aucune donnée ou en cas d'erreur
    réseau — l'appelant décide du repli (fenêtre plus large, résolution
    dégradée), cette fonction ne fabrique jamais de valeur approximative
    elle-même.
    """
    kwargs: dict = {"start": start, "end": end, "timeout": FETCH_TIMEOUT}
    if interval is not None:
        kwargs["interval"] = interval
    try:
        hist = yf.Ticker(symbol).history(**kwargs)
    except Exception as exc:
        logger.warning(
            f"[{symbol}] Erreur historique {start.isoformat()}..{end.isoformat()} : {exc}"
        )
        return None
    if hist.empty:
        return None
    idx = hist.index.get_indexer([at_utc], method="nearest")[0]
    return float(hist["Close"].iloc[idx]), hist.index[idx].to_pydatetime()


def get_historical_price(ticker: str, at: datetime) -> tuple[float | None, str]:
    """Retourne (prix, résolution) pour `ticker` à l'instant historique `at`.

    Résolution :
      - "intraday" : barre 1 minute la plus proche de `at`, si `at` est dans
        les ~30 derniers jours (fenêtre intraday connue de yfinance).
      - "daily_close" : repli sur la clôture du jour de `at` — hors fenêtre
        intraday, ou si le fetch 1m échoue/est vide.

    `at` doit être timezone-aware (offset explicite) — un fill saisi avec un
    datetime naïf est une source d'erreur silencieuse connue pour ce
    diagnostic (Upgrade 4), donc rejeté plutôt que supposé UTC ou local.
    """
    if at.tzinfo is None:
        raise ValueError("`at` doit être timezone-aware (offset explicite requis)")

    at_utc = at.astimezone(UTC)
    age_days = (datetime.now(UTC) - at_utc).days

    if age_days <= _INTRADAY_HISTORY_WINDOW_DAYS:
        bar = _nearest_historical_bar(
            ticker, at_utc,
            start=at_utc - timedelta(minutes=5), end=at_utc + timedelta(minutes=5),
            interval="1m",
        )
        if bar is not None:
            return bar[0], "intraday"

    bar = _nearest_historical_bar(
        ticker, at_utc, start=at_utc - timedelta(days=1), end=at_utc + timedelta(days=1)
    )
    if bar is not None:
        return bar[0], "daily_close"

    logger.warning(f"[{ticker}] Aucun prix historique disponible pour {at_utc.isoformat()}.")
    return None, "daily_close"


def get_historical_fx_rate(pair: str, at: datetime) -> tuple[float | None, bool]:
    """Retourne (taux, is_approximate) pour la paire FX `pair` à l'instant `at`.

    `is_approximate=True` quand aucune donnée n'existe au jour même de `at`
    (jour férié FX, trou Yahoo) et qu'on retombe sur le taux du jour de
    bourse valide le plus proche via une fenêtre élargie — pour que
    l'appelant puisse le signaler plutôt que de laisser croire à une
    précision exacte.

    `at` doit être timezone-aware, même contrainte que `get_historical_price`.
    """
    if at.tzinfo is None:
        raise ValueError("`at` doit être timezone-aware (offset explicite requis)")

    at_utc = at.astimezone(UTC)

    bar = _nearest_historical_bar(
        pair, at_utc, start=at_utc - timedelta(days=1), end=at_utc + timedelta(days=1)
    )
    if bar is not None:
        return bar[0], False

    bar = _nearest_historical_bar(
        pair, at_utc, start=at_utc - timedelta(days=7), end=at_utc + timedelta(days=7)
    )
    if bar is not None:
        return bar[0], True

    logger.warning(f"[FX] Aucun taux historique disponible pour {pair} à {at_utc.isoformat()}.")
    return None, False
