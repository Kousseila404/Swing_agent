"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — JOBS (polling + kill des subprocess détachés)          ║
║  GET  /api/jobs/{job_id}       poll un job par id                ║
║  POST /api/job/{job_id}/kill   SIGTERM au groupe de process      ║
║                                                                  ║
║  Consommé uniquement par UniverseManagerPage (watch rebuild).    ║
║  Les endpoints /api/jobs (liste) et /api/job/run ont été retirés ║
║  avec la purge ControlPanel.                                     ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
import os
import signal as _signal

import psutil
from fastapi import APIRouter, HTTPException, Security

from modules import api_core
from modules.api_schemas import (
    GenericOkResponse,
    JobOutputResponse,
)
from modules.log import logger

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobOutputResponse)
def get_job(job_id: str):
    return api_core.get_job_output(job_id)


@router.post("/job/{job_id}/kill", response_model=GenericOkResponse)
def kill_job(job_id: str, _auth: None = Security(api_core.require_auth)):
    """Envoie SIGTERM au groupe de process du job puis reap si zombie."""
    meta_path = api_core.JOBS_DIR / f"{job_id}.json"
    if not meta_path.exists():
        raise HTTPException(404, "Job introuvable")
    meta = json.loads(meta_path.read_text())
    pid = meta.get("pid", -1)
    try:
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, _signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            # Process déjà mort ou PID recyclé — on tente un kill direct psutil
            try:
                psutil.Process(pid).terminate()
            except psutil.NoSuchProcess:
                pass
        # Reap immédiat pour éviter un zombie si l'enfant est direct
        try:
            p = psutil.Process(pid)
            p.wait(timeout=1.0)
        except (psutil.NoSuchProcess, psutil.TimeoutExpired):
            pass
        return {"ok": True, "message": f"SIGTERM envoyé (PID {pid})"}
    except Exception as e:
        logger.error(f"[API /kill/{job_id}] {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Erreur serveur interne") from e
