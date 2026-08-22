"""Journal d'exécution — book `my_portfolio` (Upgrade 4, incrément 2).

CRUD + calcul de référence pour `data/my_portfolio_executions.csv`. Log
volontaire ASSISTÉ (formulaire côté frontend, à venir) : ce dépôt n'a
aucune intégration API eToro (`broker_gateway.py` ne supporte qu'Alpaca) —
il n'existe aucun moyen de capturer un fill automatiquement. Ce module ne
fait que calculer, a posteriori, le contexte marché d'un fill SAISI
manuellement (marché ouvert ? prix/FX de référence historiques ? écart
en bps/$ ?) — pas une capture automatique.

Réutilise `exchange_hours.is_open` (Upgrade 4, incrément 1) et
`modules.tracker.market.get_historical_price`/`get_historical_fx_rate`
(ce même incrément). Formule de slippage : même **concept** que
`Slippage_Bps` de `trade_journal.csv` (Lot 13, `modules/utils.py`),
`(fill - référence) / référence × 10000`, mais la "référence" ici est un
prix de marché reconstruit a posteriori (`my_portfolio` n'a pas de reco
système), pas la reco TITAN.

Schéma CSV propre à ce fichier — pas de réutilisation de
`modules.utils.ensure_csv_schema` (câblée en dur sur le schéma
`trade_journal.csv`, `CSV_SCHEMA`/`CSV_PATH` du même module, donc pas
paramétrable pour un second fichier). FileLock + écriture atomique
`.tmp`→rename, même pattern que
`modules.tracker.evaluation.load_journal`/`save_journal`.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from filelock import FileLock

from modules import exchange_hours
from modules.log import logger
from modules.tracker.market import get_historical_fx_rate, get_historical_price

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = _PROJECT_ROOT / "data" / "my_portfolio_executions.csv"
CSV_LOCK_PATH = _PROJECT_ROOT / "data" / "my_portfolio_executions.csv.lock"

CSV_SCHEMA = [
    "Date",
    "Ticker",
    "Direction",
    "Shares",
    "Fill_Price_Native",
    "Currency",
    "Executed_At",
    "Primary_Exchange",
    "Market_Open_At_Fill",
    "Reference_Price_USD",
    "Reference_Price_Resolution",
    "Slippage_Bps",
    "Slippage_Usd",
    "Notes",
]

# Même paire/convention que routers/my_portfolio.py::_FX_PAIR_FOR_CURRENCY —
# dupliqué plutôt qu'importé pour garder ce module indépendant de la couche
# HTTP (le CRUD doit rester testable/utilisable sans FastAPI, même principe
# que my_portfolio_earnings.py vis-à-vis du router).
_FX_PAIR_FOR_CURRENCY = {"EUR": "EURUSD=X", "HKD": "USDHKD=X"}


def _historical_usd_multiplier(currency: str, at: datetime) -> float | None:
    """×→USD au taux FX HISTORIQUE à `at` (pas le taux live de `_usd_multiplier`).

    Fail-open : `None` si la devise n'a pas de paire configurée ou si le
    taux historique est indisponible — l'appelant doit alors renoncer au
    calcul de référence plutôt que d'halluciner une conversion.

    Repli implicite sur le jour de bourse valide le plus proche <= `at` :
    porté par `get_historical_fx_rate` lui-même (jour férié FX). Ce module
    ne distingue pas ce repli d'un taux exact par un flag dédié — le champ
    CSV `Reference_Price_Resolution` (intraday/daily_close) couvre déjà le
    cas d'imprécision le plus significatif (le prix de référence, pas le
    taux FX, dont l'écart jour-à-jour est marginal pour ce diagnostic).
    """
    if currency == "USD":
        return 1.0
    pair = _FX_PAIR_FOR_CURRENCY.get(currency)
    if pair is None:
        logger.error(f"[my_portfolio_executions] Devise {currency} sans paire FX configurée")
        return None
    rate, _date_iso = get_historical_fx_rate(pair, at)
    if rate is None or rate <= 0:
        return None
    return rate if currency == "EUR" else 1.0 / rate


def compute_reference(
    *,
    price_ticker: str,
    currency: str,
    primary_exchange: str,
    direction: str,
    shares: float,
    fill_price_native: float,
    executed_at: datetime,
) -> dict[str, Any]:
    """Calcule le contexte marché a posteriori d'un fill.

    Fail-open : `reference_price_usd`/`slippage_bps`/`slippage_usd` restent
    `None` si le prix ou le FX historiques sont indisponibles — jamais de
    valeur approximative affichée comme certaine (même principe que
    `routers/my_portfolio.py::_safe_price`).

    Signe de `slippage_bps`/`slippage_usd` : POSITIF = exécution coûteuse,
    quel que soit le sens du trade — un BUY payé plus cher que la référence
    ET un SELL vendu moins cher que la référence sont tous deux comptés
    positivement (déviation documentée : la spec donne la formule brute
    `(fill-ref)/ref×10000` "signée selon la direction" sans préciser le
    sens du flip ; ce choix aligne le signe sur "coût" plutôt que sur "sens
    du prix", cohérent avec l'exemple CNC de la spec — BUY, fill>ref,
    493 bps positif — et avec le vocabulaire "coût d'exécution cumulé" de
    la tuile agrégée).
    """
    if direction not in ("BUY", "SELL"):
        raise ValueError(f"Direction invalide : {direction!r} (attendu BUY ou SELL)")

    market_open = exchange_hours.is_open(primary_exchange, executed_at)
    native_ref_price, resolution = get_historical_price(price_ticker, executed_at)
    fx = _historical_usd_multiplier(currency, executed_at)

    reference_price_usd: float | None = None
    slippage_bps: float | None = None
    slippage_usd: float | None = None
    if native_ref_price is not None and fx is not None and native_ref_price * fx > 0:
        reference_price_usd = native_ref_price * fx
        fill_price_usd = fill_price_native * fx
        raw_bps = (fill_price_usd - reference_price_usd) / reference_price_usd * 10000.0
        slippage_bps = raw_bps if direction == "BUY" else -raw_bps
        slippage_usd = (slippage_bps / 10000.0) * reference_price_usd * shares

    return {
        "market_open_at_fill": market_open,
        "reference_price_usd": reference_price_usd,
        "reference_price_resolution": resolution,
        "slippage_bps": slippage_bps,
        "slippage_usd": slippage_usd,
    }


def _read_locked() -> pd.DataFrame:
    """Lecture SANS acquérir de lock — l'appelant doit déjà tenir `CSV_LOCK_PATH`."""
    if not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0:
        return pd.DataFrame(columns=CSV_SCHEMA)
    try:
        return pd.read_csv(CSV_PATH, dtype={"Ticker": str})
    except Exception as exc:
        logger.warning(f"[my_portfolio_executions] lecture CSV échouée : {exc}")
        return pd.DataFrame(columns=CSV_SCHEMA)


def load_executions() -> pd.DataFrame:
    """Lecture pure du journal (aucun calcul, aucun fetch réseau)."""
    with FileLock(str(CSV_LOCK_PATH), timeout=10):
        return _read_locked()


def append_execution(row: dict[str, Any]) -> pd.DataFrame:
    """Ajoute une row (déjà calculée, voir `log_execution`) au CSV, sous lock.

    Écriture atomique `.tmp`→rename (même pattern que
    `modules.tracker.evaluation.save_journal`) : aucune corruption possible
    si le processus est interrompu en cours d'écriture.
    """
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_row = pd.DataFrame([row])[CSV_SCHEMA]
    with FileLock(str(CSV_LOCK_PATH), timeout=10):
        df = _read_locked()
        # `df` vide (fichier absent/juste initialisé) déclenche un
        # FutureWarning pandas sur pd.concat (dtypes all-NA) — inutile de le
        # traverser, la nouvelle row EST le DataFrame dans ce cas.
        df = new_row if df.empty else pd.concat([df, new_row], ignore_index=True)
        tmp_path = CSV_PATH.with_suffix(".tmp")
        df.to_csv(tmp_path, index=False)
        tmp_path.replace(CSV_PATH)
    return df


def log_execution(
    *,
    ticker: str,
    price_ticker: str,
    direction: str,
    shares: float,
    fill_price_native: float,
    currency: str,
    executed_at: datetime,
    primary_exchange: str,
    notes: str = "",
) -> dict[str, Any]:
    """Calcule la référence marché d'un fill puis le journalise.

    `executed_at` doit être timezone-aware (offset explicite) — un
    datetime naïf est une source d'erreur silencieuse connue pour ce
    diagnostic (voir cas limite "erreur de fuseau à la saisie" de la
    spec), donc rejeté plutôt que supposé UTC ou local.

    Le journal reste indépendant de `my_portfolio_data.POSITIONS` (pas de
    contrainte de clé étrangère) — `ticker`/`price_ticker`/`currency`/
    `primary_exchange` sont fournis par l'appelant (résolus depuis la
    position au moment de la saisie), pas relookés ici : un fill reste un
    fait historique même si la ligne a depuis été soldée ou retirée du book.

    Retourne la row telle qu'écrite (dict), pour que l'appelant HTTP
    renvoie directement la ressource créée sans relire le CSV.
    """
    if executed_at.tzinfo is None:
        raise ValueError("`executed_at` doit être timezone-aware (offset explicite requis)")

    ref = compute_reference(
        price_ticker=price_ticker,
        currency=currency,
        primary_exchange=primary_exchange,
        direction=direction,
        shares=shares,
        fill_price_native=fill_price_native,
        executed_at=executed_at,
    )

    row: dict[str, Any] = {
        "Date": executed_at.date().isoformat(),
        "Ticker": ticker,
        "Direction": direction,
        "Shares": shares,
        "Fill_Price_Native": fill_price_native,
        "Currency": currency,
        "Executed_At": executed_at.isoformat(),
        "Primary_Exchange": primary_exchange,
        "Market_Open_At_Fill": ref["market_open_at_fill"],
        "Reference_Price_USD": ref["reference_price_usd"],
        "Reference_Price_Resolution": ref["reference_price_resolution"],
        "Slippage_Bps": ref["slippage_bps"],
        "Slippage_Usd": ref["slippage_usd"],
        "Notes": notes,
    }
    append_execution(row)
    return row


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    """Agrégats `GET /api/my_portfolio/executions` (bloc `summary`) : coût
    total $ cumulé, décompte/% de fills hors séance, top 3 pires exécutions
    par `abs(Slippage_Bps)`.

    Fail-open : bloc à zéro si le journal est vide ; les fills sans
    slippage calculable (référence indisponible au moment du log) sont
    exclus de `total_slippage_usd`/`worst_fills` mais comptés dans
    `fills_count`/`off_hours_fills_count` (le statut marché ne dépend pas
    du prix de référence).
    """
    if df.empty:
        return {
            "total_slippage_usd": 0.0,
            "fills_count": 0,
            "off_hours_fills_count": 0,
            "off_hours_fills_pct": 0.0,
            "worst_fills": [],
        }

    fills_count = len(df)
    market_open = df["Market_Open_At_Fill"].astype(bool)
    off_hours_count = int((~market_open).sum())

    valid = df[df["Slippage_Bps"].notna()]
    total_slippage_usd = float(valid["Slippage_Usd"].sum()) if not valid.empty else 0.0
    worst_fills = (
        valid.reindex(valid["Slippage_Bps"].abs().sort_values(ascending=False).index)
        .head(3)[["Date", "Ticker", "Direction", "Slippage_Bps", "Slippage_Usd", "Notes"]]
        .to_dict("records")
        if not valid.empty
        else []
    )

    return {
        "total_slippage_usd": total_slippage_usd,
        "fills_count": fills_count,
        "off_hours_fills_count": off_hours_count,
        "off_hours_fills_pct": (off_hours_count / fills_count * 100.0) if fills_count else 0.0,
        "worst_fills": worst_fills,
    }
