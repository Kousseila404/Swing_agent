"""Module — export compact de `universe_history` pour calibration hors-VPS.

`data/.universe_history/` (snapshots quotidiens complets, ~245 KB/jour) est
volontairement gitignored — c'est de la donnée de production régénérable,
pas du code. Mais la routine cloud qui calibre le prix cible (voir
`docs/price_target_design.md` §6) tourne dans un sandbox avec un clone git
frais : elle n'a PAS accès au disque du VPS, donc pas accès à ces snapshots.

Ce module exporte un sous-ensemble trié des champs nécessaires au calcul de
l'IC de calibration (prix, price_target_mean analystes, pillars TITAN) vers
un fichier JSONL gzippé **suivi par git** (`data/calibration/`, non couvert
par les patterns `data/*.json` du .gitignore car dans un sous-dossier dédié).
Réécrit intégralement à chaque run (peu coûteux : ~500 tickers × ~110 jours).

Usage :
    python -m modules.price_target_calibration_export
    (appelé quotidiennement par run_titan.sh, juste après le snapshot
    universe_history — voir docs/price_target_design.md §5)
"""
from __future__ import annotations

import gzip
import json
from typing import Any

from modules import api_core, universe_history
from modules.log import logger

EXPORT_PATH = api_core.BASE / "data" / "calibration" / "universe_history_export.jsonl.gz"

# Champs nécessaires au calcul du signal + de l'IC (docs/price_target_design.md §3/§6).
# Volontairement restreint : pas de champs identité/texte non nécessaires au calcul.
FIELDS = [
    "current_price",
    "price_target_mean",
    "price_target_high",
    "price_target_low",
    "quality_score",
    "value_score",
    "risk_score",
    "momentum_score",
    "piotroski_score",
    "growth_score",
    "f_score",
    "peg_ratio",
    "forward_pe",
    "ev_to_ebitda",
    "trailing_pe",
    "sector",
    "titan_tilt_flags",
    "titan_composite_score",
    "data_quality",
]


def export_full() -> int:
    """Réécrit intégralement l'export à partir de tous les snapshots locaux
    disponibles. Retourne le nombre de lignes (ticker × jour) écrites.
    """
    EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for d, snap in universe_history.iter_snapshots():
        for ticker, row in (snap.get("tickers") or {}).items():
            entry: dict[str, Any] = {"date": d.isoformat(), "ticker": ticker}
            for f in FIELDS:
                entry[f] = row.get(f)
            lines.append(json.dumps(entry, ensure_ascii=False))

    tmp = EXPORT_PATH.with_suffix(EXPORT_PATH.suffix + ".tmp")
    payload = ("\n".join(lines) + "\n" if lines else "").encode("utf-8")
    tmp.write_bytes(gzip.compress(payload))
    tmp.replace(EXPORT_PATH)
    return len(lines)


def _cli() -> int:
    n = export_full()
    if n == 0:
        logger.warning("[PriceTargetCalibrationExport] aucun snapshot disponible — export vide")
        return 1
    logger.info(f"[PriceTargetCalibrationExport] export OK → {EXPORT_PATH.name} ({n} lignes)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
