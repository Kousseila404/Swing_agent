"""CLI handlers pour les commandes Alpaca (--alpaca-test, --alpaca-sync).

Extrait de main.py lors du split Phase 2 pour réduire la taille du monolithe.
"""
from __future__ import annotations

import config
from modules.log import logger


def run_alpaca_test() -> None:
    """Teste la connexion Alpaca Markets et affiche les infos du compte.
    Commande : python main.py --alpaca-test
    """
    logger.info("━" * 60)
    logger.info("  🔌  ALPACA CONNECTION TEST")
    logger.info("━" * 60)

    broker_mode = getattr(config, "BROKER_MODE", "paper").lower()
    if broker_mode != "alpaca":
        logger.error(
            "BROKER_MODE='paper' dans .env — changez en BROKER_MODE=alpaca "
            "puis relancez --alpaca-test."
        )
        return

    api_key    = getattr(config, "ALPACA_API_KEY",    "")
    secret_key = getattr(config, "ALPACA_SECRET_KEY", "")
    base_url   = getattr(config, "ALPACA_BASE_URL",   "https://paper-api.alpaca.markets")

    if not api_key or not secret_key:
        logger.error(
            "ALPACA_API_KEY ou ALPACA_SECRET_KEY manquant dans .env.\n"
            "1. Créez un compte sur https://alpaca.markets (gratuit)\n"
            "2. Allez dans Dashboard → Paper Trading → API Keys\n"
            "3. Copiez Key ID → ALPACA_API_KEY et Secret Key → ALPACA_SECRET_KEY\n"
            "4. Relancez --alpaca-test"
        )
        return

    try:
        from modules.broker_gateway import AlpacaBroker
        broker = AlpacaBroker()
        info = broker.test_connection()

        logger.info("  ✅ Connexion réussie !")
        logger.info(f"  📊 Mode      : {info['mode'].upper()}")
        logger.info(f"  💼 Equity    : ${info['equity']:,.2f}")
        logger.info(f"  💰 Cash      : ${info['cash']:,.2f}")
        logger.info(f"  🔋 Buying power : ${info['buying_power']:,.2f}")
        logger.info(f"  📡 URL       : {base_url}")
        logger.info(f"  🔑 API Key   : {api_key[:8]}...")
        logger.info("━" * 60)

        positions = broker.get_open_positions()
        logger.info(f"  📦 Positions ouvertes sur Alpaca : {len(positions)}")
        for p in positions:
            logger.info(
                f"     {p.ticker} {p.direction} ×{p.size} @ ${p.entry:.2f} | "
                f"PnL={p.unrealized_pnl:+.2f}$"
            )

        logger.info("━" * 60)
        logger.info("  → Pour activer : BROKER_MODE=alpaca est déjà configuré.")
        logger.info("  → Le bot soumettra les ordres sur Alpaca dès le prochain scan.")

    except Exception as exc:
        logger.error(f"  ❌ Connexion échouée : {exc}")
        logger.info("  Vérifiez vos clés API et l'URL base (paper vs live).")


def run_alpaca_sync() -> None:
    """Synchronise les positions Alpaca ↔ CSV.

    1. sync_fills_from_alpaca() → met à jour les trades OPEN fermés par bracket
    2. import_positions_to_csv() → importe les nouvelles positions Alpaca dans le CSV

    Commande : python main.py --alpaca-sync
    """
    logger.info("━" * 60)
    logger.info("  🔄  ALPACA SYNC")
    logger.info("━" * 60)

    broker_mode = getattr(config, "BROKER_MODE", "paper").lower()
    if broker_mode != "alpaca":
        logger.error("BROKER_MODE doit être 'alpaca' pour utiliser --alpaca-sync.")
        return

    try:
        from modules.broker_gateway import AlpacaBroker
        broker = AlpacaBroker()

        synced = broker.sync_fills_from_alpaca()
        logger.info(f"  ✅ Fills sync : {synced} position(s) fermée(s) importée(s)")

        imported = broker.import_positions_to_csv()
        logger.info(
            f"  ✅ Import positions : {imported} nouvelle(s) position(s) ajoutée(s)"
        )

        equity = broker.get_account_equity()
        positions = broker.get_open_positions()
        logger.info(f"  💼 Equity Alpaca : ${equity:,.2f}")
        logger.info(f"  📦 Positions Alpaca ouvertes : {len(positions)}")
        logger.info("━" * 60)
        logger.info(
            "  Sync terminée. Consultez data/trade_journal.csv pour vérification."
        )

    except Exception as exc:
        logger.error(f"  ❌ Erreur sync Alpaca : {exc}")
