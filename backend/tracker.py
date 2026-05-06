#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  SWING AGENT V2 — TRACKER (CLI thin — orchestration only)        ║
║                                                                  ║
║  La logique métier vit dans `modules/tracker/` :                 ║
║    - state      : constantes + logger + heartbeat + CB/equity IO ║
║    - market     : heures de marché + get_current_price           ║
║    - killswitch : daily drawdown + emergency liquidate           ║
║    - evaluation : lecture CSV + evaluate_trades                  ║
║    - cycle      : run_cycle (orchestrateur)                      ║
║                                                                  ║
║  Modes d'utilisation :                                           ║
║    - Single run (CronJob VPS) : python tracker.py                ║
║    - Boucle continue (Mac)    : python tracker.py --loop         ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

import config
from modules.tracker.cycle import run_cycle
from modules.tracker.state import (
    LOOP_INTERVAL,
    PID_LOCK_PATH,
    init_workspace,
    logger,
)


def _acquire_pid_lock() -> None:
    """Bug J — prévient les instances multiples via PID lock atomique.

    Création atomique avec O_EXCL, gestion explicite des locks orphelins
    (process mort mais fichier resté) via os.kill(pid, 0).
    """
    my_pid = os.getpid()
    for _ in range(2):
        try:
            with PID_LOCK_PATH.open("x") as f:
                f.write(f"{my_pid}\n")
            return
        except FileExistsError:
            try:
                raw = PID_LOCK_PATH.read_text().strip()
                other_pid = int(raw.split("\n", 1)[0]) if raw else 0
            except Exception:
                other_pid = 0
            alive = False
            if other_pid > 0:
                try:
                    os.kill(other_pid, 0)
                    alive = True
                except (ProcessLookupError, PermissionError):
                    alive = other_pid <= 0
                except OSError:
                    alive = False
            if alive:
                logger.warning(
                    f"[PID Lock] Instance déjà en cours (PID {other_pid}, lock {PID_LOCK_PATH})."
                )
                sys.exit(0)
            logger.warning(
                f"[PID Lock] Lock orphelin détecté (PID {other_pid} mort) — remplacement."
            )
            try:
                PID_LOCK_PATH.unlink(missing_ok=True)
            except Exception as e:
                logger.error(f"[PID Lock] Impossible de retirer le lock orphelin: {e}")
                sys.exit(1)
    logger.error("[PID Lock] Échec d'acquisition après 2 tentatives.")
    sys.exit(1)


def _release_pid_lock() -> None:
    """Nettoyage du PID lock — best-effort (tolère suppression externe)."""
    try:
        PID_LOCK_PATH.unlink(missing_ok=True)
    except Exception as e:
        logger.debug(f"[PID Lock] Nettoyage ignoré : {e}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tracker V2 — Surveillance & clôture automatique des Paper Trades",
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

    init_workspace()
    _acquire_pid_lock()
    try:
        try:
            config.validate_startup()
        except Exception as _cfg_exc:
            logger.critical(f"[Tracker] Config invalide : {_cfg_exc}")
            sys.exit(2)

        if args.loop:
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
        else:
            run_cycle()
    finally:
        _release_pid_lock()


if __name__ == "__main__":
    main()
