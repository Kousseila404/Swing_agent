"""File de révision de thèse — propositions non validées (routines cloud).

CONTRAINTE NON NÉGOCIABLE : ce module ne modifie JAMAIS
`data/my_portfolio_thesis.json` (ni son bloc `verification`, ni aucun autre
champ) — il n'importe même pas `modules.my_portfolio_thesis`. Il ne gère
qu'un store séparé, `data/thesis_review_queue.json` : une file de
PROPOSITIONS non validées, pas une source de vérité. Le seul chemin qui
écrit dans `verification.derniere_verification`/`verdict` reste
`PATCH /api/my_portfolio/{ticker}/thesis` (my_portfolio_thesis.update_thesis),
déclenché par une validation humaine dans l'UI.

Alimenté typiquement par une routine cloud (Claude Code Routine, mensuelle)
authentifiée avec `THESIS_REVIEW_TOKEN` — un token à portée restreinte
(modules/api_core.py) qui échoue systématiquement contre `require_auth`
(donc contre `PATCH .../thesis`). L'impossibilité d'écrire dans
`verification` est garantie par la séparation d'authentification au niveau
routeur, pas seulement par une consigne dans le prompt de la routine — voir
`routers/thesis_review_queue.py` et les tests associés.

Persistance : même pattern IO que `modules/my_portfolio_thesis.py` (FileLock
+ écriture atomique `.tmp` + rename).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from modules.log import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORE_PATH = _PROJECT_ROOT / "data" / "thesis_review_queue.json"
_LOCK_PATH = _PROJECT_ROOT / "data" / "thesis_review_queue.json.lock"

SCHEMA_VERSION = 1

EXECUTION_TYPES = ("complete", "light")
ENTRY_STATUSES = ("pending", "validated", "dismissed")


def _load_store_unlocked() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return {"schema_version": SCHEMA_VERSION, "entries": {}}
    try:
        payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"[thesis_review_queue] lecture échouée, store vide retourné: {e}")
        return {"schema_version": SCHEMA_VERSION, "entries": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
        return {"schema_version": SCHEMA_VERSION, "entries": {}}
    return payload


def _save_store_unlocked(store: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(STORE_PATH.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STORE_PATH)
    except Exception as e:
        logger.error(f"[thesis_review_queue] écriture échouée: {e}")
        tmp.unlink(missing_ok=True)
        raise


def _validate_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in findings:
        constat = (f.get("constat") or "").strip()
        if not constat:
            raise ValueError("chaque finding nécessite un 'constat' non vide")
        out.append({
            "topic": (f.get("topic") or "").strip() or None,
            "constat": constat,
            "source": (f.get("source") or "").strip() or None,
        })
    return out


def add_entry(
    ticker: str,
    *,
    execution_type: str,
    findings: list[dict[str, Any]],
    proposed_verdict: str | None = None,
    run_date: str | None = None,
) -> dict[str, Any]:
    """Ajoute une proposition de révision — APPEND ONLY. Ne touche jamais
    `data/my_portfolio_thesis.json` (voir contrainte en tête de module).
    """
    if execution_type not in EXECUTION_TYPES:
        raise ValueError(f"execution_type invalide {execution_type!r} — attendu un de {EXECUTION_TYPES}")

    entry = {
        "id": f"rev_{uuid.uuid4().hex[:10]}",
        "ticker": ticker,
        "date": run_date or datetime.now(timezone.utc).date().isoformat(),
        "execution_type": execution_type,
        "findings": _validate_findings(findings),
        "proposed_verdict": (proposed_verdict or "").strip() or None,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    with FileLock(str(_LOCK_PATH), timeout=10):
        store = _load_store_unlocked()
        store["entries"].setdefault(ticker, []).append(entry)
        store["schema_version"] = SCHEMA_VERSION
        _save_store_unlocked(store)
    return entry


def list_entries(ticker: str) -> list[dict[str, Any]]:
    """Lecture pure — entrées d'un ticker, ordre chronologique (plus récent
    en dernier). Liste vide si aucune entrée (jamais de KeyError)."""
    with FileLock(str(_LOCK_PATH), timeout=10):
        store = _load_store_unlocked()
    return store["entries"].get(ticker, [])


def update_entry_status(ticker: str, entry_id: str, status: str) -> dict[str, Any]:
    """Marque une entrée validée/ignorée — action humaine depuis l'UI,
    réservée au token complet côté routeur (jamais au token de revue
    restreint). Ne touche que ce store, jamais verification.*.
    """
    if status not in ENTRY_STATUSES:
        raise ValueError(f"status invalide {status!r} — attendu un de {ENTRY_STATUSES}")

    with FileLock(str(_LOCK_PATH), timeout=10):
        store = _load_store_unlocked()
        entries = store["entries"].get(ticker, [])
        entry = next((e for e in entries if e["id"] == entry_id), None)
        if entry is None:
            raise KeyError(f"entrée {entry_id!r} introuvable pour {ticker!r}")
        entry["status"] = status
        entry["status_updated_at"] = datetime.now(timezone.utc).isoformat()
        _save_store_unlocked(store)
        return entry
