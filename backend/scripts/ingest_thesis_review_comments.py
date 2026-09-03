"""Ingère les commentaires de l'issue GitHub #14 (propositions de la routine
cloud mensuelle de revue de thèse BNP.PA) dans `data/thesis_review_queue.json`.

Voir `scripts/publish_thesis_review_context.py` pour le contexte du pont
GitHub (le sandbox cloud ne peut pas atteindre swing.webcatalyste.fr
directement).

CONTRAINTE NON NÉGOCIABLE : ce script n'importe JAMAIS
`modules.my_portfolio_thesis` et n'appelle jamais `update_thesis` — il ne
fait qu'ajouter des entrées via `thesis_review_queue.add_entry`, exactement
la même garantie structurelle que `routers/thesis_review_queue.py`. Un
commentaire malveillant/mal formé sur l'issue ne peut au pire que produire
une entrée invalide dans la file de propositions (que l'humain ignore dans
l'UI) — jamais écrire dans verification.*.

Idempotent : suit le dernier id de commentaire ingéré dans un state local
(`data/.thesis_review_ingest_state.json`, gitignored) — jamais ré-ingéré
deux fois, même si le script est relancé plusieurs fois par erreur.

Usage :
    python -m scripts.ingest_thesis_review_comments
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import requests  # noqa: E402

from modules import thesis_review_queue  # noqa: E402
from modules.log import logger  # noqa: E402

REPO = "Kousseila404/Swing_agent"
ISSUE_NUMBER = 14
TICKER = "BNP.PA"
STATE_PATH = _BACKEND_ROOT / "data" / ".thesis_review_ingest_state.json"

_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _github_token() -> str:
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN absent — impossible d'ingérer")
    return token


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"last_comment_id": 0}
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"last_comment_id": 0}
    return payload if isinstance(payload, dict) else {"last_comment_id": 0}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def fetch_comments() -> list[dict[str, Any]]:
    r = requests.get(
        f"https://api.github.com/repos/{REPO}/issues/{ISSUE_NUMBER}/comments",
        headers={"Authorization": f"Bearer {_github_token()}", "Accept": "application/vnd.github+json"},
        params={"per_page": 100},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def parse_submission(comment_body: str) -> dict[str, Any] | None:
    """Extrait le bloc JSON d'une proposition depuis un commentaire.

    Fail-open : `None` si absent/invalide — un commentaire humain sur cette
    issue (ou un commentaire malformé) ne doit jamais planter l'ingestion,
    juste être ignoré.
    """
    m = _JSON_BLOCK_RE.search(comment_body)
    if not m:
        return None
    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or "execution_type" not in payload:
        return None
    return payload


def ingest() -> int:
    """Ingère les nouveaux commentaires depuis le dernier run. Retourne le
    nombre d'entrées effectivement ajoutées à la file."""
    state = _load_state()
    last_id = state.get("last_comment_id", 0)
    comments = fetch_comments()
    new_comments = sorted((c for c in comments if c["id"] > last_id), key=lambda c: c["id"])

    ingested = 0
    for c in new_comments:
        payload = parse_submission(c.get("body", ""))
        if payload is None:
            logger.info(f"[thesis_review_bridge] commentaire #{c['id']} ignoré (pas de bloc JSON valide)")
        else:
            try:
                thesis_review_queue.add_entry(
                    TICKER,
                    execution_type=payload["execution_type"],
                    findings=payload.get("findings", []),
                    proposed_verdict=payload.get("proposed_verdict"),
                )
                ingested += 1
                logger.info(f"[thesis_review_bridge] commentaire #{c['id']} ingéré ({payload['execution_type']})")
            except ValueError as e:
                logger.warning(f"[thesis_review_bridge] commentaire #{c['id']} invalide, ignoré: {e}")
        state["last_comment_id"] = c["id"]

    _save_state(state)
    return ingested


if __name__ == "__main__":
    n = ingest()
    print(f"Entrées ingérées : {n}")
