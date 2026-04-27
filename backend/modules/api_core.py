"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE — API CORE                                               ║
║  Constantes partagées, helpers IO, auth Bearer, gestion des jobs ║
║  détachés. Consommé par api.py (app FastAPI) + routers/*.        ║
║                                                                  ║
║  Toute valeur qui doit être monkey-patchée en tests se lit via   ║
║  le module (ex: `api_core.UNIVERSE_QUANTAMENTAL_PATH` plutôt     ║
║  qu'un `from api_core import UNIVERSE_QUANTAMENTAL_PATH` figé à  ║
║  l'import) pour que pytest.monkeypatch fonctionne sans tricks.   ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

import psutil
from fastapi import HTTPException, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from filelock import FileLock

from modules.duckdb_journal import read_journal_df
from modules.log import logger

# ─────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parent.parent

CSV_PATH       = BASE / "data" / "trade_journal.csv"
CSV_LOCK_PATH  = BASE / "data" / "trade_journal.csv.lock"
EQUITY_PATH    = BASE / "data" / "equity_state.json"
TRADING_PATH   = BASE / "data" / "trading_state.json"
MACRO_PATH     = BASE / "data" / "macro_state.json"
MACRO_CALENDAR_PATH = BASE / "data" / "macro_calendar.json"
CHARTS_DIR     = BASE / "data" / "charts"
DB_PATH        = BASE / "data" / "market.duckdb"
TRACKER_PID_PATH     = BASE / "tracker.pid"
TRACKER_HEARTBEAT_PATH = BASE / "data" / "tracker_heartbeat.json"
UNIVERSE_QUANTAMENTAL_PATH = BASE / "data" / "universe.json"
JOBS_DIR       = BASE / "data" / ".jobs"
PYTHON_BIN     = sys.executable

LOG_PATHS: dict[str, Path] = {
    "agent":   BASE / "logs" / "agent.log",
    "tracker": BASE / "logs" / "tracker.log",
    "cron":    BASE / "logs" / "cron_scan.log",
}

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHFABCDJsu]")

# Capital de référence pour /equity_curve, /performance_metrics, /portfolio.
# Lu depuis config.ACCOUNT_SIZE pour rester cohérent avec tracker / broker
# (fallback 100_000 si config non importable — ne devrait jamais arriver côté API).
try:
    import config as _config
    INITIAL_CAPITAL = float(getattr(_config, "ACCOUNT_SIZE", 100_000.0))
except Exception:
    INITIAL_CAPITAL = 100_000.0


# ─────────────────────────────────────────────────────────────────
# AUTH — Bearer token (fail-closed par défaut)
# ─────────────────────────────────────────────────────────────────
# Endpoints mutants refusés par défaut si API_TOKEN absent. Opt-in explicite
# via ALLOW_UNAUTHENTICATED=true pour dev local uniquement.
API_TOKEN      = os.getenv("API_TOKEN", "")
ALLOW_UNAUTH   = os.getenv("ALLOW_UNAUTHENTICATED", "false").lower() in {"1", "true", "yes"}
bearer_scheme  = HTTPBearer(auto_error=False)

if not API_TOKEN and not ALLOW_UNAUTH:
    logger.warning(
        "[API] API_TOKEN absent — endpoints mutants refusés (set ALLOW_UNAUTHENTICATED=true pour dev local)"
    )


def check_auth(credentials: HTTPAuthorizationCredentials | None) -> None:
    """Core auth check — réutilisable hors FastAPI Security().

    Fail-closed : refuse si API_TOKEN absent ET ALLOW_UNAUTHENTICATED!=true.
    Extrait pour pouvoir être appelé conditionnellement (ex : /api/sectors
    où seul refresh_momentum=true exige l'auth).
    """
    if not API_TOKEN:
        if ALLOW_UNAUTH:
            return
        raise HTTPException(
            status_code=503,
            detail="Auth non configurée — set API_TOKEN ou ALLOW_UNAUTHENTICATED=true",
        )
    if credentials is None or credentials.credentials != API_TOKEN:
        raise HTTPException(status_code=401, detail="Token invalide ou manquant")


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> None:
    """Dépendance FastAPI — vérifie le Bearer token. Fail-closed."""
    check_auth(credentials)


# ─────────────────────────────────────────────────────────────────
# CORRUPTED CACHE — 503 typé pour JSON tronqué
# ─────────────────────────────────────────────────────────────────

class CorruptedCacheError(RuntimeError):
    """Un fichier JSON de cache a un contenu non-parseable (truncation, write partiel…).

    Distingué d'un fichier manquant : remonte une HTTPException 503 côté API
    pour que le frontend affiche une bannière explicite au lieu d'interpréter
    un `{}` vide comme un état de marché neutre.
    """

    def __init__(self, path: Path, original: Exception) -> None:
        super().__init__(f"Corrupted JSON cache: {path.name} ({original})")
        self.path = path
        self.original = original


async def corrupted_cache_handler(_request: Request, exc: CorruptedCacheError) -> JSONResponse:
    """Expose les caches JSON corrompus en 503 avec un payload typé.

    Le frontend peut alors afficher une bannière dédiée au lieu d'un "0 trades"
    trompeur. 503 (plutôt que 500) signale qu'on attend une régénération du
    fichier côté backend pour que l'endpoint redevienne sain.
    """
    try:
        rel_path = str(exc.path.relative_to(BASE))
    except ValueError:
        rel_path = exc.path.name
    return JSONResponse(
        status_code=503,
        content={
            "error":      "corrupted_cache",
            "detail":     str(exc),
            "cache_file": exc.path.name,
            "cache_path": rel_path,
            "recovery":   "Régénérer le cache (rebuild universe / macro / scan) "
                          "ou restaurer depuis data/.universe_backups_quantamental/.",
        },
    )


# ─────────────────────────────────────────────────────────────────
# HELPERS — LECTURE DONNÉES
# ─────────────────────────────────────────────────────────────────

def read_json(path: Path, default=None):
    """
    Lit un cache JSON. Distingue trois cas :
      1. Fichier absent / vide                → retourne `default` (ou {}).
      2. Fichier présent et JSON valide       → retourne l'objet parsé.
      3. Fichier présent mais JSON corrompu   → lève `CorruptedCacheError`.

    Le cas (3) est explicitement typé pour que les endpoints puissent le
    convertir en 503 (voir handler `corrupted_cache_handler`).
    Retourner silencieusement {} masquait des fichiers tronqués par un
    kill -9 pendant un write (bug reporté en prod : equity_state vidé
    → sizing Kelly sur base 0 → trades refusés en cascade).
    """
    if not path.exists() or path.stat().st_size == 0:
        return default if default is not None else {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        logger.error(
            f"[API] JSON corrompu : {path} — {exc}. "
            "L'endpoint retournera 503 pour alerter l'UI."
        )
        raise CorruptedCacheError(path, exc) from exc
    except OSError as exc:
        logger.warning(f"[API] I/O error reading {path}: {exc}")
        return default if default is not None else {}


def load_journal() -> list[dict]:
    if not CSV_PATH.exists():
        return []
    try:
        with FileLock(str(CSV_LOCK_PATH), timeout=10):
            df = read_journal_df()
        return df.fillna("").to_dict(orient="records")
    except Exception:
        return []


def load_equity() -> dict:
    data = read_json(EQUITY_PATH, {})
    if not data:
        return {
            "starting_equity": INITIAL_CAPITAL,
            "current_equity":  INITIAL_CAPITAL,
            "realized_pnl":    0.0,
            "unrealized_pnl":  0.0,
            "date":            "N/A",
            "last_update":     None,
            "open_positions":  [],
        }
    return data


def load_macro() -> dict:
    return read_json(MACRO_PATH, {})


def tail_log(path: Path, n: int = 200) -> list[str]:
    """Renvoie les `n` dernières lignes d'un fichier log en streaming.

    Streaming via deque(maxlen=n) → RAM = O(n) au lieu de O(file_size).
    Les logs tracker (RotatingFileHandler 5 MB × 3) peuvent peser 15 MB ;
    charger tout ça en RAM pour garder les 200 dernières lignes est du gâchis.
    """
    if not path.exists():
        return []
    try:
        buf: deque[str] = deque(maxlen=n)
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                buf.append(ANSI_RE.sub("", line).rstrip("\n"))
        return list(buf)
    except Exception:
        return []


def etag_from_mtime(path: Path) -> str | None:
    """Renvoie un ETag faible dérivé de la mtime (epoch int) du fichier.

    Convention : weak ETag W/"<epoch>" — suffisant pour détecter qu'un cache
    JSON côté back a été réécrit (atomic rename → mtime update). Retourne
    None si le fichier est absent (l'endpoint n'émettra pas d'ETag, le client
    verra toujours du 200 OK jusqu'à ce que le fichier existe).
    """
    try:
        if not path.exists():
            return None
        return f'W/"{int(path.stat().st_mtime)}"'
    except OSError:
        return None


def etag_matches(if_none_match: str | None, current_etag: str | None) -> bool:
    """Teste si le header If-None-Match du client couvre le ETag courant.

    Tolère les W/-prefix et les listes CSV — suffisant pour notre cas simple.
    """
    if not if_none_match or not current_etag:
        return False
    candidates = [c.strip() for c in if_none_match.split(",") if c.strip()]
    return current_etag in candidates or any(
        c.replace("W/", "") == current_etag.replace("W/", "") for c in candidates
    )


def parse_updated_at(updated_at: str | None) -> tuple[float | None, float | None]:
    """
    Parse un timestamp ISO-8601 ("2026-04-20T10:30:00Z") en (epoch_seconds, age_days).
    Retourne (None, None) si la chaîne est absente ou non-parseable.
    """
    if not updated_at or not isinstance(updated_at, str):
        return None, None
    try:
        clean = updated_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        ts = dt.timestamp()
        age_seconds = datetime.now(dt.tzinfo).timestamp() - ts
        age_days = max(0.0, age_seconds / 86_400.0)
        return ts, round(age_days, 2)
    except (ValueError, TypeError):
        return None, None


# ─────────────────────────────────────────────────────────────────
# HELPERS — JOBS
# ─────────────────────────────────────────────────────────────────

# Garde les 10 jobs les plus récents ; reap les zombies orphelins avant rotation.
JOBS_RETENTION = 10


def reap_and_rotate_jobs() -> None:
    """
    Conserve uniquement les `JOBS_RETENTION` jobs les plus récents dans
    `data/.jobs/`. Reap d'abord les processus zombies des metas supprimées
    pour éviter d'accumuler des <defunct> dans le process tree uvicorn.
    """
    if not JOBS_DIR.exists():
        return
    metas: list[tuple[float, Path]] = []
    for f in JOBS_DIR.glob("*.json"):
        try:
            metas.append((f.stat().st_mtime, f))
        except Exception:
            pass
    if len(metas) <= JOBS_RETENTION:
        return
    metas.sort(key=lambda t: t[0], reverse=True)
    for _mtime, meta_path in metas[JOBS_RETENTION:]:
        try:
            meta = json.loads(meta_path.read_text())
            pid = int(meta.get("pid", -1))
            if pid > 0:
                try:
                    p = psutil.Process(pid)
                    if p.status() == psutil.STATUS_ZOMBIE:
                        p.wait(timeout=0.1)
                except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                    pass
                except Exception:
                    pass
            log_path = Path(meta.get("log_path", ""))
            if log_path.exists():
                log_path.unlink(missing_ok=True)
        except Exception:
            pass
        meta_path.unlink(missing_ok=True)


def launch_job(cmd: list[str], name: str) -> dict:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    reap_and_rotate_jobs()
    job_id    = uuid.uuid4().hex[:8]
    log_path  = JOBS_DIR / f"{job_id}.log"
    meta_path = JOBS_DIR / f"{job_id}.json"
    with open(log_path, "w") as fout:
        proc = subprocess.Popen(
            cmd, stdout=fout, stderr=subprocess.STDOUT,
            cwd=str(BASE), start_new_session=True,
        )
    meta = {
        "job_id": job_id, "name": name,
        "cmd": " ".join(cmd), "pid": proc.pid,
        "started": datetime.now().strftime("%H:%M:%S"),
        "log_path": str(log_path),
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    return meta


def job_running(pid: int) -> bool:
    """
    True ssi le PID référence un process vivant et non-zombie. psutil gère
    la détection cross-platform et distingue l'état zombie (process mort
    mais non wait()-é) d'un process réellement en cours.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        p = psutil.Process(pid)
        if p.status() == psutil.STATUS_ZOMBIE:
            try:
                p.wait(timeout=0.0)
            except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                pass
            return False
        return p.is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    except Exception:
        return False


def list_jobs(n: int = 12) -> list[dict]:
    if not JOBS_DIR.exists():
        return []
    metas = []
    for f in JOBS_DIR.glob("*.json"):
        try:
            m = json.loads(f.read_text())
            m["running"] = job_running(m.get("pid", -1))
            metas.append(m)
        except Exception:
            pass
    return sorted(metas, key=lambda m: m.get("started", ""), reverse=True)[:n]


def get_job_output(job_id: str) -> dict:
    meta_path = JOBS_DIR / f"{job_id}.json"
    if not meta_path.exists():
        raise HTTPException(404, "Job introuvable")
    meta = json.loads(meta_path.read_text())
    log_path = Path(meta.get("log_path", ""))
    running = job_running(meta.get("pid", -1))
    lines = []
    if log_path.exists():
        raw = log_path.read_text(errors="replace")
        lines = ANSI_RE.sub("", raw).splitlines()
    return {"job_id": job_id, "running": running, "cmd": meta.get("cmd"), "lines": lines[-300:]}


# Jobs TITAN — seul subset réellement câblé sur le CLI actuel de main.py.
# Les modes legacy (--score-universe, --backtest, --vector-backtest, --strategy-research,
# --update-cache) ont été retirés lors du pivot quantamental ; les relancer
# exposerait les endpoints à 404/traceback.
JOB_COMMANDS: dict[str, list[str]] = {
    "macro":         [PYTHON_BIN, "main.py", "--macro"],
    "tracker_start": [PYTHON_BIN, "tracker.py", "--loop"],
    "tracker_once":  [PYTHON_BIN, "tracker.py"],
    "alpaca_test":   [PYTHON_BIN, "main.py", "--alpaca-test"],
    "alpaca_sync":   [PYTHON_BIN, "main.py", "--alpaca-sync"],
}


def running_universe_rebuild_job() -> str | None:
    """Retourne le job_id d'un rebuild/refresh universe en cours, ou None.

    Couvre les deux modes : full (universe_engine) et staggered (universe_scheduler).
    """
    for job in list_jobs(30):
        if not job.get("running"):
            continue
        cmd = job.get("cmd", "")
        if (
            "modules.universe_engine" in cmd or "universe_engine.py" in cmd
            or "modules.universe_scheduler" in cmd or "universe_scheduler.py" in cmd
        ):
            return job.get("job_id")
    return None
