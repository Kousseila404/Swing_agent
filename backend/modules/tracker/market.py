"""Accès aux prix de marché pour le tracker.

Stratégie adaptative :
  - Si BROKER_MODE=alpaca : Alpaca Data API (temps réel, pas de délai).
  - Sinon pendant heures de marché US : yfinance intraday (1m, ~15 min retard).
  - Sinon : yfinance EOD (dernière clôture officielle).
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pandas as pd
import yfinance as yf

import config

from .state import FETCH_TIMEOUT, logger

# Cache mémoire des taux de change (5 min) — évite de marteler yfinance à
# chaque requête dashboard pour une donnée qui ne bouge pas seconde par
# seconde. Contrairement aux prix actions (aucune couche de cache, voir
# get_current_price_detailed), un taux FX stale de quelques minutes est
# un compromis acceptable.
_FX_CACHE: dict[str, tuple[float, float]] = {}  # pair -> (rate, fetched_epoch)
_FX_CACHE_TTL_SECONDS = 300.0


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
    # 1) Source de vérité : Alpaca clock si dispo
    try:
        from modules.broker_gateway import AlpacaBroker, get_broker
        broker = get_broker()
        if isinstance(broker, AlpacaBroker):
            client = broker._get_client()
            clock = client.get_clock()
            return bool(clock.is_open)
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


# Fenêtre connue d'historique intraday 1-minute yfinance (Upgrade 4 —
# journal d'exécution my_portfolio, reconstruction de prix de référence
# passés). Au-delà, repli sur la clôture journalière.
_INTRADAY_MAX_AGE_DAYS = 30.0


def get_historical_price(ticker: str, at: datetime) -> tuple[float | None, str | None]:
    """Retourne (prix, resolution) pour `ticker` à l'instant passé `at`.

    Extension additive pour le journal d'exécution (Upgrade 4) —
    reconstruction d'un prix de référence a posteriori, distinct de
    `get_current_price_detailed` (prix "maintenant").

    `resolution` :
      - "intraday" : barre 1-minute la plus proche de `at`, si `at` est dans
        la fenêtre `_INTRADAY_MAX_AGE_DAYS` (limite connue de yfinance).
      - "daily_close" : repli clôture journalière la plus proche AVANT `at`,
        si l'intraday est indisponible ou `at` trop ancien.
    `(None, None)` si les deux échouent — jamais de valeur approximative
    silencieuse.

    `at` doit être timezone-aware (offset explicite requis).
    """
    if at.tzinfo is None:
        raise ValueError("`at` doit être timezone-aware (offset explicite requis)")

    at_utc = at.astimezone(UTC)
    age_days = (datetime.now(UTC) - at_utc).total_seconds() / 86400

    if 0 <= age_days <= _INTRADAY_MAX_AGE_DAYS:
        try:
            hist = yf.Ticker(ticker).history(
                start=at_utc - timedelta(minutes=15),
                end=at_utc + timedelta(minutes=15),
                interval="1m",
                timeout=FETCH_TIMEOUT,
            )
            if not hist.empty:
                at_ts = pd.Timestamp(at_utc)
                nearest_pos = hist.index.get_indexer([at_ts], method="nearest")[0]
                price = float(hist["Close"].iloc[nearest_pos])
                return price, "intraday"
        except Exception as exc:
            logger.warning(f"[{ticker}] Erreur prix historique intraday à {at_utc} : {exc}")

    try:
        day_start = datetime(at_utc.year, at_utc.month, at_utc.day, tzinfo=UTC)
        hist = yf.Ticker(ticker).history(
            start=day_start - timedelta(days=10),
            end=day_start + timedelta(days=1),
            timeout=FETCH_TIMEOUT,
        )
        if not hist.empty:
            return float(hist["Close"].iloc[-1]), "daily_close"
        logger.warning(f"[{ticker}] Aucune donnée de prix historique disponible pour {at_utc}.")
    except Exception as exc:
        logger.warning(f"[{ticker}] Erreur prix historique daily_close à {at_utc} : {exc}")

    return None, None


def get_historical_fx_rate(pair: str, at: datetime) -> float | None:
    """Taux de change historique pour `pair` (ex: "EURUSD=X") à l'instant `at`.

    Contrairement à `get_fx_rate` (taux live, cache 5 min), interroge
    l'historique yfinance à la date de `at`. Repli automatique sur le taux
    de bourse valide le plus proche AVANT `at` si le jour exact est un jour
    férié FX ou que les données sont incomplètes (cas limite "FX historique
    manquant" de la spec) — jamais de valeur codée en dur.

    `at` doit être timezone-aware. `None` si aucune donnée sur la fenêtre.
    """
    if at.tzinfo is None:
        raise ValueError("`at` doit être timezone-aware (offset explicite requis)")

    at_utc = at.astimezone(UTC)
    try:
        day_start = datetime(at_utc.year, at_utc.month, at_utc.day, tzinfo=UTC)
        hist = yf.Ticker(pair).history(
            start=day_start - timedelta(days=10),
            end=day_start + timedelta(days=1),
            timeout=FETCH_TIMEOUT,
        )
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
        logger.warning(f"[FX] Aucune donnée historique pour {pair} à {at_utc}.")
    except Exception as exc:
        logger.warning(f"[FX] Erreur taux historique {pair} à {at_utc} : {exc}")

    return None
