"""TITAN threshold alerts — user-defined seuils par ticker (Tier B #3).

Use case : "alerter Telegram si AAPL passe TITAN ≥ 75". Permet à l'utilisateur
de surveiller passivement des tickers qui ne sont pas (encore) dans son
portefeuille. Le module monitor_alerts daily évalue ces alertes en plus des
signaux natifs (drift entry, drop 7j, support break).

Storage : JSON + FileLock (cohérent avec proposals.py — pas de DuckDB pour
un payload < 1 KB qui mute rarement). Schéma :

    {
      "version": 1,
      "alerts": [
        {
          "id":         "ALT_AB12CD34",
          "ticker":     "AAPL",
          "direction":  "above" | "below",
          "threshold":  75.0,
          "created_at": "2026-04-29T10:30:00Z",
          "last_fired_at": null | "2026-04-30T...",
          "fire_count":   0
        }
      ]
    }

Une alerte est "armed" tant qu'elle n'a pas été déclenchée. Après firing :
- last_fired_at est mis à jour
- fire_count incrémenté
- l'alerte reste active (l'utilisateur la supprime explicitement). Cooldown
  anti-spam de COOLDOWN_HOURS entre 2 firings successifs.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from filelock import FileLock

from modules import api_core
from modules.log import logger

ALERTS_PATH = api_core.BASE / "data" / "titan_alerts.json"
ALERTS_LOCK_PATH = api_core.BASE / "data" / "titan_alerts.json.lock"

# Cooldown anti-spam — une alerte qui a fired récemment ne re-fire pas avant
# COOLDOWN_HOURS. Sinon on enverrait une notif tous les jours tant que la
# condition reste vraie.
COOLDOWN_HOURS = 24

# Direction : 'above' = TITAN ≥ threshold, 'below' = TITAN ≤ threshold.
# (Pas de cross_up/cross_down pour rester simple — l'utilisateur peut combiner
# above + delete-on-fire dans un futur enhancement.)
ALLOWED_DIRECTIONS = {"above", "below"}


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@dataclass
class TitanAlert:
    id:             str
    ticker:         str
    direction:      str  # 'above' | 'below'
    threshold:      float
    created_at:     str
    last_fired_at:  str | None = None
    fire_count:     int = 0
    note:           str | None = None  # optionnel — contexte utilisateur


def _read_all_unlocked() -> list[dict[str, Any]]:
    if not ALERTS_PATH.exists():
        return []
    try:
        raw = json.loads(ALERTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error(f"[titan_alerts] read failed: {exc}")
        return []
    return list(raw.get("alerts") or [])


def _write_all_unlocked(items: list[dict[str, Any]]) -> None:
    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "alerts": items}
    tmp = ALERTS_PATH.with_suffix(f".tmp.{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ALERTS_PATH)


def list_alerts() -> list[dict[str, Any]]:
    """Liste toutes les alertes (sans lock — read-only)."""
    return _read_all_unlocked()


def add_alert(
    *,
    ticker: str,
    direction: str,
    threshold: float,
    note: str | None = None,
) -> dict[str, Any]:
    """Crée une nouvelle alerte. Idempotent par (ticker, direction, threshold) —
    si une alerte identique existe déjà, on retourne l'existante.
    """
    ticker = (ticker or "").upper().strip()
    direction = (direction or "").lower().strip()
    if not ticker:
        raise ValueError("ticker requis")
    if direction not in ALLOWED_DIRECTIONS:
        raise ValueError(f"direction invalide: {direction!r} (attendu: above | below)")
    try:
        thr = float(threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"threshold invalide: {threshold!r}") from exc
    if not (0 <= thr <= 100):
        raise ValueError(f"threshold hors bornes [0, 100]: {thr}")

    ALERTS_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(ALERTS_LOCK_PATH), timeout=10):
        items = _read_all_unlocked()
        # Dédup : même ticker + direction + seuil → on retourne l'existante.
        for it in items:
            if (
                it.get("ticker") == ticker
                and it.get("direction") == direction
                and abs(float(it.get("threshold") or 0) - thr) < 0.01
            ):
                return it
        new_alert = TitanAlert(
            id=f"ALT_{uuid.uuid4().hex[:8].upper()}",
            ticker=ticker,
            direction=direction,
            threshold=round(thr, 2),
            created_at=_iso(_now_utc()),
            note=note,
        )
        as_dict = asdict(new_alert)
        items.append(as_dict)
        _write_all_unlocked(items)
        return as_dict


def remove_alert(alert_id: str) -> bool:
    """Supprime une alerte par id. Retourne True si supprimée."""
    alert_id = (alert_id or "").strip()
    if not alert_id:
        return False
    ALERTS_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(ALERTS_LOCK_PATH), timeout=10):
        items = _read_all_unlocked()
        before = len(items)
        items = [it for it in items if it.get("id") != alert_id]
        if len(items) == before:
            return False
        _write_all_unlocked(items)
        return True


def evaluate_alerts(scored_universe: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Parcourt les alertes et retourne celles déclenchées (cooldown respecté).

    Met à jour `last_fired_at` + `fire_count` sur les alertes firing, mais ne
    supprime pas — l'utilisateur garde le contrôle. Le caller (monitor_alerts)
    est responsable de l'envoi Telegram.
    """
    if not scored_universe:
        return []

    cooldown_cutoff = _now_utc() - timedelta(hours=COOLDOWN_HOURS)
    triggered: list[dict[str, Any]] = []

    ALERTS_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(ALERTS_LOCK_PATH), timeout=10):
        items = _read_all_unlocked()
        any_change = False
        for it in items:
            t = it.get("ticker")
            if not t:
                continue
            row = scored_universe.get(t) or {}
            cur = row.get("titan_composite_score")
            if not isinstance(cur, (int, float)):
                continue
            direction = it.get("direction")
            thr = float(it.get("threshold") or 0)
            fired = (
                (direction == "above" and float(cur) >= thr)
                or (direction == "below" and float(cur) <= thr)
            )
            if not fired:
                continue
            # Cooldown
            last = it.get("last_fired_at")
            if last:
                try:
                    last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                    if last_dt > cooldown_cutoff:
                        continue
                except ValueError:
                    pass
            it["last_fired_at"] = _iso(_now_utc())
            it["fire_count"] = int(it.get("fire_count") or 0) + 1
            triggered.append({
                **it,
                "current_titan": round(float(cur), 2),
            })
            any_change = True
        if any_change:
            _write_all_unlocked(items)
    return triggered
