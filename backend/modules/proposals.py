"""Module — file de propositions d'achat (auto-proposer + veto humain).

Chaque proposition est un trade *suggéré* par le moteur d'allocation TITAN,
en attente d'approbation explicite de l'utilisateur. Tant qu'elle n'est pas
approuvée, **aucun trade n'est ouvert** dans le journal.

États (state machine append-only) :

    pending → approved → executed   (chemin nominal)
    pending → rejected               (veto utilisateur)
    pending → expired                (TTL dépassé sans décision)

Stockage :
  • `data/proposals.json`        — file vivante (toutes propositions, tous statuts)
  • `data/proposals_audit.jsonl` — audit append-only (1 event = 1 ligne)

FileLock unique sur le JSON pour cohérence cross-process avec l'API.
"""
from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from filelock import FileLock

from modules import api_core
from modules.log import logger

# ─────────────────────────────────────────────────────────────────
# CHEMINS — lus via api_core.BASE pour être monkey-patchable en tests
# ─────────────────────────────────────────────────────────────────
PROPOSALS_PATH      = api_core.BASE / "data" / "proposals.json"
PROPOSALS_LOCK_PATH = api_core.BASE / "data" / "proposals.json.lock"
PROPOSALS_AUDIT_PATH = api_core.BASE / "data" / "proposals_audit.jsonl"

# Statuts terminaux : pas de transition possible.
_TERMINAL_STATUSES = {"executed", "rejected", "expired"}

# TTL par défaut : une proposition non décidée 36 h est marquée expired.
# 36h couvre un weekend (vendredi soir → lundi matin) sans expirer prématurément.
DEFAULT_TTL_HOURS = 36

# Garde uniquement les N dernières propositions terminées dans la file vivante.
# Au-delà, elles ne servent qu'à l'audit (qui reste complet via .jsonl).
_LIVE_HISTORY_LIMIT = 100

# Cooldown anti-veto : combien de jours un ticker rejected doit "sécher" avant
# de pouvoir être reproposé. Évite que l'auto_proposer repropose dès le lendemain
# un ticker que l'utilisateur vient de veto-er. 7 jours = fenêtre courte qui
# laisse la config bouger (news, swing régime) sans harceler.
# Override via env var VETO_COOLDOWN_DAYS (0 = cooldown désactivé, comportement pré-fix).
def _veto_cooldown_days() -> float:
    raw = (os.getenv("VETO_COOLDOWN_DAYS") or "").strip()
    if not raw:
        return 7.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 7.0


# Cooldown anti-churn WIN/LOSS : un ticker clôturé récemment (TP, SL, TS, time exit)
# ne peut pas être reproposé pendant N jours. Default 14j = aligné avec l'horizon
# Quantamental Long-Term. Évite le cycle observé le 2026-04-22/23 : CTRA WIN +1.5%
# le 22/04 → re-proposé 23/04 à -4.6% du prix d'exit. Frais + slippage en pure perte.
# Override via env var WIN_COOLDOWN_DAYS (0 = cooldown désactivé).
def _win_cooldown_days() -> float:
    raw = (os.getenv("WIN_COOLDOWN_DAYS") or "").strip()
    if not raw:
        return 14.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 14.0


def _recently_closed_tickers(cutoff: datetime) -> set[str]:
    """Tickers dont un trade WIN/LOSS a un Exit_Date >= cutoff (local naive).

    Lecture read-only du trade journal — pas de FileLock (le CSV est écrit
    atomiquement via tmp+rename, donc un lecteur voit soit l'ancien soit le
    nouveau, jamais partiel ; un FileLock ici risquerait un interblocage avec
    le tracker qui écrit sous son propre lock).
    """
    try:
        from modules.utils import CSV_PATH
    except Exception:
        return set()
    if not CSV_PATH.exists():
        return set()
    try:
        import pandas as pd
        df = pd.read_csv(CSV_PATH, dtype={"Ticker": str})
    except Exception as exc:
        logger.warning(f"[Proposals] Échec lecture trade journal pour cooldown WIN : {exc}")
        return set()
    if df.empty or "Status" not in df.columns or "Exit_Date" not in df.columns:
        return set()
    closed = df[df["Status"].isin(["WIN", "LOSS"])].dropna(subset=["Exit_Date"])
    if closed.empty:
        return set()
    exit_dt = pd.to_datetime(closed["Exit_Date"], errors="coerce")
    mask = exit_dt.notna() & (exit_dt >= pd.Timestamp(cutoff))
    return set(closed.loc[mask, "Ticker"].astype(str).str.upper().str.strip())


@dataclass
class Proposal:
    """Une proposition d'achat — trade complet + contexte qui l'a justifié."""
    id: str
    created_at: str
    expires_at: str
    status: str  # pending | approved | rejected | expired | executed

    # Trade (doit être valide pour /portfolio/execute)
    ticker: str
    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    size: int
    sector: str
    signal: str

    # Contexte décisionnel (titan_score, weight_pct, sector exposure, macro…)
    context: dict[str, Any] = field(default_factory=dict)

    # Décision (rempli quand status sort de pending)
    decided_at: str | None = None
    decided_by: str | None = None  # "user" pour MVP — réservé pour multi-user
    rejection_reason: str | None = None
    order_id: str | None = None  # rempli après exécution


# ─────────────────────────────────────────────────────────────────
# IO — atomic read/write sous FileLock
# ─────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    """ISO-8601 UTC sans microsecondes — uniforme avec macro_state.json etc."""
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_all_unlocked() -> list[dict[str, Any]]:
    """Lit la file complète. À appeler sous FileLock."""
    if not PROPOSALS_PATH.exists() or PROPOSALS_PATH.stat().st_size == 0:
        return []
    try:
        data = json.loads(PROPOSALS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.error(f"[Proposals] JSON corrompu {PROPOSALS_PATH}: {e}")
        # Backup avant de repartir vide pour ne pas perdre l'audit visuel.
        backup = PROPOSALS_PATH.with_suffix(f".corrupt.{int(_now_utc().timestamp())}.json")
        try:
            PROPOSALS_PATH.rename(backup)
            logger.warning(f"[Proposals] Fichier corrompu déplacé → {backup.name}")
        except OSError:
            pass
        return []
    if not isinstance(data, list):
        logger.error(f"[Proposals] Format inattendu (non-list) dans {PROPOSALS_PATH}")
        return []
    return data


def _write_all_unlocked(items: list[dict[str, Any]]) -> None:
    """Écrit la file (atomic via tmp+rename). À appeler sous FileLock."""
    PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROPOSALS_PATH.with_suffix(f".tmp.{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PROPOSALS_PATH)


def _append_audit(event: str, proposal: dict[str, Any], extra: dict[str, Any] | None = None) -> None:
    """Append-only audit log (jsonl). Fail-open : un échec audit ne bloque pas la file."""
    try:
        PROPOSALS_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts":       _iso(_now_utc()),
            "event":    event,
            "id":       proposal.get("id"),
            "ticker":   proposal.get("ticker"),
            "status":   proposal.get("status"),
        }
        if extra:
            record.update(extra)
        with PROPOSALS_AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"[Proposals] Audit write failed: {e}")


def _trim_live_history(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tronque les propositions terminées au-delà de _LIVE_HISTORY_LIMIT.

    On garde tous les pending (jamais tronqués) + les N plus récentes terminées.
    L'audit complet reste dans `proposals_audit.jsonl`.
    """
    pending = [p for p in items if p.get("status") == "pending"]
    terminal = [p for p in items if p.get("status") != "pending"]
    terminal.sort(key=lambda p: p.get("decided_at") or p.get("created_at") or "", reverse=True)
    return pending + terminal[:_LIVE_HISTORY_LIMIT]


# ─────────────────────────────────────────────────────────────────
# CRUD PUBLIC API
# ─────────────────────────────────────────────────────────────────

def make_proposal(
    *,
    ticker: str,
    direction: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    size: int,
    sector: str,
    signal: str,
    context: dict[str, Any] | None = None,
    ttl_hours: float = DEFAULT_TTL_HOURS,
) -> Proposal:
    """Construit une proposition (sans la persister). Pure — testable."""
    now = _now_utc()
    return Proposal(
        id=f"PROP_{uuid.uuid4().hex[:8].upper()}",
        created_at=_iso(now),
        expires_at=_iso(now + timedelta(hours=ttl_hours)),
        status="pending",
        ticker=ticker.upper().strip(),
        direction=direction,
        entry=round(float(entry), 4),
        stop_loss=round(float(stop_loss), 4),
        take_profit=round(float(take_profit), 4),
        size=int(size),
        sector=sector or "",
        signal=signal or "AUTO_PROPOSAL",
        context=context or {},
    )


def enqueue_batch(proposals: Iterable[Proposal]) -> list[dict[str, Any]]:
    """Ajoute un lot de propositions à la file. Trois règles de dédup :

    1. **Pending actif** : si un ticker a déjà une proposition pending, on skip.
    2. **Cooldown anti-veto** (audit 2026-04-23) : si un ticker a été rejected
       récemment (< VETO_COOLDOWN_DAYS, default 7j), on skip aussi. Évite le
       harcèlement "auto_proposer repropose ce que l'utilisateur vient de veto".
       Env var VETO_COOLDOWN_DAYS=0 désactive le cooldown (comportement legacy).
    3. **Cooldown anti-churn WIN/LOSS** (audit 2026-04-23 LT) : si un ticker a
       été clôturé récemment (TP/SL/TS/time exit) < WIN_COOLDOWN_DAYS (default
       14j — horizon Long-Term), on skip. Évite le re-churn observé CTRA 22→23/04.
       Env var WIN_COOLDOWN_DAYS=0 désactive.

    Retourne les propositions effectivement insérées (sérialisées).
    """
    new_items = [asdict(p) for p in proposals]
    if not new_items:
        return []

    cooldown_days = _veto_cooldown_days()
    cooldown_cutoff_iso: str | None = None
    if cooldown_days > 0:
        cooldown_cutoff_iso = _iso(_now_utc() - timedelta(days=cooldown_days))

    win_cooldown_days = _win_cooldown_days()
    recent_closed: set[str] = set()
    if win_cooldown_days > 0:
        # Trade journal écrit en local naive (DATE_FMT), on compare en local naive.
        win_cutoff_local = datetime.now() - timedelta(days=win_cooldown_days)
        recent_closed = _recently_closed_tickers(win_cutoff_local)

    inserted: list[dict[str, Any]] = []
    PROPOSALS_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(PROPOSALS_LOCK_PATH), timeout=10):
        existing = _read_all_unlocked()
        pending_tickers = {
            p["ticker"] for p in existing if p.get("status") == "pending"
        }
        # Tickers vetoed récemment (fallback decided_at manquant → on ignore).
        recent_vetoes: set[str] = set()
        if cooldown_cutoff_iso is not None:
            recent_vetoes = {
                p["ticker"] for p in existing
                if p.get("status") == "rejected"
                and (p.get("decided_at") or "") >= cooldown_cutoff_iso
            }
        for item in new_items:
            t = item["ticker"]
            if t in pending_tickers:
                logger.info(
                    f"[Proposals] Skip {t} — proposition pending déjà en file"
                )
                continue
            if t in recent_vetoes:
                logger.info(
                    f"[Proposals] Skip {t} — vetoed récemment "
                    f"(cooldown {cooldown_days:g}j)"
                )
                continue
            if t in recent_closed:
                logger.info(
                    f"[Proposals] Skip {t} — clôturé récemment "
                    f"(WIN/LOSS cooldown {win_cooldown_days:g}j)"
                )
                continue
            existing.append(item)
            pending_tickers.add(t)
            inserted.append(item)
        if inserted:
            _write_all_unlocked(_trim_live_history(existing))
            for item in inserted:
                _append_audit("created", item)
    return inserted


def list_all(status: str | None = None) -> list[dict[str, Any]]:
    """Liste toutes les propositions, filtrées par statut si fourni.

    Effectue un sweep `expire_pending` à la lecture pour qu'une UI qui poll
    régulièrement voie les expirations sans dépendre d'un cron séparé.
    """
    expire_pending()  # prend son propre lock — non-réentrant
    with FileLock(str(PROPOSALS_LOCK_PATH), timeout=10):
        items = _read_all_unlocked()
    if status:
        items = [p for p in items if p.get("status") == status]
    items.sort(key=lambda p: p.get("created_at") or "", reverse=True)
    return items


def get(proposal_id: str) -> dict[str, Any] | None:
    """Récupère une proposition par ID (None si introuvable)."""
    with FileLock(str(PROPOSALS_LOCK_PATH), timeout=10):
        items = _read_all_unlocked()
    return next((p for p in items if p.get("id") == proposal_id), None)


def update_status(
    proposal_id: str,
    new_status: str,
    *,
    decided_by: str = "user",
    rejection_reason: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    """Transition de statut atomique. Lève ValueError si :
      - la proposition n'existe pas,
      - elle est déjà dans un statut terminal,
      - la transition demandée est invalide (ex: rejected → executed).

    Retourne la proposition mise à jour.
    """
    if new_status not in {"approved", "rejected", "expired", "executed"}:
        raise ValueError(f"Statut cible invalide: {new_status!r}")

    with FileLock(str(PROPOSALS_LOCK_PATH), timeout=10):
        items = _read_all_unlocked()
        idx = next(
            (i for i, p in enumerate(items) if p.get("id") == proposal_id), None
        )
        if idx is None:
            raise ValueError(f"Proposition introuvable: {proposal_id}")

        item = items[idx]
        current = item.get("status", "pending")

        # Transitions valides : pending → {approved, rejected, expired},
        # approved → executed (post-écriture journal).
        valid = {
            "pending":  {"approved", "rejected", "expired"},
            "approved": {"executed"},
        }
        if current in _TERMINAL_STATUSES:
            raise ValueError(
                f"Proposition {proposal_id} déjà terminée ({current})"
            )
        if new_status not in valid.get(current, set()):
            raise ValueError(
                f"Transition invalide: {current} → {new_status}"
            )

        item["status"] = new_status
        item["decided_at"] = _iso(_now_utc())
        item["decided_by"] = decided_by
        if rejection_reason is not None:
            item["rejection_reason"] = rejection_reason
        if order_id is not None:
            item["order_id"] = order_id

        items[idx] = item
        _write_all_unlocked(_trim_live_history(items))
        _append_audit(new_status, item, {"decided_by": decided_by})
        return item


def expire_pending() -> int:
    """Marque expired toutes les propositions pending dont expires_at < now.

    Retourne le nombre de propositions expirées. Idempotent — appelable à chaque
    lecture sans surcoût (no-op si rien à faire).
    """
    now_iso = _iso(_now_utc())
    expired_count = 0
    with FileLock(str(PROPOSALS_LOCK_PATH), timeout=10):
        items = _read_all_unlocked()
        changed = False
        for item in items:
            if item.get("status") != "pending":
                continue
            if (item.get("expires_at") or "") < now_iso:
                item["status"] = "expired"
                item["decided_at"] = now_iso
                item["decided_by"] = "system"
                expired_count += 1
                changed = True
                _append_audit("expired", item, {"decided_by": "system"})
        if changed:
            _write_all_unlocked(_trim_live_history(items))
    return expired_count


def expire_all_pending(reason: str = "user_regenerate") -> int:
    """Force-expire TOUTES les propositions pending (ignore `expires_at`).

    Utilisé par le flow "Régénérer un plan" côté UI — l'utilisateur veut
    changer ses paramètres (capital, mode, include_held) et repartir propre.

    On passe par le statut `expired` (pas `rejected`) pour éviter le cooldown
    veto 7 j qui skipperait les tickers à la prochaine génération. L'audit
    jsonl trace `expired` + reason pour que l'origine soit reconstruisible.
    """
    now_iso = _iso(_now_utc())
    expired_count = 0
    with FileLock(str(PROPOSALS_LOCK_PATH), timeout=10):
        items = _read_all_unlocked()
        changed = False
        for item in items:
            if item.get("status") != "pending":
                continue
            item["status"] = "expired"
            item["decided_at"] = now_iso
            item["decided_by"] = "system"
            item["rejection_reason"] = reason  # trace pour l'audit
            expired_count += 1
            changed = True
            _append_audit("expired", item,
                          {"decided_by": "system", "reason": reason})
        if changed:
            _write_all_unlocked(_trim_live_history(items))
    return expired_count
