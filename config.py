"""
╔══════════════════════════════════════════════════════════════════╗
║  SWING QUANT V5 — LE TITAN — CONFIGURATION                      ║
║  Univers Multi-Actifs | Moteur Long/Short | Filtre Macro VIX    ║
╚══════════════════════════════════════════════════════════════════╝
"""

# ─────────────────────────────────────────────────────────────────
# 1. CLÉS API  (à remplir avant le premier lancement)
# ─────────────────────────────────────────────────────────────────
import logging
import os
from pathlib import Path
from dotenv import load_dotenv

_cfg_logger = logging.getLogger("Config")

# Charge le .env depuis le répertoire du fichier config.py (chemin absolu)
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# API LLM — Anthropic (Claude)
LLM_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ── Vérification des clés au démarrage (loguée, pas printée) ─────────────────
def _check_env() -> None:
    keys = {
        "ANTHROPIC_API_KEY": LLM_API_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }
    for name, val in keys.items():
        if val:
            _cfg_logger.debug(f"[ENV] {name} → OK")
        else:
            _cfg_logger.warning(f"[ENV] {name} → MANQUANT ⚠️")

_check_env()
LLM_MODEL   = "claude-sonnet-4-20250514"

# ─────────────────────────────────────────────────────────────────
# 2. UNIVERS MULTI-ACTIFS (US Tech + Crypto)
# ─────────────────────────────────────────────────────────────────
# Format yfinance : tickers US natifs + suffixe -USD pour Crypto
TICKERS: dict[str, list[str]] = {
    "US_TECH": [
        "NVDA",    # NVIDIA — Leader Semiconducteurs IA
        "TSLA",    # Tesla — EV + Energie
        "PLTR",    # Palantir — Data / IA gouvernementale
        "MSTR",    # MicroStrategy — Proxy Bitcoin
        "META",    # Meta Platforms — Réseaux sociaux / Metavers
        "COIN",    # Coinbase — Exchange Crypto coté
    ],
    "CRYPTO": [
        "BTC-USD",  # Bitcoin
        "ETH-USD",  # Ethereum
        "SOL-USD",  # Solana
    ],
}

# Liste aplatie — rétrocompatibilité avec les modules qui itèrent sur les tickers
TICKERS_FLAT: list[str] = [t for tickers in TICKERS.values() for t in tickers]

# ─────────────────────────────────────────────────────────────────
# 3. MOTEUR LONG / SHORT
# ─────────────────────────────────────────────────────────────────
ALLOW_SHORTS = True   # Active les positions Short (vente à découvert)

# ─────────────────────────────────────────────────────────────────
# 4. INDICES MACRO-ÉCONOMIQUES
# ─────────────────────────────────────────────────────────────────
MACRO_INDEX = "^GSPC"   # S&P 500 — Référence du régime macro
VIX_INDEX   = "^VIX"    # CBOE Volatility Index — Indice de peur

# ─────────────────────────────────────────────────────────────────
# 5. PARAMÈTRES DU SCANNER (seuils de détection)
# ─────────────────────────────────────────────────────────────────
VOLUME_AVG_PERIOD    = 20      # Jours pour la moyenne de volume
VOLUME_SPIKE_RATIO   = 0.5     # Volume > X * moyenne 20j

RSI_PERIOD           = 14      # Période standard RSI
RSI_OVERSOLD         = 50      # Seuil de sur-vente (signal Long)
RSI_OVERBOUGHT       = 50      # Seuil de sur-achat (signal Short)

LOOKBACK_DAYS        = 60      # Historique minimum pour indicateurs

# ─────────────────────────────────────────────────────────────────
# 6. PARAMÈTRES DE RISQUE
# ─────────────────────────────────────────────────────────────────
CAPITAL              = 10_000  # Capital total en dollars/euros
MAX_RISK_PER_TRADE   = 0.01    # 1% du capital max par trade (Risk Parity V8)
MAX_POSITIONS        = 5       # Nombre max de positions simultanées

# V17 — Prop Firm Risk Management (FTMO / TopStep 100k)
TOTAL_CAPITAL        = 100_000 # Capital total du compte Prop Firm (USD)
RISK_PER_TRADE       = 0.0025  # 0.25% du capital risqué par trade (= 250$ sur 100k)

# ─────────────────────────────────────────────────────────────────
# 7. LOGGING
# ─────────────────────────────────────────────────────────────────
LOG_FILE  = "logs/agent.log"
LOG_LEVEL = "INFO"

# ─────────────────────────────────────────────────────────────────
# 8. BACKTESTER — PARAMÈTRES DE RÉALISME MARCHÉ
# ─────────────────────────────────────────────────────────────────
COMMISSION_PCT          = 0.001    # 0.1% par exécution (entrée ET sortie)
SLIPPAGE_PCT            = 0.0005   # 0.05% de slippage par exécution
MIN_VOLUME_FILTER       = 50_000   # Volume moyen 5j minimum (liquidité)
BACKTEST_YEARS          = 5        # Années d'historique pour le backtest

# Walk-Forward Optimization
WALK_FORWARD_WINDOWS    = 3        # Nombre de fenêtres glissantes
RISK_FREE_RATE          = 0.03     # Taux sans risque annuel (Rf = 3%)
MIN_TRADES_FOR_CONFIDENCE = 30     # Trades min en validation pour score fiable
