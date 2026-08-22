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

# Fenêtre yfinance où l'historique 1-minute est disponible (limite connue de
# la source) — au-delà, `get_historical_price` replie sur le close journalier.
_HISTORICAL_INTRADAY_MAX_AGE_DAYS = 30

# Nombre de jours en arrière qu'`get_historical_fx_rate` accepte de remonter
# pour trouver le dernier taux de change connu quand la date exacte est un
# jour férié FX / un trou de données Yahoo.
_FX_HISTORICAL_FALLBACK_MAX_DAYS = 7

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


def get_historical_price(ticker: str, at: datetime) -> tuple[float | None, str]:
    """Retourne (prix, resolution) pour `ticker` à l'instant historique `at`.

    Extension additive (Upgrade 4 — journal d'exécution
    `my_portfolio`, docs/UPGRADES_MY_PORTFOLIO.md) : `get_current_price_detailed`
    ne sait fetcher que "maintenant", ici `at` est une date/heure passée
    arbitraire (fill à reconstruire a posteriori).

    `resolution` ∈ {"intraday", "daily_close"} : "intraday" (barre 1-minute
    la plus proche de `at`) seulement si `at` est dans la fenêtre
    `_HISTORICAL_INTRADAY_MAX_AGE_DAYS` où yfinance sert un historique
    intraday — au-delà (ou si le fetch intraday échoue/est vide), repli sur
    le close journalier de la date de `at` (résolution dégradée mais seule
    disponible, à afficher tel quel côté UI plutôt que de laisser croire à
    une précision intraday inexistante).

    `at` doit être timezone-aware (même contrainte que
    `exchange_hours.is_open` — un datetime naïf est une source d'erreur
    silencieuse pour ce diagnostic, donc rejeté plutôt que supposé UTC/local).
    """
    if at.tzinfo is None:
        raise ValueError("`at` doit être timezone-aware (offset explicite requis)")

    age_days = (datetime.now(UTC) - at.astimezone(UTC)).days
    if 0 <= age_days <= _HISTORICAL_INTRADAY_MAX_AGE_DAYS:
        price = _intraday_price_near(ticker, at)
        if price is not None:
            return price, "intraday"

    return _daily_close_price(ticker, at), "daily_close"


def _intraday_price_near(ticker: str, at: datetime) -> float | None:
    try:
        window = timedelta(minutes=90)
        hist = yf.Ticker(ticker).history(
            start=at - window, end=at + window, interval="1m", timeout=FETCH_TIMEOUT
        )
        if hist.empty:
            return None
        diffs = [abs(ts - at) for ts in hist.index]
        closest_pos = diffs.index(min(diffs))
        return float(hist["Close"].iloc[closest_pos])
    except Exception as exc:
        logger.warning(f"[{ticker}] Erreur historique intraday @ {at}: {exc}")
        return None


def _daily_close_price(ticker: str, at: datetime) -> float | None:
    try:
        day = at.date()
        start = datetime(day.year, day.month, day.day, tzinfo=at.tzinfo)
        hist = yf.Ticker(ticker).history(
            start=start, end=start + timedelta(days=1), timeout=FETCH_TIMEOUT
        )
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as exc:
        logger.warning(f"[{ticker}] Erreur close journalier @ {at}: {exc}")
        return None


def get_historical_fx_rate(pair: str, at: datetime) -> tuple[float | None, bool]:
    """Taux de change historique (pas live) pour `pair` à l'instant `at`.

    Retourne (taux, approximated). `approximated=True` signale que la date
    exacte de `at` n'avait pas de donnée FX (jour férié FX / trou Yahoo) et
    qu'un repli sur le dernier taux de bourse valide avant `at` a été utilisé
    à la place (voir cas limite "FX historique manquant", Upgrade 4,
    docs/UPGRADES_MY_PORTFOLIO.md) — à distinguer d'un `None` (aucune donnée
    trouvable même en remontant `_FX_HISTORICAL_FALLBACK_MAX_DAYS` jours).

    `at` doit être timezone-aware (même contrainte que `get_historical_price`).
    """
    if at.tzinfo is None:
        raise ValueError("`at` doit être timezone-aware (offset explicite requis)")

    try:
        target_date = at.date()
        start = at - timedelta(days=_FX_HISTORICAL_FALLBACK_MAX_DAYS)
        hist = yf.Ticker(pair).history(
            start=start, end=at + timedelta(days=1), timeout=FETCH_TIMEOUT
        )
        if hist.empty:
            return None, False

        exact = hist.loc[hist.index.date == target_date]
        if not exact.empty:
            return float(exact["Close"].iloc[-1]), False

        before = hist.loc[hist.index.date < target_date]
        if not before.empty:
            return float(before["Close"].iloc[-1]), True

        return None, False
    except Exception as exc:
        logger.warning(f"[FX-hist] Erreur taux historique {pair} @ {at}: {exc}")
        return None, False
