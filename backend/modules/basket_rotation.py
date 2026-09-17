"""Rotation du panier — sorties rank-based (stratégie « basket », 2026-09-17).

Le backtest qui a de l'edge est un panier top-N rééquilibré : ce qui sort
du top-N est vendu, ce qui y entre est acheté. Côté achats, le proposer +
auto-approve (mode basket) font le travail. Côté ventes, ce module :

  1. relit le classement TITAN courant (`compute_ranks`) ;
  2. pour chaque position OPEN, compte les jours **consécutifs** où le rang
     dépasse `BASKET_EXIT_RANK` (état persisté `data/.basket_rotation_state.json`,
     une lecture par jour calendaire) ;
  3. après `BASKET_EXIT_CONFIRM_DAYS` jours confirmés, ferme la position au
     marché via le broker (`close_position`, qui annule les jambes stop) et
     tague `Close_Reason=ROTATION`.

Garde-fous : jamais pendant un killswitch, jamais si le prix courant est
indisponible, jamais plus de `max_exits` par run, et un ticker dont la
décision LT est ADD_ON (thèse intacte + correction) est **exempté** — la
rotation ne doit pas vendre ce que la couche fondamentale veut renforcer.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import config
from modules.log import logger

_BACKEND = Path(__file__).resolve().parents[1]
STATE_PATH = _BACKEND / "data" / ".basket_rotation_state.json"


def _load_state() -> dict[str, Any]:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[Rotation] état illisible : {exc}")
    return {"streaks": {}, "last_date": None}


def _save_state(state: dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(STATE_PATH)
    except Exception as exc:
        logger.warning(f"[Rotation] état non persisté : {exc}")


def update_streaks(
    state: dict[str, Any],
    open_tickers: list[str],
    ranks: dict[str, int],
    exit_rank: int,
    today: str,
) -> dict[str, Any]:
    """Met à jour les séries « hors panier » (pure, testable).

    Un ticker sans rang (absent de l'univers scoré) compte comme hors panier.
    Une seule mise à jour par jour calendaire.
    """
    if state.get("last_date") == today:
        return state
    streaks: dict[str, int] = dict(state.get("streaks") or {})
    for t in open_tickers:
        rank = ranks.get(t)
        out = rank is None or rank > exit_rank
        streaks[t] = (streaks.get(t, 0) + 1) if out else 0
    for t in list(streaks):
        if t not in open_tickers:
            streaks.pop(t)
    return {"streaks": streaks, "last_date": today, "ranks_seen": {t: ranks.get(t) for t in open_tickers}}


def plan_exits(
    state: dict[str, Any],
    confirm_days: int,
    exempt: set[str] | None = None,
    max_exits: int = 3,
) -> list[str]:
    """Tickers dont la série ≥ confirm_days, hors exemptions (ADD_ON), triés par série décroissante."""
    exempt = exempt or set()
    cands = [(t, n) for t, n in (state.get("streaks") or {}).items() if n >= confirm_days and t not in exempt]
    cands.sort(key=lambda x: -x[1])
    return [t for t, _ in cands[:max_exits]]


def run_rotation(dry_run: bool = False) -> dict[str, Any]:
    """Point d'entrée (appelé par auto_approve en mode basket, 1×/jour utile)."""
    if str(getattr(config, "STRATEGY_MODE", "basket")).lower() != "basket":
        return {"skipped": "STRATEGY_MODE != basket"}
    try:
        from modules.tracker.killswitch import is_trading_allowed
        if not is_trading_allowed():
            return {"skipped": "killswitch"}
    except Exception:
        pass

    from modules.lt_exit_policy import compute_ranks
    from modules.sector_metrics import get_scored_universe
    from modules.tracker.evaluation import load_journal

    scored = get_scored_universe() or {}
    if not scored:
        return {"skipped": "univers vide"}
    ranks = compute_ranks(scored)
    df = load_journal()
    open_df = df[df["Status"] == "OPEN"]
    open_tickers = sorted({str(t).upper() for t in open_df["Ticker"].tolist()})
    today = date.today().isoformat()

    state = update_streaks(_load_state(), open_tickers, ranks, int(config.BASKET_EXIT_RANK), today)
    _save_state(state)

    # Exemption : dernière décision LT = ADD_ON (persistée par le tracker).
    exempt: set[str] = set()
    if "Last_LT_Action" in open_df.columns:
        for _, r in open_df.iterrows():
            if str(r.get("Last_LT_Action") or "").strip() == "ADD_ON":
                exempt.add(str(r["Ticker"]).upper())

    to_exit = plan_exits(state, int(config.BASKET_EXIT_CONFIRM_DAYS), exempt=exempt,
                         max_exits=int(getattr(config, "BASKET_MAX_EXITS_PER_RUN", 3)))
    result: dict[str, Any] = {
        "date": today, "open": open_tickers, "streaks": state.get("streaks"),
        "exempt": sorted(exempt), "to_exit": to_exit, "closed": [], "failed": [],
    }
    if dry_run or not to_exit:
        return result

    from modules.broker_gateway import get_broker
    from modules.tracker.market import get_current_price
    broker = get_broker()
    for t in to_exit:
        px = get_current_price(t)
        if px is None or px <= 0:
            result["failed"].append({"ticker": t, "reason": "prix indisponible"})
            continue
        row = open_df[open_df["Ticker"].astype(str).str.upper() == t].iloc[0]
        try:
            entry = float(row.get("Entry") or 0)
        except (TypeError, ValueError):
            entry = 0.0
        status = "WIN" if entry and px > entry else "LOSS"
        ok = False
        try:
            ok = broker.close_position(t, px, status, str(row.get("Direction") or "LONG"), update_csv=True)
        except Exception as exc:
            logger.error(f"[Rotation] close {t} : {exc}")
        if ok:
            _tag_close_reason(t, "ROTATION")
            result["closed"].append({"ticker": t, "exit": px, "status": status, "rank": ranks.get(t)})
            logger.warning(f"[Rotation] 🔄 {t} vendu @ {px:.2f} (rang #{ranks.get(t)} > {config.BASKET_EXIT_RANK} "
                           f"depuis {state['streaks'].get(t)} j) → {status}")
            _notify(t, px, status, ranks.get(t))
        else:
            result["failed"].append({"ticker": t, "reason": "close non confirmé"})
    if result["closed"]:
        state["streaks"] = {k: v for k, v in state["streaks"].items() if k not in {c["ticker"] for c in result["closed"]}}
        _save_state(state)
    return result


def _tag_close_reason(ticker: str, reason: str) -> None:
    """Après close_position (qui ne remplit pas Close_Reason), tague la ligne fermée la plus récente."""
    try:
        import pandas as pd
        from filelock import FileLock

        from modules.utils import CSV_LOCK_PATH, CSV_PATH
        with FileLock(str(CSV_LOCK_PATH), timeout=10):
            df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
            mask = (df["Ticker"].str.upper() == ticker.upper()) & (df["Status"].isin(["WIN", "LOSS"])) & (df["Exit_Date"] != "")
            if mask.any():
                idx = df[mask].index[-1]
                df.at[idx, "Close_Reason"] = reason
                df.to_csv(CSV_PATH, index=False)
        try:
            from modules.duckdb_journal import sync_from_csv
            sync_from_csv(csv_path=CSV_PATH, db_path=CSV_PATH.with_name("trade_journal.duckdb"))
        except Exception:
            pass
    except Exception as exc:
        logger.debug(f"[Rotation] tag Close_Reason {ticker} : {exc}")


def _notify(ticker: str, px: float, status: str, rank: int | None) -> None:
    try:
        from modules.alerter import _send_telegram_message
        _send_telegram_message(
            f"🔄 <b>Rotation panier</b> — {ticker} vendu @ {px:.2f} ({status})\n"
            f"Rang TITAN #{rank} hors du top-{config.BASKET_EXIT_RANK} depuis "
            f"{config.BASKET_EXIT_CONFIRM_DAYS} jours — la place est libérée pour le prochain candidat."
        )
    except Exception as exc:
        logger.debug(f"[Rotation] Telegram : {exc}")


if __name__ == "__main__":
    import sys
    print(json.dumps(run_rotation(dry_run="--apply" not in sys.argv), indent=1, ensure_ascii=False))
    print("(dry-run — ajouter --apply pour exécuter)" if "--apply" not in sys.argv else "")
