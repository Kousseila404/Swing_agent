"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — UNIVERSE (Quantamental Long-Term)                      ║
║  GET  /api/universe                                              ║
║  POST /api/universe/rebuild                                      ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request, Response, Security
from pydantic import BaseModel

from modules import api_core
from modules.api_schemas import (
    JobLaunchResponse,
    UniverseResponse,
)
from modules.log import logger

# Champs de scoring TITAN exposés sur chaque ticker via /api/universe (Option A
# scanner). On garde un sous-ensemble léger : les composantes utiles à l'UI
# (badge TITAN, F-Score, tris). Le scoring complet (breakdowns, diagnostics)
# reste accessible via /api/ticker_analysis et /api/sectors.
_SCORE_FIELDS_EXPOSED: tuple[str, ...] = (
    "titan_composite_score",
    "quality_score",
    "value_score",
    "risk_score",
    "sentiment_score",
    "momentum_score",
    "piotroski_score",
    "growth_score",
    "f_score",
    "f_score_max",
    "dq_coef",
)

# Fenêtre du drift TITAN. 7 jours = standard "weekly momentum" — assez court
# pour capter une upgrade silencieuse, assez long pour ignorer le bruit.
_DRIFT_WINDOW_DAYS = 7

# Sparkline TITAN — 30 jours = ~22 trading days, mais on cap à 12 points
# pour le rendu visuel (le frontend trace une mini-line). On lit les snapshots
# disponibles dans la fenêtre, sans interpoler — moins de points = trait plus
# court, ce qui reflète honnêtement la profondeur d'historique.
_SPARKLINE_WINDOW_DAYS = 30
_SPARKLINE_MAX_POINTS = 12


# Statut portefeuille — précédence : HELD > PROPOSED > VETOED > WATCH > None.
# HELD = position OPEN (broker/journal). PROPOSED = pending dans la file.
# VETOED = rejected dans la fenêtre cooldown. WATCH = approved/executed récents
# (déjà déclenché mais utile pour signaler "tu viens de l'acheter").
_VETOED_LOOKBACK_DAYS = 7   # aligné avec VETO_COOLDOWN_DAYS
_WATCH_LOOKBACK_DAYS = 14   # aligné avec WIN_COOLDOWN_DAYS


def _load_portfolio_status() -> dict[str, str]:
    """Renvoie {ticker: status} agrégé : HELD / PROPOSED / VETOED / WATCH.

    Best-effort sur chaque source — un échec lecture proposals ne casse pas la
    page (HELD reste lisible et inversement).
    """
    out: dict[str, str] = {}

    # WATCH (clôtures récentes / executed — précédence la plus basse, écrit en 1er).
    try:
        from datetime import datetime as _dt

        from modules import proposals as _proposals
        cutoff_iso = (
            _dt.utcnow() - timedelta(days=_WATCH_LOOKBACK_DAYS)
        ).isoformat(timespec="seconds") + "Z"
        for p in _proposals.list_all() or []:
            t = (p.get("ticker") or "").upper().strip()
            if not t:
                continue
            status = p.get("status")
            decided = p.get("decided_at") or ""
            if status in {"executed", "approved"} and decided >= cutoff_iso:
                out[t] = "WATCH"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[API /universe] WATCH status load failed: {exc}")

    # VETOED (rejected récents).
    try:
        from datetime import datetime as _dt

        from modules import proposals as _proposals
        cutoff_iso = (
            _dt.utcnow() - timedelta(days=_VETOED_LOOKBACK_DAYS)
        ).isoformat(timespec="seconds") + "Z"
        for p in _proposals.list_all() or []:
            t = (p.get("ticker") or "").upper().strip()
            if not t:
                continue
            if p.get("status") == "rejected" and (p.get("decided_at") or "") >= cutoff_iso:
                out[t] = "VETOED"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[API /universe] VETOED status load failed: {exc}")

    # PROPOSED (pending — précédence > VETOED car action immédiate possible).
    try:
        from modules import proposals as _proposals
        for p in _proposals.list_all(status="pending") or []:
            t = (p.get("ticker") or "").upper().strip()
            if t:
                out[t] = "PROPOSED"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[API /universe] PROPOSED status load failed: {exc}")

    # HELD (positions OPEN — précédence absolue : si on l'a, peu importe le reste).
    try:
        from modules.utils import get_open_tickers
        for t in get_open_tickers() or set():
            out[(t or "").upper().strip()] = "HELD"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[API /universe] HELD status load failed: {exc}")

    return out


def _load_sparkline_series(window_days: int = _SPARKLINE_WINDOW_DAYS) -> dict[str, list[float]]:
    """Renvoie {ticker: [titan_scores chronologiques sur la fenêtre]}.

    Best-effort : si universe_history KO, dict vide. On lit chaque snapshot
    disponible une seule fois, donc le coût est O(snapshots × tickers) — trivial
    sur 7-30 snapshots × 500 tickers (qq ms).
    """
    try:
        from modules import universe_history as uh
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[API /universe] universe_history unavailable: {exc}")
        return {}

    try:
        snaps = uh.list_snapshots()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[API /universe] list_snapshots failed: {exc}")
        return {}

    if not snaps:
        return {}

    cutoff = date.today() - timedelta(days=window_days)
    eligible = [d for d in snaps if d >= cutoff]
    if len(eligible) > _SPARKLINE_MAX_POINTS:
        # Échantillonnage uniforme — garde le 1er et le dernier, points
        # intermédiaires régulièrement espacés.
        step = (len(eligible) - 1) / (_SPARKLINE_MAX_POINTS - 1)
        eligible = [eligible[round(i * step)] for i in range(_SPARKLINE_MAX_POINTS)]

    series: dict[str, list[float]] = {}
    for d in eligible:
        try:
            snap = uh.read_snapshot(d)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[API /universe] sparkline read {d} failed: {exc}")
            continue
        if not snap:
            continue
        for tk, row in (snap.get("tickers") or {}).items():
            v = row.get("titan_composite_score")
            if isinstance(v, (int, float)):
                series.setdefault(tk, []).append(round(float(v), 2))
    return series


def _load_drift_baseline(window_days: int = _DRIFT_WINDOW_DAYS) -> dict[str, float]:
    """Renvoie {ticker: titan_composite_score} le plus proche de J-window_days
    *avant* (jamais après pour éviter le lookahead). Dict vide si aucun
    snapshot exploitable. Best-effort : tout exception → dict vide + warning.
    """
    try:
        from modules import universe_history as uh
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[API /universe] universe_history unavailable: {exc}")
        return {}

    try:
        snaps = uh.list_snapshots()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[API /universe] list_snapshots failed: {exc}")
        return {}

    if not snaps:
        return {}

    target = date.today() - timedelta(days=window_days)
    # On veut le snapshot le plus récent ≤ target ; sinon, le plus ancien
    # disponible (cas warm-up où l'historique est < window_days).
    candidate: date | None = None
    for d in snaps:
        if d <= target:
            candidate = d
        else:
            break
    if candidate is None:
        candidate = snaps[0]

    try:
        snap = uh.read_snapshot(candidate)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[API /universe] read_snapshot {candidate} failed: {exc}")
        return {}

    if not snap:
        return {}

    out: dict[str, float] = {}
    for tk, row in (snap.get("tickers") or {}).items():
        v = row.get("titan_composite_score")
        if isinstance(v, (int, float)):
            out[tk] = float(v)
    return out

router = APIRouter(prefix="/api", tags=["universe"])


# Seuil au-delà duquel un univers est considéré "stale" — 1 semaine aligne
# avec la cadence de rebuild recommandée (rotation sectorielle = lente).
_UNIVERSE_STALE_THRESHOLD_DAYS = 7

# Champs requis pour qu'un ticker soit considéré "complet". market_cap est
# critique (filtre quantamental), sector sert à la rotation.
_TICKER_REQUIRED_FIELDS = ("market_cap", "sector")


class UniverseRebuildRequest(BaseModel):
    """Payload optionnel pour POST /api/universe/rebuild."""
    indices: list[str] | None = None   # ex: ["sp500", "ndx100"]
    min_market_cap: float | None = None
    workers: int | None = None
    max_retries: int | None = None
    timeout: float | None = None
    # Mode staggered (défaut) = universe_scheduler, quota-friendly (budget 50/j,
    # skip tickers < 14j, merge non-destructif). Mode full = universe_engine
    # (réservé aux urgences : ~1500 calls FMP, crame le quota journalier).
    mode: str | None = None            # "staggered" (default) | "full"
    budget: int | None = None          # override scheduler budget
    min_age_days: float | None = None  # override scheduler staleness threshold
    force: list[str] | None = None     # tickers forcés (bypass min_age_days)


def _ticker_is_incomplete(ticker_data: dict) -> bool:
    """True si au moins un champ critique est None / manquant / vide."""
    if not isinstance(ticker_data, dict):
        return True
    for field_name in _TICKER_REQUIRED_FIELDS:
        value = ticker_data.get(field_name)
        if value is None or value == "" or value == "Unknown":
            return True
    return False


@router.post("/universe/rebuild", response_model=JobLaunchResponse)
def rebuild_quantamental_universe(
    req: UniverseRebuildRequest = UniverseRebuildRequest(),
    _auth: None = Security(api_core.require_auth),
):
    """
    Rafraîchit l'univers quantamental en subprocess détaché.

    Deux modes (mode="staggered" par défaut) :
      • staggered → universe_scheduler : budget 50 tickers/jour, skip ceux
        refreshés < 14j, merge non-destructif. **Quota-friendly** (~150 calls
        FMP sur 250/j disponibles). Mode recommandé pour les usages réguliers.
      • full → universe_engine : rebuild intégral (~500 tickers × 3 calls =
        1500 calls FMP). Crame le quota journalier → **à réserver aux
        urgences** (corruption universe.json, nouveau déploiement).

    Téléchargement lourd → renvoie immédiatement {job_id} pour polling
    via GET /api/jobs/{job_id}. Refuse (409) si un job est déjà en cours.
    """
    existing = api_core.running_universe_rebuild_job()
    if existing:
        raise HTTPException(
            409, f"Rebuild universe déjà en cours (job {existing})",
        )

    mode = (req.mode or "staggered").strip().lower()
    if mode not in {"staggered", "full"}:
        raise HTTPException(
            400, f"Mode invalide: {mode!r}. Autorisés: staggered | full",
        )

    # Indices : commun aux deux modes.
    indices_arg: str | None = None
    if req.indices:
        allowed = {"sp500", "ndx100"}
        cleaned = [x.strip().lower() for x in req.indices if isinstance(x, str)]
        bad = [x for x in cleaned if x not in allowed]
        if bad:
            raise HTTPException(
                400, f"Indices invalides: {bad}. Autorisés: {sorted(allowed)}",
            )
        if cleaned:
            indices_arg = ",".join(cleaned)

    if mode == "staggered":
        cmd: list[str] = [api_core.PYTHON_BIN, "-m", "modules.universe_scheduler"]
        if indices_arg:
            cmd += ["--indices", indices_arg]

        # Cap 500 : l'univers prod = 500 tickers, un one-shot refresh complet
        # doit pouvoir passer. Le cap historique à 250 était conservateur pour
        # la quota FMP (250/j) mais FMP est disabled par default depuis Lot 11.
        budget = 50 if req.budget is None else max(1, min(int(req.budget), 500))
        cmd += ["--budget", str(budget)]

        min_age = 14.0 if req.min_age_days is None else max(0.0, float(req.min_age_days))
        cmd += ["--min-age-days", f"{min_age:g}"]

        if req.workers is not None:
            w = max(1, min(int(req.workers), 32))
            cmd += ["--workers", str(w)]

        if req.force:
            forced = [t.strip().upper() for t in req.force if isinstance(t, str) and t.strip()]
            if forced:
                cmd += ["--force", ",".join(forced)]

        cmd += ["--reason", f"staggered refresh (api, budget={budget})"]

        label = f"Universe Refresh (staggered · budget {budget})"
        meta = api_core.launch_job(cmd, label)
        return {"ok": True, "job": meta}

    # mode == "full" : rebuild intégral via universe_engine.
    cmd = [api_core.PYTHON_BIN, "-m", "modules.universe_engine"]
    if indices_arg:
        cmd += ["--indices", indices_arg]

    if req.min_market_cap is not None:
        if req.min_market_cap < 0 or req.min_market_cap > 1e15:
            raise HTTPException(400, "min_market_cap hors bornes")
        cmd += ["--min-cap", f"{float(req.min_market_cap):.0f}"]

    if req.workers is not None:
        w = max(1, min(int(req.workers), 32))
        cmd += ["--workers", str(w)]

    if req.max_retries is not None:
        r = max(0, min(int(req.max_retries), 5))
        cmd += ["--max-retries", str(r)]

    if req.timeout is not None:
        t = max(1.0, min(float(req.timeout), 60.0))
        cmd += ["--timeout", f"{t:.1f}"]

    cmd += ["--reason", "full rebuild (api)"]

    meta = api_core.launch_job(cmd, "Universe Rebuild (full · quantamental)")
    return {"ok": True, "job": meta}


@router.get("/universe", response_model=UniverseResponse)
def get_quantamental_universe(
    request: Request,
    response: Response,
    sector: str | None = None,
):
    """
    SOURCE OF TRUTH — univers quantamental long-terme.
    Lit data/universe.json. Payload : tickers enrichis (sector, market_cap,
    forward_pe, price targets, recommandations analystes), agrégation
    sectorielle, stats du dernier rebuild.

    Filtre optionnel ?sector=Technology pour restreindre la réponse.

    Conditional GET : émet un ETag basé sur la mtime de universe.json. Le
    client peut renvoyer `If-None-Match` → 304 Not Modified (zéro payload)
    tant que le rebuild n'a pas tourné. Le filtre `sector` est inclus dans
    l'ETag car il change la réponse.

    Métadonnées W8 :
      - last_updated_timestamp / stale_days / is_stale : âge du cache
      - is_incomplete par ticker (flag injecté dans chaque entrée)
      - incomplete_count / incomplete_ratio : agrégat global
    """
    path = api_core.UNIVERSE_QUANTAMENTAL_PATH

    # ETag = W/"<mtime>:<sector>:scored-v2". Un refresh/rebuild universe.json
    # bump mtime ; changer ?sector invalide aussi. Le suffixe `scored-v2` force
    # l'invalidation d'anciens caches client (v1 = scores, v2 = +drift).
    base_etag = api_core.etag_from_mtime(path)
    if base_etag:
        etag = f'{base_etag[:-1]}:{sector or "all"}:scored-v4"'
        if api_core.etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers={"ETag": etag})
        response.headers["ETag"] = etag
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = {"version": 0, "tickers": {}, "sectors": {}, "stats": {}}
    except json.JSONDecodeError as e:
        # Remonte via le handler global (503 corrupted_cache).
        raise api_core.CorruptedCacheError(path, e) from e
    except OSError as e:
        logger.error(f"[API /universe] I/O error: {e}", exc_info=True)
        raise HTTPException(500, f"Lecture universe.json impossible: {e}") from e

    running_job = api_core.running_universe_rebuild_job()
    tickers: dict = data.get("tickers", {}) or {}

    if sector:
        sec = sector.strip()
        tickers = {
            t: f for t, f in tickers.items()
            if (f.get("sector") or "Unknown") == sec
        }

    # ── Merge des scores TITAN (Option A — scanner quantamental) ──
    # `get_scored_universe()` est cache mtime-keyed sur universe.json :
    # premier appel = scoring complet, requêtes suivantes = lookup mémoire.
    # On échoue silencieusement si le scoring lève (univers minimaliste,
    # provider cassé) — la page reste utilisable même sans scores.
    scored: dict[str, dict] = {}
    try:
        from modules.sector_metrics import get_scored_universe
        scored = get_scored_universe() or {}
    except Exception as exc:  # noqa: BLE001 — best-effort enrichment
        logger.warning(f"[API /universe] scoring unavailable: {exc}")

    # ── Drift TITAN J-7 (Tier S #1) — détecte les upgrades silencieuses ──
    # Best-effort : si historique vide / read fail, drift=None partout.
    drift_baseline = _load_drift_baseline()

    # ── Sparkline TITAN 30j (Tier S #4) — micro-historique inline ──
    sparklines = _load_sparkline_series()

    # ── Status portefeuille (Tier A #3) — HELD/PROPOSED/VETOED/WATCH ──
    portfolio_status = _load_portfolio_status()

    # ── Annotation is_incomplete par ticker (W8) + injection scores + drift ──
    # On enrichit une copie superficielle pour ne pas muter le cache en mémoire.
    annotated: dict[str, dict] = {}
    incomplete_count = 0
    for t, f in tickers.items():
        incomplete = _ticker_is_incomplete(f)
        if incomplete:
            incomplete_count += 1
        scored_row = scored.get(t) or {}
        score_extras = {
            field: scored_row.get(field)
            for field in _SCORE_FIELDS_EXPOSED
            if field in scored_row
        }
        # Drift : current - baseline. None si pas de baseline pour ce ticker
        # OU si le titan courant est manquant.
        drift_val: float | None = None
        cur_titan = score_extras.get("titan_composite_score")
        baseline_titan = drift_baseline.get(t)
        if (
            isinstance(cur_titan, (int, float))
            and isinstance(baseline_titan, (int, float))
        ):
            drift_val = round(float(cur_titan) - float(baseline_titan), 2)
        spark = sparklines.get(t) or []
        annotated[t] = {
            **f,
            **score_extras,
            "titan_drift_7d": drift_val,
            "titan_baseline_7d": baseline_titan,
            "titan_sparkline": spark,
            "portfolio_status": portfolio_status.get(t),
            "is_incomplete": incomplete,
        }

    total = len(annotated)
    incomplete_ratio = round(incomplete_count / total, 4) if total else 0.0

    # ── Métadonnées de fraîcheur (W8) ──
    last_ts, stale_days = api_core.parse_updated_at(data.get("updated_at"))
    is_stale = stale_days is not None and stale_days > _UNIVERSE_STALE_THRESHOLD_DAYS

    return {
        "version":        data.get("version"),
        "updated_at":     data.get("updated_at"),
        "last_updated_timestamp":   last_ts,
        "stale_days":               stale_days,
        "is_stale":                 is_stale,
        "staleness_threshold_days": _UNIVERSE_STALE_THRESHOLD_DAYS,
        "incomplete_count":         incomplete_count,
        "incomplete_ratio":         incomplete_ratio,
        "source_indices": data.get("source_indices", []),
        "filter":         data.get("filter", {}),
        "stats":          data.get("stats", {}),
        "sectors":        data.get("sectors", {}),
        "tickers":        annotated,
        "count":          total,
        "rebuilding":     running_job is not None,
        "rebuild_job_id": running_job,
        "last_change":    data.get("last_change"),
    }
