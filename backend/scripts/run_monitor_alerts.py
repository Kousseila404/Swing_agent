#!/usr/bin/env python3
"""run_monitor_alerts.py — Cron daily monitor.

Scanne positions OPEN, détecte signaux (drift / TITAN drop / support break)
et envoie 1 message Telegram consolidé. Idéal en cron quotidien.

Usage :
    cd /home/swing/swingquant/backend
    ./venv/bin/python -m scripts.run_monitor_alerts          # send réel
    ./venv/bin/python -m scripts.run_monitor_alerts --dry    # preview only

Crontab suggéré (jours de bourse, 18h UTC = 14h ET, après close NYSE) :
    0 18 * * 1-5 cd /home/swing/swingquant/backend && \
        ./venv/bin/python -m scripts.run_monitor_alerts >> logs/monitor.log 2>&1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ajoute backend/ au PYTHONPATH pour les `from modules ...`.
_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from modules import monitor_alerts  # noqa: E402
from modules.log import logger  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run monitor alerts")
    parser.add_argument("--dry", action="store_true",
                        help="Dry run (pas d'envoi Telegram)")
    parser.add_argument("--json", action="store_true",
                        help="Output diag en JSON sur stdout")
    args = parser.parse_args()

    result = monitor_alerts.run_daily_monitor(dry_run=args.dry)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        n = result.get("n_alerts", 0)
        sent = result.get("sent", False)
        if n == 0:
            logger.info("[monitor_alerts] no signals — nothing sent")
        elif args.dry:
            logger.info(f"[monitor_alerts] dry run · {n} alert(s) · preview ready")
        elif sent:
            logger.info(f"[monitor_alerts] {n} alert(s) sent to Telegram")
        else:
            logger.error(f"[monitor_alerts] FAIL — {result.get('error')}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
