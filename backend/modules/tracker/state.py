"""Constantes, paths, logger et IO d'état persistant pour le tracker.

État persisté (data/) :
  - equity_state.json           — starting/current equity, PnL, open_positions
  - trading_state.json          — flag blocked (NUCLEAR_STOP) + date
  - circuit_breaker_state.json  — peak_equity + pause_remaining + multiplier
  - tracker_heartbeat.json      — timestamp + epoch + status du dernier cycle
  - alert_cooldown.json         — cooldowns des alertes SL proximity

Ces fichiers sont lus par d'autres processus (API, scanner, dashboard), d'où
le fail-open systématique sur les lectures.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from modules.risk import DrawdownCircuitBreaker
from modules.utils import CSV_PATH, ensure_csv_schema

# ─────────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────────
_BACKEND_ROOT      = Path(__file__).resolve().parents[2]  # modules/tracker/state.py → backend/
LOG_FILE           = _BACKEND_ROOT / "logs" / "tracker.log"
LOOP_INTERVAL      = 300            # secondes entre chaque cycle (5 min)
DATE_FMT           = "%Y-%m-%d %H:%M"
FETCH_TIMEOUT      = 10             # secondes max pour les appels yfinance
PID_LOCK_PATH      = Path("/tmp/swing_tracker.lock")  # Bug J — instance unique

EQUITY_STATE_PATH    = _BACKEND_ROOT / "data" / "equity_state.json"
TRADING_STATE_PATH   = _BACKEND_ROOT / "data" / "trading_state.json"
ALERT_COOLDOWN_PATH  = _BACKEND_ROOT / "data" / "alert_cooldown.json"
CB_STATE_PATH        = _BACKEND_ROOT / "data" / "circuit_breaker_state.json"
HEARTBEAT_PATH       = _BACKEND_ROOT / "data" / "tracker_heartbeat.json"

SL_ALERT_COOLDOWN_HOURS = 4
THESIS_ALERT_COOLDOWN_HOURS = 24  # Anti-spam thesis_stop : 1 alerte BROKEN/jour/ticker
THESIS_CHECK_INTERVAL_MIN = 60    # Throttle global : on n'évalue thesis_stop qu'1×/heure
                                   # (le préfetch scored_universe coûte ~5 MB JSON parse).
PRICE_ALERTS_CHECK_INTERVAL_MIN = 5  # Intraday price_alerts : check toutes les 5 min.
                                      # Le cron daily 16:30 NY (run_monitor_alerts) reste
                                      # le filet pour les tickers hors-portefeuille.

# Cooldowns par action LT (refonte 2026-04-29). Calibrés par sévérité :
#   ADD_ON : 48h — suggérer un renfort 2× max par fenêtre, le marché bouge.
#   TRIM : 24h — érosion de thèse, on alerte une fois par jour.
#   EXIT_VALUATION : 24h — survalorisation persistante.
#   EXIT_CATASTROPHE : 6h — vraie urgence, mais pas de spam intra-journée
#     si le SL est touché à plusieurs reprises sur du gap intraday.
LT_DECISION_COOLDOWN_HOURS = {
    "ADD_ON":            48,
    "TRIM":              24,
    "EXIT_THESIS":       THESIS_ALERT_COOLDOWN_HOURS,  # 24h, aligné rétrocompat thesis_break
    "EXIT_VALUATION":    24,
    "EXIT_CATASTROPHE":   6,
    # Audit 2026-05-12 — earnings TRIM J-3 si gain > +10 %.
    # 12h : un earnings se passe sur 1-3j, on veut être ré-alerté à J-3 puis
    # potentiellement à J-2/J-1 si la position évolue, mais sans spammer.
    "EARNINGS_TRIM":     12,
    # Audit 2026-08-14 — MAX_HOLDING_DAYS différé si thèse INTACT/ADD_ON.
    # 24h : days_held n'avance qu'une fois/jour, un rappel quotidien suffit
    # tant que la position reste en hold prolongé.
    "TIMEOUT_DEFERRED":  24,
}


# ─────────────────────────────────────────────────────────────────
# INITIALISATION DE L'ESPACE DE TRAVAIL
# ─────────────────────────────────────────────────────────────────
def init_workspace() -> None:
    """Crée data/ et logs/ + initialise le journal CSV au schéma canonique (Bug H).
    Idempotent."""
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ensure_csv_schema(CSV_PATH)


# ─────────────────────────────────────────────────────────────────
# LOGGING — Console colorée + Fichier neutre
# ─────────────────────────────────────────────────────────────────
class _ColorFormatter(logging.Formatter):
    """Injecte des codes ANSI selon le niveau de log (console uniquement)."""

    _RESET = "\033[0m"
    _COLORS = {
        logging.DEBUG: "\033[37m",         # Gris
        logging.INFO: "\033[97m",          # Blanc brillant
        logging.WARNING: "\033[93m",       # Jaune
        logging.ERROR: "\033[91m",         # Rouge
        logging.CRITICAL: "\033[91m\033[1m",  # Rouge gras
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self._COLORS.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname:<8}{self._RESET}"
        return super().format(record)


def setup_logger() -> logging.Logger:
    """Initialise le logger `Tracker` (fichier rotatif + console colorée).
    Idempotent — ne réattache pas les handlers si déjà présents.
    Sous pytest, le RotatingFileHandler est supprimé pour éviter de polluer
    le fichier de log de production avec la sortie des tests."""
    log = logging.getLogger("Tracker")
    if log.handlers:
        return log

    log.setLevel(logging.INFO)

    fmt      = "%(asctime)s | %(levelname)s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    # Skip file handler under pytest — sinon les logs des tests vont dans
    # backend/logs/tracker.log (fichier de prod).
    if "pytest" not in sys.modules:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))
        log.addHandler(fh)

    # Console colorée — uniquement en mode interactif (évite la duplication
    # dans tracker.log quand cron redirige stdout vers le fichier).
    if sys.stdout.isatty():
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(_ColorFormatter(fmt, datefmt=date_fmt))
        log.addHandler(ch)
    return log


# Setup immédiat — tout submodule fait `from modules.tracker.state import logger`
logger = setup_logger()


# ─────────────────────────────────────────────────────────────────
# HEARTBEAT — signal de vie pour /api/tracker_health
# ─────────────────────────────────────────────────────────────────
def write_heartbeat(cycle_status: str = "ok") -> None:
    """Écrit un heartbeat horodaté à chaque cycle tracker.

    Permet au dashboard / /api/tracker_health de détecter silencieusement un
    tracker tombé sans attendre qu'une alerte humaine remarque l'absence de
    mouvements sur le CSV. Un circuit-breaker drawdown inopérant = pire qu'aucun.
    """
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "epoch":     time.time(),
            "pid":       os.getpid(),
            "status":    cycle_status,
        }
        tmp = HEARTBEAT_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(HEARTBEAT_PATH)
    except Exception as exc:
        logger.debug(f"[Heartbeat] Erreur écriture : {exc}")


# ─────────────────────────────────────────────────────────────────
# CIRCUIT BREAKER — persistance cross-process
# ─────────────────────────────────────────────────────────────────
# Singleton initialisé lazily dans cycle.py (peak_equity inconnu au boot).
_circuit_breaker: DrawdownCircuitBreaker | None = None


def get_circuit_breaker() -> DrawdownCircuitBreaker | None:
    return _circuit_breaker


def set_circuit_breaker(cb: DrawdownCircuitBreaker) -> None:
    global _circuit_breaker
    _circuit_breaker = cb


def save_cb_state(cb: DrawdownCircuitBreaker, multiplier: float) -> None:
    """Persiste l'état du circuit breaker dans data/circuit_breaker_state.json.

    Phase 2 audit — sauve aussi `equity_history` (buffer rolling) pour que le
    peak rolling survive aux restarts du tracker.
    """
    try:
        state = {
            "peak_equity":    round(cb.peak_equity, 2),
            "pause_remaining": cb._pause_remaining,
            "size_multiplier": round(multiplier, 4),
            "is_paused":       cb.is_paused(),
            "equity_history":  [round(v, 2) for v in getattr(cb, "_equity_history", [])],
            "rolling_window":  getattr(cb, "_rolling_window", 20),
            "updated_at":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        CB_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CB_STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(CB_STATE_PATH)
    except Exception as exc:
        logger.debug(f"[CircuitBreaker] Erreur sauvegarde état : {exc}")


def read_circuit_breaker_state() -> dict:
    """Lit l'état persisté du circuit breaker (utilisable par le scanner/main.py).

    Returns:
        Dict avec is_paused, size_multiplier, peak_equity.
        Valeurs neutres si le fichier est absent (fail-open).
    """
    try:
        if CB_STATE_PATH.exists():
            with open(CB_STATE_PATH, encoding="utf-8") as fh:
                return json.load(fh)
    except Exception:
        pass
    return {"is_paused": False, "size_multiplier": 1.0, "peak_equity": 0.0}


# ─────────────────────────────────────────────────────────────────
# EQUITY STATE IO — starting_equity journalier
# ─────────────────────────────────────────────────────────────────
def load_equity_state() -> dict:
    """Charge l'état de l'équité depuis equity_state.json (fail-open)."""
    if not EQUITY_STATE_PATH.exists():
        return {}
    try:
        with open(EQUITY_STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning(f"[Killswitch] Impossible de lire equity_state.json : {exc}")
        return {}


def save_equity_state(starting_equity: float) -> None:
    """Persiste le STARTING_EQUITY et la date du jour dans equity_state.json."""
    from datetime import date as _date
    EQUITY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "starting_equity": starting_equity,
        "date": _date.today().isoformat(),
    }
    tmp = EQUITY_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(EQUITY_STATE_PATH)
    logger.info(f"[Killswitch] STARTING_EQUITY enregistré : ${starting_equity:,.2f}")
