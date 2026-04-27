"""
╔══════════════════════════════════════════════════════════════════╗
║  SWINGQUANT TITAN — FASTAPI BACKEND (Quantamental Long-Term)     ║
║                                                                  ║
║  Point d'entrée FastAPI. Ne contient QUE la composition :        ║
║    - lifespan (validation config au startup)                     ║
║    - CORS                                                        ║
║    - static mount /charts                                        ║
║    - exception handler CorruptedCacheError                       ║
║    - include_router(...) pour chaque domaine                     ║
║                                                                  ║
║  La logique métier vit dans routers/* ; les helpers IO / auth /  ║
║  jobs vivent dans modules/api_core.py.                           ║
║                                                                  ║
║  Endpoints :                                                     ║
║    GET  /api/status                 /api/market_status           ║
║    GET  /api/portfolio              /api/equity_curve            ║
║    GET  /api/performance_metrics    /api/portfolio/recommendations║
║    GET  /api/macro                  /api/macro_calendar          ║
║    GET  /api/universe               /api/universe/rebuild (POST) ║
║    GET  /api/sectors                /api/sectors/{sector}        ║
║    GET  /api/history/snapshots      /api/history/ticker/{t}      ║
║    GET  /api/data_health            /api/data_health/refresh_flagged (POST) ║
║    GET  /api/jobs/{id}              /api/job/{id}/kill           ║
║    GET  /api/proposals              /api/proposals/refresh (POST) ║
║    POST /api/proposals/regenerate   /api/proposals/approve_batch ║
║    POST /api/proposals/reject_batch /api/proposals/{id}/approve  ║
║    POST /api/trade/add              /api/trade/close             ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

# Ajoute backend/ au PYTHONPATH pour que `import config` et les `from modules …`
# fonctionnent quand uvicorn pointe sur api:app depuis n'importe quel CWD.
_BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(_BASE))

from modules import api_core  # noqa: E402
from modules.log import logger  # noqa: E402
from routers import (  # noqa: E402
    backtest as backtest_router,
    data_health as data_health_router,
    jobs as jobs_router,
    macro as macro_router,
    portfolio as portfolio_router,
    proposals as proposals_router,
    sectors as sectors_router,
    system as system_router,
    trades as trades_router,
    universe as universe_router,
)

# ─────────────────────────────────────────────────────────────────
# BACKWARD-COMPAT RE-EXPORTS — conservés pour les tests qui importent
# directement depuis `api` (tests/test_api_corrupted_cache.py,
# tests/test_api_universe_metadata.py). Les tests monkey-patchent
# désormais `modules.api_core` mais on garde les alias pour éviter
# tout diff sur des imports de type `api._read_json`.
# ─────────────────────────────────────────────────────────────────
_read_json          = api_core.read_json
_load_equity        = api_core.load_equity
_load_macro         = api_core.load_macro
_load_journal       = api_core.load_journal
_tail_log           = api_core.tail_log
CorruptedCacheError = api_core.CorruptedCacheError
INITIAL_CAPITAL     = api_core.INITIAL_CAPITAL
UNIVERSE_QUANTAMENTAL_PATH = api_core.UNIVERSE_QUANTAMENTAL_PATH


# ─────────────────────────────────────────────────────────────────
# LIFESPAN — validation de la config une fois le logger applicatif prêt
# ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def _lifespan(_app: FastAPI):
    try:
        from config import validate_startup
        validate_startup()
        logger.info("[API] Configuration validée — startup OK")
    except Exception as exc:
        logger.critical(f"[API] Validation config échouée : {exc}")
        raise
    yield


# ─────────────────────────────────────────────────────────────────
# APP + CORS
# ─────────────────────────────────────────────────────────────────
# Par défaut : uniquement les origines de dev local. Production = CORS_ORIGINS explicite.
# (L'ancien domaine Streamlit swing.webcatalyste.fr a été retiré — dashboard migré React.)
_ALLOWED_ORIGINS = [o.strip() for o in os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000",
).split(",") if o.strip()]

app = FastAPI(title="SwingQuant TITAN API", version="2.0.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

# GZip pour tout payload > 1 KB. Économise ~10× sur /api/universe (638 KB → 60 KB),
# /api/performance_metrics (equity_by_date + pnl_series complets),
# /api/portfolio (journal sérialisé). Negligible côté CPU sur ces tailles.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Static mount pour les charts PNG du Live Feed (alerter.py écrit dans data/charts/).
api_core.CHARTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/charts", StaticFiles(directory=str(api_core.CHARTS_DIR)), name="charts")

# Exception handler typé pour les caches JSON corrompus — exposé en 503.
app.add_exception_handler(CorruptedCacheError, api_core.corrupted_cache_handler)


# ─────────────────────────────────────────────────────────────────
# ROUTERS
# ─────────────────────────────────────────────────────────────────
app.include_router(system_router.router)
app.include_router(macro_router.router)
app.include_router(portfolio_router.router)
app.include_router(universe_router.router)
app.include_router(sectors_router.router)
app.include_router(jobs_router.router)
app.include_router(trades_router.router)
app.include_router(proposals_router.router)
app.include_router(backtest_router.router)
app.include_router(data_health_router.router)


# ─────────────────────────────────────────────────────────────────
# MAIN (dev only — production lance via systemd + uvicorn)
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
