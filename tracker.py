#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  SWING QUANT V20 — TRACKER                                       ║
║  Surveillance temps réel & Clôture automatique des Paper Trades  ║
╚══════════════════════════════════════════════════════════════════╝

Modes d'utilisation :
  - Single run (CronJob VPS) : python tracker.py
  - Boucle continue (Mac)    : python tracker.py --loop
"""

import sys
import time
import logging
import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

# ─────────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────────
CSV_PATH      = Path("data/trade_journal.csv")
LOG_FILE      = Path("logs/tracker.log")
LOOP_INTERVAL = 300          # secondes entre chaque cycle (5 min)
DATE_FMT      = "%Y-%m-%d %H:%M"
FETCH_TIMEOUT = 10           # secondes max accordées à yfinance


# ─────────────────────────────────────────────────────────────────
# LOGGING — Console colorée + Fichier neutre
# ─────────────────────────────────────────────────────────────────
class _ColorFormatter(logging.Formatter):
    """Injecte des codes ANSI selon le niveau de log (console uniquement)."""

    _COLORS = {
        logging.DEBUG:    "\033[37m",         # Gris
        logging.INFO:     "\033[97m",         # Blanc brillant
        logging.WARNING:  "\033[93m",         # Jaune
        logging.ERROR:    "\033[91m",         # Rouge
        logging.CRITICAL: "\033[91m\033[1m",  # Rouge gras
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self._COLORS.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname:<8}{_RESET}"
        return super().format(record)


def _setup_logger() -> logging.Logger:
    """Initialise le logger dédié au tracker (fichier + console)."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    log = logging.getLogger("Tracker")
    log.setLevel(logging.INFO)

    fmt      = "%(asctime)s | %(levelname)s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    # Handler fichier (texte brut, sans ANSI — lisible sur VPS)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))

    # Handler console (avec couleurs ANSI)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(_ColorFormatter(fmt, datefmt=date_fmt))

    log.addHandler(fh)
    log.addHandler(ch)
    return log


logger = _setup_logger()


# ─────────────────────────────────────────────────────────────────
# 1. LECTURE DU CSV
# ─────────────────────────────────────────────────────────────────
def load_journal() -> pd.DataFrame:
    """
    Charge data/trade_journal.csv et garantit que les colonnes de
    clôture Exit_Price et Exit_Date existent.

    Quitte proprement si le fichier est introuvable.
    """
    if not CSV_PATH.exists():
        logger.error(f"Fichier introuvable : {CSV_PATH.resolve()}")
        sys.exit(1)

    df = pd.read_csv(CSV_PATH, dtype={"Ticker": str})

    # Création des colonnes de clôture si elles sont absentes
    for col in ("Exit_Price", "Exit_Date"):
        if col not in df.columns:
            df[col] = None
            logger.info(f"Colonne '{col}' absente — créée automatiquement.")

    return df


# ─────────────────────────────────────────────────────────────────
# 2. RÉCUPÉRATION DES PRIX — yfinance (méthode rapide)
# ─────────────────────────────────────────────────────────────────
def get_current_price(ticker: str) -> float | None:
    """
    Retourne le dernier prix connu pour un ticker.

    Stratégie en deux niveaux pour maximiser la fiabilité :
      1. fast_info['lastPrice']  — instanntané, zéro téléchargement d'historique
      2. history(period='1d')   — fallback si fast_info est vide

    Retourne None en cas d'échec total (le script continue sans crasher).
    """
    try:
        ticker_obj = yf.Ticker(ticker)

        # Niveau 1 : fast_info — le plus léger disponible dans yfinance
        price = ticker_obj.fast_info.get("lastPrice")
        if price is not None and float(price) > 0:
            return float(price)

        # Niveau 2 : fallback sur le dernier close de la journée
        hist = ticker_obj.history(period="1d", timeout=FETCH_TIMEOUT)
        if not hist.empty:
            return float(hist["Close"].iloc[-1])

        logger.warning(f"[{ticker}] Aucune donnée de prix disponible.")
        return None

    except Exception as exc:  # timeout, connexion, données malformées…
        logger.error(f"[{ticker}] Erreur yfinance : {exc}")
        return None


# ─────────────────────────────────────────────────────────────────
# 3. LOGIQUE D'ARBITRAGE — Le Juge
# ─────────────────────────────────────────────────────────────────
def _log_close(ticker: str, status: str, exit_price: float,
               target: float, entry: float) -> None:
    """Log la clôture d'un trade via le logger (fichier + console colorée)."""
    icon = "✅" if status == "WIN" else "❌"
    # WARNING → jaune console (LOSS), INFO → blanc console (WIN)
    log_fn = logger.warning if status == "LOSS" else logger.info
    log_fn(
        f"{icon} [{ticker}] CLOSED → {status} | "
        f"Entry={entry:.4f} | Target={target:.4f} | Exit={exit_price:.4f}"
    )


def evaluate_trades(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    Parcourt toutes les lignes OPEN, récupère le prix actuel et
    applique la logique WIN / LOSS.

    Hypothèse : positions LONG (TP > Entry > SL).
    Pour des shorts, les conditions >= / <= seraient inversées.

    Retourne :
      - Le DataFrame mis à jour.
      - Le nombre de trades effectivement clôturés ce cycle.
    """
    open_mask   = df["Status"] == "OPEN"
    open_trades = df[open_mask]

    if open_trades.empty:
        logger.info("Aucune position à surveiller.")
        return df, 0

    logger.info(f"Évaluation de {len(open_trades)} position(s) OPEN…")

    closed_count = 0
    now_str      = datetime.now().strftime(DATE_FMT)

    for idx, row in open_trades.iterrows():
        ticker = str(row["Ticker"]).strip().upper()
        entry  = float(row["Entry"])
        sl     = float(row["Stop_Loss"])
        tp     = float(row["Take_Profit"])

        current_price = get_current_price(ticker)

        # Erreur réseau déjà loguée dans get_current_price — on passe au suivant
        if current_price is None:
            continue

        logger.debug(
            f"[{ticker}] Prix={current_price:.4f} | SL={sl:.4f} | TP={tp:.4f}"
        )

        # ── Take Profit atteint ─────────────────────────────────
        if current_price >= tp:
            df.at[idx, "Status"]     = "WIN"
            df.at[idx, "Exit_Price"] = round(current_price, 6)
            df.at[idx, "Exit_Date"]  = now_str
            _log_close(ticker, "WIN", current_price, tp, entry)
            closed_count += 1

        # ── Stop Loss touché ────────────────────────────────────
        elif current_price <= sl:
            df.at[idx, "Status"]     = "LOSS"
            df.at[idx, "Exit_Price"] = round(current_price, 6)
            df.at[idx, "Exit_Date"]  = now_str
            _log_close(ticker, "LOSS", current_price, sl, entry)
            closed_count += 1

        # ── Trade toujours en cours ─────────────────────────────
        else:
            pct_to_tp = ((tp - current_price) / current_price) * 100
            pct_to_sl = ((current_price - sl) / current_price) * 100
            logger.info(
                f"[{ticker}] En cours | Prix={current_price:.4f} | "
                f"+{pct_to_tp:.2f}% → TP | -{pct_to_sl:.2f}% → SL"
            )

    return df, closed_count


# ─────────────────────────────────────────────────────────────────
# 4. SAUVEGARDE ATOMIQUE DU CSV
# ─────────────────────────────────────────────────────────────────
def save_journal(df: pd.DataFrame) -> None:
    """
    Écrit dans un fichier .tmp, puis renomme en .csv.
    Cette approche atomique garantit qu'une interruption en cours
    d'écriture ne corrompt jamais le journal.
    """
    tmp_path = CSV_PATH.with_suffix(".tmp")
    try:
        df.to_csv(tmp_path, index=False)
        tmp_path.replace(CSV_PATH)
        logger.info(f"Journal sauvegardé → {CSV_PATH}")
    except Exception as exc:
        logger.error(f"Échec de la sauvegarde : {exc}")
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise  # On remonte l'exception pour interrompre le cycle


# ─────────────────────────────────────────────────────────────────
# 5. CYCLE PRINCIPAL
# ─────────────────────────────────────────────────────────────────
def run_cycle() -> None:
    """
    Un cycle complet :
      1. Lecture du journal
      2. Évaluation de toutes les positions OPEN
      3. Sauvegarde si au moins un trade a été clôturé
    """
    separator = "─" * 60
    logger.info(separator)
    logger.info("TRACKER V20 — Début du cycle")
    logger.info(separator)

    df = load_journal()

    # Vérification préliminaire : évite les appels réseau si inutile
    if (df["Status"] == "OPEN").sum() == 0:
        logger.info("Aucune position OPEN. Fin du cycle.")
        return

    df, closed = evaluate_trades(df)

    if closed > 0:
        save_journal(df)
        logger.info(f"Cycle terminé — {closed} trade(s) clôturé(s) et sauvegardé(s).")
    else:
        logger.info("Cycle terminé — Aucun trade clôturé ce cycle.")


# ─────────────────────────────────────────────────────────────────
# POINT D'ENTRÉE
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Tracker V20 — Surveillance & clôture automatique des Paper Trades",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  python tracker.py           # Single run (CronJob VPS)\n"
            "  python tracker.py --loop    # Boucle toutes les 5 min (Mac)\n"
        ),
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help=(
            f"Active la boucle continue avec un intervalle de {LOOP_INTERVAL}s. "
            "Sans ce flag : exécution unique (idéal pour CronJob VPS)."
        ),
    )
    args = parser.parse_args()

    if args.loop:
        # ── Mode boucle continue — utilisation locale sur Mac ───
        logger.info(
            f"Mode BOUCLE activé — Intervalle : {LOOP_INTERVAL}s. "
            "Appuyez sur Ctrl+C pour stopper proprement."
        )
        try:
            while True:
                run_cycle()
                logger.info(
                    f"Prochain cycle dans {LOOP_INTERVAL // 60} min "
                    f"({datetime.now().strftime('%H:%M:%S')} + {LOOP_INTERVAL}s)…"
                )
                time.sleep(LOOP_INTERVAL)
        except KeyboardInterrupt:
            logger.info("Arrêt manuel (KeyboardInterrupt). À bientôt.")
            sys.exit(0)

    else:
        # ── Mode single run — utilisation via CronJob VPS ───────
        run_cycle()
