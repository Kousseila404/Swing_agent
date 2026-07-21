"""Cache disque TTL générique — pattern partagé entre enrichers secondaires
(finnhub_provider, sec_edgar, finnhub_news).

Étape 0 roadmap : ces modules dupliquaient chacun leur propre lecture/écriture
JSON+TTL sur disque (variantes mineures : timestamp stocké vs mtime fichier,
versionning de schéma optionnel). Ce module factorise la mécanique commune ;
chaque caller garde son propre répertoire/TTL/clé de cache — seule la
lecture/écriture est partagée, pas d'API Provider ABC ici (ces sources
enrichissent `universe.json`, elles ne produisent pas de `FinancialRatios`).

Politique fail-open : toute erreur de lecture/écriture (fichier corrompu,
disque plein, payload non sérialisable) est avalée — un cache cassé ne doit
jamais faire planter la pipeline d'enrichissement, il force juste un re-fetch.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from modules.log import logger


def read_json_cache(
    path: Path,
    ttl_seconds: float,
    *,
    schema_version: int | None = None,
    use_mtime: bool = False,
    label: str = "disk_cache",
) -> dict[str, Any] | None:
    """Lit un fichier cache JSON si présent et frais. None sinon (miss/expiré/corrompu).

    use_mtime=True : âge dérivé de `path.stat().st_mtime` — pour les caches qui
    n'écrivent pas de timestamp interne (`write_json_cache(..., stamp=False)`).
    use_mtime=False (défaut) : âge dérivé du champ `_cached_at` écrit par
    `write_json_cache`.
    """
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    if use_mtime:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return None
    else:
        cached_at = payload.get("_cached_at")
        if not isinstance(cached_at, (int, float)):
            return None
        age = time.time() - cached_at

    if age > ttl_seconds:
        return None

    if schema_version is not None and payload.get("_schema_version") != schema_version:
        logger.info(
            f"[{label}] {path.stem} cache schema obsolète "
            f"(v={payload.get('_schema_version')}, attendu v{schema_version}) — re-fetch."
        )
        return None

    return payload


def write_json_cache(
    path: Path,
    payload: dict[str, Any],
    *,
    schema_version: int | None = None,
    stamp: bool = True,
    label: str = "disk_cache",
) -> None:
    """Écrit un payload JSON atomiquement (tmp + rename), en créant le
    répertoire parent si besoin.

    stamp=True (défaut) : ajoute `_cached_at`=now — requis pour un `read_json_cache`
    ultérieur avec `use_mtime=False`.
    schema_version, si fourni, est ajouté comme `_schema_version`.
    """
    out = dict(payload)
    if stamp:
        out["_cached_at"] = time.time()
    if schema_version is not None:
        out["_schema_version"] = schema_version
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(out), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.warning(f"[{label}] cache write failed for {path.name}: {e}")


def dir_cache_stats(cache_dir: Path, *, pattern: str = "*.json") -> dict[str, Any]:
    """Stats agrégées sur un répertoire de cache disque — pour `/api/data_health`.

    Contrairement à `read_json_cache`, ne filtre PAS par TTL/schema : on veut
    l'état brut de ce qui est sur disque (expiré ou non) pour observer l'usage
    réel d'une source (combien de tickers cachés, âge, taux d'erreur). Âge
    dérivé de `_cached_at` si présent, sinon `mtime` fichier — couvre les deux
    conventions utilisées par les callers de ce module. Fail-open : fichier
    illisible/corrompu est ignoré silencieusement, jamais levé.
    """
    if not cache_dir.exists():
        return {
            "n_cached": 0, "oldest_age_sec": None, "youngest_age_sec": None,
            "median_age_sec": None, "n_errors": 0,
        }
    now = time.time()
    ages: list[float] = []
    n_errors = 0
    for path in cache_dir.glob(pattern):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        cached_at = payload.get("_cached_at")
        if isinstance(cached_at, (int, float)):
            age = now - cached_at
        else:
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue
        ages.append(age)
        if payload.get("error"):
            n_errors += 1
    return {
        "n_cached": len(ages),
        "oldest_age_sec": round(max(ages), 1) if ages else None,
        "youngest_age_sec": round(min(ages), 1) if ages else None,
        "median_age_sec": round(sorted(ages)[len(ages) // 2], 1) if ages else None,
        "n_errors": n_errors,
    }


def dir_cache_error_entries(
    cache_dir: Path,
    *,
    pattern: str = "*.json",
    ticker_from_name: Callable[[str], str] = lambda stem: stem,
) -> list[dict[str, Any]]:
    """Liste `{ticker, error}` des entrées en erreur d'un cache disque — pendant
    de `dir_cache_stats` (Étape 18 : détail par ticker plutôt que compteur
    agrégé, même geste que l'Étape 14 pour `insider_error`/`finnhub_error`
    dans `universe.json`).

    `ticker_from_name` extrait le ticker depuis le nom de fichier (stem, sans
    extension) — les conventions de nommage diffèrent selon la source
    (`insider_{TICKER}.json`, `{TICKER}.json`, `{TICKER}_{days}d.json`...).
    Un même ticker peut apparaître dans plusieurs fichiers (ex. plusieurs
    fenêtres de jours pour `finnhub_news`) — la première erreur rencontrée
    est gardée, dédupliqué par ticker. Fail-open : fichier illisible/corrompu
    ignoré silencieusement, jamais levé.
    """
    if not cache_dir.exists():
        return []
    seen: dict[str, str] = {}
    for path in sorted(cache_dir.glob(pattern)):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        error = payload.get("error")
        if not error:
            continue
        ticker = ticker_from_name(path.stem)
        if ticker and ticker not in seen:
            seen[ticker] = error
    return [{"ticker": t, "error": e} for t, e in sorted(seen.items())]
