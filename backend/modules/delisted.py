"""Registry — tickers historiquement présents dans `universe.json`.

Audit S1.1 (2026-04-27) — survivorship bias.
================================================
`universe_engine.fetch_sp500_tickers()` lit le SP500 / NDX100 *actuel* depuis
Wikipedia. Tout backtest qui ne consomme que `data/universe.json` voit donc
exclusivement les survivants — les sociétés délistées (faillite, M&A, sortie
d'index) sont invisibles, ce qui gonfle artificiellement les performances
historiques.

Solution minimale :
  • À chaque `save_universe()`, on détecte les tickers qui étaient présents
    dans le payload précédent ET absents du nouveau, et on les enregistre
    dans `data/delisted.json` avec :
        - `first_seen`   : ISO date de la 1re apparition
        - `removed_at`   : ISO date à laquelle le ticker a disparu
        - `last_sector`  : secteur GICS au moment du retrait (utile filtres)
        - `last_payload` : snapshot fundamental au moment du retrait
                           (gardé pour rejouer les scores rétro-actifs).
  • L'historique vit dans un seul JSON ; pour 500 tickers et un turnover de
    ~10/an on tient au-dessous de 1 MB sur 20 ans.

Usage backtest :
    from modules import delisted
    active = delisted.get_active_at(target_date)  # set[str]
    # → universe = (current ∪ delisted_avant_target) ∩ snapshot.tickers
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from modules.log import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DELISTED_PATH = _PROJECT_ROOT / "data" / "delisted.json"

# Audit S1.1 — extension Telegram (2026-04-27).
# Au-delà de ce nombre de tickers retirés en une seule passe, on push une
# alerte Telegram. Calibration : un index reshuffle SP500 typique = 5-10
# tickers (annual rebalance). Au-dessus de 5, ça mérite une notification —
# soit un événement réel (M&A wave), soit un bug pipeline (mauvaise lecture
# Wikipedia → faux positif de masse). Dans les deux cas on veut savoir.
_DELISTED_ALERT_THRESHOLD = 5

# Sécurise les writes concurrents (auto_proposer + cron + scheduler peuvent se
# chevaucher en théorie). FileLock serait plus solide mais le contrat de cette
# fonction est "best-effort, fail-open" — un Lock thread-local est suffisant
# car save_universe est lui-même protégé par FileLock.
_LOCK = Lock()


def _today_iso() -> str:
    return datetime.now(UTC).date().isoformat()


def load_registry() -> dict[str, Any]:
    """Charge le registry. Squelette vide si fichier absent ou corrompu.

    Schéma : {"version": 1, "tickers": {ticker: {first_seen, removed_at, ...}}}
    """
    if not DELISTED_PATH.exists():
        return {"version": 1, "tickers": {}}
    try:
        return json.loads(DELISTED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"[Delisted] Load failed: {e} — repartant à zéro")
        return {"version": 1, "tickers": {}}


def _save_registry(payload: dict[str, Any]) -> None:
    """Atomic write : tmp → rename. Fail-open pour ne pas bloquer save_universe."""
    DELISTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DELISTED_PATH.with_suffix(".json.tmp")
    try:
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(DELISTED_PATH)
    except OSError as e:
        logger.warning(f"[Delisted] Save failed: {e}")


def record_diff(
    prev_tickers: dict[str, dict[str, Any]] | None,
    new_tickers: dict[str, dict[str, Any]],
    *,
    today: str | None = None,
    alert: bool = True,
) -> dict[str, list[str]]:
    """Met à jour `delisted.json` à partir d'un diff prev → new.

    Side effects :
      • Tickers présents prev mais pas new → marqués `removed_at = today`,
        `last_payload` snappé.
      • Tickers nouveaux dans new (jamais vus) → enregistrés avec
        `first_seen = today` (le registry sert aussi de history of arrivals).
      • Si `len(removed) >= _DELISTED_ALERT_THRESHOLD` ET `alert=True` :
        alerte Telegram (utile pour repérer index reshuffle ou bug pipeline).

    Retourne un summary `{"added": [...], "removed": [...]}` pour le log appelant.
    """
    today = today or _today_iso()
    summary: dict[str, list[str]] = {"added": [], "removed": []}

    with _LOCK:
        registry = load_registry()
        ledger: dict[str, dict[str, Any]] = registry.setdefault("tickers", {})

        prev_set = set((prev_tickers or {}).keys())
        new_set = set(new_tickers.keys())

        # 1. Removals
        for t in sorted(prev_set - new_set):
            entry = ledger.get(t) or {}
            # Si le ticker était déjà flaggé delisted (re-entrée puis re-sortie),
            # on enregistre la dernière date de retrait — pas besoin d'historiser
            # tous les cycles, on veut juste savoir "était-il actif à date T ?".
            entry["removed_at"] = today
            entry.setdefault("first_seen", today)
            prev_payload = (prev_tickers or {}).get(t) or {}
            entry["last_sector"] = prev_payload.get("sector")
            entry["last_payload"] = prev_payload
            ledger[t] = entry
            summary["removed"].append(t)

        # 2. Additions (incl. réincarnations) — on note first_seen si jamais vu,
        # et on efface removed_at en cas de re-entrée (ticker à nouveau actif).
        for t in sorted(new_set - prev_set):
            entry = ledger.get(t) or {}
            entry.setdefault("first_seen", today)
            entry["removed_at"] = None  # actif → pas de delisting courant
            ledger[t] = entry
            summary["added"].append(t)

        _save_registry(registry)

    if summary["added"] or summary["removed"]:
        logger.info(
            f"[Delisted] universe diff @ {today} : "
            f"+{len(summary['added'])} added, "
            f"-{len(summary['removed'])} removed"
        )

    # Alerte Telegram sur delisting massif (fail-open, ne bloque jamais le
    # pipeline universe_engine).
    if alert and len(summary["removed"]) >= _DELISTED_ALERT_THRESHOLD:
        try:
            from modules.alerter import _send_telegram_message
            sample = summary["removed"][:10]
            sample_str = ", ".join(sample)
            if len(summary["removed"]) > 10:
                sample_str += f", … (+{len(summary['removed']) - 10} autres)"
            # On joint le secteur quand connu pour distinguer un reshuffle
            # sectoriel (mass exit Tech ?) d'un événement diffus.
            sectors = {}
            for t in summary["removed"]:
                sec = ((prev_tickers or {}).get(t) or {}).get("sector") or "?"
                sectors[sec] = sectors.get(sec, 0) + 1
            top_sectors = sorted(sectors.items(), key=lambda kv: -kv[1])[:3]
            sec_str = " · ".join(f"{s} ×{n}" for s, n in top_sectors)
            msg = (
                "📉 <b>TITAN — Delisting massif détecté</b>\n"
                f"{len(summary['removed'])} tickers retirés de l'univers @ {today} "
                f"(seuil alerte ≥ {_DELISTED_ALERT_THRESHOLD})\n"
                f"Échantillon : <code>{sample_str}</code>\n"
                f"Secteurs : {sec_str}\n"
                f"Audit : <code>data/delisted.json</code> ou GET /api/delisted\n"
                "Hypothèses : (1) index reshuffle SP500 / NDX100, "
                "(2) M&A wave, (3) bug pipeline → vérifier source Wikipedia."
            )
            _send_telegram_message(msg)
            logger.info(
                f"[Delisted] alerte Telegram envoyée "
                f"({len(summary['removed'])} tickers ≥ seuil {_DELISTED_ALERT_THRESHOLD})"
            )
        except Exception as e:
            logger.warning(f"[Delisted] alerte Telegram échouée: {e}")

    return summary


def get_active_at(target: date | str, *, current_tickers: set[str] | None = None) -> set[str]:
    """Retourne le set des tickers qui étaient actifs à `target`.

    Définition "actif à T" :
        first_seen ≤ T ET (removed_at est None OU removed_at > T).

    Args:
        target          : date d'évaluation (ISO string ou date).
        current_tickers : si fourni, filtre supplémentaire pour intersect avec
                          la composition courante de `universe.json` (utile
                          quand on teste un univers étendu — tickers actifs
                          historiquement mais hors univers aujourd'hui sont
                          retournés *en plus*, sans être filtrés).

    Si le registry est vide (premier run), retourne `current_tickers or set()`.
    """
    target_iso = target.isoformat() if isinstance(target, date) else str(target)
    registry = load_registry()
    tickers_meta: dict[str, dict[str, Any]] = registry.get("tickers") or {}

    if not tickers_meta:
        return set(current_tickers) if current_tickers else set()

    out: set[str] = set()
    for tk, meta in tickers_meta.items():
        first = meta.get("first_seen")
        removed = meta.get("removed_at")
        if first and first > target_iso:
            continue
        if removed and removed <= target_iso:
            continue
        out.add(tk)
    if current_tickers:
        # Tickers courants jamais passés par delisted (premier run) : on les inclut.
        out |= set(current_tickers)
    return out


def list_delisted() -> list[dict[str, Any]]:
    """Retourne les tickers actuellement marqués delisted (removed_at non null).

    Trié par removed_at desc — utile pour audit / UI.
    """
    registry = load_registry()
    out: list[dict[str, Any]] = []
    for tk, meta in (registry.get("tickers") or {}).items():
        if meta.get("removed_at"):
            out.append({
                "ticker":     tk,
                "first_seen": meta.get("first_seen"),
                "removed_at": meta.get("removed_at"),
                "sector":     meta.get("last_sector"),
            })
    out.sort(key=lambda r: r.get("removed_at") or "", reverse=True)
    return out
