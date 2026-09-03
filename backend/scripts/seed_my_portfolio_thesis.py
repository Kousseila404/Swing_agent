"""Seed `data/my_portfolio_thesis.json` — migration unique 2026-09-03.

Les anciens champs texte libres `reason`/`sell_signal` de
`my_portfolio_data.POSITIONS` ont été retirés du code source (remplacés par
la thèse structurée éditable, `modules/my_portfolio_thesis.py`). Ce script
migre leur contenu HISTORIQUE (capturé ici avant suppression, voir le diff
du commit qui a introduit ce script) vers le nouveau store, une seule fois :

  - `reason`       → `why_bought.catalyseurs` (item unique)
  - `sell_signal`  → `sell_signals` (item unique, statut "a_surveiller" —
    ni "intact" ni "declenche" ne seraient honnêtes : personne n'a encore
    évalué ce signal migré dans le nouveau modèle)

`valorisation`/`role_portefeuille`/`verification` restent vides ("à
documenter" côté UI) — seul l'utilisateur peut les renseigner, via
`PATCH /api/my_portfolio/{ticker}/thesis`. Aucune valeur inventée ici.

Note ticker LNVGY : la position POSITIONS/routing s'appelle "LNVGY" (voir
`my_portfolio_data.py`, `price_ticker="0992.HK"`) — le store est donc amorcé
sous la clé "LNVGY" pour rester cohérent avec tous les autres endpoints
(`/api/my_portfolio`, `/price_history`, la route frontend `#/my_portfolio/
LNVGY`), pas sous "0992.HK" (qui est seulement le symbole de cotation
interrogé pour le prix).

Idempotent : `seed_if_empty` n'écrase jamais un ticker déjà présent dans le
store (donc jamais une thèse déjà éditée par l'utilisateur).

Usage :
    python -m scripts.seed_my_portfolio_thesis
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from datetime import UTC, datetime  # noqa: E402

from modules import my_portfolio_thesis  # noqa: E402

# (ticker, catalyseur migré depuis `reason`, libellé migré depuis `sell_signal`)
_MIGRATION: list[tuple[str, str, str]] = [
    ("BNP.PA", "Stabilisateur, corrélation max 0.22 avec le book", "Beta >0.8 durable ou corrélation >0.40"),
    ("FMX", "Seule exposition staples + Amérique latine", "Dégradation durable OXXO/Coca volumes"),
    ("PSX", "Décorrélation historique du marché (raffinage)", "Retournement crack spreads 2-3 trimestres"),
    ("DRH", "Seule exposition REIT/immobilier", "Chute RevPAR durable, coupe dividende"),
    ("CNC", "Beta le plus bas du book, quasi non corrélé", "Guidance abaissée 2 trimestres consécutifs"),
    ("HRTG", "Meilleur diversifiant mesuré (corr +0.037)", "Corrélation moyenne >0.30 durable"),
    ("LNVGY", "Conviction WS la plus forte (5.00/5)", "2 trimestres manqués consécutifs"),
    ("MU", "Pari sur contrats NAND prix plancher", "Retour cycle surcapacité classique"),
    ("NUTX", "Décorrélé (max 0.18) malgré beta élevé", "Effondrement revenus arbitrage"),
    ("ERO", "Exposition matériaux/Brésil, réduit (corrélé MU)", "Corrélation MU >0.50"),
]


def _build_seed() -> dict[str, dict[str, Any]]:
    now = datetime.now(UTC).isoformat()
    seed: dict[str, dict[str, Any]] = {}
    for ticker, catalyseur, sell_signal_libelle in _MIGRATION:
        seed[ticker] = {
            "why_bought": {
                "catalyseurs": [catalyseur],
                "valorisation": None,
                "role_portefeuille": None,
            },
            "sell_signals": [{
                "id": f"sig_{ticker.replace('.', '').lower()}_migrated",
                "libelle": sell_signal_libelle,
                "statut": "a_surveiller",
                "note": None,
                "date_maj": None,
            }],
            "verification": {
                "derniere_verification": None,
                "verdict": None,
                "historique_verifications": [],
            },
            "updated_at": now,
        }
    return seed


def main() -> int:
    seed = _build_seed()
    added = my_portfolio_thesis.seed_if_empty(seed)
    print(f"Tickers amorcés : {added} / {len(seed)} (les autres avaient déjà une thèse — non écrasée)")
    try:
        label = my_portfolio_thesis.STORE_PATH.relative_to(_BACKEND_ROOT)
    except ValueError:
        label = my_portfolio_thesis.STORE_PATH
    print(f"Store : {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
