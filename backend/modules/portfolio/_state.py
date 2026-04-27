"""Lecture de l'état courant du portefeuille : positions ouvertes, cash dispo,
exposition par secteur.

Source : trade_journal.csv (OPEN positions) + equity_state.json pour le cash.
Utilisé par /api/portfolio/recommendations pour :
  1. Soustraire le capital déjà investi du budget dispo.
  2. Flagger les recos qui pousseraient un secteur > 30 % cap.
  3. Éviter de re-recommander à l'achat un ticker déjà en OPEN.

Stateless — un appel = une lecture disque ; pas de cache car le journal
change à chaque trade ajouté/clôturé (mtime-keying ferait duplication vs.
api_core.load_journal). La lecture est sous FileLock pour cohérence avec
les autres consommateurs (tracker, /api/portfolio).
"""
from __future__ import annotations

from typing import Any

from modules import api_core
from modules.log import logger


def _safe_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN guard


def read_open_positions() -> list[dict[str, Any]]:
    """Retourne la liste des positions OPEN normalisées : {ticker, direction,
    entry, size, sector, notional_usd, current_price, unrealized_pnl}.

    notional_usd est calculé à l'entry price faute de fetch live ici — c'est
    le proxy "capital engagé" pour le budget. `current_price` et `unrealized_pnl`
    sont lus depuis equity_state si dispo (alimenté par le tracker).
    """
    journal = api_core.load_journal()
    equity = api_core.load_equity()
    live_map: dict[str, dict[str, Any]] = {}
    for p in equity.get("open_positions") or []:
        tk = str(p.get("ticker") or "").strip().upper()
        if tk:
            live_map[tk] = p

    out: list[dict[str, Any]] = []
    for row in journal:
        if row.get("Status") != "OPEN":
            continue
        ticker = str(row.get("Ticker") or "").strip().upper()
        if not ticker:
            continue
        entry = _safe_float(row.get("Entry"))
        size = _safe_float(row.get("Size"))
        if entry is None or size is None or entry <= 0 or size <= 0:
            continue
        notional = entry * size
        live = live_map.get(ticker) or {}
        out.append({
            "ticker":        ticker,
            "direction":     row.get("Direction") or "LONG",
            "entry":         round(entry, 4),
            "size":          size,
            "sector":        row.get("Sector") or "Unknown",
            "notional_usd":  round(notional, 2),
            "current_price": _safe_float(live.get("current_price")),
            "unrealized_pnl": _safe_float(live.get("unrealized_pnl")),
            "order_id":      row.get("Order_ID") or "",
        })
    return out


def compute_current_exposure(
    positions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Agrège les positions OPEN en exposition totale + par secteur.

    Retourne :
      {
        total_invested_usd: float,          # somme notional_usd OPEN
        n_open_positions: int,
        sector_usd: {sector: usd},          # $ par secteur
        sector_tickers: {sector: [TK, ...]},
        tickers_open: [TK, ...],            # set pour lookup O(1)
      }

    Si `positions` non fourni → appelle `read_open_positions()`.
    """
    if positions is None:
        try:
            positions = read_open_positions()
        except Exception as e:
            logger.warning(f"[PortfolioState] read_open_positions failed: {e}")
            positions = []

    total = 0.0
    sector_usd: dict[str, float] = {}
    sector_tickers: dict[str, set[str]] = {}
    tickers_open_set: set[str] = set()
    duplicated_tickers: list[str] = []
    for p in positions:
        n = p.get("notional_usd") or 0.0
        sec = p.get("sector") or "Unknown"
        tk = p["ticker"]
        total += n   # on garde la somme des notionals — chaque ligne OPEN est un vrai capital engagé
        sector_usd[sec] = sector_usd.get(sec, 0.0) + n
        sector_tickers.setdefault(sec, set()).add(tk)
        if tk in tickers_open_set:
            duplicated_tickers.append(tk)
        tickers_open_set.add(tk)
    # Comptage UNIQUE de tickers — un doublon journal (fill cassé, reopen
    # accidentel) ne doit pas bloquer le free_slots gate artificiellement.
    n_unique = len(tickers_open_set)
    result = {
        "total_invested_usd":  round(total, 2),
        "n_open_positions":    n_unique,
        "n_open_rows":         len(positions),  # rows CSV (peut > n_unique si doublon)
        "sector_usd":          {s: round(v, 2) for s, v in sector_usd.items()},
        "sector_tickers":      {s: sorted(ts) for s, ts in sector_tickers.items()},
        "tickers_open":        sorted(tickers_open_set),
    }
    if duplicated_tickers:
        result["duplicated_open_tickers"] = sorted(set(duplicated_tickers))
        logger.warning(
            f"[PortfolioState] {len(set(duplicated_tickers))} ticker(s) "
            f"en doublon dans trade_journal.csv OPEN : "
            f"{sorted(set(duplicated_tickers))[:5]}..."
        )
    return result
