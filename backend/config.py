"""
╔══════════════════════════════════════════════════════════════════╗
║  SWINGQUANT TITAN — CONFIGURATION                                ║
║  Quantamental Long-Term | Broker Alpaca | Macro Regime           ║
╚══════════════════════════════════════════════════════════════════╝

Config minimaliste post-pivot TITAN. Ne contient QUE les variables
consommées par le périmètre quantamental survivant :

  • Clés API (Telegram, Anthropic, Alpaca)
  • Capital / sizing par défaut (ACCOUNT_SIZE, RISK_PER_TRADE)
  • Seuils macro (VIX thresholds, indices de référence)
  • Slippage estimé (utils.estimate_adaptive_slippage)
  • Logging

Les paramètres swing legacy (RSI, trailing stop, backtester,
walk-forward, etc.) ont été supprimés lors du Sprint 2 (W1).
tracker.py conserve ses defaults hardcodés via getattr() pour
rester opérationnel sur le portefeuille résiduel.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

_cfg_logger = logging.getLogger("Config")

# Charge le .env depuis le répertoire du fichier config.py (chemin absolu)
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

# ─────────────────────────────────────────────────────────────────
# 1. CLÉS API
# ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# Anthropic (Claude) — analyse IA des candidats quantamentaux
LLM_API_KEY = os.getenv("ANTHROPIC_API_KEY")
LLM_MODEL   = "claude-sonnet-4-6"
ENABLE_AI_ANALYSIS = True   # Active les verdicts IA dans les alertes Telegram

# ─────────────────────────────────────────────────────────────────
# 2. BROKER (Alpaca Markets)
# ─────────────────────────────────────────────────────────────────
# BROKER_MODE : "paper"  → PaperBroker (CSV, zéro risque, défaut)
#               "alpaca" → AlpacaBroker (ordres réels Alpaca Markets)
BROKER_MODE       = os.getenv("BROKER_MODE", "paper")
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
# Paper  : https://paper-api.alpaca.markets
# Live   : https://api.alpaca.markets
ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# ─────────────────────────────────────────────────────────────────
# 3. CAPITAL & SIZING
# ─────────────────────────────────────────────────────────────────
# Capital de référence pour les alertes et le fallback broker quand
# aucun snapshot equity_state n'est disponible.
ACCOUNT_SIZE     = 100_000    # USD
# Risque par trade utilisé comme valeur par défaut dans kelly_rolling().
RISK_PER_TRADE   = 0.0025     # 0.25 %

# ─────────────────────────────────────────────────────────────────
# 4. RÉGIME MACRO (MacroEngine)
# ─────────────────────────────────────────────────────────────────
MACRO_INDEX = "^GSPC"   # S&P 500 — référence du régime macro
VIX_INDEX   = "^VIX"    # CBOE Volatility Index
VIX_BULL_MAX  = 30.0    # VIX < 30 → zone BULL
VIX_PANIC_MIN = 35.0    # VIX ≥ 35 → CRASH_PANIC (aucun trade)
REGIME_CONFIRMATION_DAYS = 3   # Jours consécutifs avant de valider un changement

# ─────────────────────────────────────────────────────────────────
# 5. SLIPPAGE (utils.estimate_adaptive_slippage)
# ─────────────────────────────────────────────────────────────────
SLIPPAGE_PCT = 0.0005   # 0.05 % — plancher pour l'estimation adaptive

# ─────────────────────────────────────────────────────────────────
# 5bis. TIME EXIT + TRAILING STOP (modules/tracker/evaluation.py)
# ─────────────────────────────────────────────────────────────────
# Defaults V4.1 Long-Term (2026-04-23) — recalibrés sur la thèse Quantamental
# Long-Term. V3 (activation 3 %, lock 50 %, ATR 1.5×/1.0×, hold 10 j) → 6/7
# "WIN" = TS touché sur retracement intraday à +1–2 %, aucun TP atteint.
# V4 swing (8 %, 0.35, 2.5×/2.0×) était déjà trop serré pour du LT.
# V4.1 LT : laisse une position mûrir 2 mois et ne verrouille le SL qu'à
# +15 % de profit (~1 σ-horizon 30 j), pour permettre aux TP +20–40 % de
# cuire sans être étouffés par le bruit intraday.

# Durée max de détention avant time exit forcé (WIN/LOSS selon PnL instantané).
# 60j = 2 mois, horizon moyen de convergence d'une thèse fondamentale.
MAX_HOLDING_DAYS = 60

# Mode % fixe (fallback si pas d'OHLCV) :
# Audit 2026-05-12 — activation 15 → 8 %, lock 25 → 40 %.
# V4.1 LT (15 %/25 %) était trop conservateur pour des positions LT 60j :
# sur des tickers σ-30 % qui plafonnent à +5-15 %, le TS ne s'activait
# JAMAIS et les gains se rendaient en retournement. 8 % active le TS plus
# tôt et 40 % du gain locké donne un buffer raisonnable (vs 25 %
# qui équivalait à un quasi-breakeven au moment de l'activation).
TRAILING_STOP_ACTIVATION_PCT = 8.0   # plafond du seuil d'activation (sera utilisé
                                      # si TP_distance × RATIO > 8 %)
TRAILING_STOP_ACTIVATION_RATIO = 0.5  # ratio du TP à partir duquel activer le trailing.
                                      # Avec RATIO=0.5, un TP +16 % active le TS à +8 %.
                                      # TP +30 % → TS activé à +15 % (cap 8 %=floor).
TRAILING_STOP_LOCK_PCT       = 0.40  # verrouille 40 % du gain sous le SL
#
# Mode ATR-adaptatif (prioritaire si OHLCV dispo) :
# ATR calculé sur 14 j → court terme ; on compense par un multiplicateur large
# pour coller à un hold LT.
# Audit 2026-05-12 — ATR_ACTIVATION_MULT 3.5 → 2.5. Sur σ-30 %, ATR≈$2 →
# 3.5×ATR=$7=+6 % → quasi équivalent à l'ancien plancher 15 %, donc TS ATR
# rarement actif. 2.5×ATR cohérent avec activation_pct=8 %.
TRAILING_STOP_ATR_ACTIVATION_MULT = 2.5   # active si profit $ ≥ 2.5 × ATR
TRAILING_STOP_ATR_TRAIL_MULT      = 3.0   # SL = plus-haut − 3.0 × ATR (inchangé,
                                            # buffer LT vs noise intraday)

# ─────────────────────────────────────────────────────────────────
# 6. LOGGING
# ─────────────────────────────────────────────────────────────────
LOG_FILE  = "logs/agent.log"
LOG_LEVEL = "INFO"


# ─────────────────────────────────────────────────────────────────
# 7. VÉRIFICATION AU DÉMARRAGE
# ─────────────────────────────────────────────────────────────────

def _check_env() -> None:
    keys = {
        "ANTHROPIC_API_KEY":  LLM_API_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID":   TELEGRAM_CHAT_ID,
    }
    for name, val in keys.items():
        if val:
            _cfg_logger.debug(f"[ENV] {name} → OK")
        else:
            _cfg_logger.warning(f"[ENV] {name} → MANQUANT ⚠️")
    if BROKER_MODE == "alpaca":
        for name, val in [("ALPACA_API_KEY", ALPACA_API_KEY), ("ALPACA_SECRET_KEY", ALPACA_SECRET_KEY)]:
            if val:
                _cfg_logger.debug(f"[ENV] {name} → OK")
            else:
                _cfg_logger.error(f"[ENV] {name} → MANQUANT (requis pour BROKER_MODE=alpaca) ❌")


def _validate_config() -> None:
    """Vérifie la cohérence des paramètres critiques au démarrage.

    NB : n'est PAS appelée à l'import — l'appel à l'import-time propageait
    un ValueError avant que le logger de l'app soit initialisé (message
    perdu en prod). Les entry points (api.py lifespan, tracker.py, main.py)
    invoquent explicitement validate_startup().
    """
    errors = []
    if not (0 < RISK_PER_TRADE < 0.05):
        errors.append(f"RISK_PER_TRADE={RISK_PER_TRADE} doit être dans (0, 5%)")
    if ACCOUNT_SIZE <= 0:
        errors.append(f"ACCOUNT_SIZE={ACCOUNT_SIZE} doit être > 0")
    if VIX_BULL_MAX >= VIX_PANIC_MIN:
        errors.append(f"VIX_BULL_MAX={VIX_BULL_MAX} doit être < VIX_PANIC_MIN={VIX_PANIC_MIN}")
    if SLIPPAGE_PCT < 0:
        errors.append(f"SLIPPAGE_PCT={SLIPPAGE_PCT} doit être ≥ 0")
    if errors:
        for err in errors:
            _cfg_logger.error(f"[Config] ERREUR : {err}")
        raise ValueError(f"Configuration invalide ({len(errors)} erreur(s)): {'; '.join(errors)}")


def validate_startup() -> None:
    """Point d'entrée public : vérifie l'env + la cohérence des paramètres.

    À appeler depuis chaque entry point (api.py lifespan, tracker.py main,
    main.py CLI). Séparé de l'import-time pour que les erreurs remontent via
    le logger applicatif déjà configuré.
    """
    _check_env()
    _validate_config()
