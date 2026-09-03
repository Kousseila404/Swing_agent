"""Thèse d'investissement structurée — book `my_portfolio` (données éditoriales).

Remplace les anciens champs texte libres `reason`/`sell_signal` de
`my_portfolio_data.POSITIONS` (supprimés — voir migration
`scripts/seed_my_portfolio_thesis.py`) par trois blocs structurés :

  - `why_bought`    : catalyseurs (liste), valorisation, rôle dans le book.
  - `sell_signals`  : liste de signaux de vente, chacun avec un statut
    (`intact` | `a_surveiller` | `declenche`), une note libre optionnelle,
    une date de dernière évaluation.
  - `verification`  : dernière date de vérification + verdict, avec
    historique des vérifications passées (append-only, jamais écrasé).

Persisté dans `data/my_portfolio_thesis.json` (survit aux redéploiements,
contrairement à `POSITIONS` qui est du code source statique — même besoin
que `my_portfolio_risk.json`/`my_portfolio_executions.csv`, même pattern IO
FileLock + écriture atomique `.tmp` + rename).

CONTRAINTE NON NÉGOCIABLE : tout le contenu de ce module est éditorial.
`update_thesis` ne fait QUE persister ce qu'on lui donne — aucune fonction
ici ne lit une donnée de marché, un score TITAN, ou n'importe quelle API
pour déduire/calculer un statut, un verdict ou une date. Si personne n'a
écrit "thèse intacte" via `update_thesis`, ce champ reste `None`, jamais
une valeur inférée.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from modules.log import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORE_PATH = _PROJECT_ROOT / "data" / "my_portfolio_thesis.json"
_LOCK_PATH = _PROJECT_ROOT / "data" / "my_portfolio_thesis.json.lock"

SCHEMA_VERSION = 1

SELL_SIGNAL_STATUSES = ("intact", "a_surveiller", "declenche")

# Au-delà de ce nombre de jours depuis `derniere_verification`, la page
# détail affiche un badge d'alerte de fraîcheur — même logique visuelle que
# le badge earnings (`earnings_data_stale`, 48h) et le badge prix (3j).
VERIFICATION_STALE_DAYS = 90

_EMPTY_WHY_BOUGHT: dict[str, Any] = {"catalyseurs": [], "valorisation": None, "role_portefeuille": None}
_EMPTY_VERIFICATION: dict[str, Any] = {
    "derniere_verification": None, "verdict": None, "historique_verifications": [],
}


def _empty_thesis() -> dict[str, Any]:
    return {
        "why_bought": dict(_EMPTY_WHY_BOUGHT),
        "sell_signals": [],
        "verification": dict(_EMPTY_VERIFICATION),
        "updated_at": None,
    }


def _load_store_unlocked() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return {"schema_version": SCHEMA_VERSION, "tickers": {}}
    try:
        payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"[my_portfolio_thesis] lecture échouée, store vide retourné: {e}")
        return {"schema_version": SCHEMA_VERSION, "tickers": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("tickers"), dict):
        return {"schema_version": SCHEMA_VERSION, "tickers": {}}
    return payload


def _save_store_unlocked(store: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(STORE_PATH.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STORE_PATH)
    except Exception as e:
        logger.error(f"[my_portfolio_thesis] écriture échouée: {e}")
        tmp.unlink(missing_ok=True)
        raise


def get_thesis(ticker: str) -> dict[str, Any]:
    """Lecture pure — thèse d'un ticker, ou le squelette vide (jamais de KeyError)."""
    with FileLock(str(_LOCK_PATH), timeout=10):
        store = _load_store_unlocked()
    return store["tickers"].get(ticker, _empty_thesis())


def get_all_theses() -> dict[str, dict[str, Any]]:
    """Lecture pure — toutes les thèses persistées, `{ticker: thesis}`."""
    with FileLock(str(_LOCK_PATH), timeout=10):
        store = _load_store_unlocked()
    return store["tickers"]


def _normalize_sell_signals(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Valide + normalise la liste (0..N signaux, aucun cas particulier selon
    la taille). Génère un `id` stable pour tout signal qui n'en a pas encore
    (nouveau signal saisi côté UI)."""
    out: list[dict[str, Any]] = []
    for item in items:
        statut = item.get("statut")
        if statut not in SELL_SIGNAL_STATUSES:
            raise ValueError(f"statut invalide {statut!r} — attendu un de {SELL_SIGNAL_STATUSES}")
        libelle = (item.get("libelle") or "").strip()
        if not libelle:
            raise ValueError("libelle requis pour chaque signal de vente")
        out.append({
            "id": item.get("id") or f"sig_{uuid.uuid4().hex[:8]}",
            "libelle": libelle,
            "statut": statut,
            "note": item.get("note") or None,
            "date_maj": item.get("date_maj") or None,
        })
    return out


def update_thesis(
    ticker: str,
    *,
    why_bought: dict[str, Any] | None = None,
    sell_signals: list[dict[str, Any]] | None = None,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Applique une mise à jour partielle (chaque bloc est optionnel) et
    persiste. Écriture éditoriale pure — voir contrainte en tête de module.

    `why_bought` : dict avec un sous-ensemble de `{catalyseurs, valorisation,
    role_portefeuille}` — seules les clés présentes sont remplacées (les
    autres champs déjà documentés sont conservés tels quels).

    `sell_signals` : remplace la liste complète (l'appelant renvoie l'état
    voulu, pas un diff — évite toute logique d'upsert par id côté backend).

    `verification` : DOIT contenir `derniere_verification` (ISO date) +
    `verdict` si fourni — l'entrée précédemment active est automatiquement
    poussée dans `historique_verifications` avant d'être remplacée (jamais
    perdue). Lève `ValueError` si le format est invalide.
    """
    with FileLock(str(_LOCK_PATH), timeout=10):
        store = _load_store_unlocked()
        existing = store["tickers"].get(ticker, _empty_thesis())
        current: dict[str, Any] = {
            "why_bought": dict(existing.get("why_bought") or _EMPTY_WHY_BOUGHT),
            "sell_signals": list(existing.get("sell_signals") or []),
            "verification": dict(existing.get("verification") or _EMPTY_VERIFICATION),
            "updated_at": existing.get("updated_at"),
        }

        if why_bought is not None:
            wb = current["why_bought"]
            if "catalyseurs" in why_bought:
                wb["catalyseurs"] = [str(c).strip() for c in (why_bought["catalyseurs"] or []) if str(c).strip()]
            if "valorisation" in why_bought:
                wb["valorisation"] = (why_bought["valorisation"] or "").strip() or None
            if "role_portefeuille" in why_bought:
                wb["role_portefeuille"] = (why_bought["role_portefeuille"] or "").strip() or None

        if sell_signals is not None:
            current["sell_signals"] = _normalize_sell_signals(sell_signals)

        if verification is not None:
            new_date = verification.get("derniere_verification")
            new_verdict = (verification.get("verdict") or "").strip()
            if not new_date or not new_verdict:
                raise ValueError("verification requiert derniere_verification ET verdict, tous les deux non vides")
            date.fromisoformat(new_date)  # lève ValueError si format invalide

            v = current["verification"]
            history = list(v.get("historique_verifications") or [])
            # On n'archive que s'il y avait une vérification active à remplacer
            # (première vérification jamais écrite → rien à pousser).
            if v.get("derniere_verification") and v.get("verdict"):
                history.append({"date": v["derniere_verification"], "verdict": v["verdict"]})
            current["verification"] = {
                "derniere_verification": new_date,
                "verdict": new_verdict,
                "historique_verifications": history,
            }

        current["updated_at"] = datetime.now(UTC).isoformat()
        store["tickers"][ticker] = current
        store["schema_version"] = SCHEMA_VERSION
        _save_store_unlocked(store)
        return current


def seed_if_empty(seed: dict[str, dict[str, Any]]) -> int:
    """Amorce le store avec `seed` UNIQUEMENT pour les tickers absents — ne
    jamais écraser une thèse déjà éditée. Retourne le nombre de tickers
    effectivement amorcés."""
    with FileLock(str(_LOCK_PATH), timeout=10):
        store = _load_store_unlocked()
        added = 0
        for ticker, thesis in seed.items():
            if ticker not in store["tickers"]:
                store["tickers"][ticker] = thesis
                added += 1
        if added:
            store["schema_version"] = SCHEMA_VERSION
            _save_store_unlocked(store)
        return added
