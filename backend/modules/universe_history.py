"""Module — historisation des fundamentaux + scores TITAN.

Chaque appel écrit un snapshot daté (1 par jour, dédup en place) contenant :
  • L'état brut de l'univers (fundamentaux yfinance/FMP, momentum)
  • Les scores TITAN calculés ce jour (Q/V/R/S/M + composite)
  • Le contexte macro (régime, VIX) → permet l'analyse régime-conditionnelle

Format : JSON gzippé. ~110 KB/snapshot pour 500 tickers (vs 632 KB raw),
~195 MB sur 5 ans. Pas de dépendance pyarrow → reste portable et débuggeable
(zcat snapshot_YYYYMMDD.json.gz | jq).

Stockage : `data/.universe_history/snapshot_YYYYMMDD.json.gz`. Le `.` préfixe
le dossier (cohérent avec `.universe_backups_quantamental/`).

Usage :
    from modules.universe_history import write_snapshot, read_snapshot, iter_snapshots

    # Écriture (auto-appelée depuis sector_metrics après scoring)
    write_snapshot(scored_universe, macro_state, universe_updated_at)

    # Lecture
    snap = read_snapshot(date(2026, 4, 22))
    for d, snap in iter_snapshots(date(2025, 1, 1), date(2026, 4, 22)):
        ...

Le module est **pure storage** — aucune logique de scoring, aucun fetch live.
Pour backtester on lira les snapshots et on appellera _score_universe sur les
fundamentaux historiques (ce qui permet de re-scorer rétro-actif avec une
nouvelle méthodologie).
"""
from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from modules import api_core
from modules.log import logger

# ─────────────────────────────────────────────────────────────────
# CHEMINS
# ─────────────────────────────────────────────────────────────────
HISTORY_DIR = api_core.BASE / "data" / ".universe_history"

# TTL : on garde 5 ans = 1825 jours. Au-delà on archive (hors scope du MVP,
# implémentation séparée du sweep si on hit la limite).
_RETENTION_DAYS = 1825

# Pattern fichier : snapshot_YYYYMMDD.json.gz (lexicographique = chronologique).
_SNAPSHOT_PATTERN = "snapshot_{date}.json.gz"


def _snapshot_path(d: date) -> Path:
    return HISTORY_DIR / _SNAPSHOT_PATTERN.format(date=d.strftime("%Y%m%d"))


def _today() -> date:
    return datetime.now(UTC).date()


# ─────────────────────────────────────────────────────────────────
# WRITE
# ─────────────────────────────────────────────────────────────────

def write_snapshot(
    scored_universe: dict[str, dict[str, Any]],
    *,
    universe_updated_at: str | None = None,
    macro: dict[str, Any] | None = None,
    snapshot_date: date | None = None,
) -> Path:
    """Persiste un snapshot daté du scoring + contexte. Dedup : si un snapshot
    existe déjà pour la même date, il est écrasé (1 snapshot canonique/jour).

    Args:
        scored_universe : output de `_score_universe()` (raw + scores).
        universe_updated_at : timestamp ISO de la dernière reconstruction.
        macro : contenu de `macro_state.json` (régime, VIX…). None si KO.
        snapshot_date : override pour tests. Défaut = today UTC.

    Returns: Path du fichier écrit.
    """
    snapshot_date = snapshot_date or _today()
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "snapshot_date":       snapshot_date.isoformat(),
        "snapshot_at":         datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "universe_updated_at": universe_updated_at,
        "n_tickers":           len(scored_universe),
        "macro":               macro or {},
        "tickers":             scored_universe,
    }

    out = _snapshot_path(snapshot_date)
    # Écriture atomique : tmp → rename pour éviter qu'un read concurrent
    # tombe sur un fichier tronqué pendant l'écriture.
    tmp = out.with_suffix(out.suffix + ".tmp")
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    tmp.write_bytes(gzip.compress(raw))
    tmp.replace(out)
    return out


def write_snapshot_safe(
    scored_universe: dict[str, dict[str, Any]],
    *,
    universe_updated_at: str | None = None,
    macro: dict[str, Any] | None = None,
) -> Path | None:
    """Wrapper fail-open : log l'erreur mais ne lève pas. À utiliser dans les
    chemins critiques (scoring, scheduler) où une erreur disque ne doit PAS
    casser la production de scores.
    """
    try:
        return write_snapshot(
            scored_universe,
            universe_updated_at=universe_updated_at,
            macro=macro,
        )
    except OSError as e:
        logger.error(f"[UniverseHistory] write_snapshot failed: {e}")
        return None
    except Exception as e:
        logger.error(f"[UniverseHistory] unexpected error: {e}", exc_info=True)
        return None


# ─────────────────────────────────────────────────────────────────
# READ
# ─────────────────────────────────────────────────────────────────

def read_snapshot(d: date) -> dict[str, Any] | None:
    """Charge un snapshot pour une date donnée. None si introuvable.

    Lève RuntimeError si le fichier existe mais est corrompu — appelant doit
    décider quoi faire (skip vs fail).
    """
    p = _snapshot_path(d)
    if not p.exists():
        return None
    try:
        raw = gzip.decompress(p.read_bytes())
        return json.loads(raw)
    except (OSError, json.JSONDecodeError, EOFError) as e:
        raise RuntimeError(
            f"[UniverseHistory] snapshot corrupted: {p.name} ({e})"
        ) from e


def list_snapshots() -> list[date]:
    """Toutes les dates de snapshot disponibles, ordre chronologique croissant.

    Retourne [] si HISTORY_DIR n'existe pas (cas premier run).
    Ignore les fichiers .tmp orphelins (écriture interrompue) — log un warn.
    """
    if not HISTORY_DIR.exists():
        return []
    out: list[date] = []
    for p in sorted(HISTORY_DIR.glob("snapshot_*.json.gz")):
        if p.name.endswith(".tmp"):
            logger.warning(
                f"[UniverseHistory] tmp file orphelin (à nettoyer) : {p.name}"
            )
            continue
        try:
            ds = p.stem.replace("snapshot_", "").replace(".json", "")
            out.append(datetime.strptime(ds, "%Y%m%d").date())
        except ValueError:
            logger.warning(f"[UniverseHistory] nom inattendu, skip : {p.name}")
    return out


def iter_snapshots(
    start: date | None = None,
    end: date | None = None,
) -> Iterator[tuple[date, dict[str, Any]]]:
    """Itère sur les snapshots dans [start, end] inclusif (None = pas de borne).

    Lazy : lit chaque snapshot à la demande, ne charge pas tout en RAM.
    Skip + log les snapshots corrompus (vs RuntimeError de read_snapshot)
    pour permettre un backtest robuste face à 1-2 fichiers cassés.
    """
    for d in list_snapshots():
        if start is not None and d < start:
            continue
        if end is not None and d > end:
            continue
        try:
            snap = read_snapshot(d)
        except RuntimeError as e:
            logger.warning(f"[UniverseHistory] skip {d}: {e}")
            continue
        if snap is not None:
            yield d, snap


# ─────────────────────────────────────────────────────────────────
# QUERY HELPERS — extraction par ticker / par champ
# ─────────────────────────────────────────────────────────────────

def ticker_history(
    ticker: str,
    *,
    start: date | None = None,
    end: date | None = None,
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Récupère l'historique d'UN ticker sur la fenêtre donnée.

    Returns: [{date, ticker, ...fields demandés}, ...] trié par date croissante.
    Si `fields` est None, retourne TOUS les champs disponibles ce jour-là.
    """
    ticker = ticker.upper().strip()
    out: list[dict[str, Any]] = []
    for d, snap in iter_snapshots(start, end):
        row = (snap.get("tickers") or {}).get(ticker)
        if row is None:
            continue
        if fields is not None:
            entry = {f: row.get(f) for f in fields}
        else:
            entry = dict(row)
        entry["date"] = d.isoformat()
        entry["ticker"] = ticker
        out.append(entry)
    return out


def universe_field_series(
    field: str,
    *,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, list[tuple[str, float | None]]]:
    """Pour un champ donné (ex: 'titan_composite_score'), retourne
    {ticker: [(date_iso, value), ...]} sur la fenêtre.

    Utile pour des analyses style "comment le score TITAN d'AAPL a évolué
    sur 6 mois".
    """
    out: dict[str, list[tuple[str, float | None]]] = {}
    for d, snap in iter_snapshots(start, end):
        for ticker, row in (snap.get("tickers") or {}).items():
            v = row.get(field)
            out.setdefault(ticker, []).append((d.isoformat(), v))
    return out


# ─────────────────────────────────────────────────────────────────
# CLI — `python -m modules.universe_history snapshot`
# ─────────────────────────────────────────────────────────────────

def _cli_snapshot() -> int:
    """Snapshot manuel — lit l'univers actuel + scoring + macro et persiste.
    Utilisé par le cron quotidien (run_titan.sh).
    """
    import json as _json

    from modules.sector_metrics import get_scored_universe

    scored = get_scored_universe()
    if not scored:
        logger.error("[UniverseHistory CLI] scored universe vide — skip snapshot")
        return 1

    universe_updated_at = None
    if api_core.UNIVERSE_QUANTAMENTAL_PATH.exists():
        try:
            raw = _json.loads(api_core.UNIVERSE_QUANTAMENTAL_PATH.read_text())
            universe_updated_at = (raw or {}).get("updated_at")
        except (OSError, _json.JSONDecodeError):
            pass

    macro = api_core.load_macro()
    out = write_snapshot_safe(
        scored,
        universe_updated_at=universe_updated_at,
        macro=macro,
    )
    if out is None:
        return 2
    logger.info(f"[UniverseHistory CLI] snapshot OK → {out.name} ({len(scored)} tickers)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli_snapshot())
