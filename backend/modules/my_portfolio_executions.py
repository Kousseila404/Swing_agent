"""Journal de qualité d'exécution — book `my_portfolio` (Upgrade 4, incrément 3).

`my_portfolio` n'a aujourd'hui aucun journal de transactions (book statique,
`shares`/`entry_price` saisis à la main, sans horodatage d'exécution) — voir
docs/UPGRADES_MY_PORTFOLIO.md, Upgrade 4. Ce module fournit le CRUD du
journal `data/my_portfolio_executions.csv` : saisie manuelle d'un fill
(aucune intégration eToro), calcul du prix de référence historique
(`market.get_historical_price`/`get_historical_fx_rate`) et du slippage
associé (même formule que `Slippage_Bps` du book TITAN,
`modules/utils.py` — `(fill - ref) / ref × 10000`, signé selon la direction).

Pattern IO : identique à `modules/tracker/evaluation.py::load_journal`/
`save_journal` (CSV + `FileLock` + écriture atomique via `.tmp` + rename) —
même garanties (pas de corruption si interrompu, pas d'écrasement concurrent
d'un POST simultané).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from filelock import FileLock

from modules import exchange_hours
from modules.log import logger
from modules.my_portfolio_data import POSITIONS, WATCHLIST
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

# Devise native -> place de cotation principale / paire FX yfinance — mêmes
# valeurs que `_FX_PAIR_FOR_CURRENCY`/`_usd_multiplier` de
# `routers/my_portfolio.py`, dupliquées ici en petite constante locale
# plutôt qu'importées (`modules/` ne doit pas dépendre de `routers/`).
_CURRENCY_TO_EXCHANGE = {"EUR": "EURONEXT_PARIS", "HKD": "HKEX"}
_CURRENCY_TO_FX_PAIR = {"EUR": "EURUSD=X", "HKD": "USDHKD=X"}


def _lookup_position(ticker: str) -> dict[str, Any]:
    """Résout `price_ticker`/`currency` pour `ticker` depuis POSITIONS/WATCHLIST.

    Le journal reste indépendant de l'état courant du book (cas limite
    "position déjà soldée / pas dans POSITIONS" de la spec) : un ticker
    absent (renommé, retiré depuis) retombe sur `price_ticker=ticker` /
    `currency=USD` plutôt que d'échouer — un fill reste un fait historique,
    pas de contrainte de clé étrangère stricte.
    """
    for row in (*POSITIONS, *WATCHLIST):
        if row.get("ticker") == ticker:
            return {"price_ticker": row.get("price_ticker", ticker), "currency": row.get("currency", "USD")}
    logger.warning(
        f"[my_portfolio_executions] {ticker} absent de POSITIONS/WATCHLIST — "
        "devise USD / place US supposées par défaut"
    )
    return {"price_ticker": ticker, "currency": "USD"}


def _historical_usd_multiplier(currency: str, at: datetime) -> tuple[float | None, bool]:
    """Retourne (multiplicateur USD, is_approximate) au taux FX historique `at`."""
    if currency == "USD":
        return 1.0, False
    pair = _CURRENCY_TO_FX_PAIR.get(currency)
    if pair is None:
        logger.error(f"[my_portfolio_executions] Devise {currency} sans paire FX configurée")
        return None, False
    rate, is_approximate = get_historical_fx_rate(pair, at)
    if rate is None or rate <= 0:
        return None, is_approximate
    return (rate if currency == "EUR" else 1.0 / rate), is_approximate


def compute_execution(
    ticker: str,
    direction: str,
    shares: float,
    fill_price_native: float,
    executed_at: datetime,
    notes: str = "",
) -> dict[str, Any]:
    """Calcule tous les champs dérivés d'un fill (référence, slippage, marché).

    `executed_at` doit être timezone-aware (offset explicite requis) — même
    contrainte que `exchange_hours.is_open`/`get_historical_price` (cas
    limite "erreur de fuseau à la saisie" de la spec : mieux vaut échouer
    fort que deviner un fuseau).

    Fail-open : `Reference_Price_USD`/`Slippage_Bps`/`Slippage_Usd` restent
    `None` si le prix de référence ou le taux FX historique sont
    indisponibles — jamais d'ordre de grandeur approximatif fabriqué.
    """
    if executed_at.tzinfo is None:
        raise ValueError("`executed_at` doit être timezone-aware (offset explicite requis)")
    if direction not in ("BUY", "SELL"):
        raise ValueError(f"`direction` doit être BUY ou SELL, reçu : {direction!r}")

    pos = _lookup_position(ticker)
    price_ticker = pos["price_ticker"]
    currency = pos["currency"]
    primary_exchange = _CURRENCY_TO_EXCHANGE.get(currency, "US")

    market_open = exchange_hours.is_open(primary_exchange, executed_at)
    ref_price_native, resolution = get_historical_price(price_ticker, executed_at)
    fx, fx_is_approximate = _historical_usd_multiplier(currency, executed_at)

    reference_price_usd: float | None = None
    slippage_bps: float | None = None
    slippage_usd: float | None = None

    if fx is not None and ref_price_native is not None:
        reference_price_usd = ref_price_native * fx
        fill_price_usd = fill_price_native * fx
        if reference_price_usd:
            direction_sign = 1.0 if direction == "BUY" else -1.0
            slippage_bps = direction_sign * (fill_price_usd - reference_price_usd) / reference_price_usd * 10000
            slippage_usd = (slippage_bps / 10000.0) * shares * reference_price_usd

    final_notes = notes
    if fx_is_approximate:
        final_notes = f"{notes} (FX approximatif)".strip()

    return {
        "Date": executed_at.date().isoformat(),
        "Ticker": ticker,
        "Direction": direction,
        "Shares": shares,
        "Fill_Price_Native": fill_price_native,
        "Currency": currency,
        "Executed_At": executed_at.isoformat(),
        "Primary_Exchange": primary_exchange,
        "Market_Open_At_Fill": market_open,
        "Reference_Price_USD": reference_price_usd,
        "Reference_Price_Resolution": resolution,
        "Slippage_Bps": slippage_bps,
        "Slippage_Usd": slippage_usd,
        "Notes": final_notes,
    }


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=CSV_SCHEMA)


def load_executions() -> pd.DataFrame:
    """Charge `data/my_portfolio_executions.csv` sous `FileLock`.

    Fail-open : fichier absent (avant le premier log) → DataFrame vide au
    schéma canonique, jamais d'erreur.
    """
    with FileLock(str(CSV_LOCK_PATH), timeout=10):
        if not CSV_PATH.exists():
            return _empty_df()
        try:
            return pd.read_csv(CSV_PATH, dtype={"Ticker": str})
        except Exception as exc:
            logger.error(f"[my_portfolio_executions] Lecture échouée, journal vide retourné : {exc}")
            return _empty_df()


def _save_executions(df: pd.DataFrame) -> None:
    """Écrit dans un `.tmp` puis renomme atomiquement — même garantie que
    `evaluation.save_journal` (pas de corruption si le processus est
    interrompu, pas d'écrasement concurrent d'un autre POST)."""
    tmp_path = CSV_PATH.with_suffix(".tmp")
    with FileLock(str(CSV_LOCK_PATH), timeout=10):
        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            df.to_csv(tmp_path, index=False)
            tmp_path.replace(CSV_PATH)
        except Exception as exc:
            logger.error(f"[my_portfolio_executions] Écriture échouée : {exc}")
            tmp_path.unlink(missing_ok=True)
            raise


def log_execution(
    ticker: str,
    direction: str,
    shares: float,
    fill_price_native: float,
    executed_at: datetime,
    notes: str = "",
) -> dict[str, Any]:
    """Calcule les champs dérivés d'un fill et l'ajoute au journal (append)."""
    row = compute_execution(ticker, direction, shares, fill_price_native, executed_at, notes)
    with FileLock(str(CSV_LOCK_PATH), timeout=10):
        df = pd.read_csv(CSV_PATH, dtype={"Ticker": str}) if CSV_PATH.exists() else _empty_df()
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _save_executions(df)
    return row


def get_summary(df: pd.DataFrame) -> dict[str, Any]:
    """Agrégats du journal : coût cumulé, % hors séance, pires exécutions.

    Fail-open : lignes sans slippage calculable (référence/FX indisponibles
    au moment du fill) exclues des agrégats de coût plutôt que traitées
    comme un coût nul.
    """
    if df.empty:
        return {
            "total_slippage_usd": None,
            "n_fills": 0,
            "n_fills_outside_market": 0,
            "pct_fills_outside_market": None,
            "worst_executions": [],
        }

    slippage = pd.to_numeric(df["Slippage_Usd"], errors="coerce")
    market_open = df["Market_Open_At_Fill"].astype(bool)
    n_fills = len(df)
    n_outside = int((~market_open).sum())

    worst = df.assign(_abs_bps=pd.to_numeric(df["Slippage_Bps"], errors="coerce").abs())
    worst = worst.dropna(subset=["_abs_bps"]).sort_values("_abs_bps", ascending=False).head(3)
    worst_executions = [
        {
            "ticker": r["Ticker"],
            "executed_at": r["Executed_At"],
            "slippage_bps": r["Slippage_Bps"],
            "slippage_usd": r["Slippage_Usd"],
        }
        for _, r in worst.iterrows()
    ]

    return {
        "total_slippage_usd": float(slippage.dropna().sum()) if slippage.notna().any() else None,
        "n_fills": n_fills,
        "n_fills_outside_market": n_outside,
        "pct_fills_outside_market": round(100.0 * n_outside / n_fills, 1),
        "worst_executions": worst_executions,
    }
