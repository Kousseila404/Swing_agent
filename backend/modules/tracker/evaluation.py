"""Lecture/écriture du journal CSV + évaluation des positions OPEN.

Logique de clôture V4 (par ordre de priorité dans evaluate_trades) :
  1. Time exit       — MAX_HOLDING_DAYS dépassé → clôture forcée (WIN/LOSS selon PnL).
  2. Trailing stop   — ATR-adaptatif si OHLCV dispo, sinon % fixe.
                       V4 (2026-04-23) : seuils relevés (activation 8 % / ATR 2.5×,
                       trail 35 % / ATR 2.0×) après audit montrant que la V3 clôturait
                       à +1–2 % sur retracement intraday au lieu de laisser courir
                       vers le TP. Tous les seuils sont pilotés par config.py.
  3. SL/TP standard  — WIN si TP atteint / LOSS si SL touché (Bug D corrigé :
                       asymétrie LONG vs SHORT).
  4. Clamp gap overnight — si le prix réel dépasse SL de > 1.5%, on clamp au SL
                       pour que le PnL réalisé ne reflète pas le slippage brut.
"""
from __future__ import annotations

import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd
from filelock import FileLock

import config
from modules.alerter import send_close_alert
from modules.utils import CSV_LOCK_PATH, CSV_PATH, ensure_csv_schema

from .market import get_current_price
from .state import ALERT_COOLDOWN_PATH, DATE_FMT, SL_ALERT_COOLDOWN_HOURS, logger


# ─────────────────────────────────────────────────────────────────
# COOLDOWN ALERTES SL PROXIMITY (anti-spam)
# ─────────────────────────────────────────────────────────────────
def can_send_sl_alert(ticker: str) -> bool:
    """True si le cooldown SL proximity pour ce ticker est écoulé."""
    try:
        if ALERT_COOLDOWN_PATH.exists():
            with open(ALERT_COOLDOWN_PATH, encoding="utf-8") as fh:
                state = json.load(fh)
            last_str = state.get("sl_proximity", {}).get(ticker, "")
            if last_str:
                last_dt   = datetime.strptime(last_str, "%Y-%m-%d %H:%M:%S")
                elapsed_h = (datetime.now() - last_dt).total_seconds() / 3600
                return elapsed_h >= SL_ALERT_COOLDOWN_HOURS
    except Exception:
        pass
    return True


def mark_sl_alert_sent(ticker: str) -> None:
    """Enregistre l'heure d'envoi de l'alerte SL proximity pour ce ticker."""
    try:
        state: dict = {}
        if ALERT_COOLDOWN_PATH.exists():
            with open(ALERT_COOLDOWN_PATH, encoding="utf-8") as fh:
                state = json.load(fh)
        state.setdefault("sl_proximity", {})[ticker] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ALERT_COOLDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERT_COOLDOWN_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────
# JOURNAL IO — FileLock systématique (Bug I)
# ─────────────────────────────────────────────────────────────────
def load_journal() -> pd.DataFrame:
    """Charge data/trade_journal.csv sous FileLock + schéma canonique (Bug H+I)."""
    with FileLock(str(CSV_LOCK_PATH), timeout=10):
        ensure_csv_schema(CSV_PATH)

        if not CSV_PATH.exists():
            logger.error(f"Fichier introuvable : {CSV_PATH.resolve()}")
            sys.exit(1)

        df = pd.read_csv(CSV_PATH, dtype={"Ticker": str})
    return df


def save_journal(df: pd.DataFrame) -> None:
    """Écrit dans un .tmp, puis renomme atomiquement en .csv sous FileLock.

    Garantit :
      - Aucune corruption si le processus est interrompu (rename atomique).
      - Aucun écrasement concurrent d'un append de l'alerter (FileLock — Bug I).
    """
    tmp_path = CSV_PATH.with_suffix(".tmp")
    with FileLock(str(CSV_LOCK_PATH), timeout=10):
        try:
            df.to_csv(tmp_path, index=False)
            tmp_path.replace(CSV_PATH)
            logger.info(f"Journal sauvegardé → {CSV_PATH}")
        except Exception as exc:
            logger.error(f"Échec de la sauvegarde : {exc}")
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise


# ─────────────────────────────────────────────────────────────────
# LOG CLÔTURE + ALERTE TELEGRAM
# ─────────────────────────────────────────────────────────────────
def _log_close(ticker: str, status: str, exit_price: float,
               target: float, entry: float, direction: str = "LONG") -> None:
    """Log la clôture et envoie l'alerte Telegram (TP ou SL touché)."""
    icon = "✅" if status == "WIN" else "❌"
    log_fn = logger.warning if status == "LOSS" else logger.info
    log_fn(
        f"{icon} [{ticker}] CLOSED → {status} | "
        f"Entry={entry:.4f} | Target={target:.4f} | Exit={exit_price:.4f}"
    )
    try:
        send_close_alert(ticker, direction, status, entry, exit_price)
    except Exception as exc:
        logger.warning(f"[{ticker}] Échec alerte clôture : {exc}")


# ─────────────────────────────────────────────────────────────────
# ÉVALUATION DES POSITIONS OPEN (cœur de la logique trade)
# ─────────────────────────────────────────────────────────────────
def evaluate_trades(df: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Parcourt toutes les lignes OPEN, applique la logique V3 complète.

    Returns:
      (df, closed_count, modified_count) — modified = trailing stops mis à jour.
    """
    open_mask   = df["Status"] == "OPEN"
    open_trades = df[open_mask]

    if open_trades.empty:
        logger.info("Aucune position à surveiller.")
        return df, 0, 0

    logger.info(f"Évaluation de {len(open_trades)} position(s) OPEN…")

    closed_count   = 0
    modified_count = 0
    now_str        = datetime.now().strftime(DATE_FMT)

    trailing_activation_floor = float(getattr(config, "TRAILING_STOP_ACTIVATION_PCT", 15.0))
    trailing_activation_ratio = float(getattr(config, "TRAILING_STOP_ACTIVATION_RATIO", 0.5))
    trailing_lock_pct         = float(getattr(config, "TRAILING_STOP_LOCK_PCT", 0.25))
    atr_activation_mult       = float(getattr(config, "TRAILING_STOP_ATR_ACTIVATION_MULT", 3.5))
    atr_trail_mult            = float(getattr(config, "TRAILING_STOP_ATR_TRAIL_MULT", 3.0))

    # Durée max depuis best_strategy.json si dispo, sinon config
    # V4.1 LT : default 60j (ancien hardcoded 10 était incohérent avec la thèse
    # Quantamental Long-Term — horizon typique de convergence 1-3 mois).
    max_holding_days = int(getattr(config, "MAX_HOLDING_DAYS", 60))
    try:
        _bsp = Path("data/best_strategy.json")
        if _bsp.exists():
            _bsd = json.loads(_bsp.read_text())
            _raw_params = _bsd.get("params", _bsd)
            _ts = (_raw_params.get("max_holding_days")
                   or _bsd.get("param_time_stop"))
            if _ts and int(_ts) > 0:
                max_holding_days = int(_ts)
    except Exception:
        pass

    # ── Phase 1 — Préfetch parallèle (prix + OHLCV) ────────────────
    # Chaque ticker déclenche 1–2 appels réseau (yfinance / Alpaca) et 1 lecture
    # DuckDB. Séquentiellement : N × 150 ms. En parallèle : ~(N/8) × 150 ms.
    # Les tests monkeypatchent `get_current_price` et `modules.market_db.read_ohlcv` ;
    # on passe donc par les mêmes symboles ici.
    tickers_to_fetch: list[tuple[int, str]] = []
    for idx, row in open_trades.iterrows():
        t = str(row["Ticker"]).strip().upper()
        # Dédoublonne : si un ticker apparaît 2× (rare), on fetch une seule fois.
        tickers_to_fetch.append((idx, t))

    unique_tickers = list({t for _, t in tickers_to_fetch})

    def _fetch(ticker: str) -> tuple[str, float | None, pd.DataFrame | None]:
        price = get_current_price(ticker)
        hist = None
        try:
            from modules.market_db import read_ohlcv
            hist = read_ohlcv(ticker, days=30)
        except Exception:
            pass
        # Fallback yfinance quand la DuckDB locale est absente / vide.
        # Sans ça, le bloc trailing stop ATR (calcul EMA9 + TR + 3.5×ATR ci-dessous)
        # est sauté en silence et le TS reste figé à sa valeur d'ouverture —
        # ce qui rend inopérant le recalibrage LT V4.1 (ATR mults 3.5/3.0).
        if hist is None:
            try:
                import yfinance as yf
                df = yf.download(
                    ticker, period="45d", interval="1d",
                    progress=False, auto_adjust=True, threads=False,
                )
                if df is not None and not df.empty and len(df) >= 15:
                    # yf.download retourne parfois des MultiIndex cols — on aplati.
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    hist = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            except Exception:
                hist = None
        return ticker, price, hist

    market_data: dict[str, tuple[float | None, pd.DataFrame | None]] = {}
    if unique_tickers:
        # max_workers=8 : bon compromis I/O-bound sans saturer yfinance.
        workers = min(8, len(unique_tickers))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for t, price, hist in pool.map(_fetch, unique_tickers):
                market_data[t] = (price, hist)

    for idx, row in open_trades.iterrows():
        ticker    = str(row["Ticker"]).strip().upper()
        entry     = float(row["Entry"])
        direction = str(row.get("Direction", "LONG")).strip().upper()

        try:
            sl = float(row.get("Stop_Loss") or "nan")
        except (ValueError, TypeError):
            sl = float("nan")
        try:
            tp = float(row.get("Take_Profit") or "nan")
        except (ValueError, TypeError):
            tp = float("nan")

        current_price, df_hist = market_data.get(ticker, (None, None))
        if current_price is None:
            continue

        # P&L du trade (positif = en profit, quelle que soit la direction)
        if direction == "SHORT":
            pct_gain = (entry - current_price) / entry * 100
        else:
            pct_gain = (current_price - entry) / entry * 100

        logger.debug(
            f"[{ticker}] {direction} | Prix={current_price:.4f} | "
            f"SL={sl:.4f} | TP={tp:.4f} | P&L={pct_gain:+.2f}%"
        )

        # ── 1. Time exit (priorité maximale) ───────────────────────
        entry_date_str = str(row.get("Date", ""))
        try:
            entry_dt  = datetime.strptime(entry_date_str[:10], "%Y-%m-%d")
            days_held = (datetime.now() - entry_dt).days
        except Exception:
            days_held = 0

        if days_held >= max_holding_days:
            status = "WIN" if pct_gain > 0 else "LOSS"
            try:
                from modules.broker_gateway import get_broker
                get_broker().close_position(ticker, current_price, status, direction, update_csv=False)
            except Exception as e:
                logger.error(f"[{ticker}] Erreur Broker Timeout: {e}")
            df.at[idx, "Status"]     = status
            df.at[idx, "Exit_Price"] = round(current_price, 6)
            df.at[idx, "Exit_Date"]  = now_str
            logger.info(
                f"⏰ [{ticker}] FERMETURE TEMPS ({days_held}j ≥ {max_holding_days}j) | "
                f"Exit={current_price:.4f} | P&L={pct_gain:+.2f}% → {status}"
            )
            try:
                send_close_alert(ticker, direction, "TIMEOUT", entry, current_price, days_held)
            except Exception as exc:
                logger.warning(f"[{ticker}] Erreur alerte timeout : {exc}")
            closed_count += 1
            continue

        # ── 2. Trailing Stop Adaptatif (ATR) vs Fixe (%) ────────────
        _today_str = datetime.now().strftime("%Y-%m-%d")
        _last_ts   = str(row.get("Last_TS_Update", "") or "")
        _ts_already_updated = _last_ts.startswith(_today_str)

        atr, ema9 = None, None
        if not _ts_already_updated and df_hist is not None:
            try:
                if len(df_hist) >= 15:
                    tr1 = df_hist['High'] - df_hist['Low']
                    tr2 = (df_hist['High'] - df_hist['Close'].shift()).abs()
                    tr3 = (df_hist['Low'] - df_hist['Close'].shift()).abs()
                    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                    atr = float(tr.rolling(14).mean().iloc[-1])
                    ema9 = float(df_hist['Close'].ewm(span=9, adjust=False).mean().iloc[-1])
            except Exception:
                pass

        is_trailing_active = False
        new_sl = sl

        if atr is not None and ema9 is not None and atr > 0 and not _ts_already_updated:
            # Mode Adaptatif : active si profit $ ≥ atr_activation_mult × ATR.
            # Trail à atr_trail_mult ATR du plus-haut (ancrage EMA9-0.2ATR conservé
            # comme filet anti-cassure de tendance court terme).
            profit_dollars = current_price - entry if direction == "LONG" else entry - current_price
            if profit_dollars >= atr_activation_mult * atr:
                is_trailing_active = True
                if direction == "LONG":
                    new_sl = max(entry, min(ema9 - atr * 0.2, current_price - atr * atr_trail_mult))
                else:
                    new_sl = min(entry, max(ema9 + atr * 0.2, current_price + atr * atr_trail_mult))
        elif not math.isnan(sl) and not _ts_already_updated:
            # Mode Fixe (%) fallback. Seuil d'activation adaptatif :
            #   min(TRAILING_STOP_ACTIVATION_PCT, TP_distance × RATIO)
            # Raison audit 2026-04-23 : sur un ticker σ-40 % avec TP +20 %,
            # l'ancien seuil fixe 15 % n'activait le trailing que juste avant
            # le TP → zéro utilité. Cap adaptatif au ratio du gain pour que le
            # trailing s'enclenche au moins à la moitié du chemin vers TP.
            if direction == "LONG" and not math.isnan(tp) and tp > entry:
                tp_distance_pct = (tp - entry) / entry * 100.0
                effective_activation = min(trailing_activation_floor, tp_distance_pct * trailing_activation_ratio)
            elif direction == "SHORT" and not math.isnan(tp) and tp < entry:
                tp_distance_pct = (entry - tp) / entry * 100.0
                effective_activation = min(trailing_activation_floor, tp_distance_pct * trailing_activation_ratio)
            else:
                effective_activation = trailing_activation_floor

            if pct_gain >= effective_activation:
                is_trailing_active = True
                if direction == "LONG":
                    new_sl = entry + (current_price - entry) * trailing_lock_pct
                else:
                    new_sl = entry - (entry - current_price) * trailing_lock_pct

        if is_trailing_active:
            if (direction == "LONG" and new_sl > sl) or (direction == "SHORT" and new_sl < sl):
                try:
                    from modules.broker_gateway import get_broker
                    get_broker().update_stop_loss(ticker, new_sl, direction, update_csv=False)
                except Exception as e:
                    logger.error(f"[{ticker}] Erreur Broker Trailing SL: {e}")
                sl = new_sl
                df.at[idx, "Stop_Loss"]      = round(new_sl, 4)
                df.at[idx, "Last_TS_Update"] = _today_str
                mode_str = " (ATR)" if atr is not None else ""
                logger.info(f"🔒 [{ticker}] TRAILING STOP{mode_str} : SL → {new_sl:.4f} (+{pct_gain:.1f}%)")
                modified_count += 1

        # ── 3. Conditions SL/TP (Bug D corrigé — asymétrie LONG/SHORT) ──
        if direction == "SHORT":
            is_win  = current_price <= tp
            is_loss = current_price >= sl
        else:
            is_win  = current_price >= tp
            is_loss = current_price <= sl

        if is_win:
            try:
                from modules.broker_gateway import get_broker
                get_broker().close_position(ticker, current_price, "WIN", direction, update_csv=False)
            except Exception as e:
                logger.error(f"[{ticker}] Erreur Broker Win Close: {e}")
            df.at[idx, "Status"]     = "WIN"
            df.at[idx, "Exit_Price"] = round(current_price, 6)
            df.at[idx, "Exit_Date"]  = now_str
            _log_close(ticker, "WIN", current_price, tp, entry, direction)
            closed_count += 1

        elif is_loss:
            # Clamp gap overnight (exit réel significativement au-delà du SL)
            gap_clamped = False
            if not math.isnan(sl) and sl > 0:
                if direction == "LONG" and current_price < sl:
                    gap_slippage_pct = (sl - current_price) / sl * 100
                    if gap_slippage_pct > 1.5:
                        logger.warning(
                            f"⚠️ [{ticker}] GAP OVERNIGHT détecté : "
                            f"exit réel={current_price:.4f} < SL={sl:.4f} "
                            f"(slippage gap={gap_slippage_pct:.1f}%) → clamp au SL"
                        )
                        current_price = sl
                        gap_clamped = True
                elif direction == "SHORT" and current_price > sl:
                    gap_slippage_pct = (current_price - sl) / sl * 100
                    if gap_slippage_pct > 1.5:
                        logger.warning(
                            f"⚠️ [{ticker}] GAP OVERNIGHT SHORT détecté : "
                            f"exit réel={current_price:.4f} > SL={sl:.4f} "
                            f"(slippage gap={gap_slippage_pct:.1f}%) → clamp au SL"
                        )
                        current_price = sl
                        gap_clamped = True

            # pct_gain recalculé sur le prix COMPTABLE (post-clamp) — sinon un
            # trailing stop remonté au-dessus de l'entrée se clôture "WIN" en
            # PnL $ mais "LOSS" en status, ce qui pourrit les stats. Le clamp
            # aligne Exit_Price sur le SL ; l'issue du trade doit l'être aussi.
            if direction == "SHORT":
                pct_gain_final = (entry - current_price) / entry * 100
            else:
                pct_gain_final = (current_price - entry) / entry * 100
            sl_status = "WIN" if pct_gain_final > 0 else "LOSS"
            if gap_clamped and sl_status == "WIN":
                logger.info(
                    f"🔒 [{ticker}] TS PROFIT LOCKED : exit clampé@SL={current_price:.4f} "
                    f"(entry={entry:.4f}, P&L comptable={pct_gain_final:+.2f}%) → WIN"
                )
            try:
                from modules.broker_gateway import get_broker
                get_broker().close_position(ticker, current_price, sl_status, direction, update_csv=False)
            except Exception as e:
                logger.error(f"[{ticker}] Erreur Broker Loss Close: {e}")
            df.at[idx, "Status"]     = sl_status
            df.at[idx, "Exit_Price"] = round(current_price, 6)
            df.at[idx, "Exit_Date"]  = now_str
            _log_close(ticker, sl_status, current_price, sl, entry, direction)
            closed_count += 1

        else:
            pct_to_tp = abs((tp - current_price) / current_price) * 100 if not math.isnan(tp) else float("nan")
            pct_to_sl = abs((current_price - sl) / current_price) * 100 if not math.isnan(sl) else float("nan")
            logger.info(
                f"[{ticker}] {direction} En cours | Prix={current_price:.4f} | "
                f"P&L={pct_gain:+.2f}% | {pct_to_tp:.2f}%→TP | {pct_to_sl:.2f}%→SL"
            )

    return df, closed_count, modified_count
