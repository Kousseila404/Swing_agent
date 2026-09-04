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

# Checklist de départ par ticker pour la routine cloud de revue de thèse
# (voir scripts/publish_thesis_review_context.py) — chaque item : le
# critère (`label`), sa valeur/cible de référence connue (`reference`) et
# ce qui constituerait un signal à surveiller (`watch_for`). Sert de guide
# pour l'analyse COMPLÈTE (nouveaux résultats trimestriels) ; la veille
# LÉGÈRE ne vérifie que si un événement matériel touche l'un de ces
# critères, sans deep-dive.
#
# 100% éditorial et 100% lecture seule côté routine cloud (même contrainte
# que `verification` — voir docstring de module) : un item qui devient
# obsolète (nouvelle guidance, changement de business model) se corrige ici
# à la main, jamais réécrit automatiquement. Absent pour un ticker = liste
# vide, la routine se rabat sur la checklist "veille légère" générique.
REFERENCE_METRICS: dict[str, list[dict[str, str]]] = {
    "BNP.PA": [
        {"label": "Ratio CET1", "reference": "13,0% (objectif atteint en avance)",
         "watch_for": "Baisse durable sous 12,5%"},
        {"label": "Coût du risque", "reference": "39 bps (guidance <40 bps)",
         "watch_for": "Dépassement durable de la guidance"},
        {"label": "Croissance du revenu / effet jaws", "reference": "T2 2026 : +12% revenu, jaws +3,7 pts à périmètre constant",
         "watch_for": "Dégradation notable de l'un ou l'autre"},
        {"label": "Trajectoire 2028", "reference": "ROTE cible >13%, coûts/revenus cible <56%",
         "watch_for": "Tout langage de guidance qui recule"},
        {"label": "Valeurs résiduelles Arval (leasing véhicules)", "reference": "Pression persistante mais contenue",
         "watch_for": "Dégradation marquée (au lieu de stabilisation)"},
    ],
    "FMX": [
        {"label": "Croissance same-store sales OXXO", "reference": "Historiquement mid-to-high single digit %",
         "watch_for": "Ralentissement net ou négatif sur plusieurs trimestres"},
        {"label": "Marge EBITDA consolidée", "reference": "Stable à en amélioration lente",
         "watch_for": "Compression durable (coûts logistique/change)"},
        {"label": "Exposition FX (MXN/BRL)", "reference": "Couverture partielle habituelle",
         "watch_for": "Dévaluation forte non couverte impactant les résultats"},
        {"label": "Rythme d'ouverture de magasins OXXO", "reference": "Expansion nette continue",
         "watch_for": "Ralentissement significatif du rythme d'ouverture"},
    ],
    "PSX": [
        {"label": "Marges de raffinage (crack spreads)", "reference": "Cycliques, dépendent du marché produits raffinés",
         "watch_for": "Compression durable sur plusieurs trimestres"},
        {"label": "Taux d'utilisation des raffineries", "reference": "Proche de la capacité nominale hors maintenance",
         "watch_for": "Baisse structurelle (hors arrêts planifiés)"},
        {"label": "Cash-flow distribuable / couverture du dividende", "reference": "Dividende couvert par le cash-flow opérationnel",
         "watch_for": "Couverture qui se dégrade"},
        {"label": "Discipline capex / transition énergétique", "reference": "Investissements ciblés, retour sur capital prioritaire",
         "watch_for": "Sur-investissement non rentable annoncé"},
    ],
    "DRH": [
        {"label": "RevPAR (revenu par chambre disponible)", "reference": "Tendance du secteur hôtelier haut de gamme US",
         "watch_for": "Baisse durable sur plusieurs trimestres"},
        {"label": "FFO/AFFO par action", "reference": "Doit couvrir le dividende versé",
         "watch_for": "Dégradation qui menace la couverture du dividende"},
        {"label": "Taux d'occupation", "reference": "Proche des niveaux pré-pandémie/pairs du secteur",
         "watch_for": "Recul structurel vs pairs"},
        {"label": "Politique de dividende", "reference": "Maintenu",
         "watch_for": "Coupe ou gel annoncé"},
    ],
    "CNC": [
        {"label": "Medical Loss Ratio (MLR)", "reference": "Dans la fourchette de guidance annuelle",
         "watch_for": "Dépassement durable au-dessus de la guidance"},
        {"label": "Guidance BPA", "reference": "Confirmée aux publications trimestrielles",
         "watch_for": "Révision à la baisse répétée"},
        {"label": "Évolution des membres Medicaid", "reference": "Stabilisation post-redéterminations",
         "watch_for": "Attrition plus forte que prévu"},
        {"label": "Risque réglementaire (taux Medicaid négociés, politique fédérale santé)", "reference": "Pas de décision défavorable majeure en cours",
         "watch_for": "Décision réglementaire ou législative défavorable majeure"},
    ],
    "HRTG": [
        {"label": "Combined ratio", "reference": "Sous 100% (rentabilité technique)",
         "watch_for": "Passage durable au-dessus de 100%"},
        {"label": "Coût de la réassurance (renouvellements)", "reference": "Répercuté dans les primes",
         "watch_for": "Hausse forte non répercutée dans les primes"},
        {"label": "Sinistralité catastrophes (saison ouragans)", "reference": "Dans les limites de la couverture réassurance",
         "watch_for": "Saison catastrophique impactant significativement les fonds propres"},
        {"label": "Valeur comptable par action", "reference": "Croissance régulière",
         "watch_for": "Érosion durable"},
    ],
    "LNVGY": [
        {"label": "Part de marché PC mondiale", "reference": "Position n°1 ou n°2 mondiale",
         "watch_for": "Perte de parts durable face à HP/Dell"},
        {"label": "Rentabilité ISG (serveurs/infrastructure IA)", "reference": "Trajectoire vers la rentabilité",
         "watch_for": "Pertes qui ne se résorbent pas"},
        {"label": "Marge brute groupe", "reference": "Stable à en amélioration lente",
         "watch_for": "Compression durable"},
        {"label": "Risque géopolitique (contrôle export puces, relations Chine/US)", "reference": "Pas de nouvelle restriction majeure",
         "watch_for": "Nouvelle restriction affectant directement l'activité"},
    ],
    "MU": [
        {"label": "Prix mémoire DRAM/NAND (cycle du secteur)", "reference": "Cycle haussier porté par la demande IA/serveurs",
         "watch_for": "Retournement baissier confirmé du cycle"},
        {"label": "Marge brute", "reference": "En expansion avec le cycle",
         "watch_for": "Compression durable en dehors du cycle normal"},
        {"label": "Demande HBM (mémoire haute bande passante, IA/datacenters)", "reference": "Croissance forte, carnet de commandes",
         "watch_for": "Ralentissement net de la demande IA"},
        {"label": "Discipline capacité (Micron + concurrents Samsung/SK Hynix)", "reference": "Discipline relative de l'offre",
         "watch_for": "Annonce de sur-capacité menaçant les prix"},
    ],
    "NUTX": [
        {"label": "Croissance du nombre d'établissements/lits", "reference": "Expansion continue",
         "watch_for": "Ralentissement net de l'expansion"},
        {"label": "Marge EBITDA par établissement", "reference": "Élevée (modèle facturation hors réseau)",
         "watch_for": "Compression durable"},
        {"label": "Risque réglementaire (No Surprises Act, arbitrage de facturation hors réseau)", "reference": "Modèle actuel toléré par le cadre réglementaire",
         "watch_for": "Décision réglementaire ou judiciaire défavorable majeure sur le modèle de facturation"},
        {"label": "Mix payeurs / taux de recouvrement", "reference": "Stable",
         "watch_for": "Dégradation notable du taux de recouvrement effectif"},
    ],
    "ERO": [
        {"label": "Production de cuivre (guidance vs réalisé)", "reference": "Dans la fourchette de guidance annuelle",
         "watch_for": "Manqué de guidance de production répété"},
        {"label": "AISC (coût total maintenu de production)", "reference": "Compétitif vs pairs du secteur",
         "watch_for": "Hausse durable des coûts"},
        {"label": "Teneur du minerai (grade)", "reference": "Stable sur les gisements en production",
         "watch_for": "Baisse structurelle de la teneur"},
        {"label": "Endettement / capex de développement (nouvelles mines)", "reference": "Levier maîtrisé",
         "watch_for": "Dérapage de capex ou hausse forte du levier"},
    ],
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
