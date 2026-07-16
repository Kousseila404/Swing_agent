#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  SWINGQUANT TITAN — POINT D'ENTRÉE CLI (Quantamental Long-Term)  ║
║                                                                  ║
║  Modes survivants après le pivot TITAN V2 :                      ║
║                                                                  ║
║  [MACRO]                                                         ║
║    python main.py --macro           Régime macro (S&P500 / VIX)  ║
║                                                                  ║
║  [SANTÉ — cron matin]                                            ║
║    python main.py --health-check    Health check Telegram        ║
║                                                                  ║
║  [ALPACA BROKER]                                                 ║
║    python main.py --alpaca-test     Test connexion Alpaca        ║
║    python main.py --alpaca-sync     Sync positions ↔ CSV        ║
║                                                                  ║
║  Les modes swing/backtest/optimize/alpha legacy ont été          ║
║  supprimés lors du pivot quantamental (voir universe_engine.py   ║
║  + sector_metrics.py pour la nouvelle chaîne TITAN).             ║
╚══════════════════════════════════════════════════════════════════╝
"""
import argparse
import sys

from modules.cli_alpaca import run_alpaca_sync as _run_alpaca_sync
from modules.cli_alpaca import run_alpaca_test as _run_alpaca_test
from modules.cli_misc import run_macro_mode
from modules.log import logger


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SwingQuant TITAN — CLI quantamental long-term",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python main.py --macro          Affiche le régime macro actuel
  python main.py --health-check   Ping Telegram santé du bot
  python main.py --alpaca-test    Test connexion Alpaca
  python main.py --alpaca-sync    Sync positions Alpaca → journal
        """,
    )

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--macro", action="store_true",
        help="Affiche le régime macro-économique actuel (S&P500 / VIX)",
    )
    mode_group.add_argument(
        "--health-check", action="store_true",
        help="Envoie un message Telegram de santé du bot. Appeler via cron à 9h.",
    )
    mode_group.add_argument(
        "--alpaca-test", action="store_true",
        help=(
            "Teste la connexion Alpaca Markets et affiche les infos du compte. "
            "Prérequis : ALPACA_API_KEY + ALPACA_SECRET_KEY dans .env + BROKER_MODE=alpaca."
        ),
    )
    mode_group.add_argument(
        "--alpaca-sync", action="store_true",
        help=(
            "Synchronise les positions Alpaca ouvertes → trade_journal.csv. "
            "Importe aussi les fills récents pour mettre à jour les trades OPEN clôturés par bracket."
        ),
    )
    mode_group.add_argument(
        "--auto-approve-proposals", action="store_true",
        help=(
            "Auto-approuve les propositions pending qui remplissent la règle "
            "TITAN>=80 (ou 70-80 + support ON + Piotroski>=7), budget capé "
            "2/run et 5/semaine. Appeler via cron en heures de marché."
        ),
    )
    mode_group.add_argument(
        "--daily-digest", action="store_true",
        help=(
            "Envoie un résumé Telegram quotidien actionnable : propositions "
            "en attente (top 3), positions ouvertes, P&L. Appeler via cron le matin."
        ),
    )

    args = parser.parse_args()

    try:
        from config import validate_startup
        validate_startup()
    except Exception as e:
        logger.critical(f"[main] Config invalide : {e}")
        sys.exit(2)

    try:
        if args.macro:
            run_macro_mode()
        elif getattr(args, "health_check", False):
            from modules.alerter import send_health_check
            send_health_check()
        elif getattr(args, "alpaca_test", False):
            _run_alpaca_test()
        elif getattr(args, "alpaca_sync", False):
            _run_alpaca_sync()

    except KeyboardInterrupt:
        logger.info("⚠️  Arrêt demandé par l'utilisateur (Ctrl+C)")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"💀 Erreur fatale non gérée : {e}", exc_info=True)
        try:
            from modules.alerter import send_crash_alert
            send_crash_alert(str(e))
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
