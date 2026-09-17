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
║    GET  /api/delisted               /api/wfo                     ║
║    GET  /api/wfo/history                                         ║
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
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Ajoute backend/ au PYTHONPATH pour que `import config` et les `from modules …`
# fonctionnent quand uvicorn pointe sur api:app depuis n'importe quel CWD.
_BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(_BASE))

from modules import api_core  # noqa: E402
from modules.log import logger  # noqa: E402
from routers import (  # noqa: E402
    attribution as attribution_router,
)
from routers import (
    audit as audit_router,
)
from routers import (
    backtest as backtest_router,
)
from routers import (
    calendar as calendar_router,
)
from routers import (
    data_health as data_health_router,
)
from routers import (
    jobs as jobs_router,
)
from routers import (
    macro as macro_router,
)
from routers import (
    monitor as monitor_router,
)
from routers import (
    my_portfolio as my_portfolio_router,
)
from routers import (
    my_portfolio_executions as my_portfolio_executions_router,
)
from routers import (
    my_portfolio_thesis as my_portfolio_thesis_router,
)
from routers import (
    news as news_router,
)
from routers import (
    peers as peers_router,
)
from routers import performance as performance_router  # noqa: E402
from routers import (
    portfolio as portfolio_router,
)
from routers import (
    proposals as proposals_router,
)
from routers import (
    sec_filings as sec_filings_router,
)
from routers import (
    sector_benchmark as sector_benchmark_router,
)
from routers import (
    sectors as sectors_router,
)
from routers import (
    system as system_router,
)
from routers import (
    thesis_review_queue as thesis_review_queue_router,
)
from routers import (
    ticker_analysis as ticker_analysis_router,
)
from routers import (
    trades as trades_router,
)
from routers import (
    universe as universe_router,
)
from routers import (
    watchlist as watchlist_router,
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

# Garde-fou : un wildcard "*" combiné avec un Bearer token est non seulement
# inutile (Authorization n'est pas envoyé sur cross-origin avec credentials)
# mais expose en plus tous les endpoints publics à n'importe quel domaine.
# On retire le wildcard et on log un warning explicite.
if "*" in _ALLOWED_ORIGINS:
    logger.warning(
        "[API] CORS_ORIGINS contient '*' — retiré (incompatible avec Bearer auth). "
        "Listez explicitement les domaines frontend autorisés."
    )
    _ALLOWED_ORIGINS = [o for o in _ALLOWED_ORIGINS if o != "*"]
if not _ALLOWED_ORIGINS:
    logger.warning(
        "[API] Aucune origine CORS autorisée — l'UI ne pourra pas appeler l'API. "
        "Définir CORS_ORIGINS=https://votre-frontend.example."
    )

app = FastAPI(title="SwingQuant TITAN API", version="2.0.0", lifespan=_lifespan)


# Security headers (defense in depth — le reverse proxy en prod en pose
# probablement déjà mais on ne dépend pas de lui).
#   - X-Content-Type-Options: nosniff   → bloque le MIME sniffing.
#   - X-Frame-Options: DENY              → empêche l'embed iframe (clickjacking).
#   - Referrer-Policy: same-origin       → fuite minimale sur les liens sortants.
#   - Cache-Control private par défaut sur /api/* (les endpoints sensibles
#     posent leur propre Cache-Control plus strict si besoin).
# Middleware ASGI pur (pas BaseHTTPMiddleware) : pas de buffering du body ni
# de task group par requête, compatible streaming / StaticFiles.
class _SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope.get("path", "").startswith("/api/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "same-origin")
                if is_api:
                    headers.setdefault("Cache-Control", "private, max-age=0")
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(_SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "If-None-Match"],
    expose_headers=["ETag"],
)

# GZip pour tout payload > 1 KB. Économise ~10× sur /api/universe (638 KB → 60 KB),
# /api/performance_metrics (equity_by_date + pnl_series complets),
# /api/portfolio (journal sérialisé). Negligible côté CPU sur ces tailles.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Static mount pour les charts PNG du Live Feed (alerter.py écrit dans data/charts/).
api_core.CHARTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/charts", StaticFiles(directory=str(api_core.CHARTS_DIR)), name="charts")

# Exception handler typé pour les caches JSON corrompus — exposé en 503.
# Starlette type le handler avec Exception en base ; le nôtre est typé sur la
# sous-classe CorruptedCacheError → faux positif connu, d'où le ignore ciblé.
app.add_exception_handler(CorruptedCacheError, api_core.corrupted_cache_handler)  # type: ignore[arg-type]


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
app.include_router(audit_router.router)
app.include_router(ticker_analysis_router.router)
app.include_router(peers_router.router)
app.include_router(watchlist_router.router)
app.include_router(calendar_router.router)
app.include_router(news_router.router)
app.include_router(sec_filings_router.router)
app.include_router(monitor_router.router)
app.include_router(sector_benchmark_router.router)
app.include_router(attribution_router.router)
app.include_router(my_portfolio_router.router)
app.include_router(my_portfolio_executions_router.router)
app.include_router(my_portfolio_thesis_router.router)
app.include_router(thesis_review_queue_router.router)
app.include_router(performance_router.router)


# ─────────────────────────────────────────────────────────────────
# MAIN (dev only — production lance via systemd + uvicorn)
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
