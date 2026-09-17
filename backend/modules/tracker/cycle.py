"""Orchestrateur — un cycle complet de tracker.

Pipeline :
  1. Vérif blocage (killswitch posé au cycle précédent → gel des entrées,
     mais les positions OPEN restent surveillées : audit 2026-09-17)
  2. Sync fills Alpaca (mode alpaca uniquement)
  2b. Ré-armement des stops manquants chez le broker (audit 2026-09-17, P0-1)
  3. Lecture du journal
  4. Early-return si aucune position OPEN (heartbeat=idle)
  5. Vérif Daily Drawdown — killswitch (freeze | liquidate selon config)
  6. Circuit Breaker Drawdown Progressif (échantillonné 1×/jour)
  7. Évaluation des positions OPEN (SL/TP/trailing/time exit)
  8. Sauvegarde fusionnée si ≥ 1 trade modifié
  9. Snapshot enrichi du portefeuille
 10. Heartbeat (signal de vie pour /api/tracker_health)
"""
from __future__ import annotations

from datetime import date

import config
from modules.risk import DrawdownCircuitBreaker

from .evaluation import evaluate_trades, load_journal, save_journal
from .killswitch import (
    check_daily_drawdown,
    emergency_liquidate_all,
    estimate_portfolio_equity,
    freeze_new_entries,
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


def _alpaca_broker():
    """Retourne l'AlpacaBroker actif, ou None (mode paper / erreur)."""
    if getattr(config, "BROKER_MODE", "paper").lower() != "alpaca":
        return None
    try:
        from modules.broker_gateway import AlpacaBroker, get_broker
        broker = get_broker()
        return broker if isinstance(broker, AlpacaBroker) else None
    except Exception as exc:
        logger.warning(f"[BrokerGateway] init Alpaca échouée : {exc}")
        return None


def _rearm_protective_stops(broker, df):
    """Pose un stop GTC chez le broker pour toute position OPEN qui n'en a pas.

    Retourne le DataFrame (SL fallback persisté si le journal n'en avait pas)
    et le nombre de stops armés.
    """
    open_rows = df[df["Status"] == "OPEN"]
    if open_rows.empty:
        return df, 0
    try:
        actions = broker.ensure_protective_stops(open_rows.to_dict("records"))
    except Exception as exc:
        logger.warning(f"[StopGuard] ensure_protective_stops : {exc}")
        return df, 0
    armed = 0
    for a in actions:
        if not a.get("ok"):
            logger.error(f"[StopGuard] ❌ {a.get('ticker')} : stop NON armé @ {a.get('stop_price')}")
            continue
        armed += 1
        if a.get("fallback"):
            mask = (df["Status"] == "OPEN") & (df["Ticker"].astype(str).str.upper() == a["ticker"])
            for idx in df[mask].index:
                df.at[idx, "Stop_Loss"] = round(float(a["stop_price"]), 4)
                if not str(df.at[idx, "Initial_SL"] or "").strip() or str(df.at[idx, "Initial_SL"]) == "nan":
                    df.at[idx, "Initial_SL"] = round(float(a["stop_price"]), 4)
        try:
            from modules.alerter import _send_telegram_message
            _send_telegram_message(
                f"🛡️ <b>Stop ré-armé</b> — {a['ticker']} @ {float(a['stop_price']):.2f}"
                + (f" (TP {float(a['take_profit']):.2f}, OCO)" if a.get("take_profit") else "")
                + ("\n⚠️ Journal sans SL : plancher catastrophe −35 % appliqué." if a.get("fallback") else "")
            )
        except Exception as exc:
            logger.debug(f"[StopGuard] Telegram : {exc}")
    if armed:
        logger.warning(f"[StopGuard] {armed} stop(s) ré-armé(s) chez le broker.")
    return df, armed


def _update_circuit_breaker(current_equity: float) -> float:
    """Circuit breaker progressif, échantillonné 1×/jour.

    Audit 2026-09-17 (P1-6) : le tracker est un process one-shot (cron 2 min)
    → `cb.update()` n'était jamais appelé et l'historique restait à 1 lecture
    (`n_history=1` à chaque cycle). On restaure l'état persisté, on pousse
    une lecture par jour calendaire (fenêtre 60 séances) et on persiste.
    """
    today = date.today().isoformat()
    cb = get_circuit_breaker()
    if cb is None:
        saved = read_circuit_breaker_state()
        hist = saved.get("equity_history") or []
        window = int(getattr(config, "CB_ROLLING_WINDOW_DAYS", 60) or 60)
        peak = max(float(saved.get("peak_equity", 0) or 0), current_equity)
        cb = DrawdownCircuitBreaker(
            peak_equity=peak,
            pause_days=int(getattr(config, "CB_PAUSE_DAYS", 5)),
            rolling_window=window,
            equity_history=hist if hist else None,
            dd_reduce_75_pct=float(getattr(config, "CB_DD_REDUCE_75_PCT", -8.0)),
            dd_reduce_50_pct=float(getattr(config, "CB_DD_REDUCE_50_PCT", -12.0)),
            dd_pause_pct=float(getattr(config, "CB_DD_PAUSE_PCT", -16.0)),
        )
        cb._pause_remaining = int(saved.get("pause_remaining", 0) or 0)
        cb.last_reading_date = saved.get("last_reading_date")  # type: ignore[attr-defined]
        set_circuit_breaker(cb)

    last = getattr(cb, "last_reading_date", None)
    if last != today:
        cb.update(current_equity)
        cb.last_reading_date = today  # type: ignore[attr-defined]
        logger.info(
            f"[CircuitBreaker] Lecture journalière poussée : ${current_equity:,.2f} "
            f"(peak rolling ${cb.peak_equity:,.2f}, n_history={len(cb._equity_history)})"
        )

    cb_multiplier = cb.get_size_multiplier(current_equity)
    if cb.is_paused():
        logger.warning(
            f"[CircuitBreaker] PAUSE ACTIVE — drawdown ≤ {cb.dd_pause_pct:.0f}% vs peak. "
            f"Nouvelles entrées bloquées ({cb._pause_remaining} séance(s) restante(s))."
        )
    elif cb_multiplier < 1.0:
        logger.warning(
            f"[CircuitBreaker] Drawdown détecté — taille des nouvelles positions réduite "
            f"à {cb_multiplier*100:.0f}% de la normale."
        )
    save_cb_state(cb, cb_multiplier)
    return cb_multiplier


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

    # ── Garde 1 : blocage des nouvelles entrées ──────────────────
    # Audit 2026-09-17 : un killswitch actif ne doit PAS rendre le tracker
    # aveugle — les positions OPEN continuent d'être surveillées (stops,
    # trailing, time-exit). Seules les nouvelles entrées sont gelées
    # (le proposer/auto-approve lisent trading_state.json).
    entries_blocked = not is_trading_allowed()
    if entries_blocked:
        logger.warning(
            "[Killswitch] TRADING_BLOCKED = True — nouvelles entrées gelées ; "
            "surveillance des positions OPEN maintenue."
        )

    broker = _alpaca_broker()

    # ── Sync Alpaca fills ─────────────────────────────────────────
    if broker is not None:
        try:
            synced = broker.sync_fills_from_alpaca()
            if synced > 0:
                logger.info(f"[AlpacaSync] {synced} ligne(s) réconciliée(s) avec Alpaca")
        except Exception as exc:
            logger.warning(f"[AlpacaSync] Erreur sync : {exc}")

    df = load_journal()

    # ── Garde stops : chaque position OPEN doit avoir un stop broker ──
    if broker is not None:
        df, armed = _rearm_protective_stops(broker, df)
        if armed:
            save_journal(df)

    # Early-return si aucune position OPEN — snapshot mis à jour quand-même.
    if (df["Status"] == "OPEN").sum() == 0:
        logger.info("Aucune position OPEN. Fin du cycle.")
        try:
            save_equity_snapshot(df)
        except Exception as exc:
            logger.warning(f"[Snapshot] Erreur mise à jour (no-open): {exc}")
        write_heartbeat(cycle_status="blocked" if entries_blocked else "idle")
        return

    # ── Garde 2 : Daily Drawdown Killswitch ─────────────────────
    if not entries_blocked and check_daily_drawdown(df):
        action = str(getattr(config, "KILLSWITCH_ACTION", "freeze")).lower()
        if action == "liquidate":
            df = emergency_liquidate_all(df)
            save_journal(df)
            logger.critical(
                "[NUCLEAR STOP] Journal sauvegardé après liquidation d'urgence. "
                "Trading suspendu jusqu'à demain."
            )
            write_heartbeat(cycle_status="nuclear_stop")
            return
        freeze_new_entries(df)
        entries_blocked = True

    # ── Circuit Breaker Drawdown Progressif ──────────────────────
    current_equity = estimate_portfolio_equity(df)
    _update_circuit_breaker(current_equity)

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

    write_heartbeat(cycle_status="blocked" if entries_blocked else "ok")
