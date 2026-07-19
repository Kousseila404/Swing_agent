"""Agent d'analyse des chiffres (local) — Étape 0bis roadmap (2026-07-19).

Pas une 2e routine cloud : un agent cloud n'a accès ni aux secrets `.env`,
ni à l'API live, ni au bot Telegram — seulement à un clone Git. Ce module
tourne via le cron local existant (`run_titan.sh`, qui a déjà accès à tout).

Purement lecture/analyse :
  - Lit `/api/data_health` (severity, staleness fondamentaux, dq_sanitize)
    via HTTP local (API déjà up à ce stade du cron) — même source de vérité
    que le dashboard, pas de logique de seuils dupliquée ici.
  - Lit `wfo_history.jsonl` (via `wfo_monitor`) et le nombre de snapshots
    `universe_history` (via `universe_history.list_snapshots()`).
  - Lit le journal de trades clos (`evaluation.load_journal()` +
    `perf_metrics.compute_metrics()`).
  - Calcule une tendance vs le run précédent et pousse un digest Telegram
    via le canal existant (`modules.alerter`), pas un nouveau mécanisme.
  - Persiste un constat structuré dans `data/metrics_history.jsonl` (même
    pattern que `wfo_history.jsonl`) — Telegram est éphémère, ce fichier est
    la mémoire durable pour juger une tendance sur plusieurs semaines.

Interdiction stricte : ce module ne modifie JAMAIS les poids/logique de
`modules/sector_metrics/_scoring.py` ni aucun autre code de scoring. Le jour
où WFO produit un IC significatif (seuil repris du commentaire
`wfo_monitor.py` : "un alpha viable a IC > 0.05"), l'agent se contente
d'écrire une **proposition markdown** dans `data/wfo_proposals/` pour
relecture humaine — jamais d'auto-application sur la logique qui pilote de
l'argent réel.

Usage :
    python -m modules.metrics_agent                 # exécution standard
    python -m modules.metrics_agent --no-alert
    python -m modules.metrics_agent --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from modules import audit_summary, universe_history, wfo_monitor
from modules.log import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
METRICS_HISTORY_PATH = _PROJECT_ROOT / "data" / "metrics_history.jsonl"
WFO_PROPOSAL_DIR = _PROJECT_ROOT / "data" / "wfo_proposals"

_HTTP_TIMEOUT = 10.0

# Seuil de significativité IC — repris du commentaire déjà présent dans
# `wfo_monitor.py` ("empiriquement, un alpha viable a IC > 0.05 ; entre 0.02
# et 0.05 = signal marginal ; < 0.02 = bruit"). Pas une valeur inventée ici.
_IC_SIGNIFICANT_THRESHOLD = 0.05

# Seuils de progression repris tels quels de `audit_summary._TH` (déjà
# l'unique référence documentée dans le projet pour ces seuils) —
# volontairement pas redéfinis ici pour éviter toute divergence silencieuse.
_MIN_SNAPSHOTS = audit_summary._TH["min_snapshots_history"]
_MIN_TRADES_FOR_TRUST = audit_summary._TH["min_periods_for_trust"]
_MIN_WFO_FOLDS = audit_summary._TH["wfo_min_folds"]


# ─────────────────────────────────────────────────────────────────
# Historique JSONL (même pattern que wfo_monitor._append_history)
# ─────────────────────────────────────────────────────────────────

def _append_history(entry: dict[str, Any]) -> None:
    METRICS_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    try:
        with open(METRICS_HISTORY_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as e:
        logger.warning(f"[MetricsAgent] history append failed: {e}")


def _read_history(last_n: int = 10) -> list[dict[str, Any]]:
    if not METRICS_HISTORY_PATH.exists():
        return []
    try:
        lines = METRICS_HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        logger.warning(f"[MetricsAgent] history read failed: {e}")
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


# ─────────────────────────────────────────────────────────────────
# Collecte des sources
# ─────────────────────────────────────────────────────────────────

def _data_health_snapshot(api_url: str) -> dict[str, Any]:
    """GET /api/data_health (public, sans auth). Fail-open sur erreur réseau."""
    try:
        r = requests.get(f"{api_url}/api/data_health", timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"[MetricsAgent] /api/data_health indisponible : {e}")
        return {"error": str(e)}


def _trades_snapshot() -> dict[str, Any]:
    """Compte WIN/LOSS clos depuis le journal de trades réel."""
    try:
        from modules import perf_metrics
        from modules.tracker import evaluation
        df = evaluation.load_journal()
        m = perf_metrics.compute_metrics(df)
        return {
            "wins": m["wins"],
            "losses": m["losses"],
            "total_closed": m["total_closed"],
        }
    except Exception as e:
        logger.warning(f"[MetricsAgent] journal trades illisible : {e}")
        return {"wins": 0, "losses": 0, "total_closed": 0, "error": str(e)}


# ─────────────────────────────────────────────────────────────────
# Proposition WFO (jamais d'auto-application sur le code de scoring)
# ─────────────────────────────────────────────────────────────────

def _maybe_write_wfo_proposal(wfo_latest: dict[str, Any] | None) -> Path | None:
    """Si le dernier run WFO a un IC significatif, écrit une proposition
    markdown pour relecture humaine. Ne touche à aucun fichier de code.
    Idempotent : un seul fichier par timestamp de run WFO.
    """
    if not wfo_latest or wfo_latest.get("status") != "ok":
        return None
    n_folds = wfo_latest.get("n_folds") or 0
    avg_ic = wfo_latest.get("avg_ic_test")
    if n_folds <= 0 or avg_ic is None or avg_ic < _IC_SIGNIFICANT_THRESHOLD:
        return None

    ts_slug = str(wfo_latest.get("timestamp", "unknown")).replace(":", "-")
    path = WFO_PROPOSAL_DIR / f"proposal_{ts_slug}.md"
    if path.exists():
        return None  # déjà écrit pour ce run WFO

    wfo_full = audit_summary._load_wfo() or {}
    prod = wfo_full.get("prod_weights") or {}
    avg = wfo_full.get("avg_weights") or wfo_latest.get("avg_weights") or {}
    deltas = wfo_full.get("weight_deltas") or {}

    lines = [
        f"# Proposition WFO — {wfo_latest.get('timestamp')}",
        "",
        f"IC test moyen (OOS) : {avg_ic:+.4f} "
        f"(seuil significativité {_IC_SIGNIFICANT_THRESHOLD:+.2f})",
        f"Folds : {n_folds}",
        "",
        "## Poids proposés (moyenne OOS) vs poids production actuels",
        "",
        "| Pilier | Prod | WFO (OOS) | Δ |",
        "|---|---|---|---|",
    ]
    for p in sorted(set(prod) | set(avg)):
        lines.append(
            f"| {p} | {prod.get(p, 0):.3f} | {avg.get(p, 0):.3f} | "
            f"{deltas.get(p, 0):+.3f} |"
        )
    lines += [
        "",
        "**Proposition à relire manuellement — aucun code de scoring n'a été",
        "modifié automatiquement.** Si validée, appliquer les poids soi-même",
        "dans `modules/sector_metrics/_scoring.py`.",
    ]

    try:
        WFO_PROPOSAL_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"[MetricsAgent] Proposition WFO écrite → {path}")
    except OSError as e:
        logger.warning(f"[MetricsAgent] écriture proposition WFO échouée : {e}")
        return None
    return path


# ─────────────────────────────────────────────────────────────────
# Digest Telegram
# ─────────────────────────────────────────────────────────────────

def _trend_arrow(delta: float, *, noise: float = 0.0) -> str:
    if delta > noise:
        return "↗️"
    if delta < -noise:
        return "↘️"
    return "→"


def _build_digest_text(entry: dict[str, Any], previous: dict[str, Any] | None) -> str:
    lines = ["\U0001f4ca <b>TITAN — Agent qualité data & signal</b>", ""]

    stale_ratio = entry.get("stale_ratio")
    if stale_ratio is None:
        lines.append("\U0001f5d3 Staleness fondamentaux : indisponible")
    else:
        trend = ""
        prev_ratio = (previous or {}).get("stale_ratio")
        if prev_ratio is not None:
            d = (stale_ratio - prev_ratio) * 100
            trend = f" {_trend_arrow(d, noise=0.05)} ({d:+.1f}pt)"
        lines.append(
            f"\U0001f5d3 Staleness fondamentaux : {stale_ratio * 100:.1f}%{trend} "
            f"({entry.get('n_severe_stale', 0)} sévères)"
        )

    n_dq = entry.get("n_dq_sanitize")
    if n_dq is None:
        lines.append("⚠️ dq_sanitize : indisponible")
    else:
        trend = ""
        prev_dq = (previous or {}).get("n_dq_sanitize")
        if prev_dq is not None:
            d = n_dq - prev_dq
            trend = f" {_trend_arrow(d)} ({d:+d})"
        lines.append(f"⚠️ dq_sanitize : {n_dq}{trend}")

    wfo = entry.get("wfo") or {}
    ic = wfo.get("avg_ic_test")
    ic_str = f"{ic:+.4f}" if ic is not None else "?"
    lines.append(
        f"\U0001f9ea WFO : {wfo.get('n_folds') or 0} folds (seuil {_MIN_WFO_FOLDS}) "
        f"| IC {ic_str} (significatif ≥ {_IC_SIGNIFICANT_THRESHOLD:+.2f})"
    )

    n_snap = entry.get("n_snapshots", 0)
    check = " ✅" if n_snap >= _MIN_SNAPSHOTS else ""
    lines.append(f"\U0001f4da Snapshots historique : {n_snap}/{_MIN_SNAPSHOTS}{check}")

    trades = entry.get("trades") or {}
    total_closed = trades.get("total_closed", 0)
    check = " ✅" if total_closed >= _MIN_TRADES_FOR_TRUST else ""
    lines.append(
        f"\U0001f4bc Trades clos : {total_closed}/{_MIN_TRADES_FOR_TRUST}{check} "
        f"({trades.get('wins', 0)}W/{trades.get('losses', 0)}L)"
    )

    if entry.get("wfo_proposal_written"):
        lines.append("")
        lines.append(
            "\U0001f195 IC significatif détecté → proposition de poids écrite "
            f"dans <code>{entry['wfo_proposal_written']}</code> "
            "(relecture manuelle requise, aucun code touché)."
        )

    return "\n".join(lines)


def _send_alert(text: str) -> None:
    try:
        from modules.alerter import _send_telegram_message
        _send_telegram_message(text)
        logger.info(f"[MetricsAgent] digest envoyé ({len(text)} chars)")
    except Exception as e:
        logger.warning(f"[MetricsAgent] envoi Telegram échoué : {e}")


# ─────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────

def run_agent(
    *,
    api_url: str | None = None,
    alert: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    """Collecte les métriques du jour, calcule la tendance, persiste et
    envoie le digest Telegram. Retourne l'entrée structurée."""
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    if api_url is None:
        api_url = os.getenv("TITAN_API_URL", "http://localhost:8000")

    previous_snap = _read_history(last_n=1)
    previous = previous_snap[0] if previous_snap else None

    health = _data_health_snapshot(api_url)
    universe = health.get("universe") or {}
    fetched_at = universe.get("fetched_at") or {}
    n_tickers = universe.get("n_tickers") or 0
    n_stale = fetched_at.get("n_stale") or 0
    stale_ratio = round(n_stale / n_tickers, 4) if n_tickers else None

    wfo_hist = wfo_monitor._read_history(last_n=1)
    wfo_latest = wfo_hist[-1] if wfo_hist else None

    entry: dict[str, Any] = {
        "timestamp": timestamp,
        "severity_global": health.get("severity_global"),
        "n_tickers": n_tickers,
        "stale_ratio": stale_ratio,
        "n_severe_stale": fetched_at.get("n_severe") or 0,
        "n_dq_sanitize": (health.get("sanitize") or {}).get("n_tickers_flagged"),
        "n_snapshots": len(universe_history.list_snapshots()),
        "wfo": {
            "n_folds": (wfo_latest or {}).get("n_folds"),
            "avg_ic_test": (wfo_latest or {}).get("avg_ic_test"),
            "status": (wfo_latest or {}).get("status"),
        },
        "trades": _trades_snapshot(),
    }

    proposal_path = _maybe_write_wfo_proposal(wfo_latest)
    entry["wfo_proposal_written"] = str(proposal_path) if proposal_path else None

    if persist:
        _append_history(entry)

    if alert:
        _send_alert(_build_digest_text(entry, previous))

    return entry


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def _main() -> int:
    p = argparse.ArgumentParser(
        prog="metrics_agent",
        description="Agent local d'analyse des chiffres (data quality + WFO + "
                     "trades) — digest Telegram + historique JSONL.",
    )
    p.add_argument("--api-url", default=None,
                   help="Base URL de l'API (défaut: $TITAN_API_URL ou "
                        "http://localhost:8000)")
    p.add_argument("--no-alert", action="store_true",
                   help="Skip l'envoi Telegram.")
    p.add_argument("--no-persist", action="store_true",
                   help="N'écrit pas data/metrics_history.jsonl")
    p.add_argument("--json", action="store_true",
                   help="Sortie JSON sur stdout (sinon : résumé texte).")
    args = p.parse_args()

    entry = run_agent(
        api_url=args.api_url,
        alert=not args.no_alert,
        persist=not args.no_persist,
    )

    if args.json:
        print(json.dumps(entry, indent=2, ensure_ascii=False))
        return 0

    print(f"=== Metrics Agent — {entry['timestamp']} ===")
    print(f"Severity global   : {entry.get('severity_global')}")
    print(f"Staleness ratio   : {entry.get('stale_ratio')}")
    print(f"dq_sanitize       : {entry.get('n_dq_sanitize')}")
    print(f"Snapshots         : {entry.get('n_snapshots')}/{_MIN_SNAPSHOTS}")
    wfo = entry.get("wfo") or {}
    print(f"WFO folds/IC      : {wfo.get('n_folds')} / {wfo.get('avg_ic_test')}")
    trades = entry.get("trades") or {}
    print(f"Trades clos       : {trades.get('total_closed')} "
          f"({trades.get('wins')}W/{trades.get('losses')}L)")
    if entry.get("wfo_proposal_written"):
        print(f"Proposition WFO   : {entry['wfo_proposal_written']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
