"""Bear-market hedging via inverse-ETF (SH/SDS).

Audit 2026-05-12 — comblement d'un gap stratégique : en `BEAR_MARKET` /
`CRASH_PANIC`, le pipeline TITAN bloque toute nouvelle entrée LONG (cf.
`_gate_regime` dans auto_proposer.py + `regime_adjusted_risk` qui retourne 0
en CRASH_PANIC). Conséquence : le book reste long-only et descend avec
l'indice, sans aucun mécanisme défensif. Cet écart de protection est
visible dans les drawdowns historiques.

Stratégie :
  • Quand `confirmed_regime` ∈ {BEAR_MARKET, CRASH_PANIC} pendant ≥
    `BEAR_HEDGE_MIN_DAYS` (défaut 3 j), ouvrir une position LONG sur SH
    (ProShares Short S&P500 1×) à hauteur de `BEAR_HEDGE_PCT_OF_BOOK`
    (défaut 10 %) du capital total.
  • Quand `confirmed_regime` redevient BULL_MARKET pendant ≥ 3 j
    consécutifs, fermer la position de hedge.
  • Idempotent : ne ré-ouvre pas si déjà un hedge ouvert ; ne ferme pas si
    déjà fermé.
  • Pas de SL/TP — le hedge se vit par cycles de régime, pas par niveaux.

Activation :
  • Off par défaut (`BEAR_HEDGE_ENABLED=False` dans `config.py`).
  • Activer une fois testé en paper sur un cycle BEAR.

Outputs :
  • `data/bear_hedge_state.json` : dernier état (ticker hedge ouvert / fermé,
    timestamp, regime au moment de l'action).
  • Logs INFO/WARN.
  • Submit/close via `broker_gateway` (PaperBroker ou AlpacaBroker).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import config
from modules.log import logger

# ─────────────────────────────────────────────────────────────────
# CONSTANTES (overridables via config.py)
# ─────────────────────────────────────────────────────────────────
BEAR_HEDGE_ENABLED       = getattr(config, "BEAR_HEDGE_ENABLED", False)
BEAR_HEDGE_TICKER        = getattr(config, "BEAR_HEDGE_TICKER", "SH")  # 1× inverse SP500
BEAR_HEDGE_PCT_OF_BOOK   = float(getattr(config, "BEAR_HEDGE_PCT_OF_BOOK", 0.10))
BEAR_HEDGE_MIN_DAYS      = int(getattr(config, "BEAR_HEDGE_MIN_DAYS", 3))
BEAR_REGIMES             = ("BEAR_MARKET", "CRASH_PANIC")
BULL_REGIMES             = ("BULL_MARKET",)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATE_PATH = _PROJECT_ROOT / "data" / "bear_hedge_state.json"
_MACRO_PATH = _PROJECT_ROOT / "data" / "macro_state.json"


# ─────────────────────────────────────────────────────────────────
# DATACLASS — résultat décision
# ─────────────────────────────────────────────────────────────────
@dataclass
class HedgeDecision:
    action: str          # "OPEN" | "CLOSE" | "HOLD_HEDGE" | "HOLD_NO_HEDGE" | "SKIP"
    reason: str
    ticker: str | None = None
    size: int | None = None
    estimated_cost: float | None = None
    regime: str | None = None
    days_in_regime: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action":         self.action,
            "reason":         self.reason,
            "ticker":         self.ticker,
            "size":           self.size,
            "estimated_cost": self.estimated_cost,
            "regime":         self.regime,
            "days_in_regime": self.days_in_regime,
        }


# ─────────────────────────────────────────────────────────────────
# STATE IO
# ─────────────────────────────────────────────────────────────────
def _load_state() -> dict[str, Any]:
    """Lit `data/bear_hedge_state.json`. Fail-open : retourne {} si absent."""
    if not _STATE_PATH.exists():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[BearHedge] state read failed: {e}")
        return {}


def _save_state(state: dict[str, Any]) -> None:
    """Écrit l'état (atomique tmp+rename)."""
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(_STATE_PATH)
    except OSError as e:
        logger.warning(f"[BearHedge] state save failed: {e}")


def _load_macro() -> dict[str, Any]:
    if not _MACRO_PATH.exists():
        return {}
    try:
        return json.loads(_MACRO_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[BearHedge] macro_state read failed: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────
# DÉCISION
# ─────────────────────────────────────────────────────────────────
def decide(
    macro: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    today: date | None = None,
) -> HedgeDecision:
    """Décide si on doit ouvrir/fermer un hedge bear. Pure (pas d'IO broker).

    Args:
        macro : output de `_load_macro` (or injecté pour les tests).
        state : output de `_load_state` (or injecté).
        today : date de référence (défaut today, utile pour tests).

    Returns:
        HedgeDecision — action peut être :
          OPEN          → ouvrir hedge (régime BEAR persistant + pas déjà ouvert)
          CLOSE         → fermer hedge (régime redevenu BULL persistant)
          HOLD_HEDGE    → hedge ouvert, régime toujours BEAR → ne rien faire
          HOLD_NO_HEDGE → pas de hedge, régime pas encore confirmé BEAR
          SKIP          → désactivé / inputs invalides
    """
    if not BEAR_HEDGE_ENABLED:
        return HedgeDecision(action="SKIP", reason="BEAR_HEDGE_ENABLED=False")
    macro = macro if macro is not None else _load_macro()
    state = state if state is not None else _load_state()
    today = today or date.today()

    regime = macro.get("confirmed_regime")
    candidate_since_str = macro.get("candidate_since")
    if not regime or not candidate_since_str:
        return HedgeDecision(
            action="SKIP",
            reason="macro_state incomplete (regime/candidate_since absent)",
        )

    # Days in confirmed regime — basé sur candidate_since (date à laquelle le
    # régime courant a été détecté pour la 1ère fois ; la confirmation N-jours
    # est faite par macro_engine en amont).
    try:
        candidate_since = date.fromisoformat(str(candidate_since_str)[:10])
    except ValueError:
        return HedgeDecision(
            action="SKIP", reason=f"candidate_since unparseable: {candidate_since_str}",
        )
    days_in_regime = (today - candidate_since).days

    has_hedge = bool(state.get("hedge_open"))
    hedge_ticker = state.get("hedge_ticker") or BEAR_HEDGE_TICKER

    if regime in BEAR_REGIMES:
        if has_hedge:
            return HedgeDecision(
                action="HOLD_HEDGE",
                reason=f"hedge {hedge_ticker} déjà ouvert, régime {regime} confirmé",
                ticker=hedge_ticker, regime=regime, days_in_regime=days_in_regime,
            )
        if days_in_regime < BEAR_HEDGE_MIN_DAYS:
            return HedgeDecision(
                action="HOLD_NO_HEDGE",
                reason=(
                    f"régime {regime} depuis {days_in_regime}j "
                    f"< min {BEAR_HEDGE_MIN_DAYS}j (anti-whipsaw)"
                ),
                regime=regime, days_in_regime=days_in_regime,
            )
        return HedgeDecision(
            action="OPEN",
            reason=(
                f"régime {regime} confirmé depuis {days_in_regime}j ≥ "
                f"{BEAR_HEDGE_MIN_DAYS}j — ouvrir hedge {BEAR_HEDGE_TICKER} "
                f"{BEAR_HEDGE_PCT_OF_BOOK:.0%}"
            ),
            ticker=BEAR_HEDGE_TICKER,
            regime=regime, days_in_regime=days_in_regime,
        )

    if regime in BULL_REGIMES:
        if not has_hedge:
            return HedgeDecision(
                action="HOLD_NO_HEDGE",
                reason=f"régime {regime}, pas de hedge ouvert",
                regime=regime, days_in_regime=days_in_regime,
            )
        if days_in_regime < BEAR_HEDGE_MIN_DAYS:
            return HedgeDecision(
                action="HOLD_HEDGE",
                reason=(
                    f"BULL_MARKET depuis {days_in_regime}j < min "
                    f"{BEAR_HEDGE_MIN_DAYS}j — attendre confirmation avant unwind"
                ),
                ticker=hedge_ticker, regime=regime, days_in_regime=days_in_regime,
            )
        return HedgeDecision(
            action="CLOSE",
            reason=(
                f"BULL_MARKET confirmé depuis {days_in_regime}j ≥ "
                f"{BEAR_HEDGE_MIN_DAYS}j — fermer hedge {hedge_ticker}"
            ),
            ticker=hedge_ticker,
            regime=regime, days_in_regime=days_in_regime,
        )

    # Régime inconnu (ex: CRASH_PANIC déjà géré, ou nouveau régime ajouté)
    return HedgeDecision(
        action="SKIP",
        reason=f"régime {regime} non géré",
        regime=regime, days_in_regime=days_in_regime,
    )


# ─────────────────────────────────────────────────────────────────
# EXÉCUTION (broker)
# ─────────────────────────────────────────────────────────────────
def _current_book_size() -> float:
    """Estime le capital total via broker_gateway (Alpaca) ou ACCOUNT_SIZE."""
    try:
        from modules.broker_gateway import get_broker
        eq = float(get_broker().get_account_equity())
        if eq > 0:
            return eq
    except Exception as e:
        logger.warning(f"[BearHedge] broker equity unavailable: {e} — fallback ACCOUNT_SIZE")
    return float(getattr(config, "ACCOUNT_SIZE", 100_000))


def _fetch_hedge_price(ticker: str) -> float | None:
    """Récupère le prix courant du ticker hedge (yfinance fallback)."""
    try:
        from modules.tracker.market import get_current_price
        return get_current_price(ticker)
    except Exception as e:
        logger.warning(f"[BearHedge] get_current_price({ticker}) failed: {e}")
        return None


def execute_decision(decision: HedgeDecision) -> dict[str, Any]:
    """Exécute la décision via broker. Met à jour `bear_hedge_state.json`.

    Side effects :
      • Submit/close ordre via broker_gateway.
      • Write `data/bear_hedge_state.json`.
      • Log INFO/WARN.

    Returns :
      Dict avec `success`, `decision`, `order_id` (si OPEN/CLOSE), `error`.
    """
    out: dict[str, Any] = {
        "success": False,
        "decision": decision.to_dict(),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    if decision.action in ("SKIP", "HOLD_HEDGE", "HOLD_NO_HEDGE"):
        out["success"] = True
        out["noop"] = True
        return out

    if decision.action == "OPEN":
        ticker = decision.ticker or BEAR_HEDGE_TICKER
        book_size = _current_book_size()
        hedge_budget = book_size * BEAR_HEDGE_PCT_OF_BOOK
        price = _fetch_hedge_price(ticker)
        if not price or price <= 0:
            logger.warning(f"[BearHedge] OPEN abort — prix {ticker} indisponible")
            out["error"] = f"price_unavailable_{ticker}"
            return out
        size = max(1, int(hedge_budget / price))
        try:
            from modules.broker_gateway import get_broker
            scan = SimpleNamespace(
                ticker=ticker,
                direction="LONG",
                price=price,
                stop_loss=float("nan"),    # pas de SL : géré par cycle régime
                take_profit=float("nan"),  # pas de TP non plus
                position_size=size,
                rr_ratio=float("nan"),
                signal="BEAR_HEDGE",
                sector_etf=ticker,
            )
            result = get_broker().submit_order(scan)
            if result.success:
                state = {
                    "hedge_open":      True,
                    "hedge_ticker":    ticker,
                    "hedge_size":      size,
                    "hedge_price":     price,
                    "hedge_amount_usd": round(size * price, 2),
                    "opened_at":       datetime.now().isoformat(timespec="seconds"),
                    "regime_at_open":  decision.regime,
                    "order_id":        result.order_id,
                }
                _save_state(state)
                logger.info(
                    f"[BearHedge] OPEN {ticker} ×{size} @ ${price:.2f} "
                    f"(book ${book_size:,.0f} × {BEAR_HEDGE_PCT_OF_BOOK:.0%})"
                )
                out["success"] = True
                out["order_id"] = result.order_id
                out["size"] = size
            else:
                logger.warning(f"[BearHedge] OPEN broker rejected: {result.message}")
                out["error"] = result.message
        except Exception as e:
            logger.exception(f"[BearHedge] OPEN failed: {e}")
            out["error"] = str(e)[:120]
        return out

    if decision.action == "CLOSE":
        state = _load_state()
        ticker = state.get("hedge_ticker") or decision.ticker or BEAR_HEDGE_TICKER
        try:
            price = _fetch_hedge_price(ticker)
            if not price or price <= 0:
                logger.warning(f"[BearHedge] CLOSE — prix {ticker} indisponible, on tente quand-même")
                price = float(state.get("hedge_price") or 0) or 1.0
            from modules.broker_gateway import get_broker
            ok = get_broker().close_position(
                ticker, price, status="WIN", direction="LONG",
            )
            if ok:
                state = {
                    **state,
                    "hedge_open":    False,
                    "closed_at":     datetime.now().isoformat(timespec="seconds"),
                    "close_price":   price,
                    "regime_at_close": decision.regime,
                }
                _save_state(state)
                logger.info(f"[BearHedge] CLOSE {ticker} @ ${price:.2f}")
                out["success"] = True
            else:
                logger.warning(f"[BearHedge] CLOSE broker returned False for {ticker}")
                out["error"] = "broker_close_returned_false"
        except Exception as e:
            logger.exception(f"[BearHedge] CLOSE failed: {e}")
            out["error"] = str(e)[:120]
        return out

    out["error"] = f"unknown action {decision.action}"
    return out


# ─────────────────────────────────────────────────────────────────
# CLI : invoqué depuis run_titan.sh ou cron dédié
# ─────────────────────────────────────────────────────────────────
def run_once() -> dict[str, Any]:
    """Une passe complète : decide + execute. Retourne le résultat à logger."""
    decision = decide()
    return execute_decision(decision)


if __name__ == "__main__":
    import sys
    result = run_once()
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result.get("success") else 1)
