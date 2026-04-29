"""Price alerts — seuils prix multi-niveaux par ticker.

Use case : depuis un entry_plan généré par /api/ticker_analysis, l'utilisateur
peut watcher chaque tier (limit -8%, -15%, …) pour être ping Telegram quand le
cours touche le niveau. Permet d'opérationnaliser le "wait pullback" sans avoir
à surveiller manuellement.

Diffère de `titan_alerts` (qui watch des seuils TITAN composite, pas des prix)
et de `watchlist` (1 target_buy unique par ticker, généraliste). Ici plusieurs
alertes coexistent sur un même ticker (1 par tier).

Storage : JSON + FileLock (cohérent avec titan_alerts.py — payload < quelques
KB, mute peu).

    {
      "version": 1,
      "alerts": [
        {
          "id":           "PRA_AB12CD34",
          "ticker":       "MU",
          "direction":    "below",          # below | above
          "target_price": 477.66,
          "weight_pct":   40.0,             # poids du tier (info)
          "note":         "Tier 1 -8% entry_plan",
          "created_at":   "2026-04-29T10:30:00Z",
          "expires_at":   null,
          "last_fired_at": null,
          "fire_count":    0
        }
      ]
    }

Cooldown anti-spam de COOLDOWN_HOURS entre 2 firings successifs. Une alerte
expirée (`expires_at` < now) est ignorée par l'évaluation mais reste listée
pour transparence (l'utilisateur la supprime).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from filelock import FileLock

from modules import api_core
from modules.log import logger

ALERTS_PATH = api_core.BASE / "data" / "price_alerts.json"
ALERTS_LOCK_PATH = api_core.BASE / "data" / "price_alerts.json.lock"

COOLDOWN_HOURS = 24
ALLOWED_DIRECTIONS = {"above", "below"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@dataclass
class PriceAlert:
    id:             str
    ticker:         str
    direction:      str  # 'below' | 'above'
    target_price:   float
    created_at:     str
    weight_pct:     float | None = None
    note:           str | None = None
    expires_at:     str | None = None
    last_fired_at:  str | None = None
    fire_count:     int = 0
    # Capture du prix au moment de la création — permet de mesurer
    # `discount_captured = (target - created) / created` quand l'alerte fire
    # et donc l'efficacité du "wait pullback" sur l'historique.
    created_price:        float | None = None
    fired_price:          float | None = None  # prix au premier firing


def _read_all_unlocked() -> list[dict[str, Any]]:
    if not ALERTS_PATH.exists():
        return []
    try:
        raw = json.loads(ALERTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error(f"[price_alerts] read failed: {exc}")
        return []
    return list(raw.get("alerts") or [])


def _write_all_unlocked(items: list[dict[str, Any]]) -> None:
    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "alerts": items}
    tmp = ALERTS_PATH.with_suffix(f".tmp.{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ALERTS_PATH)


def list_alerts(*, ticker: str | None = None,
                include_expired: bool = True) -> list[dict[str, Any]]:
    """Liste les alertes (filtrables par ticker)."""
    items = _read_all_unlocked()
    if ticker:
        t = ticker.upper().strip()
        items = [it for it in items if it.get("ticker") == t]
    if not include_expired:
        now = _now_utc()
        items = [it for it in items if not _is_expired(it, now)]
    return items


def _is_expired(it: dict[str, Any], now: datetime) -> bool:
    exp = it.get("expires_at")
    if not exp:
        return False
    try:
        return datetime.fromisoformat(exp.replace("Z", "+00:00")) < now
    except (ValueError, AttributeError):
        return False


def _lookup_current_price(ticker: str) -> float | None:
    """Best-effort lookup du dernier close OHLCV. None si indisponible."""
    try:
        from modules.market_db import read_ohlcv
        df = read_ohlcv(ticker, days=5)
        if df is None or df.empty or "Close" not in df.columns:
            return None
        last = df["Close"].dropna()
        if last.empty:
            return None
        v = float(last.iloc[-1])
        return v if v > 0 else None
    except Exception:
        return None


def add_alert(
    *,
    ticker: str,
    target_price: float,
    direction: str = "below",
    weight_pct: float | None = None,
    note: str | None = None,
    ttl_days: int | None = None,
    created_price: float | None = None,
) -> dict[str, Any]:
    """Crée une alerte prix. Idempotent par (ticker, direction, target_price).

    Args:
        ticker: symbole (uppercased)
        target_price: niveau de déclenchement (> 0)
        direction: 'below' (price ≤ target) ou 'above' (price ≥ target)
        weight_pct: poids du tier (0-100) — info seulement, pas utilisé pour fire
        note: contexte libre (ex. "Tier 1 entry_plan")
        ttl_days: si fourni, expires_at = now + ttl_days
        created_price: prix au moment de la création. Si None, on tente un
            lookup OHLCV best-effort. Permet d'évaluer l'efficacité du
            wait-pullback (discount_captured = target/created - 1).
    """
    ticker = (ticker or "").upper().strip()
    direction = (direction or "below").lower().strip()
    if not ticker:
        raise ValueError("ticker requis")
    if direction not in ALLOWED_DIRECTIONS:
        raise ValueError(f"direction invalide: {direction!r}")
    try:
        tgt = float(target_price)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"target_price invalide: {target_price!r}") from exc
    if tgt <= 0:
        raise ValueError(f"target_price doit être > 0: {tgt}")

    expires_at: str | None = None
    if ttl_days is not None:
        try:
            d = int(ttl_days)
            if d > 0:
                expires_at = _iso(_now_utc() + timedelta(days=d))
        except (TypeError, ValueError):
            pass

    # Capture le prix de référence à la création si non fourni.
    if created_price is None:
        created_price = _lookup_current_price(ticker)

    ALERTS_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(ALERTS_LOCK_PATH), timeout=10):
        items = _read_all_unlocked()
        for it in items:
            if (
                it.get("ticker") == ticker
                and it.get("direction") == direction
                and abs(float(it.get("target_price") or 0) - tgt) < 0.01
            ):
                return it
        new_alert = PriceAlert(
            id=f"PRA_{uuid.uuid4().hex[:8].upper()}",
            ticker=ticker,
            direction=direction,
            target_price=round(tgt, 4),
            weight_pct=round(float(weight_pct), 2) if weight_pct is not None else None,
            note=note,
            created_at=_iso(_now_utc()),
            expires_at=expires_at,
            created_price=round(created_price, 4) if created_price is not None else None,
        )
        as_dict = asdict(new_alert)
        items.append(as_dict)
        _write_all_unlocked(items)
        return as_dict


def remove_alert(alert_id: str) -> bool:
    """Supprime une alerte par id."""
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


def compute_stats() -> dict[str, Any]:
    """Statistiques sur l'efficacité du wait-pullback.

    Sur l'historique des alertes (toutes confondues, expirées comprises) :

      - hit_rate : % d'alertes qui ont fired au moins une fois
      - median_days_to_fire : médiane des durées (created_at → last_fired_at)
        pour les fired
      - median_discount_captured : (target − created) / created sur les
        fired *avec* created_price connu — négatif = on a réussi à acheter
        moins cher que le prix au moment de la création de l'alerte
      - n_active / n_fired / n_expired_unfired

    Permet de répondre concrètement : "est-ce que le wait pullback gagne ?"
    """
    items = _read_all_unlocked()
    n_total = len(items)
    if n_total == 0:
        return {
            "n_total": 0, "n_fired": 0, "n_active": 0, "n_expired_unfired": 0,
            "hit_rate_pct": None,
            "median_days_to_fire": None,
            "median_discount_captured_pct": None,
        }

    now = _now_utc()
    n_fired = 0
    n_active = 0
    n_expired_unfired = 0
    days_to_fire: list[float] = []
    discounts: list[float] = []

    for it in items:
        fired = bool(it.get("last_fired_at"))
        expired = _is_expired(it, now)
        if fired:
            n_fired += 1
            try:
                ca = datetime.fromisoformat(it["created_at"].replace("Z", "+00:00"))
                fa = datetime.fromisoformat(it["last_fired_at"].replace("Z", "+00:00"))
                days_to_fire.append((fa - ca).total_seconds() / 86400.0)
            except (ValueError, KeyError, TypeError, AttributeError):
                pass
            cp = it.get("created_price")
            tgt = it.get("target_price")
            if cp and tgt and cp > 0:
                try:
                    discounts.append((float(tgt) - float(cp)) / float(cp) * 100.0)
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
        elif expired:
            n_expired_unfired += 1
        else:
            n_active += 1

    def _median(values: list[float]) -> float | None:
        if not values:
            return None
        s = sorted(values)
        n = len(s)
        return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2.0

    return {
        "n_total":            n_total,
        "n_fired":            n_fired,
        "n_active":           n_active,
        "n_expired_unfired":  n_expired_unfired,
        "hit_rate_pct":       round(n_fired / n_total * 100.0, 1) if n_total else None,
        "median_days_to_fire": round(_median(days_to_fire), 1)
            if days_to_fire else None,
        "median_discount_captured_pct": round(_median(discounts), 2)
            if discounts else None,
    }


def evaluate_alerts(prices_by_ticker: dict[str, float]) -> list[dict[str, Any]]:
    """Évalue les alertes contre un snapshot de prix courants.

    Args:
        prices_by_ticker: { "MU": 519.20, "AAPL": 178.45, … }

    Returns:
        Liste des alertes déclenchées (avec current_price ajouté). Met à jour
        `last_fired_at` + `fire_count` dans le storage. Le caller (monitor_alerts)
        est responsable de l'envoi Telegram.
    """
    if not prices_by_ticker:
        return []

    now = _now_utc()
    cooldown_cutoff = now - timedelta(hours=COOLDOWN_HOURS)
    triggered: list[dict[str, Any]] = []

    ALERTS_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(ALERTS_LOCK_PATH), timeout=10):
        items = _read_all_unlocked()
        any_change = False
        for it in items:
            t = it.get("ticker")
            if not t or _is_expired(it, now):
                continue
            cur = prices_by_ticker.get(t)
            if not isinstance(cur, (int, float)):
                continue
            direction = it.get("direction")
            tgt = float(it.get("target_price") or 0)
            fired = (
                (direction == "below" and float(cur) <= tgt)
                or (direction == "above" and float(cur) >= tgt)
            )
            if not fired:
                continue
            last = it.get("last_fired_at")
            if last:
                try:
                    last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                    if last_dt > cooldown_cutoff:
                        continue
                except ValueError:
                    pass
            it["last_fired_at"] = _iso(now)
            it["fire_count"] = int(it.get("fire_count") or 0) + 1
            # Capture le prix au PREMIER firing (laisse intact les firings suivants).
            if it.get("fired_price") is None:
                it["fired_price"] = round(float(cur), 4)
            triggered.append({**it, "current_price": round(float(cur), 4)})
            any_change = True
        if any_change:
            _write_all_unlocked(items)
    return triggered
