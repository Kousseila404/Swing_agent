"""Helpers purs du moteur portfolio."""
from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from typing import Any


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _age_days_from_iso(iso_ts: Any, now: float | None = None) -> float | None:
    """Convertit un timestamp ISO ("2026-04-17T16:10:22Z" ou avec offset) en âge
    en jours (float) depuis maintenant. None si non-parseable — le caller
    traite ça comme "âge inconnu" plutôt que crash.
    """
    if not isinstance(iso_ts, str) or not iso_ts:
        return None
    try:
        normalized = iso_ts.replace("Z", "+00:00") if iso_ts.endswith("Z") else iso_ts
        dt = datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    now_ts = now if now is not None else time.time()
    age_sec = now_ts - dt.timestamp()
    return max(0.0, age_sec / 86400.0)
