"""Nuclear Killswitch & snapshot enrichi du portefeuille.

Flux killswitch :
  1. check_daily_drawdown : compare current equity vs starting equity du jour.
  2. Si drawdown ≥ MAX_DAILY_DRAWDOWN_PCT → emergency_liquidate_all ferme tout.
  3. _set_trading_blocked(True) persiste le blocage jusqu'au lendemain 00h.

Snapshot : save_equity_snapshot est appelé en fin de cycle même sans trade
clôturé, pour que le dashboard ait toujours une valorisation fraîche.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime

import pandas as pd

import config
from modules.risk import calculate_drawdown

from .market import get_current_price
from .state import (
    DATE_FMT,
    EQUITY_STATE_PATH,
    TRADING_STATE_PATH,
    load_equity_state,
    logger,
    save_equity_state,
)


def get_starting_equity(current_equity: float) -> float:
    """Retourne le STARTING_EQUITY de la journée en cours.

    Si c'est un nouveau jour (ou premier démarrage), enregistre `current_equity`
    comme référence de la journée et le persiste dans equity_state.json.
    """
    state = load_equity_state()
    today_str = date.today().isoformat()

    if state.get("date") == today_str and state.get("starting_equity", 0) > 0:
        return float(state["starting_equity"])

    logger.info(
        f"[Killswitch] Nouveau jour ({today_str}). "
        f"Reset STARTING_EQUITY → ${current_equity:,.2f}"
    )
    save_equity_state(current_equity)
    return current_equity


def is_trading_allowed() -> bool:
    """Vérifie si le trading est autorisé.

    Le blocage est automatiquement levé si :
      1. La date stockée dans trading_state.json est antérieure à aujourd'hui ;
      2. ET (Phase 2 audit — hysteresis) l'equity courante est revenue à ≥
         `KILLSWITCH_RELIFT_RECOVERY_PCT` de l'equity peak (défaut 96 %, soit
         ≤ 4 % drawdown vs peak). Si on n'a pas encore récupéré, on garde le
         killswitch fermé pour un jour de plus — sinon le filet auto-relevait
         minuit suivant un crash sans confirmer la reprise.

    En cas d'erreur de lecture de l'equity (broker down, fichier manquant), on
    relève quand même le killswitch (fail-open : ne pas bloquer indéfiniment
    pour un problème d'observabilité).
    """
    if not TRADING_STATE_PATH.exists():
        return True
    try:
        with open(TRADING_STATE_PATH, encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception:
        return True

    if not state.get("blocked", False):
        return True

    blocked_date = state.get("date", "")
    if blocked_date == date.today().isoformat():
        return False

    # Nouveau jour : appliquer l'hysteresis avant de relever.
    if not _has_equity_recovered(state):
        logger.warning(
            "[Killswitch] Nouveau jour mais drawdown vs peak NON résorbé — "
            "blocage maintenu 1 jour de plus (hysteresis)."
        )
        # Roll la date à aujourd'hui pour ne pas re-évaluer chaque cycle ; le
        # blocage tiendra jusqu'à demain prochain test.
        _set_trading_blocked(True)
        return False

    logger.info(
        "[Killswitch] Nouveau jour + equity recovery confirmé — blocage levé."
    )
    _set_trading_blocked(False)
    return True


def _has_equity_recovered(state: dict) -> bool:
    """Hysteresis : equity courante ≥ recovery_pct × peak avant de relever.

    Sources de référence (par ordre de priorité) :
      1. `state["peak_equity"]` posé au moment du nuclear stop.
      2. circuit_breaker_state.json `peak_equity` (si dispo).
      3. equity_state.json `starting_equity` (fallback rough).

    Returns True si reprise OK, False si encore en drawdown profond. Fail-open
    si on ne peut pas lire l'équity courante (broker down, etc.).
    """
    recovery_pct = float(getattr(config, "KILLSWITCH_RELIFT_RECOVERY_PCT", 0.96))
    peak = 0.0
    try:
        peak = float(state.get("peak_equity", 0) or 0)
    except (TypeError, ValueError):
        peak = 0.0
    if peak <= 0:
        try:
            from .state import CB_STATE_PATH
            if CB_STATE_PATH.exists():
                with open(CB_STATE_PATH, encoding="utf-8") as fh:
                    cb = json.load(fh)
                peak = float(cb.get("peak_equity", 0) or 0)
        except Exception:
            pass
    if peak <= 0:
        # Pas de référence fiable → fail-open.
        return True

    # Lit l'equity courante via broker (Alpaca) ou fallback ACCOUNT_SIZE.
    try:
        broker_mode = str(getattr(config, "BROKER_MODE", "paper")).lower().strip()
        if broker_mode == "alpaca":
            from modules.broker_gateway import get_broker
            current = float(get_broker().get_account_equity())
        else:
            current = float(getattr(config, "ACCOUNT_SIZE", 100_000))
    except Exception as exc:
        logger.warning(
            f"[Killswitch] hysteresis : equity courante illisible ({exc}) → "
            f"fail-open (relèvement permis)."
        )
        return True

    return current >= recovery_pct * peak


def _set_trading_blocked(blocked: bool, *, peak_equity: float | None = None) -> None:
    """Écrit l'état de blocage dans trading_state.json.

    Phase 2 audit — ajoute optionnellement `peak_equity` au moment du nuclear
    stop pour permettre l'hysteresis à `is_trading_allowed()` (refus de
    relever tant que l'equity courante < 96 % du peak).
    """
    TRADING_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state: dict = {
        "blocked": blocked,
        "date": date.today().isoformat(),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    # Préserve le peak existant (si re-écriture sans nouveau peak fourni) pour
    # ne pas perdre la référence d'hysteresis sur des appels successifs.
    if peak_equity is not None and peak_equity > 0:
        state["peak_equity"] = float(peak_equity)
    elif TRADING_STATE_PATH.exists():
        try:
            with open(TRADING_STATE_PATH, encoding="utf-8") as fh:
                old = json.load(fh)
            if isinstance(old.get("peak_equity"), (int, float)):
                state["peak_equity"] = float(old["peak_equity"])
        except Exception:
            pass
    tmp = TRADING_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(TRADING_STATE_PATH)


def estimate_portfolio_equity(df: pd.DataFrame) -> float:
    """Estime l'équité courante.

    Audit S2.4 (2026-04-27) — en mode `BROKER_MODE=alpaca`, l'equity réelle vit
    chez le broker (cash drag, dividends, fees, frais d'emprunt). Le killswitch
    -4 % daily se déclenchait sur l'equity *paper* (CSV) qui ignore tous ces
    frottements → décisions désynchronisées vs réalité comptable du compte.

    En mode alpaca on délègue à `get_broker().get_account_equity()` (fail-open
    sur le calcul CSV si l'API broker tombe — ne jamais bloquer le killswitch
    sur un problème réseau).
    """
    broker_mode = str(getattr(config, "BROKER_MODE", "paper")).lower().strip()
    if broker_mode == "alpaca":
        try:
            from modules.broker_gateway import get_broker
            live_equity = float(get_broker().get_account_equity())
            if live_equity > 0:
                return live_equity
        except Exception as exc:
            logger.warning(
                f"[Killswitch] broker.get_account_equity échec → "
                f"fallback CSV : {exc}"
            )

    base_equity = float(getattr(config, "ACCOUNT_SIZE", 100_000))

    realized_pnl = 0.0
    closed_trades = df[df["Status"].isin(["WIN", "LOSS", "EMERGENCY_CLOSED"])]
    for _, row in closed_trades.iterrows():
        try:
            entry   = float(row["Entry"])
            exit_px = row.get("Exit_Price", "")
            if exit_px == "" or pd.isna(exit_px):
                continue
            exit_px   = float(exit_px)
            size      = float(row.get("Size", 1) or 1)
            direction = str(row.get("Direction", "LONG")).strip().upper()
            if direction == "SHORT":
                realized_pnl += (entry - exit_px) * size
            else:
                realized_pnl += (exit_px - entry) * size
        except (ValueError, TypeError):
            continue

    unrealized_pnl = 0.0
    open_trades = df[df["Status"] == "OPEN"]
    for _, row in open_trades.iterrows():
        try:
            entry       = float(row["Entry"])
            size        = float(row.get("Size", 1) or 1)
            direction   = str(row.get("Direction", "LONG")).strip().upper()
            exit_px_raw = row.get("Exit_Price", "")
            if exit_px_raw == "" or pd.isna(exit_px_raw):
                continue
            current_px = float(exit_px_raw)
            if direction == "SHORT":
                unrealized_pnl += (entry - current_px) * size
            else:
                unrealized_pnl += (current_px - entry) * size
        except (ValueError, TypeError):
            continue

    return base_equity + realized_pnl + unrealized_pnl


def save_equity_snapshot(df: pd.DataFrame) -> None:
    """Persiste un snapshot enrichi du portefeuille dans equity_state.json.

    Inclut realized_pnl, unrealized_pnl, current_equity, et la liste détaillée
    des positions OPEN (prix courant live via get_current_price).
    """
    base_equity = float(getattr(config, "ACCOUNT_SIZE", 100_000))

    realized_pnl = 0.0
    closed = df[df["Status"].isin(["WIN", "LOSS", "EMERGENCY_CLOSED"])]
    for _, row in closed.iterrows():
        try:
            entry   = float(row["Entry"])
            exit_px = row.get("Exit_Price", "")
            if exit_px == "" or pd.isna(exit_px):
                continue
            exit_px   = float(exit_px)
            size      = float(row.get("Size", 1) or 1)
            direction = str(row.get("Direction", "LONG")).strip().upper()
            if direction == "SHORT":
                realized_pnl += (entry - exit_px) * size
            else:
                realized_pnl += (exit_px - entry) * size
        except (ValueError, TypeError):
            continue

    unrealized_pnl  = 0.0
    open_positions  = []
    open_trades     = df[df["Status"] == "OPEN"]
    seen_tickers: set[str] = set()

    for _, row in open_trades.iterrows():
        try:
            ticker = str(row["Ticker"]).strip().upper()
            if ticker in seen_tickers:
                continue
            seen_tickers.add(ticker)

            entry      = float(row["Entry"])
            size       = float(row.get("Size", 1) or 1)
            direction  = str(row.get("Direction", "LONG")).strip().upper()
            entry_date = str(row.get("Date", ""))

            try:
                sl = float(row.get("Stop_Loss") or "nan")
            except ValueError:
                sl = float("nan")
            try:
                tp = float(row.get("Take_Profit") or "nan")
            except ValueError:
                tp = float("nan")

            current_px = get_current_price(ticker)
            if current_px is None:
                continue

            if direction == "SHORT":
                pnl = (entry - current_px) * size
            else:
                pnl = (current_px - entry) * size

            unrealized_pnl += pnl
            pct_from_entry  = (current_px - entry) / entry * 100 if entry != 0 else 0.0
            pct_to_sl = abs((current_px - sl) / current_px * 100) if not math.isnan(sl) else None
            pct_to_tp = abs((tp - current_px) / current_px * 100) if not math.isnan(tp) else None

            open_positions.append({
                "ticker":         ticker,
                "direction":      direction,
                "entry":          round(entry, 4),
                "current_price":  round(current_px, 4),
                "size":           int(size),
                "unrealized_pnl": round(pnl, 2),
                "pct_from_entry": round(pct_from_entry, 2),
                "stop_loss":      round(sl, 4) if not math.isnan(sl) else None,
                "take_profit":    round(tp, 4) if not math.isnan(tp) else None,
                "pct_to_sl":      round(pct_to_sl, 2) if pct_to_sl is not None else None,
                "pct_to_tp":      round(pct_to_tp, 2) if pct_to_tp is not None else None,
                "entry_date":     entry_date,
            })
        except (ValueError, TypeError) as exc:
            logger.debug(f"[Snapshot] Erreur position {row.get('Ticker', '?')}: {exc}")
            continue

    current_equity  = base_equity + realized_pnl + unrealized_pnl

    # Ne réinitialise pas starting_equity — get_starting_equity() s'en charge
    # depuis check_daily_drawdown si c'est un nouveau jour.
    existing_state  = load_equity_state()
    starting_equity = float(existing_state.get("starting_equity", base_equity))

    state = {
        "starting_equity": starting_equity,
        "current_equity":  round(current_equity, 2),
        "realized_pnl":    round(realized_pnl, 2),
        "unrealized_pnl":  round(unrealized_pnl, 2),
        "date":            date.today().isoformat(),
        "last_update":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "open_positions":  open_positions,
    }

    EQUITY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EQUITY_STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)

    logger.info(
        f"[Snapshot] Capital: ${current_equity:,.2f} | "
        f"Réalisé: ${realized_pnl:+,.2f} | "
        f"Latent: ${unrealized_pnl:+,.2f} ({len(open_positions)} pos. live)"
    )


def emergency_liquidate_all(df: pd.DataFrame) -> pd.DataFrame:
    """NUCLEAR STOP — ferme toutes les positions OPEN + pose TRADING_BLOCKED=True.

    Appelle le broker (close_position) pour chaque position, puis marque le CSV.
    Retourne le DataFrame mis à jour.
    """
    now_str   = datetime.now().strftime(DATE_FMT)
    open_mask = df["Status"] == "OPEN"
    count     = open_mask.sum()

    if count == 0:
        logger.warning("[NUCLEAR STOP] Aucune position OPEN à fermer.")
    else:
        try:
            from modules.broker_gateway import get_broker
            broker = get_broker()
            for _idx, row in df[open_mask].iterrows():
                ticker = str(row["Ticker"]).strip().upper()
                direction = str(row.get("Direction", "LONG")).strip().upper()
                broker.close_position(
                    ticker,
                    float(row.get("Exit_Price", 0) or 0),
                    "EMERGENCY_CLOSED",
                    direction,
                    update_csv=False,
                )
        except Exception as exc:
            logger.error(f"[NUCLEAR STOP] Erreur broker: {exc}")

        df.loc[open_mask, "Status"]      = "EMERGENCY_CLOSED"
        df.loc[open_mask, "Exit_Date"]   = now_str
        # Phase 7 audit — granularité de la raison de fermeture pour analyse perf.
        df.loc[open_mask, "Close_Reason"] = "EMERGENCY_DD"
        logger.critical(f"[NUCLEAR STOP] {count} position(s) fermée(s) en urgence.")

    # Phase 2 audit — capture le peak equity au moment du stop pour l'hysteresis
    # de relevage (cf. _has_equity_recovered).
    try:
        peak = estimate_portfolio_equity(df)
    except Exception:
        peak = None
    _set_trading_blocked(True, peak_equity=peak)
    logger.critical(
        "[NUCLEAR STOP] TRADING_BLOCKED = True. "
        "Le bot ne prendra aucun nouveau trade jusqu'à demain 00h00 "
        "(et tant que l'equity n'est pas revenue à ≥ 96 % du peak)."
    )
    return df


def check_daily_drawdown(df: pd.DataFrame) -> bool:
    """Vérifie si le daily drawdown a atteint le seuil critique.

    Flux :
      1. Estime l'équité courante depuis le journal.
      2. Récupère (ou initialise) le STARTING_EQUITY de la journée.
      3. Calcule le drawdown via modules/risk.calculate_drawdown().
      4. Si drawdown ≤ -MAX_DAILY_DRAWDOWN_PCT → retourne True.
    """
    current_equity  = estimate_portfolio_equity(df)
    starting_equity = get_starting_equity(current_equity)
    max_dd          = float(getattr(config, "MAX_DAILY_DRAWDOWN_PCT", 4.0))

    try:
        drawdown_pct = calculate_drawdown(current_equity, starting_equity)
    except ValueError as exc:
        logger.error(f"[Killswitch] Erreur calcul drawdown : {exc}")
        return False

    logger.info(
        f"[Killswitch] Équité : ${current_equity:,.2f} | "
        f"Départ : ${starting_equity:,.2f} | "
        f"Drawdown : {drawdown_pct:+.2f}%  (seuil : -{max_dd:.1f}%)"
    )

    if drawdown_pct <= -max_dd:
        logger.critical(
            f"[NUCLEAR STOP] ⚠️  Drawdown {drawdown_pct:.2f}% ≤ -{max_dd:.1f}% — "
            "Déclenchement du killswitch !"
        )
        return True

    return False
