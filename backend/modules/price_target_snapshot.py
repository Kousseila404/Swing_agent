"""Snapshot quotidien du prix cible fondamental 12 mois — persistance dédiée
pour validation hit-rate différée (§2/§5 `docs/price_target_design.md`).

Calcule `price_target` pour tout l'univers scoré avec les poids appris par
`price_target_calibration.py` (`data/.price_target_calibration/state.json`),
puis persiste :
  - un snapshot daté `data/.price_target_history/price_targets_YYYYMMDD.json.gz`
    (horizon=12 mois, prix courant au calcul — permet de mesurer le vrai
    hit-rate dès que 12 mois de recul existent, ~avril 2027), indépendant de
    `universe_history` pour ne pas dépendre de sa rétention/format.
  - `data/.price_target_history/latest.json` (non compressé) — lu par
    `auto_proposer.py` pour peupler `alloc.get("price_target")` sans
    recalculer l'univers entier à chaque proposition.

Ce module est pure storage + orchestration : la formule vit dans
`modules/price_target.py`, les poids appris dans `price_target_calibration.py`.

Usage (cron, `run_titan.sh` Step 3d — après metrics_agent, avant
POST /api/proposals/refresh) :
    python -m modules.price_target_snapshot
"""
from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from modules import api_core
from modules.log import logger
from modules.price_target import DEFAULT_BAND_PCT, DEFAULT_WEIGHTS, compute_for_universe

HISTORY_DIR = api_core.BASE / "data" / ".price_target_history"
LATEST_PATH = HISTORY_DIR / "latest.json"
CALIBRATION_STATE_PATH = api_core.BASE / "data" / ".price_target_calibration" / "state.json"

HORIZON_MONTHS = 12


def _snapshot_path(d: date) -> Path:
    return HISTORY_DIR / f"price_targets_{d.strftime('%Y%m%d')}.json.gz"


def _today() -> date:
    return datetime.now(UTC).date()


def load_calibrated_weights() -> tuple[dict[str, float], float]:
    """Lit `best_weights` de la boucle de calibration (§6 design doc).

    Fallback équi-pondéré (`price_target.DEFAULT_WEIGHTS`) si l'état de
    calibration n'existe pas encore ou est illisible — ne doit jamais faire
    échouer le snapshot quotidien.
    """
    if CALIBRATION_STATE_PATH.exists():
        try:
            state = json.loads(CALIBRATION_STATE_PATH.read_text())
            bw = state.get("best_weights") or {}
            weights = {
                "w_multiple": bw.get("w_multiple", DEFAULT_WEIGHTS["w_multiple"]),
                "w_peg": bw.get("w_peg", DEFAULT_WEIGHTS["w_peg"]),
                "w_buffett": bw.get("w_buffett", DEFAULT_WEIGHTS["w_buffett"]),
            }
            band_pct = float(bw.get("band_pct", DEFAULT_BAND_PCT))
            return weights, band_pct
        except Exception as e:
            logger.warning(f"[PriceTargetSnapshot] calibration state illisible, fallback défaut: {e}")
    return dict(DEFAULT_WEIGHTS), DEFAULT_BAND_PCT


def compute_and_persist(
    scored_universe: dict[str, dict[str, Any]] | None = None,
    *,
    snapshot_date: date | None = None,
) -> dict[str, Any]:
    """Calcule `price_target` pour tout l'univers et persiste snapshot + latest.

    Args:
        scored_universe: override pour tests. Défaut = `get_scored_universe()`.
        snapshot_date: override pour tests. Défaut = today UTC.

    Returns: résumé {n_tickers, n_with_target, weights_used, band_pct_used,
        snapshot_path}.
    """
    if scored_universe is None:
        from modules.sector_metrics import get_scored_universe
        scored_universe = get_scored_universe() or {}

    weights, band_pct = load_calibrated_weights()
    targets = compute_for_universe(scored_universe, weights=weights, band_pct=band_pct)

    snapshot_date = snapshot_date or _today()
    computed_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    records: dict[str, dict[str, Any]] = {}
    n_with_target = 0
    for ticker, pt in targets.items():
        row = scored_universe.get(ticker) or {}
        records[ticker] = {
            **pt,
            "current_price": row.get("current_price"),
            "sector": row.get("sector"),
            "horizon_months": HORIZON_MONTHS,
            "weights_used": weights,
            "band_pct_used": band_pct,
            "computed_at": computed_at,
        }
        if pt.get("method") == "fundamental_blend":
            n_with_target += 1

    payload = {
        "snapshot_date": snapshot_date.isoformat(),
        "computed_at": computed_at,
        "horizon_months": HORIZON_MONTHS,
        "weights_used": weights,
        "band_pct_used": band_pct,
        "n_tickers": len(records),
        "n_with_target": n_with_target,
        "tickers": records,
    }

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    # Snapshot daté gzippé (historisation dédiée §5/§2, 1 fichier/jour, écrasé
    # si rejoué le même jour) — écriture atomique tmp → rename.
    out = _snapshot_path(snapshot_date)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_bytes(gzip.compress(raw))
    tmp.replace(out)

    # Cache "latest" non compressé — lu par auto_proposer/ticker_analysis
    # sans dézipper ni recalculer l'univers entier.
    LATEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    latest_tmp = LATEST_PATH.with_suffix(".json.tmp")
    latest_tmp.write_bytes(raw)
    latest_tmp.replace(LATEST_PATH)

    return {
        "n_tickers": len(records),
        "n_with_target": n_with_target,
        "weights_used": weights,
        "band_pct_used": band_pct,
        "snapshot_path": str(out),
    }


def load_latest() -> dict[str, dict[str, Any]]:
    """{ticker: record} depuis le dernier snapshot persisté. {} si absent/illisible."""
    if not LATEST_PATH.exists():
        return {}
    try:
        payload = json.loads(LATEST_PATH.read_text())
        return payload.get("tickers") or {}
    except Exception as e:
        logger.warning(f"[PriceTargetSnapshot] latest.json illisible: {e}")
        return {}


def get_latest_for_ticker(ticker: str) -> dict[str, Any]:
    return load_latest().get(ticker) or {}


def _cli() -> int:
    summary = compute_and_persist()
    print(
        f"[PriceTargetSnapshot] n_tickers={summary['n_tickers']} "
        f"n_with_target={summary['n_with_target']} "
        f"weights={summary['weights_used']} band_pct={summary['band_pct_used']}"
    )
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
