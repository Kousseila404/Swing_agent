"""Publie le contexte de revue de thèse (BNP.PA) sur une issue GitHub —
le seul canal réseau atteignable par la routine cloud mensuelle.

Contexte (audit 2026-09-03) : le sandbox CCR qui exécute la routine cloud
bloque tout accès sortant vers un domaine personnalisé (swing.webcatalyste.fr
n'est pas sur la liste blanche du proxy de sortie de l'environnement) — seul
github.com/api.github.com est atteignable (déjà utilisé par d'autres
routines via `gh`). Ce script + `scripts/ingest_thesis_review_comments.py`
forment un pont : le corps de l'issue #14 porte le contexte (lu par la
routine), les commentaires portent ses propositions (ingérés côté VPS).

CONTRAINTE : lecture seule côté backend (`my_portfolio_thesis.get_thesis`,
`thesis_review_queue.list_entries`) — n'écrit jamais dans
`data/my_portfolio_thesis.json`, seulement sur GitHub.

Usage :
    python -m scripts.publish_thesis_review_context
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import requests  # noqa: E402

from modules import my_portfolio_thesis, thesis_review_queue  # noqa: E402
from modules.log import logger  # noqa: E402

REPO = "Kousseila404/Swing_agent"
ISSUE_NUMBER = 14
TICKER = "BNP.PA"

_MARKER_START = "<!-- thesis-review-context:start -->"
_MARKER_END = "<!-- thesis-review-context:end -->"


def _github_token() -> str:
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN absent — impossible de publier le contexte")
    return token


def build_context() -> dict[str, Any]:
    """Snapshot pur (aucun accès réseau) — testable sans mock GitHub."""
    thesis = my_portfolio_thesis.get_thesis(TICKER)
    recent = thesis_review_queue.list_entries(TICKER)[-3:]
    return {
        "ticker": TICKER,
        "verification": thesis["verification"],
        "recent_review_queue": recent,
        "published_at": datetime.now(UTC).isoformat(),
    }


def render_body(context: dict[str, Any]) -> str:
    block = json.dumps(context, indent=2, ensure_ascii=False)
    return (
        "Canal de communication entre la routine cloud mensuelle de revue de "
        f"thèse ({context['ticker']}) et le backend SwingQuant.\n\n"
        "- Ce corps est régénéré automatiquement (`scripts/publish_thesis_review_context.py`) "
        "— ne l'éditez pas à la main.\n"
        "- Les commentaires postés ici par la routine cloud sont ingérés automatiquement "
        "dans `data/thesis_review_queue.json` (`scripts/ingest_thesis_review_comments.py`), "
        "jamais dans `data/my_portfolio_thesis.json`.\n\n"
        f"{_MARKER_START}\n```json\n{block}\n```\n{_MARKER_END}"
    )


def publish() -> dict[str, Any]:
    context = build_context()
    body = render_body(context)
    r = requests.patch(
        f"https://api.github.com/repos/{REPO}/issues/{ISSUE_NUMBER}",
        headers={"Authorization": f"Bearer {_github_token()}", "Accept": "application/vnd.github+json"},
        json={"body": body},
        timeout=15,
    )
    r.raise_for_status()
    logger.info(f"[thesis_review_bridge] contexte {context['ticker']} publié sur issue #{ISSUE_NUMBER}")
    return context


if __name__ == "__main__":
    published = publish()
    print(f"Contexte publié : {json.dumps(published, indent=2, ensure_ascii=False)}")
