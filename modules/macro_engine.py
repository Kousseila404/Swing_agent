"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE — MACRO ENGINE V1                                       ║
║  Détermination du Régime Macro-Économique Global                ║
║                                                                  ║
║  Sources :                                                       ║
║    ^GSPC  → S&P 500 (tendance macro, EMA 200 jours)             ║
║    ^VIX   → CBOE Volatility Index (peur du marché)              ║
║                                                                  ║
║  Régimes :                                                       ║
║    BULL_MARKET  → S&P500 > EMA200 ET VIX < 25                  ║
║                   Autorise uniquement les trades LONG            ║
║    BEAR_MARKET  → S&P500 < EMA200 ET VIX < 35                  ║
║                   Autorise uniquement les trades SHORT           ║
║    CRASH_PANIC  → VIX ≥ 35 (panique extrême)                   ║
║                   BLOQUE tous les trades — protection capital    ║
║                                                                  ║
║  Anti-biais strict :                                             ║
║    - Utilise uniquement la dernière barre COMPLÈTE (no look-     ║
║      ahead), calculée après la fermeture du marché.             ║
║    - Alignement date-par-date des séries VIX / S&P500           ║
║      via inner join sur l'index commun.                          ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

import config
from modules.log import logger


# ─────────────────────────────────────────────────────────────────
# CACHE EN MÉMOIRE  (évite les appels API répétés dans le dashboard)
# ─────────────────────────────────────────────────────────────────

_REGIME_CACHE: dict = {}
_CACHE_TTL_SECONDS = 4 * 3600   # 4 heures


# ─────────────────────────────────────────────────────────────────
# TÉLÉCHARGEMENT DES DONNÉES MACRO
# ─────────────────────────────────────────────────────────────────

def _download_macro_data(years: int = 2) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """
    Télécharge les données historiques du S&P500 et du VIX.

    On télécharge 2 ans pour disposer d'au moins 200 barres de trading
    nécessaires au calcul de l'EMA 200 jours du S&P500.

    Anti-biais : on télécharge jusqu'à datetime.now().
    La sélection de la "dernière barre complète" est faite dans
    _align_and_compute() via iloc[-1] — jamais via une date future.

    Returns:
        Tuple (df_sp500, df_vix) — ou (None, None) si erreur réseau.
    """
    end   = datetime.now()
    start = end - timedelta(days=years * 365 + 90)  # +90j de marge pour l'EMA200

    try:
        df_sp = yf.Ticker(config.MACRO_INDEX).history(
            start=start, end=end, auto_adjust=True
        )
        if df_sp is None or len(df_sp) == 0:
            logger.error(f"[MacroEngine] Données {config.MACRO_INDEX} indisponibles")
            return None, None
        df_sp.index = pd.to_datetime(df_sp.index).tz_localize(None)
        df_sp = df_sp.dropna(subset=["Close"])
    except Exception as e:
        logger.error(f"[MacroEngine] Erreur téléchargement {config.MACRO_INDEX} : {e}")
        return None, None

    try:
        df_vix = yf.Ticker(config.VIX_INDEX).history(
            start=start, end=end, auto_adjust=True
        )
        if df_vix is None or len(df_vix) == 0:
            logger.error(f"[MacroEngine] Données {config.VIX_INDEX} indisponibles")
            return None, None
        df_vix.index = pd.to_datetime(df_vix.index).tz_localize(None)
        df_vix = df_vix.dropna(subset=["Close"])
    except Exception as e:
        logger.error(f"[MacroEngine] Erreur téléchargement {config.VIX_INDEX} : {e}")
        return None, None

    return df_sp, df_vix


# ─────────────────────────────────────────────────────────────────
# ALIGNEMENT & CALCUL DE L'EMA 200
# ─────────────────────────────────────────────────────────────────

def _align_and_compute(
    df_sp: pd.DataFrame,
    df_vix: pd.DataFrame,
) -> Tuple[float, float, float]:
    """
    Aligne les séries S&P500 et VIX sur leurs dates communes,
    calcule l'EMA 200 jours sur le S&P500, et extrait les 3 valeurs
    critiques de la dernière barre complète.

    Alignement par inner join sur l'index de date :
      - Les jours fériés US peuvent décaler VIX/SP500 d'un jour.
      - L'inner join garantit qu'on ne compare que des barres simultanées.
      - Aucun NaN résiduel n'entre dans la prise de décision.

    Anti-biais strict :
      - iloc[-1] = dernière barre complète (clôture passée connue).
      - On n'utilise pas shift(1) car la dernière barre est déjà formée.

    Args:
        df_sp:  DataFrame S&P500 avec colonne "Close".
        df_vix: DataFrame VIX avec colonne "Close".

    Returns:
        Tuple (sp500_close, ema200_value, vix_close) — tous positifs.

    Raises:
        ValueError: Si les données alignées sont insuffisantes.
    """
    # Calcul de l'EMA200 sur la série complète du S&P500 (Pandas natif)
    ema200_series = df_sp["Close"].ewm(span=200, adjust=False).mean()

    if ema200_series.dropna().empty:
        raise ValueError(
            "[MacroEngine] Impossible de calculer l'EMA200 — "
            "données S&P500 insuffisantes (besoin de 200+ barres)"
        )

    # Construction d'un DataFrame combiné avec inner join sur les dates
    df_combined = (
        pd.DataFrame({"sp500": df_sp["Close"], "ema200": ema200_series})
        .join(pd.DataFrame({"vix": df_vix["Close"]}), how="inner")
        .dropna()
    )

    if len(df_combined) < 10:
        raise ValueError(
            f"[MacroEngine] Seulement {len(df_combined)} barres après alignement "
            "SP500/VIX — données insuffisantes pour le régime."
        )

    # Lecture de la DERNIÈRE barre complète (aucun look-ahead)
    last = df_combined.iloc[-1]
    sp500 = float(last["sp500"])
    ema200 = float(last["ema200"])
    vix    = float(last["vix"])

    return sp500, ema200, vix


# ─────────────────────────────────────────────────────────────────
# FONCTION PRINCIPALE — RÉGIME MACRO
# ─────────────────────────────────────────────────────────────────

def get_market_regime(use_cache: bool = True) -> str:
    """
    Détermine et retourne le Régime Macro-Économique Actuel.

    Logique de décision (ordre de priorité strict) :
      1. VIX ≥ 35                        → CRASH_PANIC  (aucun trade)
      2. S&P500 > EMA200 ET VIX < 25    → BULL_MARKET  (longs uniquement)
      3. S&P500 < EMA200 ET VIX < 35    → BEAR_MARKET  (shorts uniquement)
      4. Zone ambiguë (SP>EMA200, 25≤VIX<35) → BEAR_MARKET  (prudence max)

    Args:
        use_cache: Si True (défaut), utilise le cache mémoire de 4h
                   pour éviter des appels API répétés depuis le dashboard.

    Returns:
        "BULL_MARKET" | "BEAR_MARKET" | "CRASH_PANIC"
    """
    # ── Vérification du cache ──────────────────────────────────────
    if use_cache:
        cached = _REGIME_CACHE.get("regime")
        if cached:
            ts, regime = cached
            if time.time() - ts < _CACHE_TTL_SECONDS:
                logger.debug(f"[MacroEngine] Régime depuis cache : {regime}")
                return regime

    try:
        df_sp, df_vix = _download_macro_data(years=2)

        if df_sp is None or df_vix is None:
            logger.warning(
                "[MacroEngine] Données macro indisponibles — "
                "BEAR_MARKET activé par défaut (fail-safe)"
            )
            return "BEAR_MARKET"

        sp500, ema200, vix = _align_and_compute(df_sp, df_vix)

        logger.info(
            f"[MacroEngine] S&P500={sp500:,.2f} | EMA200={ema200:,.2f} | "
            f"VIX={vix:.2f} | Δ EMA={(sp500 / ema200 - 1) * 100:+.1f}%"
        )

        # ── Règle 1 : Protection absolue du capital ───────────────
        if vix >= 35.0:
            regime = "CRASH_PANIC"

        # ── Règle 2 : Marché haussier sain ───────────────────────
        elif sp500 > ema200 and vix < 25.0:
            regime = "BULL_MARKET"

        # ── Règle 3 : Marché baissier structurel ─────────────────
        elif sp500 < ema200 and vix < 35.0:
            regime = "BEAR_MARKET"

        # ── Règle 4 : Zone ambiguë (SP>EMA200 mais VIX entre 25-35)
        #    → Prudence maximale : on traite comme BEAR
        else:
            regime = "BEAR_MARKET"

        logger.info(f"[MacroEngine] 📊 Régime détecté : ✦ {regime} ✦")

        # ── Mise en cache ──────────────────────────────────────────
        if use_cache:
            _REGIME_CACHE["regime"] = (time.time(), regime)

        return regime

    except Exception as e:
        logger.error(f"[MacroEngine] Erreur critique : {e}", exc_info=True)
        # Fail-safe : BEAR_MARKET = pas de longs incontrôlés en cas d'erreur
        return "BEAR_MARKET"


# ─────────────────────────────────────────────────────────────────
# DÉTAILS COMPLETS DU RÉGIME (pour le Dashboard)
# ─────────────────────────────────────────────────────────────────

def get_regime_details() -> dict:
    """
    Retourne un dictionnaire complet des métriques macro pour
    l'affichage dans le Dashboard Streamlit.

    Returns:
        Dict contenant :
          - regime             : "BULL_MARKET" | "BEAR_MARKET" | "CRASH_PANIC"
          - sp500              : Dernière clôture S&P500
          - ema200             : Valeur EMA 200j du S&P500
          - sp500_vs_ema200_pct: Écart relatif S&P500/EMA200 en %
          - vix                : Dernière clôture VIX
          - description        : Texte descriptif du régime
          - allowed_directions : Liste ["LONG"] | ["SHORT"] | []
          - emoji              : Emoji représentatif du régime
    """
    _REGIME_DESCRIPTIONS = {
        "BULL_MARKET": (
            "S&P500 au-dessus de l'EMA200, volatilité maîtrisée (VIX<25). "
            "Tendance haussière confirmée — Longs autorisés uniquement."
        ),
        "BEAR_MARKET": (
            "S&P500 sous l'EMA200 ou VIX élevé. "
            "Pression vendeuse structurelle — Shorts autorisés uniquement."
        ),
        "CRASH_PANIC": (
            "VIX ≥ 35 — Panique extrême sur les marchés. "
            "TOUS les trades sont bloqués — Protection du capital activée."
        ),
    }
    _REGIME_EMOJIS = {
        "BULL_MARKET": "🟢",
        "BEAR_MARKET": "🔴",
        "CRASH_PANIC": "🚨",
    }
    _ALLOWED_DIRECTIONS = {
        "BULL_MARKET": ["LONG"],
        "BEAR_MARKET": ["SHORT"],
        "CRASH_PANIC": [],
    }

    try:
        df_sp, df_vix = _download_macro_data(years=2)

        if df_sp is None or df_vix is None:
            return _fallback_regime_details()

        sp500, ema200, vix = _align_and_compute(df_sp, df_vix)

        # Appel sans cache pour avoir les valeurs fraîches
        regime = get_market_regime(use_cache=False)

        return {
            "regime":              regime,
            "sp500":               round(sp500, 2),
            "ema200":              round(ema200, 2),
            "sp500_vs_ema200_pct": round((sp500 / ema200 - 1) * 100, 2),
            "vix":                 round(vix, 2),
            "description":         _REGIME_DESCRIPTIONS.get(regime, "Régime inconnu."),
            "allowed_directions":  _ALLOWED_DIRECTIONS.get(regime, []),
            "emoji":               _REGIME_EMOJIS.get(regime, "⚪"),
        }

    except Exception as e:
        logger.error(f"[MacroEngine] get_regime_details() erreur : {e}")
        return _fallback_regime_details()


def _fallback_regime_details() -> dict:
    """Retourne des détails par défaut en cas d'erreur de téléchargement."""
    return {
        "regime":              "BEAR_MARKET",
        "sp500":               None,
        "ema200":              None,
        "sp500_vs_ema200_pct": None,
        "vix":                 None,
        "description":         "Données macro indisponibles — mode prudent activé par défaut.",
        "allowed_directions":  ["SHORT"],
        "emoji":               "⚠️",
    }
