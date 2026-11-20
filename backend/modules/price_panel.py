"""Panel de clôtures ajustées quotidiennes de l'univers (audit 2026-11-18 P0 #5).

Pourquoi : `universe_scheduler` ne rafraîchit `current_price` que pour ~100
tickers/jour (rotation ~7 j). Les rendements calculés entre deux snapshots
sur `current_price` (WFO, shadow portfolios) mélangent donc des prix figés
(retour 0 %) et des sauts de 7 jours comptés comme 1. Ce module fournit une
source unique de clôtures ajustées datées, téléchargées en lot (yfinance,
chunks de 150, circuit breaker partagé) et persistées sur disque.

Fichier : `data/.price_panel.json.gz` → {"updated_at": iso, "closes": {TICKER: {YYYY-MM-DD: close}}}

CLI (run_titan.sh, avant shadow_portfolios) :
    python -m modules.price_panel --refresh [--days 260]
"""
from __future__ import annotations

import gzip
import json
import math
from bisect import bisect_right
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from modules.log import logger

_BACKEND = Path(__file__).resolve().parents[1]
PANEL_PATH = _BACKEND / "data" / ".price_panel.json.gz"

# ≥ 13 mois de séances pour le momentum 12-1 de la stratégie cœur-satellite
# (252 + 21 séances) ; yfinance : 420 jours → période « 2y ».
DEFAULT_DAYS = 420
# Une clôture plus vieille que ça par rapport à la date demandée n'est pas
# considérée comme « le prix de ce jour » (ticker suspendu / délisté).
MAX_STALENESS_DAYS = 5

_cache: dict[str, Any] = {"mtime": None, "series": {}}


def _universe_tickers() -> list[str]:
    try:
        uni = json.loads((_BACKEND / "data" / "universe.json").read_text(encoding="utf-8"))
        tickers = uni.get("tickers") if isinstance(uni, dict) else None
        if isinstance(tickers, dict):
            return sorted(tickers)
    except Exception as exc:
        logger.warning(f"[PricePanel] univers illisible : {exc}")
    return []


def refresh(tickers: list[str] | None = None, days: int = DEFAULT_DAYS) -> dict[str, Any]:
    """Télécharge les clôtures ajustées et fusionne avec le panel existant."""
    from data_providers.yfinance_provider import YFinanceProvider

    tickers = sorted({t.upper() for t in (tickers or _universe_tickers()) if t})
    if not tickers:
        return {"ok": False, "error": "aucun ticker"}
    tickers = sorted(set(tickers) | {"SPY", "RSP"})
    series = YFinanceProvider().get_daily_history_batch(tickers, days)
    panel = _read_raw()
    closes: dict[str, dict[str, float]] = panel.get("closes") or {}
    n_ok = 0
    for t, s in series.items():
        if s is None or len(s) == 0:
            continue
        dst = closes.setdefault(t, {})
        for idx, v in s.items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv) and fv > 0:
                day = idx.date() if hasattr(idx, "date") else idx
                dst[str(day)[:10]] = round(fv, 6)
        n_ok += 1
    payload = {"updated_at": datetime.now(UTC).isoformat(timespec="seconds"), "closes": closes}
    PANEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PANEL_PATH.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))
    tmp.replace(PANEL_PATH)
    _cache["mtime"] = None
    logger.info(f"[PricePanel] {n_ok}/{len(tickers)} tickers rafraîchis")
    return {"ok": n_ok > 0, "n_ok": n_ok, "n_requested": len(tickers)}


def _read_raw() -> dict[str, Any]:
    try:
        if PANEL_PATH.exists():
            return json.loads(gzip.decompress(PANEL_PATH.read_bytes()))
    except Exception as exc:
        logger.warning(f"[PricePanel] panel illisible : {exc}")
    return {}


def _series() -> dict[str, tuple[list[str], list[float]]]:
    """{TICKER: (dates triées, closes)} — rechargé si le fichier a changé."""
    try:
        mtime = PANEL_PATH.stat().st_mtime_ns
    except OSError:
        _cache.update(mtime=None, series={})
        return {}
    if _cache["mtime"] == mtime:
        return _cache["series"]
    out: dict[str, tuple[list[str], list[float]]] = {}
    for t, by_date in (_read_raw().get("closes") or {}).items():
        ds = sorted(by_date)
        out[t] = (ds, [float(by_date[d]) for d in ds])
    _cache.update(mtime=mtime, series=out)
    return out


def available() -> bool:
    return bool(_series())


def close_on_or_before(ticker: str, d: date, max_staleness_days: int = MAX_STALENESS_DAYS) -> float | None:
    """Clôture ajustée du dernier jour de bourse ≤ d (None si absent ou trop ancien)."""
    hit = _series().get(ticker.upper())
    if not hit:
        return None
    ds, vs = hit
    i = bisect_right(ds, d.isoformat()) - 1
    if i < 0:
        return None
    if (d - date.fromisoformat(ds[i])).days > max_staleness_days:
        return None
    return vs[i]


def latest_closes(tickers: list[str] | None = None, max_age_days: int = MAX_STALENESS_DAYS) -> dict[str, float]:
    """Dernière clôture connue par ticker, si elle date de moins de `max_age_days`."""
    today = date.today()
    out: dict[str, float] = {}
    series = _series()
    for t in tickers if tickers is not None else list(series):
        hit = series.get(t.upper())
        if not hit or not hit[0]:
            continue
        last_d = date.fromisoformat(hit[0][-1])
        if (today - last_d).days <= max_age_days:
            out[t] = hit[1][-1]
    return out


def _main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="price_panel")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    args = ap.parse_args(argv)
    if not args.refresh:
        s = _series()
        print(f"{len(s)} tickers dans le panel ({PANEL_PATH})")
        return 0
    res = refresh(days=args.days)
    print(res)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_main())
