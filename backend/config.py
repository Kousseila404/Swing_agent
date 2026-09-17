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

# URL publique du frontend déployé (ex: https://titan.ton-domaine.com/).
# Optionnel — sert uniquement à construire un lien cliquable dans le digest
# Telegram (Étape 3 roadmap). Vide → le digest omet le lien sans erreur.
FRONTEND_URL = os.getenv("FRONTEND_URL", "").rstrip("/")

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
# Audit 2026-09-17 — activation 8 → 15 %, lock 40 → 30 %, ATR 2.5/3.0 → 4.0/4.0.
# Sur les 9 trades clos système : WIN moyen +3,1 % (CF/INCY/NEM/EOG coupés à
# +2,4…+4,5 % par le TS à 8 %/40 %) puis rachetés 2–3 semaines plus tard,
# LOSS moyen −16 %. Avec des TP à +100 % et un plancher −35 %, verrouiller
# 40 % d'un gain de 8 % (= +3 %) contredit la thèse LT : le TS ne doit
# protéger que des gains substantiels (≥ 15 %) et laisser respirer (30 %).
TRAILING_STOP_ACTIVATION_PCT = 15.0  # plafond du seuil d'activation (sera utilisé
                                      # si TP_distance × RATIO > 15 %)
TRAILING_STOP_ACTIVATION_RATIO = 0.5  # ratio du TP à partir duquel activer le trailing.
                                      # TP +100 % → 50 % → capé à 15 %.
TRAILING_STOP_LOCK_PCT       = 0.30  # verrouille 30 % du gain sous le SL
#
# Mode ATR-adaptatif (prioritaire si OHLCV dispo) :
# ATR calculé sur 14 j → court terme ; on compense par un multiplicateur large
# pour coller à un hold LT.
# Audit 2026-05-12 — ATR_ACTIVATION_MULT 3.5 → 2.5. Sur σ-30 %, ATR≈$2 →
# 3.5×ATR=$7=+6 % → quasi équivalent à l'ancien plancher 15 %, donc TS ATR
# rarement actif. 2.5×ATR cohérent avec activation_pct=8 %.
TRAILING_STOP_ATR_ACTIVATION_MULT = 4.0   # active si profit $ ≥ 4 × ATR (ET ≥ 15 %)
TRAILING_STOP_ATR_TRAIL_MULT      = 4.0   # SL = prix − 4 × ATR (buffer LT vs bruit)

# ─────────────────────────────────────────────────────────────────
# 5ter. BEAR HEDGE (modules/bear_hedge.py) — audit 2026-05-12
# ─────────────────────────────────────────────────────────────────
# Quand confirmed_regime ∈ {BEAR_MARKET, CRASH_PANIC} pendant ≥ MIN_DAYS jours,
# le module ouvre une position LONG sur SH (ProShares Short S&P500 1×) à
# hauteur de PCT_OF_BOOK du capital. Inverse-ETF = hedge directionnel simple,
# pas d'options, pas de short véritable (compatible PEA/CTO sans marge).
#
# Activé 2026-05-12 (avec confirmed_regime=BULL_MARKET stable, pas d'effet
# immédiat — le module ne déclenchera OPEN que si BEAR_MARKET ou CRASH_PANIC
# pendant ≥ BEAR_HEDGE_MIN_DAYS jours consécutifs).
BEAR_HEDGE_ENABLED      = True
BEAR_HEDGE_TICKER       = "SH"     # ProShares Short S&P500 — alternative : SDS (2×)
BEAR_HEDGE_PCT_OF_BOOK  = 0.10     # 10 % du capital total
BEAR_HEDGE_MIN_DAYS     = 3        # confirmation N-jours (anti-whipsaw)

# ─────────────────────────────────────────────────────────────────
# 5quater. KILLSWITCH & CIRCUIT BREAKER — recalibrés LT (audit 2026-09-17)
# ─────────────────────────────────────────────────────────────────
# Avant : drawdown journalier ≤ −4 % → liquidation de TOUTES les positions
# (« nuclear stop »). Incohérent avec une détention 60 j+ : une séance S&P à
# −4 % vendait tout au pire moment. Maintenant :
#   • KILLSWITCH_ACTION = "freeze" → gel des nouvelles entrées + alerte, les
#     stops individuels restent la seule cause de sortie. "liquidate" = ancien
#     comportement (à réserver à un compte levier/short).
#   • Seuil relevé à −6 % (VIX 30+ : le book entier peut bouger de 4 % en une
#     séance sans que la thèse d'aucune position ne soit cassée).
KILLSWITCH_ACTION       = os.getenv("KILLSWITCH_ACTION", "freeze")
MAX_DAILY_DRAWDOWN_PCT  = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "6.0"))

# Circuit breaker progressif (modules/risk.DrawdownCircuitBreaker) : le peak
# est désormais échantillonné **1×/jour** (pas 1×/cycle 2 min, cf. cycle.py)
# sur une fenêtre roulante de 60 séances, avec des seuils de drawdown LT :
#   −8 % → nouvelles positions à 75 % ; −12 % → 50 % ; −16 % → pause 5 séances.
CB_ROLLING_WINDOW_DAYS  = 60
CB_DD_REDUCE_75_PCT     = -8.0
CB_DD_REDUCE_50_PCT     = -12.0
CB_DD_PAUSE_PCT         = -16.0
CB_PAUSE_DAYS           = 5

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
