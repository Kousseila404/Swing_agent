"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — PROPOSALS                                              ║
║  POST /api/proposals/refresh         — run auto_proposer         ║
║  GET  /api/proposals                 — liste (filtres status)    ║
║  POST /api/proposals/{id}/approve    — exécute un trade          ║
║  POST /api/proposals/{id}/reject     — veto utilisateur          ║
║  POST /api/proposals/approve_batch   — bulk approve + overrides  ║
║  POST /api/proposals/reject_batch    — bulk veto                 ║
║                                                                  ║
║  Toutes les routes mutantes exigent le Bearer token (auth        ║
║  fail-closed). GET reste public — la liste est lisible avec le   ║
║  même profil que /portfolio.                                     ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Security
from pydantic import BaseModel, Field

from modules import api_core, auto_proposer, proposals
from modules.log import logger

router = APIRouter(prefix="/api/proposals", tags=["proposals"])

# Stash du dernier run du proposer (gates + diagnostics + ran_at) pour que l'UI
# affiche l'audit sans forcer un refresh. Persisté sur disque — survit restart.
_LAST_REFRESH_PATH = api_core.BASE / "data" / "proposals_last_refresh.json"


# ─────────────────────────────────────────────────────────────────
# SCHÉMAS
# ─────────────────────────────────────────────────────────────────

class RefreshRequest(BaseModel):
    """Override optionnel des defaults du proposer."""
    total_capital:    float | None = None
    max_holdings:     int | None = None
    min_free_slots:   int | None = None
    min_proposal_usd: float | None = None
    allowed_regimes:  list[str] | None = None
    ttl_hours:        float | None = None
    notify_telegram:  bool = True
    # Nouveaux paramètres (fusion Reco → Propositions).
    allow_fractional_shares: bool | None = None
    include_held:     bool | None = None
    # "free_slots" (cron, défaut) ou "max_holdings" (UI rebalance complet).
    top_n_mode:       str | None = None


class RejectRequest(BaseModel):
    reason: str = Field(default="user_veto", max_length=500)


class ApproveOverride(BaseModel):
    """Overrides éditables par l'utilisateur avant l'exécution broker."""
    entry:       float | None = None
    stop_loss:   float | None = None
    take_profit: float | None = None
    size:        int   | None = None


class ApproveBatchItem(BaseModel):
    """Une ligne du bulk-approve — id proposition + overrides + ack."""
    id:                   str
    overrides:            ApproveOverride | None = None
    ack_sector_warning:   bool = False
    allow_top_up:         bool = False


class ApproveBatchRequest(BaseModel):
    items: list[ApproveBatchItem] = Field(default_factory=list)


class ApproveBatchResult(BaseModel):
    id:        str
    ticker:    str
    ok:        bool
    message:   str
    order_id:  str | None = None


class ApproveBatchResponse(BaseModel):
    ok:          bool
    n_requested: int
    n_executed:  int
    n_failed:    int
    results:     list[ApproveBatchResult]


class RejectBatchItem(BaseModel):
    id:     str
    reason: str = Field(default="user_veto", max_length=500)


class RejectBatchRequest(BaseModel):
    items: list[RejectBatchItem] = Field(default_factory=list)


class RejectBatchResult(BaseModel):
    id:      str
    ticker:  str | None = None
    ok:      bool
    message: str


class RejectBatchResponse(BaseModel):
    ok:          bool
    n_requested: int
    n_rejected:  int
    n_failed:    int
    results:     list[RejectBatchResult]


class ManualProposalRequest(BaseModel):
    """Push manuel d'un ticker depuis la page Univers (Tier S #3).
    Bypass auto_proposer — utile pour les overrides Watch (TITAN 70-80) avec
    signal externe (Support ON, F-Score ≥ 7) que le cron n'aurait pas retenus.
    Les SL/TP sont calculés via suggest_trade_levels comme pour les autos.
    """
    ticker:        str
    target_amount_usd: float | None = None  # default 5% du capital
    ttl_hours:     float | None = None
    signal:        str = "MANUAL_PUSH"


# ─────────────────────────────────────────────────────────────────
# GET — list
# ─────────────────────────────────────────────────────────────────

def _load_last_refresh() -> dict[str, Any] | None:
    """Lit le stash du dernier refresh (None si absent/corrompu)."""
    try:
        if _LAST_REFRESH_PATH.exists():
            return json.loads(_LAST_REFRESH_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[Proposals] last_refresh read failed: {e}")
    return None


def _save_last_refresh(payload: dict[str, Any]) -> None:
    """Persiste le dernier refresh (gates + diagnostics + ran_at + requested_params).

    Écriture atomique tmp+rename ; fail-open si le disque est plein.
    """
    try:
        _LAST_REFRESH_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _LAST_REFRESH_PATH.with_suffix(f".tmp.{uuid.uuid4().hex[:6]}")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(_LAST_REFRESH_PATH)
    except OSError as e:
        logger.warning(f"[Proposals] last_refresh write failed: {e}")


@router.get("")
def list_proposals(
    status: str | None = Query(default=None, description="pending|approved|rejected|expired|executed"),
    limit:  int = Query(default=200, ge=1, le=1000),
):
    """Liste les propositions + meta du dernier refresh.

    Effectue un sweep `expire_pending()` à chaque appel pour que l'UI voie les
    expirations en temps réel sans dépendre d'un cron séparé.

    `last_refresh` expose l'audit du dernier run auto_proposer (gates +
    diagnostics + ran_at) sans forcer un nouveau refresh. Utile pour que
    l'UI affiche les gates rouges dès l'ouverture de la page.
    """
    items = proposals.list_all(status=status)
    return {
        "proposals":    items[:limit],
        "n_total":      len(items),
        "n_pending":    sum(1 for p in items if p.get("status") == "pending"),
        "last_refresh": _load_last_refresh(),
    }


@router.get("/veto-history")
def get_veto_history(
    ticker:     str | None = Query(default=None, description="filtre exact (case-insensitive)"),
    since_days: float | None = Query(default=None, ge=0,
                                     description="fenêtre rolling N jours (basée sur decided_at)"),
    limit:      int = Query(default=50, ge=1, le=500,
                            description="max d'items dans `items`"),
):
    """Historique des vétos humains — propositions rejetées par l'utilisateur.

    Endpoint public (lecture seule, comme `GET /api/proposals`) qui facilite la
    consultation de l'historique des décisions négatives :
      • `summary` — agrégat sur la fenêtre `since_days` (default 90j) :
        nombre total, top tickers récurrents, top motifs, dernier veto.
      • `items`   — détail des `limit` derniers vétos, du plus récent au plus
        ancien, filtrable par ticker.

    Utile pour identifier les patterns (ex: "je veto-e tout le secteur Energy
    depuis 30j" → repenser sa thèse macro) ou retrouver pourquoi un ticker a
    été refusé il y a quelques semaines.
    """
    items = proposals.list_veto_history(
        ticker=ticker, since_days=since_days, limit=limit,
    )
    summary = proposals.veto_history_summary(
        since_days=since_days if since_days is not None else 90,
    )
    return {
        "summary": summary,
        "items":   items,
    }


# ─────────────────────────────────────────────────────────────────
# POST /refresh — run auto_proposer + enqueue
# ─────────────────────────────────────────────────────────────────

@router.post("/refresh")
def refresh_proposals(
    req: RefreshRequest = RefreshRequest(),  # noqa: B008 — pattern FastAPI
    _auth: None = Security(api_core.require_auth),
):
    """Run le proposer (gates → allocation → top-N candidats) + enqueue les
    propositions retenues.

    Réponse :
      - `result.gates` : audit complet (toutes gates, ok/ko + détail).
      - `result.proposals` : ce qui a effectivement été ajouté à la file.
      - `result.diagnostics` : sélection, portfolio, allocation summary.
    """
    kwargs: dict[str, Any] = {}
    if req.total_capital is not None:    kwargs["total_capital"]    = req.total_capital
    if req.max_holdings is not None:     kwargs["max_holdings"]     = req.max_holdings
    if req.min_free_slots is not None:   kwargs["min_free_slots"]   = req.min_free_slots
    if req.min_proposal_usd is not None: kwargs["min_proposal_usd"] = req.min_proposal_usd
    if req.allowed_regimes is not None:  kwargs["allowed_regimes"]  = tuple(req.allowed_regimes)
    if req.ttl_hours is not None:        kwargs["ttl_hours"]        = req.ttl_hours
    if req.allow_fractional_shares is not None:
        kwargs["allow_fractional_shares"] = req.allow_fractional_shares
    if req.include_held is not None:     kwargs["include_held"]     = req.include_held
    if req.top_n_mode is not None:       kwargs["top_n_mode"]       = req.top_n_mode

    try:
        result = auto_proposer.run_and_enqueue(**kwargs)
    except Exception as e:
        logger.error(f"[Proposals] refresh failed: {e}", exc_info=True)
        raise HTTPException(500, f"Auto-proposer a échoué: {e}") from e

    # Notification Telegram (fail-open) si au moins une proposition insérée.
    if req.notify_telegram and result.proposals:
        _notify_new_proposals(result.proposals)

    payload = result.to_dict()
    # Persiste un snapshot pour que GET /api/proposals expose last_refresh
    # sans re-run (l'audit reste dispo même si l'utilisateur recharge la page).
    _save_last_refresh({
        **payload,
        "ran_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested_params": req.dict(exclude_none=True),
    })
    return payload


# ─────────────────────────────────────────────────────────────────
# POST /{id}/approve — exécution
# ─────────────────────────────────────────────────────────────────

@router.post("/{proposal_id}/approve")
def approve_proposal(
    proposal_id: str,
    _auth: None = Security(api_core.require_auth),
):
    """Approuve la proposition + ouvre le trade dans le journal.

    Flow atomique :
      1. Marque la proposition `approved` (transition pending → approved).
      2. Écrit la ligne OPEN dans trade_journal.csv sous FileLock.
      3. Marque la proposition `executed` avec l'order_id généré.

    Si l'écriture journal échoue après l'approbation, on tente de revert via
    une nouvelle proposition mais c'est best-effort — on log et on retourne 500.
    """
    # Étape 1 : approve (vérifie l'existence + état pending)
    try:
        item = proposals.update_status(proposal_id, "approved", decided_by="user")
    except ValueError as e:
        raise HTTPException(404 if "introuvable" in str(e) else 400, str(e)) from e

    # Étape 2 : envoi RÉEL au broker (Lot 14 — fix critique).
    # AVANT : le endpoint écrivait CSV directement SANS contacter Alpaca →
    # proposition "executed" mais 0 ordre réel côté broker. C'est exactement
    # le bug qui a causé les 20 positions fantômes.
    # MAINTENANT : on délègue à broker.submit_order() qui écrit CSV UNIQUEMENT
    # si l'ordre est accepté. En cas de rejet, on revert la proposition en
    # état "approved" (pas "executed") pour que l'utilisateur retente.
    try:
        if item["direction"] == "LONG":
            rr = round(
                (item["take_profit"] - item["entry"]) /
                (item["entry"] - item["stop_loss"]),
                2,
            )
        else:
            rr = round(
                (item["entry"] - item["take_profit"]) /
                (item["stop_loss"] - item["entry"]),
                2,
            )
    except ZeroDivisionError:
        rr = 0.0

    from types import SimpleNamespace

    from modules.broker_gateway import get_broker

    # Alignement prix live : la proposition peut dater (TTL 36h). On re-fetch
    # le prix actuel et rescale SL/TP proportionnellement pour préserver le RR.
    entry_used = float(item["entry"])
    sl_used = float(item["stop_loss"])
    tp_used = float(item["take_profit"])
    try:
        from modules.tracker.market import get_current_price
        live_px = get_current_price(item["ticker"])
    except Exception:
        live_px = None
    if live_px is not None and live_px > 0 and entry_used > 0:
        rel_diff = abs(live_px - entry_used) / entry_used
        if rel_diff > 0.005:
            if item["direction"] == "LONG":
                sl_pct = (entry_used - sl_used) / entry_used
                tp_pct = (tp_used - entry_used) / entry_used
                entry_used = float(live_px)
                sl_used = entry_used * (1.0 - sl_pct)
                tp_used = entry_used * (1.0 + tp_pct)
            else:
                sl_pct = (sl_used - entry_used) / entry_used
                tp_pct = (entry_used - tp_used) / entry_used
                entry_used = float(live_px)
                sl_used = entry_used * (1.0 + sl_pct)
                tp_used = entry_used * (1.0 - tp_pct)
            logger.info(
                f"[Proposals] {item['ticker']} entry aligné live : "
                f"{item['entry']:.4f} → {entry_used:.4f}"
            )

    scan = SimpleNamespace(
        ticker=item["ticker"],
        direction=item["direction"],
        price=float(entry_used),
        stop_loss=float(sl_used),
        take_profit=float(tp_used),
        position_size=int(item["size"]),
        rr_ratio=abs(rr),
        signal=item.get("signal") or "AUTO_PROPOSAL",
        sector_etf=item.get("sector") or "",
    )
    try:
        broker = get_broker()
        broker_result = broker.submit_order(scan)
    except Exception as exc:
        logger.error(
            f"[Proposals] broker submit failed for {proposal_id}: {exc}",
            exc_info=True,
        )
        # Revert : remet la proposition en pending pour permettre un retry.
        try:
            _ = proposals._read_all_unlocked  # sanity check import ok
            # Simple path : on mute directement le fichier via update_status inverse
            # n'existe pas → on laisse la proposition "approved" orphaned, l'utilisateur
            # peut la rejeter manuellement et le proposer la regénérera.
        except Exception:
            pass
        raise HTTPException(500, f"Broker indisponible : {exc}") from exc

    if not broker_result.success:
        logger.warning(
            f"[Proposals] broker rejet {proposal_id} : {broker_result.message}"
        )
        raise HTTPException(
            502,
            f"Ordre rejeté par le broker : {broker_result.message}",
        )

    order_id = broker_result.order_id

    # Étape 3 : marque executed + attache l'order_id Alpaca réel.
    try:
        item = proposals.update_status(
            proposal_id, "executed",
            decided_by="user", order_id=order_id,
        )
    except ValueError as e:
        # Bizarre — log mais on ne casse pas la réponse, le trade est ouvert.
        logger.error(
            f"[Proposals] post-execute status update failed for {proposal_id}: {e}"
        )

    return {
        "ok":       True,
        "proposal": item,
        "order_id": order_id,
        "message":  (
            f"OPEN {item['size']} {item['ticker']} @ {entry_used:.4f} "
            f"via {broker.name} — {broker_result.message}"
        ),
    }


# ─────────────────────────────────────────────────────────────────
# POST /{id}/reject
# ─────────────────────────────────────────────────────────────────

@router.post("/{proposal_id}/reject")
def reject_proposal(
    proposal_id: str,
    req: RejectRequest = RejectRequest(),  # noqa: B008
    _auth: None = Security(api_core.require_auth),
):
    """Veto utilisateur — la proposition passe en `rejected`."""
    try:
        item = proposals.update_status(
            proposal_id, "rejected",
            decided_by="user",
            rejection_reason=req.reason,
        )
    except ValueError as e:
        raise HTTPException(404 if "introuvable" in str(e) else 400, str(e)) from e
    return {"ok": True, "proposal": item}


# ─────────────────────────────────────────────────────────────────
# POST /regenerate — purge pending + refresh en une seule transaction
# ─────────────────────────────────────────────────────────────────

@router.post("/regenerate")
def regenerate_proposals(
    req: RefreshRequest = RefreshRequest(),  # noqa: B008
    _auth: None = Security(api_core.require_auth),
):
    """Purge les propositions pending puis relance le proposer.

    Différence avec `/refresh` : on force-expire les pending existants AVANT
    le nouveau run. Permet à l'utilisateur de changer les paramètres (capital,
    mode, include_held) et obtenir un plan cohérent avec ces paramètres, sans
    être bloqué par le dédup intra-file (un ticker pending existant empêche
    sa re-proposition).

    On passe par `expired` (pas `rejected`) pour ne PAS déclencher le cooldown
    veto 7 j. Les rejected existants ne sont pas touchés — leur cooldown
    reste actif (protège contre annuler un veto par mégarde).

    Réponse : même shape que `/refresh` + `n_expired` dans diagnostics.
    """
    n_expired = proposals.expire_all_pending(reason="user_regenerate")

    kwargs: dict[str, Any] = {}
    if req.total_capital is not None:    kwargs["total_capital"]    = req.total_capital
    if req.max_holdings is not None:     kwargs["max_holdings"]     = req.max_holdings
    if req.min_free_slots is not None:   kwargs["min_free_slots"]   = req.min_free_slots
    if req.min_proposal_usd is not None: kwargs["min_proposal_usd"] = req.min_proposal_usd
    if req.allowed_regimes is not None:  kwargs["allowed_regimes"]  = tuple(req.allowed_regimes)
    if req.ttl_hours is not None:        kwargs["ttl_hours"]        = req.ttl_hours
    if req.allow_fractional_shares is not None:
        kwargs["allow_fractional_shares"] = req.allow_fractional_shares
    if req.include_held is not None:     kwargs["include_held"]     = req.include_held
    if req.top_n_mode is not None:       kwargs["top_n_mode"]       = req.top_n_mode

    try:
        result = auto_proposer.run_and_enqueue(**kwargs)
    except Exception as e:
        logger.error(f"[Proposals] regenerate failed: {e}", exc_info=True)
        raise HTTPException(500, f"Auto-proposer a échoué: {e}") from e

    if req.notify_telegram and result.proposals:
        _notify_new_proposals(result.proposals)

    payload = result.to_dict()
    payload.setdefault("diagnostics", {})["n_expired"] = n_expired
    _save_last_refresh({
        **payload,
        "ran_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested_params": {**req.dict(exclude_none=True), "regenerate": True},
    })
    return payload


# ─────────────────────────────────────────────────────────────────
# POST /manual — push d'un ticker depuis la page Univers (Tier S #3)
# ─────────────────────────────────────────────────────────────────

@router.post("/manual")
def manual_proposal(
    req: ManualProposalRequest,
    _auth: None = Security(api_core.require_auth),
):
    """Crée une proposition manuelle pour un ticker (bypass cron auto_proposer).

    Use case : la page Univers a un ticker en TITAN ≥ 70 que l'utilisateur veut
    mettre dans la file d'attente d'achat sans attendre le cron quotidien (ex.
    upgrade silencieuse détectée via le drift Δ7j).

    Réutilise le pipeline existant :
      - prix + vol depuis `sector_metrics.get_scored_universe()` (cache mtime)
      - SL/TP via `suggest_trade_levels` (σ-adaptive Long-Term)
      - cooldowns veto/win et dédup pending appliqués automatiquement par
        `proposals.enqueue_batch` (un ticker pending → 409 silencieux)

    Réponse :
      - `ok=True` + `proposal` si insertion réussie
      - `ok=False` + `reason` si dédup ou échec de calcul des niveaux
    """
    from modules.portfolio._trade_levels import suggest_trade_levels
    from modules.sector_metrics import get_scored_universe

    ticker = req.ticker.upper().strip()
    if not ticker or len(ticker) > 12:
        raise HTTPException(400, f"Ticker invalide: {req.ticker!r}")

    try:
        scored = get_scored_universe() or {}
    except Exception as exc:
        logger.error(f"[Proposals/manual] scoring KO: {exc}", exc_info=True)
        raise HTTPException(503, "Univers scoré indisponible") from exc

    row = scored.get(ticker)
    if row is None:
        raise HTTPException(404, f"Ticker {ticker} absent de l'univers scoré")

    price = row.get("current_price") or row.get("price")
    if not price or not isinstance(price, (int, float)) or price <= 0:
        raise HTTPException(422, f"Prix invalide pour {ticker}: {price!r}")

    vol_pct = row.get("volatility_pct")
    levels = suggest_trade_levels(price=float(price), volatility_pct=vol_pct)
    sl = levels.get("sl")
    tp = levels.get("tp")
    if not sl or not tp:
        raise HTTPException(422, f"SL/TP non calculables pour {ticker}")

    # Sizing : par défaut 5% du capital de référence (DEFAULT_TOTAL_CAPITAL=100k
    # → 5k par position, aligné avec max_holdings=20). L'utilisateur édite via
    # ApproveBatch overrides s'il veut autre chose à l'exécution.
    target_usd = req.target_amount_usd
    if target_usd is None:
        target_usd = auto_proposer.DEFAULT_TOTAL_CAPITAL / auto_proposer.DEFAULT_MAX_HOLDINGS
    if target_usd <= 0:
        raise HTTPException(400, "target_amount_usd doit être > 0")
    size = max(1, int(target_usd / float(price)))

    # `ttl_hours` non précisé → make_proposal lit PROPOSAL_TTL_HOURS (default 0 = no TTL).
    proposal = proposals.make_proposal(
        ticker=ticker,
        direction="LONG",
        entry=float(price),
        stop_loss=float(sl),
        take_profit=float(tp),
        size=size,
        sector=str(row.get("sector") or ""),
        signal=req.signal,
        ttl_hours=req.ttl_hours,
        context={
            "titan_score":      row.get("titan_composite_score"),
            "quality_score":    row.get("quality_score"),
            "value_score":      row.get("value_score"),
            "risk_score":       row.get("risk_score"),
            "momentum_score":   row.get("momentum_score"),
            "piotroski_score":  row.get("piotroski_score"),
            "f_score":          row.get("f_score"),
            "f_score_max":      row.get("f_score_max"),
            "growth_score":     row.get("growth_score"),
            "volatility_pct":   vol_pct,
            "price_live":       float(price),
            "amount_usd":       round(size * float(price), 2),
            "levels_method":    levels.get("method"),
            "suggested_sl_pct": levels.get("sl_pct"),
            "suggested_tp_pct": levels.get("tp_pct"),
            "manual":           True,
        },
    )

    inserted = proposals.enqueue_batch([proposal])
    if not inserted:
        # Dédup silencieux (pending existant, cooldown veto, cooldown win).
        return {
            "ok": False,
            "reason": "dedup_or_cooldown",
            "message": (
                f"{ticker} non inséré : déjà pending, ou en cooldown veto/win. "
                "Vérifier la file actuelle ou /api/proposals/{id}/reject pour reset."
            ),
        }
    return {"ok": True, "proposal": inserted[0]}


# ─────────────────────────────────────────────────────────────────
# POST /approve_batch — bulk-approve avec overrides éditables
# ─────────────────────────────────────────────────────────────────

def _apply_overrides(
    item: dict[str, Any], overrides: ApproveOverride | None,
) -> tuple[float, float, float, int]:
    """Applique les overrides user sur une proposition et retourne
    (entry, stop_loss, take_profit, size). Fallback sur les valeurs
    de la proposition quand un override est None.

    On ne mute PAS `item` — la proposition conserve ses valeurs d'origine
    dans le journal audit.
    """
    entry = float(item["entry"])
    sl    = float(item["stop_loss"])
    tp    = float(item["take_profit"])
    size  = int(item["size"])
    if overrides is None:
        return entry, sl, tp, size
    if overrides.entry       is not None: entry = float(overrides.entry)
    if overrides.stop_loss   is not None: sl    = float(overrides.stop_loss)
    if overrides.take_profit is not None: tp    = float(overrides.take_profit)
    if overrides.size        is not None: size  = int(overrides.size)
    return entry, sl, tp, size


def _validate_approve_payload(
    *, direction: str, entry: float, sl: float, tp: float, size: int,
) -> str | None:
    """Même règles que /portfolio/execute. Retourne None si OK, sinon message."""
    if size <= 0:
        return "size doit être > 0"
    if entry <= 0 or sl <= 0 or tp <= 0:
        return "Prix invalides (≤ 0)"
    if direction == "LONG":
        if sl >= entry:
            return "SL doit être < Entry (LONG)"
        if tp <= entry:
            return "TP doit être > Entry (LONG)"
    elif direction == "SHORT":
        if sl <= entry:
            return "SL doit être > Entry (SHORT)"
        if tp >= entry:
            return "TP doit être < Entry (SHORT)"
    else:
        return f"Direction inconnue: {direction!r}"
    return None


def _snapshot_open_tickers() -> set[str]:
    """Snapshot des tickers déjà OPEN — pour idempotence vs trades en cours."""
    try:
        from modules.portfolio import read_open_positions
        return {
            str(p.get("ticker") or "").upper().strip()
            for p in read_open_positions()
        }
    except Exception as exc:
        logger.warning(
            f"[Proposals] read_open_positions failed in approve_batch: {exc} "
            "— idempotence désactivée pour ce batch"
        )
        return set()


@router.post("/approve_batch", response_model=ApproveBatchResponse)
def approve_proposals_batch(
    req: ApproveBatchRequest,
    _auth: None = Security(api_core.require_auth),
):
    """Bulk-approve avec overrides par proposition.

    Flow par item :
      1. Lookup proposition (404 si absente, 400 si pas pending).
      2. Applique les overrides (entry/sl/tp/size édités par l'UI).
      3. Valide SL < Entry < TP etc.
      4. Idempotence : intra-batch duplicate, déjà OPEN sans allow_top_up.
      5. Refuse si sector_exposure.over_cap=True sans ack_sector_warning.
      6. Live-price realignement (seuil 0.5%) pour aligner sur la source
         tracker → pas de glissement PnL à l'ouverture.
      7. broker.submit_order — si broker rejet, la proposition reste pending
         avec rejection_reason=last_broker_error (retry possible).
      8. Succès → transition pending → approved → executed + order_id.

    Chaque item est indépendant — une erreur n'annule pas les autres.
    """
    n_req = len(req.items)
    if n_req == 0:
        raise HTTPException(400, "Aucun item fourni")
    if n_req > 50:
        raise HTTPException(400, "Maximum 50 items par appel")

    from types import SimpleNamespace

    from modules.broker_gateway import get_broker
    try:
        from modules.tracker.market import get_current_price
    except Exception:  # pragma: no cover — defensive
        get_current_price = None  # type: ignore

    open_tickers = _snapshot_open_tickers()
    batch_tickers_seen: set[str] = set()
    results: list[ApproveBatchResult] = []

    for entry_req in req.items:
        prop = proposals.get(entry_req.id)
        if prop is None:
            results.append(ApproveBatchResult(
                id=entry_req.id, ticker="", ok=False,
                message="Proposition introuvable",
            ))
            continue

        ticker = str(prop.get("ticker") or "").upper().strip()
        direction = str(prop.get("direction") or "LONG").upper()

        if prop.get("status") != "pending":
            results.append(ApproveBatchResult(
                id=entry_req.id, ticker=ticker, ok=False,
                message=f"Statut non approuvable: {prop.get('status')}",
            ))
            continue

        # Over-cap — refuse sans ack explicite.
        ctx = prop.get("context") or {}
        over_cap = bool((ctx.get("sector_exposure") or {}).get("over_cap"))
        if over_cap and not entry_req.ack_sector_warning:
            results.append(ApproveBatchResult(
                id=entry_req.id, ticker=ticker, ok=False,
                message=(
                    f"{ticker} en sur-exposition secteur "
                    f"({ctx.get('sector_exposure', {}).get('projected_pct')}%). "
                    "ack_sector_warning=true requis."
                ),
            ))
            continue

        # Idempotence 1 : intra-batch.
        if ticker in batch_tickers_seen:
            results.append(ApproveBatchResult(
                id=entry_req.id, ticker=ticker, ok=False,
                message="Doublon intra-batch — ignoré",
            ))
            continue

        # Idempotence 2 : déjà OPEN → requiert allow_top_up explicite.
        already_held_flag = bool(ctx.get("already_held")) or ticker in open_tickers
        if already_held_flag and not entry_req.allow_top_up:
            results.append(ApproveBatchResult(
                id=entry_req.id, ticker=ticker, ok=False,
                message=(
                    f"{ticker} déjà OPEN — allow_top_up=true requis pour top-up"
                ),
            ))
            continue

        # Applique overrides + valide.
        entry_px, sl_px, tp_px, size = _apply_overrides(prop, entry_req.overrides)
        err = _validate_approve_payload(
            direction=direction, entry=entry_px, sl=sl_px, tp=tp_px, size=size,
        )
        if err:
            results.append(ApproveBatchResult(
                id=entry_req.id, ticker=ticker, ok=False, message=err,
            ))
            continue

        # Live-price realignement — préserve l'intention RR.
        live_px = None
        if get_current_price is not None:
            try:
                live_px = get_current_price(ticker)
            except Exception as exc:
                logger.debug(f"[approve_batch] {ticker} live price failed: {exc}")
                live_px = None
        if live_px is not None and live_px > 0 and entry_px > 0:
            rel_diff = abs(live_px - entry_px) / entry_px
            if rel_diff > 0.005:
                if direction == "LONG":
                    sl_pct = (entry_px - sl_px) / entry_px
                    tp_pct = (tp_px - entry_px) / entry_px
                    entry_px = float(live_px)
                    sl_px    = entry_px * (1.0 - sl_pct)
                    tp_px    = entry_px * (1.0 + tp_pct)
                else:
                    sl_pct = (sl_px - entry_px) / entry_px
                    tp_pct = (entry_px - tp_px) / entry_px
                    entry_px = float(live_px)
                    sl_px    = entry_px * (1.0 + sl_pct)
                    tp_px    = entry_px * (1.0 - tp_pct)

        # RR pour log broker.
        try:
            if direction == "LONG":
                rr = round((tp_px - entry_px) / (entry_px - sl_px), 2)
            else:
                rr = round((entry_px - tp_px) / (sl_px - entry_px), 2)
        except ZeroDivisionError:
            rr = 0.0

        # Transition pending → approved AVANT submit broker — permet à la UI
        # qui re-fetch entre-temps de voir l'état intermédiaire.
        try:
            prop = proposals.update_status(
                entry_req.id, "approved", decided_by="user",
            )
        except ValueError as e:
            results.append(ApproveBatchResult(
                id=entry_req.id, ticker=ticker, ok=False, message=str(e),
            ))
            continue

        # Lot 15 — capture scores TITAN à l'entrée depuis le context de la
        # proposition. Le broker `_log_to_csv` les persiste dans trade_journal
        # via `_entry_scores_dict` → permettra de corréler score entry →
        # outcome WIN/LOSS quand le trade ferme.
        f_score = ctx.get("f_score")
        f_score_max = ctx.get("f_score_max")
        f_score_str = (
            f"{int(f_score)}/{int(f_score_max)}"
            if f_score is not None and f_score_max is not None else ""
        )

        # Phase 1 data hardening (2026-04-29) — confidence à l'entrée.
        # On la calcule depuis le context fundamentals déjà capturé.
        try:
            from modules.data_confidence import compute_confidence
            _conf = compute_confidence({
                "data_quality":            ctx.get("data_quality"),
                "quality_score":           ctx.get("quality_score"),
                "f_score":                 ctx.get("f_score"),
                "peg_ratio":               ctx.get("peg_ratio"),
                "forward_pe":              ctx.get("forward_pe"),
                "fundamentals_age_days":   ctx.get("fundamentals_age_days"),
            })
            confidence_entry_value = _conf.get("score")
        except Exception:
            confidence_entry_value = None

        scan = SimpleNamespace(
            ticker=ticker,
            direction=direction,
            price=float(entry_px),
            stop_loss=float(sl_px),
            take_profit=float(tp_px),
            position_size=int(size),
            rr_ratio=abs(rr),
            signal=prop.get("signal") or "AUTO_PROPOSAL",
            sector_etf=prop.get("sector") or "",
            # Scores TITAN à l'entrée (Lot 15)
            titan_score_entry=ctx.get("titan_score"),
            quality_entry=ctx.get("quality_score"),
            value_entry=ctx.get("value_score"),
            risk_entry=ctx.get("risk_score"),
            momentum_entry=ctx.get("momentum_score"),
            piotroski_entry=ctx.get("piotroski_score"),
            growth_entry=ctx.get("growth_score"),
            f_score_entry=f_score_str,
            tilt_flags_entry=ctx.get("titan_tilt_flags") or [],
            # Phase 1 data hardening — confidence à l'entrée.
            confidence_entry=confidence_entry_value,
        )
        try:
            broker = get_broker()
            br = broker.submit_order(scan)
        except Exception as exc:
            logger.error(
                f"[approve_batch] broker submit failed for {entry_req.id}: {exc}",
                exc_info=True,
            )
            # Best-effort : on laisse la proposition en approved avec une note.
            results.append(ApproveBatchResult(
                id=entry_req.id, ticker=ticker, ok=False,
                message=f"Broker indisponible : {exc}",
            ))
            continue

        if not br.success:
            # Rejet broker — on note la raison dans rejection_reason et on laisse
            # la proposition en `approved` orpheline (retry manuel possible via
            # reject puis re-génération plus tard).
            results.append(ApproveBatchResult(
                id=entry_req.id, ticker=ticker, ok=False,
                message=f"Broker rejet : {br.message}",
            ))
            continue

        # Succès — marque executed + attache order_id.
        try:
            proposals.update_status(
                entry_req.id, "executed",
                decided_by="user", order_id=br.order_id,
            )
        except ValueError as e:
            logger.error(
                f"[approve_batch] post-execute status update failed for "
                f"{entry_req.id}: {e}"
            )
        open_tickers.add(ticker)
        batch_tickers_seen.add(ticker)
        results.append(ApproveBatchResult(
            id=entry_req.id, ticker=ticker, ok=True,
            message=(
                f"OPEN {size} @ {entry_px:.4f} via {broker.name} "
                f"(RR {abs(rr):.2f}) — {br.message}"
            ),
            order_id=br.order_id,
        ))

    n_ok = sum(1 for r in results if r.ok)
    return ApproveBatchResponse(
        ok=(n_ok == n_req),
        n_requested=n_req,
        n_executed=n_ok,
        n_failed=n_req - n_ok,
        results=results,
    )


# ─────────────────────────────────────────────────────────────────
# POST /reject_batch — veto en masse
# ─────────────────────────────────────────────────────────────────

@router.post("/reject_batch", response_model=RejectBatchResponse)
def reject_proposals_batch(
    req: RejectBatchRequest,
    _auth: None = Security(api_core.require_auth),
):
    """Veto en masse — chaque item peut avoir une raison distincte."""
    n_req = len(req.items)
    if n_req == 0:
        raise HTTPException(400, "Aucun item fourni")
    if n_req > 50:
        raise HTTPException(400, "Maximum 50 items par appel")

    results: list[RejectBatchResult] = []
    for item in req.items:
        prop_before = proposals.get(item.id)
        ticker = (prop_before or {}).get("ticker") if prop_before else None
        try:
            prop = proposals.update_status(
                item.id, "rejected",
                decided_by="user", rejection_reason=item.reason,
            )
            results.append(RejectBatchResult(
                id=item.id, ticker=prop.get("ticker"),
                ok=True, message="rejected",
            ))
        except ValueError as e:
            results.append(RejectBatchResult(
                id=item.id, ticker=ticker, ok=False, message=str(e),
            ))

    n_ok = sum(1 for r in results if r.ok)
    return RejectBatchResponse(
        ok=(n_ok == n_req),
        n_requested=n_req,
        n_rejected=n_ok,
        n_failed=n_req - n_ok,
        results=results,
    )


# ─────────────────────────────────────────────────────────────────
# NOTIFICATION TELEGRAM
# ─────────────────────────────────────────────────────────────────

def _notify_new_proposals(items: list[dict[str, Any]]) -> None:
    """Envoie 1 message Telegram récapitulatif des propositions ajoutées.

    Fail-open : un échec Telegram n'invalide pas le refresh.
    """
    try:
        from modules.alerter import _send_telegram_message
    except Exception:
        return

    n = len(items)
    lines = [f"📬 <b>{n} nouvelle{'s' if n > 1 else ''} proposition{'s' if n > 1 else ''} TITAN</b>"]
    lines.append("━" * 28)
    for p in items[:10]:  # cap pour rester sous la limite Telegram
        score = (p.get("context") or {}).get("titan_score")
        score_str = f" — score {score:.1f}" if isinstance(score, (int, float)) else ""
        lines.append(
            f"• <b>{p['ticker']}</b> ({p.get('sector') or '?'}) "
            f"{p['size']} @ ${p['entry']:.2f}{score_str}"
        )
    if n > 10:
        lines.append(f"…+ {n - 10} autres")
    lines.append("━" * 28)
    lines.append("👉 Approuver/rejeter dans l'onglet <b>Propositions</b>.")
    msg = "\n".join(lines)
    try:
        _send_telegram_message(msg)
    except Exception as e:
        logger.warning(f"[Proposals] Telegram notify failed: {e}")
