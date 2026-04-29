"""
╔══════════════════════════════════════════════════════════════════╗
║  ALERTES TELEGRAM — TITAN V2                                     ║
║                                                                  ║
║  Trois points d'entrée publics seulement :                       ║
║    • send_close_alert()  — clôture de trade (TP/SL/TIMEOUT)      ║
║    • send_health_check() — ping cron matin                       ║
║    • send_crash_alert()  — erreur fatale main.py                 ║
║                                                                  ║
║  Les helpers de formatage Swing legacy (ScanResult, AIVerdict,   ║
║  plan de trade, résumé scan, live feed Streamlit) ont été        ║
║  retirés lors du pivot quantamental.                             ║
╚══════════════════════════════════════════════════════════════════╝
"""
import atexit
import time
from datetime import datetime

import httpx

import config
from modules.log import logger

# Client httpx persistant pour éviter la réouverture TCP/TLS à chaque alerte.
# Sur un cycle tracker normal (~1-3 closes Telegram en rafale), on économise
# ~100-200 ms de handshake par message. Le Client httpx est thread-safe.
_TELEGRAM_CLIENT = httpx.Client(timeout=15.0)
atexit.register(_TELEGRAM_CLIENT.close)


def send_close_alert(
    ticker: str,
    direction: str,
    status: str,        # "WIN" | "LOSS" | "TIMEOUT"
    entry: float,
    exit_price: float,
    days_held: int = 0,
) -> bool:
    """
    Envoie une alerte de clôture de trade (TP atteint, SL touché, ou timeout).
    C'est le seul message envoyé pendant la vie d'un trade (pas de bruit intermédiaire).
    """
    if direction == "LONG":
        pct = (exit_price - entry) / entry * 100
    else:
        pct = (entry - exit_price) / entry * 100

    if status == "WIN":
        icon, label = "✅", "TAKE-PROFIT"
    elif status == "LOSS":
        icon, label = "❌", "STOP-LOSS"
    else:
        icon, label = "⏰", f"TIMEOUT ({days_held}j)"

    msg = (
        f"{icon} <b>CLÔTURE {label} — {ticker}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{'📈' if direction == 'LONG' else '📉'} {direction}\n"
        f"💰 Entrée : <b>${entry:.4f}</b>  →  Sortie : <b>${exit_price:.4f}</b>\n"
        f"{'✅' if pct >= 0 else '❌'} P&L : <b>{pct:+.2f}%</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S')}  |  SwingQuant TITAN"
    )
    try:
        _send_telegram_message(msg)
        logger.info(f"[{ticker}] Alerte clôture {status} envoyée ✅")
        return True
    except Exception as exc:
        logger.warning(f"[{ticker}] Échec alerte clôture : {exc}")
        return False


def send_price_alert_fired(
    ticker: str,
    direction: str,
    target_price: float,
    current_price: float,
    note: str = "",
) -> bool:
    """Alerte Telegram quand un niveau de prix est touché (entry_plan tier).

    Format minimal — un emoji 🎯, le delta, le note utilisateur.
    """
    arrow = "≤" if direction == "below" else "≥"
    delta_pct = (current_price - target_price) / target_price * 100.0 if target_price else 0.0
    note_line = f"\n📝 <i>{note}</i>" if note else ""
    msg = (
        f"🎯 <b>NIVEAU PRIX TOUCHÉ — {ticker}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Cible : <b>${target_price:.2f}</b>  ({arrow})\n"
        f"📍 Actuel : <b>${current_price:.2f}</b>  ({delta_pct:+.2f}%)"
        f"{note_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S')}  |  SwingQuant TITAN"
    )
    try:
        _send_telegram_message(msg)
        logger.info(f"[{ticker}] Price alert fired @ {current_price:.2f} ✅")
        return True
    except Exception as exc:
        logger.warning(f"[{ticker}] Échec alerte prix : {exc}")
        return False


def send_thesis_break_alert(
    ticker: str,
    direction: str,
    entry_price: float,
    current_price: float,
    reasons: list[str],
    drift: dict[str, float | int | None] | None = None,
) -> bool:
    """Alerte Telegram quand thesis_stop signale BROKEN sur une position OPEN.

    Informatif (le module ne ferme pas la position — décision LT manuelle).
    Le cooldown anti-spam est géré par l'appelant (cf. tracker/evaluation.py).
    """
    pct = ((current_price - entry_price) / entry_price * 100
           if direction == "LONG" else (entry_price - current_price) / entry_price * 100)
    titan_drift = (drift or {}).get("titan")
    f_drift = (drift or {}).get("f_score")
    drift_line = ""
    if titan_drift is not None:
        drift_line = f"📉 TITAN drift : <b>{titan_drift:+.0f} pts</b>"
        if f_drift is not None:
            drift_line += f"  |  F-Score : <b>{f_drift:+d}/9</b>"
        drift_line += "\n"

    bullets = "\n".join(f"  • {r}" for r in reasons[:5])
    msg = (
        f"🧠 <b>THÈSE CASSÉE — {ticker}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{'📈' if direction == 'LONG' else '📉'} {direction}  |  "
        f"Entrée : <b>${entry_price:.2f}</b> → ${current_price:.2f}  "
        f"({'✅' if pct >= 0 else '❌'} {pct:+.1f}%)\n"
        f"{drift_line}"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Raisons :</b>\n{bullets}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Décision LT manuelle — le tracker ne ferme pas.\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S')}  |  SwingQuant TITAN"
    )
    try:
        _send_telegram_message(msg)
        logger.info(f"[{ticker}] Alerte thesis_break envoyée ✅")
        return True
    except Exception as exc:
        logger.warning(f"[{ticker}] Échec alerte thesis_break : {exc}")
        return False


def send_health_check() -> bool:
    """
    Envoie un message Telegram de santé du bot chaque matin.

    Contenu :
    - Statut bot : ACTIF
    - Régime macro actuel + VIX
    - Positions ouvertes (count + tickers)
    - Équité estimée
    - Broker mode + IA activée ou non
    - Prochain événement macro

    Appelé via cron à 9h : python main.py --health-check
    """
    import csv as _csv
    import json
    from pathlib import Path

    _root = Path(__file__).resolve().parent.parent

    # ── Régime macro ────────────────────────────────────────────
    regime_str = "N/A"
    vix_str    = "N/A"
    try:
        macro = json.loads((_root / "data" / "macro_state.json").read_text())
        regime_str = macro.get("confirmed_regime", "N/A")
        vix_str    = f"{float(macro.get('vix', 0)):.1f}"
    except Exception:
        pass

    regime_emoji = {"BULL_MARKET": "🟢", "BEAR_MARKET": "🔴", "CRASH_PANIC": "🚨"}.get(regime_str, "⚪")

    # ── Positions ouvertes ───────────────────────────────────────
    open_tickers = []
    equity_str   = "N/A"
    try:
        from modules.utils import get_open_tickers
        open_tickers = list(get_open_tickers())
    except Exception:
        pass

    try:
        eq = json.loads((_root / "data" / "equity_state.json").read_text())
        equity_val = float(eq.get("current_equity", eq.get("starting_equity", config.ACCOUNT_SIZE)))
        equity_str = f"${equity_val:,.0f}"
    except Exception:
        equity_str = f"${config.ACCOUNT_SIZE:,.0f} (défaut)"

    # ── Broker + IA ──────────────────────────────────────────────
    try:
        from modules.broker_gateway import get_broker
        broker_str = get_broker().name
    except Exception:
        broker_str = "PaperBroker (CSV)"

    ai_str = "✅ Activée" if getattr(config, "ENABLE_AI_ANALYSIS", False) else "❌ Désactivée"

    # ── Prochain événement macro ─────────────────────────────────
    next_event_str = "Aucun dans les 7 prochains jours"
    try:
        from datetime import date as _date

        from modules.macro_engine import _load_macro_calendar
        today   = _date.today()
        events  = _load_macro_calendar()
        upcoming = []
        for evt in events:
            try:
                from datetime import datetime as _dt
                evt_date   = _dt.strptime(evt["date"], "%Y-%m-%d").date()
                days_until = (evt_date - today).days
                if 0 <= days_until <= 7:
                    upcoming.append((days_until, evt.get("label", evt.get("type", "Macro"))))
            except Exception:
                pass
        if upcoming:
            upcoming.sort()
            d, lbl = upcoming[0]
            next_event_str = f"{'Aujourd\'hui' if d == 0 else f'Dans {d}j'} : <b>{lbl}</b>"
    except Exception:
        pass

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    pos_str = f"{len(open_tickers)} — {', '.join(open_tickers)}" if open_tickers else "Aucune"

    # ── Alpha vs SPY ─────────────────────────────────────────────
    alpha_str = "N/A"
    try:
        import yfinance as _yf
        _jpath = _root / "data" / "trade_journal.csv"
        if _jpath.exists():
            _rows = []
            with open(_jpath) as _fh:
                for r in _csv.DictReader(_fh):
                    if r.get("Status") in ("WIN", "LOSS") and r.get("Exit_Date") and r.get("Date"):
                        _rows.append(r)
            if len(_rows) >= 5:
                _base = float(getattr(config, "ACCOUNT_SIZE", 100_000))
                _realized = 0.0
                _start_dates = []
                for r in _rows:
                    try:
                        _e = float(r["Entry"]); _x = float(r["Exit_Price"]); _sz = float(r.get("Size",1) or 1)
                        _dir = str(r.get("Direction","LONG")).upper()
                        _pnl = (_x - _e) * _sz if _dir == "LONG" else (_e - _x) * _sz
                        _realized += _pnl
                        _start_dates.append(r["Date"][:10])
                    except Exception:
                        pass
                if _start_dates:
                    _first_date = sorted(_start_dates)[0]
                    _spy = _yf.download("SPY", start=_first_date, progress=False, auto_adjust=True)
                    if not _spy.empty:
                        _spy_ret = (float(_spy["Close"].iloc[-1]) - float(_spy["Close"].iloc[0])) / float(_spy["Close"].iloc[0]) * 100
                        _strat_ret = _realized / _base * 100
                        _alpha = _strat_ret - _spy_ret
                        alpha_str = f"{_strat_ret:+.1f}% vs SPY {_spy_ret:+.1f}% → α={_alpha:+.1f}%"
    except Exception:
        pass

    msg = (
        f"🤖 <b>HEALTH CHECK — {now_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Bot : <b>ACTIF</b>\n"
        f"{regime_emoji} Régime : <b>{regime_str}</b>  |  VIX : <b>{vix_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Positions ouvertes : <b>{pos_str}</b>\n"
        f"💼 Équité estimée : <b>{equity_str}</b>\n"
        f"📈 Alpha : <i>{alpha_str}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔌 Broker : <i>{broker_str}</i>\n"
        f"🧠 IA : {ai_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Prochain événement : {next_event_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ SwingQuant TITAN — surveillance active"
    )

    try:
        _send_telegram_message(msg)
        logger.info("[HealthCheck] Message envoyé ✅")
        return True
    except Exception as exc:
        logger.error(f"[HealthCheck] Échec envoi : {exc}")
        return False


def send_crash_alert(error_msg: str) -> None:
    """
    Envoie une alerte Telegram immédiate si le bot crash (erreur fatale).
    Appelé depuis le handler d'exception dans main.py.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        f"💀 <b>CRASH BOT — {now_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"❌ Erreur fatale détectée :\n"
        f"<code>{error_msg[:300]}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Le bot est arrêté. Vérifiez les logs."
    )
    try:
        _send_telegram_message(msg)
    except Exception:
        pass   # Silencieux — on ne peut rien faire si Telegram est down


_TELEGRAM_MAX_LEN = 4096


def _send_telegram_message(text: str, max_retries: int = 3, retry_delay: float = 5.0) -> None:
    """
    Envoie un message via l'API Telegram Bot avec retry et gestion du rate-limit.

    Comportements :
    - Troncature automatique à 4096 caractères (limite Telegram).
    - 429 (rate limit) : backoff immédiat, pas retenté à l'infini.
    - 5xx : retry exponentiel (5s, 10s, 15s).
    - 4xx autres (401, 403…) : erreur fatale, pas de retry.

    Raises:
        RuntimeError: Si toutes les tentatives échouent ou erreur 4xx non-récupérable.
    """
    if len(text) > _TELEGRAM_MAX_LEN:
        logger.warning(
            f"[Telegram] Message tronqué : {len(text)} → {_TELEGRAM_MAX_LEN} caractères"
        )
        text = text[: _TELEGRAM_MAX_LEN - 4] + "…"

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": int(config.TELEGRAM_CHAT_ID),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = _TELEGRAM_CLIENT.post(url, json=payload)

            if response.status_code == 429:
                retry_after = response.json().get("parameters", {}).get("retry_after", 30)
                logger.warning(
                    f"[Telegram] Rate limit (429) — retry_after={retry_after}s. "
                    "Message non envoyé pour éviter le spam."
                )
                raise RuntimeError(f"[Telegram] Rate limit 429 — retry_after={retry_after}s")

            response.raise_for_status()
            return

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise RuntimeError(
                    f"[Telegram] Erreur HTTP {exc.response.status_code} (non-récupérable) : {exc}"
                ) from exc
            last_exc = exc

        except RuntimeError:
            raise

        except Exception as exc:
            last_exc = exc

        if attempt < max_retries:
            wait = retry_delay * attempt
            logger.warning(
                f"[Telegram] Tentative {attempt}/{max_retries} échouée : {last_exc} "
                f"— Nouvel essai dans {wait:.0f}s"
            )
            time.sleep(wait)

    raise RuntimeError(
        f"[Telegram] Échec après {max_retries} tentatives. Dernière erreur : {last_exc}"
    )
