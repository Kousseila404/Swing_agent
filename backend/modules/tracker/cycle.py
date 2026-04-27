"""Orchestrateur — un cycle complet de tracker.

Pipeline :
  1. Vérif blocage (Nuclear Killswitch posé au cycle précédent)
  2. Sync fills Alpaca (mode alpaca uniquement)
  3. Lecture du journal
  4. Early-return si aucune position OPEN (heartbeat=idle)
  5. Vérif Daily Drawdown — trigger killswitch si ≥ MAX_DAILY_DRAWDOWN_PCT
  6. Circuit Breaker Drawdown Progressif (singleton cross-cycle)
  7. Évaluation des positions OPEN (SL/TP/trailing/time exit)
  8. Sauvegarde si ≥ 1 trade modifié
  9. Snapshot enrichi du portefeuille
 10. Heartbeat (signal de vie pour /api/tracker_health)
"""
from __future__ import annotations

import config
from modules.risk import DrawdownCircuitBreaker

from .evaluation import evaluate_trades, load_journal, save_journal
from .killswitch import (
    check_daily_drawdown,
    emergency_liquidate_all,
    estimate_portfolio_equity,
    is_trading_allowed,
    save_equity_snapshot,
)
from .state import (
    get_circuit_breaker,
    logger,
    read_circuit_breaker_state,
    save_cb_state,
    set_circuit_breaker,
    write_heartbeat,
)


def run_cycle() -> None:
    """Un cycle complet de surveillance + clôture automatique."""
    separator = "─" * 60
    logger.info(separator)
    logger.info("TRACKER V2 — Début du cycle")
    try:
        from modules.broker_gateway import get_broker
        logger.info(f"[BrokerGateway] Mode : {get_broker().name}")
    except Exception:
        logger.info("[BrokerGateway] Mode : PaperBroker (CSV) — défaut")
    logger.info(separator)

    # ── Garde 1 : Vérification du blocage ───────────────────────
    if not is_trading_allowed():
        logger.warning(
            "[Killswitch] TRADING_BLOCKED = True. "
            "Cycle annulé jusqu'à la levée du blocage (demain 00h00)."
        )
        write_heartbeat(cycle_status="blocked")
        return

    # ── Sync Alpaca fills (si mode alpaca actif) ─────────────────
    if getattr(config, "BROKER_MODE", "paper").lower() == "alpaca":
        try:
            from modules.broker_gateway import AlpacaBroker, get_broker
            broker = get_broker()
            if isinstance(broker, AlpacaBroker):
                synced = broker.sync_fills_from_alpaca()
                if synced > 0:
                    logger.info(
                        f"[AlpacaSync] {synced} position(s) fermée(s) par Alpaca synchronisée(s)"
                    )
        except Exception as exc:
            logger.warning(f"[AlpacaSync] Erreur sync : {exc}")

    df = load_journal()

    # Early-return si aucune position OPEN — snapshot mis à jour quand-même.
    if (df["Status"] == "OPEN").sum() == 0:
        logger.info("Aucune position OPEN. Fin du cycle.")
        try:
            save_equity_snapshot(df)
        except Exception as exc:
            logger.warning(f"[Snapshot] Erreur mise à jour (no-open): {exc}")
        write_heartbeat(cycle_status="idle")
        return

    # ── Garde 2 : Daily Drawdown Killswitch ─────────────────────
    if check_daily_drawdown(df):
        df = emergency_liquidate_all(df)
        save_journal(df)
        logger.critical(
            "[NUCLEAR STOP] Journal sauvegardé après liquidation d'urgence. "
            "Trading suspendu jusqu'à demain."
        )
        write_heartbeat(cycle_status="nuclear_stop")
        return

    # ── Circuit Breaker Drawdown Progressif (singleton cross-cycle) ──
    current_equity = estimate_portfolio_equity(df)
    cb = get_circuit_breaker()
    if cb is None:
        _cb_saved = read_circuit_breaker_state()
        _peak = max(float(_cb_saved.get("peak_equity", 0) or 0), current_equity)
        cb = DrawdownCircuitBreaker(peak_equity=_peak)
        cb._pause_remaining = int(_cb_saved.get("pause_remaining", 0) or 0)
        set_circuit_breaker(cb)
        logger.info(
            f"[CircuitBreaker] Initialisé — Peak equity : ${_peak:,.2f}"
            + (f" | Pause restante : {cb._pause_remaining} cycle(s)" if cb._pause_remaining else "")
        )
    else:
        cb.update(current_equity)

    cb_multiplier = cb.get_size_multiplier(current_equity)
    if cb.is_paused():
        logger.warning(
            f"[CircuitBreaker] PAUSE ACTIVE — Drawdown > 4% vs peak. "
            f"Nouvelles entrées bloquées ({cb._pause_remaining} cycle(s) restant(s))."
        )
    elif cb_multiplier < 1.0:
        logger.warning(
            f"[CircuitBreaker] Drawdown détecté — Taille des nouvelles positions réduite "
            f"à {cb_multiplier*100:.0f}% de la normale."
        )

    # Persistance cross-process : scanner et main.py lisent cet état
    save_cb_state(cb, cb_multiplier)

    df, closed, modified = evaluate_trades(df)

    if closed > 0 or modified > 0:
        save_journal(df)
        if closed > 0:
            logger.info(f"Cycle terminé — {closed} trade(s) clôturé(s).")
        if modified > 0:
            logger.info(f"Cycle terminé — {modified} trade(s) modifié(s) (trailing stop / alerte).")
    else:
        logger.info("Cycle terminé — Aucun trade modifié ce cycle.")

    # Snapshot enrichi (PnL latent live) pour le dashboard
    try:
        save_equity_snapshot(df)
    except Exception as exc:
        logger.warning(f"[Snapshot] Erreur mise à jour: {exc}")

    write_heartbeat(cycle_status="ok")
