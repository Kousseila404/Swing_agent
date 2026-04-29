"""WFO monitor — orchestre la calibration mensuelle + alerte dégradation.

Audit S1.3 — automation (2026-04-27).
====================================
Le harness `wfo_calibration` doit tourner régulièrement pour suivre la santé
du signal composite. Trop souvent (daily) = bruit gros sur petits folds,
faux positifs. Trop rarement (annual) = on laisse dériver. Cadence mensuelle
+ historique long-terme = bon compromis.

Pipeline :
  1. Charge `run_walk_forward(train_days=60, test_days=20,
     publication_lag_days=90)` (config production-grade).
  2. Append le résultat à `data/wfo_history.jsonl` (1 ligne / run).
  3. Si `avg_ic_test < _IC_DEGRADATION_THRESHOLD` deux runs de suite (le
     courant + le précédent), envoie une alerte Telegram.
  4. Persiste aussi le résultat le plus récent dans `data/wfo_weights.json`
     (consommé par `/api/wfo`).

Usage :
    python -m modules.wfo_monitor                      # exécution standard
    python -m modules.wfo_monitor --no-alert           # skip Telegram
    python -m modules.wfo_monitor --train-days 90 --test-days 30
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from modules import wfo_calibration
from modules.log import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
WFO_HISTORY_PATH = _PROJECT_ROOT / "data" / "wfo_history.jsonl"

# Au-dessous de ce seuil, on considère que le signal composite est faible.
# 0.02 = 2 % d'IC Spearman (corrélation rang/return). Empiriquement, un
# alpha viable a IC > 0.05 ; entre 0.02 et 0.05 = signal marginal ; < 0.02
# = bruit. Deux runs consécutifs sous ce seuil = vraie dégradation, pas un
# accident statistique.
_IC_DEGRADATION_THRESHOLD = 0.02


def _append_history(entry: dict[str, Any]) -> None:
    """Append une ligne JSON dans `wfo_history.jsonl` (atomique, fail-open)."""
    WFO_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    try:
        with open(WFO_HISTORY_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as e:
        logger.warning(f"[WFOMonitor] history append failed: {e}")


def _read_history(last_n: int = 10) -> list[dict[str, Any]]:
    """Lit les `last_n` dernières lignes du fichier d'historique."""
    if not WFO_HISTORY_PATH.exists():
        return []
    try:
        lines = WFO_HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        logger.warning(f"[WFOMonitor] history read failed: {e}")
        return []
    out: list[dict[str, Any]] = []
    for ln in lines[-last_n:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _send_alert(message: str) -> None:
    """Envoie une alerte Telegram. Fail-open : log et continue si l'envoi échoue."""
    try:
        from modules.alerter import _send_telegram_message
        _send_telegram_message(message)
        logger.info(f"[WFOMonitor] alerte envoyée ({len(message)} chars)")
    except Exception as e:
        logger.warning(f"[WFOMonitor] alerte Telegram échouée: {e}")


def run_monitor(
    *,
    train_days: int = 60,
    test_days: int = 20,
    publication_lag_days: int = 90,
    alert: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    """Exécute la calibration + détection dégradation. Retourne le summary.

    `persist` : si True, écrit `data/wfo_weights.json` (consommé par /api/wfo).
    """
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        result = wfo_calibration.run_walk_forward(
            train_days=train_days,
            test_days=test_days,
            publication_lag_days=publication_lag_days,
        )
    except ValueError as e:
        # Historique trop court → pas de fold produit. On log mais on n'alerte
        # pas (ce n'est pas une dégradation, c'est un manque de données).
        logger.warning(f"[WFOMonitor] WFO impossible : {e}")
        entry = {
            "timestamp": timestamp,
            "status": "skipped",
            "reason": str(e),
            "train_days": train_days,
            "test_days": test_days,
            "publication_lag_days": publication_lag_days,
        }
        _append_history(entry)
        return entry

    if persist:
        wfo_calibration._persist(result)

    # Compactons le résultat dans l'historique (on garde le détail dans
    # wfo_weights.json) — pas la peine de stocker 50 KB par run sur disque.
    entry = {
        "timestamp":            timestamp,
        "status":               "ok",
        "train_days":           train_days,
        "test_days":            test_days,
        "publication_lag_days": publication_lag_days,
        "n_folds":              result.n_folds,
        "avg_ic_test":          round(result.avg_ic_test, 4),
        "avg_weights":          result.avg_weights,
        "n_snapshots":          result.diagnostics.get("n_snapshots", 0),
    }
    _append_history(entry)

    # Détection dégradation : 2 runs consécutifs sous le seuil.
    history = _read_history(last_n=5)
    ok_runs = [h for h in history if h.get("status") == "ok"]
    degraded = False
    if len(ok_runs) >= 2:
        last_two = ok_runs[-2:]
        if all(
            h.get("avg_ic_test") is not None
            and h["avg_ic_test"] < _IC_DEGRADATION_THRESHOLD
            for h in last_two
        ):
            degraded = True

    entry["degraded"] = degraded

    if alert and degraded:
        prev_ic = ok_runs[-2]["avg_ic_test"]
        cur_ic = ok_runs[-1]["avg_ic_test"]
        msg = (
            "⚠️ <b>TITAN — Dégradation signal composite</b>\n"
            f"avg_ic_test : précédent {prev_ic:+.4f} → courant {cur_ic:+.4f} "
            f"(seuil {_IC_DEGRADATION_THRESHOLD:+.2f})\n"
            f"Folds : {result.n_folds} | snapshots : {entry['n_snapshots']}\n"
            f"Audit : <code>data/wfo_weights.json</code> + "
            f"<code>data/wfo_history.jsonl</code>\n"
            "Action recommandée : comparer poids OOS vs prod, "
            "envisager A/B test paper avant ajustement."
        )
        _send_alert(msg)

    return entry


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def _main() -> int:
    p = argparse.ArgumentParser(prog="wfo_monitor",
        description="WFO calibration + détection dégradation + alerte Telegram.")
    p.add_argument("--train-days", type=int, default=60)
    p.add_argument("--test-days", type=int, default=20)
    p.add_argument("--publication-lag-days", type=int, default=90,
                   help="Lag anti-lookahead (défaut 90 j = 10-K typique).")
    p.add_argument("--no-alert", action="store_true",
                   help="Skip l'envoi Telegram en cas de dégradation.")
    p.add_argument("--no-persist", action="store_true",
                   help="N'écrit pas data/wfo_weights.json")
    p.add_argument("--json", action="store_true",
                   help="Sortie JSON sur stdout (sinon : tableau).")
    args = p.parse_args()

    summary = run_monitor(
        train_days=args.train_days,
        test_days=args.test_days,
        publication_lag_days=args.publication_lag_days,
        alert=not args.no_alert,
        persist=not args.no_persist,
    )

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0 if summary.get("status") == "ok" else 1

    status = summary.get("status")
    print(f"=== WFO Monitor — {summary.get('timestamp')} ===")
    if status != "ok":
        print(f"Status : {status} ({summary.get('reason', '?')})")
        return 1

    print(f"Folds          : {summary['n_folds']}")
    print(f"Avg IC test    : {summary['avg_ic_test']:+.4f}  "
          f"(seuil dégradation {_IC_DEGRADATION_THRESHOLD:+.2f})")
    print(f"Lag publication: {summary['publication_lag_days']}j")
    if summary.get("degraded"):
        print("⚠️  DÉGRADATION détectée — alerte Telegram " +
              ("envoyée" if not args.no_alert else "skippée (--no-alert)"))
    else:
        print("OK — signal composite dans la zone saine.")

    avg_w = summary.get("avg_weights") or {}
    if avg_w:
        print()
        print("Poids moyens OOS :")
        for p_name, w in sorted(avg_w.items(), key=lambda kv: -kv[1]):
            print(f"  {p_name:<22} {w:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
