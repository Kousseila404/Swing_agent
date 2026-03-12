"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE — STRATEGY FACTORY V7 (TITAN COGNITIVE — ADX PROFILING) ║
║  Orchestrateur intelligent : deux niveaux de sélection.         ║
║                                                                  ║
║  Niveau 1 — Profilage comportemental (ADX Asset Profiling) :    ║
║    ADX moyen > 25 → "MOMENTUM"  : moteurs directionnels only    ║
║    ADX moyen ≤ 25 → "MEAN REVERSION" : oscillateurs only        ║
║                                                                  ║
║  Niveau 2 — Filtre macro-directionnel :                         ║
║    BEAR + ticker sous EMA50 → override BEAR_DIRECTIONAL         ║
║    (même en MEAN REVERSION : le ticker chute structurellement)  ║
║                                                                  ║
║  Pourquoi l'ADX est le meilleur profileur :                     ║
║    - L'ADX mesure la FORCE de la tendance, pas sa direction.    ║
║    - ADX > 25 : le prix évolue en tendance → Mean Reversion     ║
║      génère des pertes (on "achète des couteaux qui tombent").  ║
║    - ADX ≤ 25 : le prix oscille → les stratégies de tendance    ║
║      génèrent des faux signaux et des whipsaws.                  ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.log import logger


# ─────────────────────────────────────────────────────────────────
# CONSTANTES DE PROFILAGE
# ─────────────────────────────────────────────────────────────────

ADX_THRESHOLD = 25    # Au-dessus : MOMENTUM. En dessous : MEAN_REVERSION.
ADX_LOOKBACK  = 50    # Nombre de bougies pour calculer l'ADX moyen (≈7j en 1H)


class StrategyFactory:
    """
    Sélectionne les archétypes de stratégies à tester lors de l'optimisation.

    Logique de routage (V7) :
      1. CRASH_PANIC → aucune stratégie (MacroEngine bloque en amont)
      2. ADX > 25 (MOMENTUM) → trend_breakout + trend_following uniquement
      3. ADX ≤ 25 (MEAN_REVERSION) → mean_reversion + breakout uniquement
         Exception : si BEAR_MARKET + sous EMA50 → BEAR_DIRECTIONAL
         (le ticker chute structurellement, même un range finit par craquer)
      4. ADX indisponible → fallback sur la logique V6 (régime + EMA50)
    """

    # Catalogues de stratégies
    ALL_STRATEGIES      = ["mean_reversion", "trend_following", "breakout", "trend_breakout"]
    MOMENTUM_STRATEGIES = ["trend_breakout", "trend_following"]   # ADX > 25
    RANGING_STRATEGIES  = ["mean_reversion", "breakout"]          # ADX ≤ 25
    BEAR_DIRECTIONAL    = ["trend_breakout", "trend_following"]   # BEAR + sous EMA50
    BULL_STRATEGIES     = ["mean_reversion", "trend_following", "breakout"]

    # ─────────────────────────────────────────────────────────────
    # PROFILEUR ADX
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def get_ticker_profile(
        df: pd.DataFrame,
        lookback: int = ADX_LOOKBACK,
    ) -> tuple[str, float]:
        """
        Détermine le profil comportemental du ticker via l'ADX moyen.

        L'ADX (Average Directional Index) mesure la FORCE de la tendance
        sans se préoccuper de sa direction (haussière ou baissière).

        Args:
            df:       DataFrame avec la colonne "ADX" (calculée par compute_indicators).
            lookback: Nombre de bougies récentes pour calculer l'ADX moyen.
                      En 1H : 50 bougies ≈ 7 jours de trading.

        Returns:
            Tuple (profile, avg_adx) :
              - profile  = "MOMENTUM" | "MEAN_REVERSION" | "UNKNOWN"
              - avg_adx  = valeur float de l'ADX moyen (np.nan si indisponible)
        """
        if "ADX" not in df.columns:
            return "UNKNOWN", np.nan

        adx_series = df["ADX"].dropna()
        if adx_series.empty:
            return "UNKNOWN", np.nan

        # Moyenne sur les N dernières bougies disponibles
        recent = adx_series.iloc[-lookback:] if len(adx_series) >= lookback else adx_series
        avg_adx = float(recent.mean())

        profile = "MOMENTUM" if avg_adx > ADX_THRESHOLD else "MEAN_REVERSION"
        return profile, avg_adx

    # ─────────────────────────────────────────────────────────────
    # AIGUILLEUR PRINCIPAL
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def select_strategies(
        ticker: str,
        df: pd.DataFrame,
        macro_regime: str,
    ) -> list[str]:
        """
        Retourne la liste des stratégies à tester pour ce ticker/régime.

        Priorité de la décision :
          1. CRASH_PANIC → vide (capital protégé)
          2. ADX profile → filtre primaire (TYPE de stratégie)
          3. EMA50 + BEAR → override pour chutes structurelles
          4. Fallback V6 si ADX indisponible

        Args:
            ticker:       Symbole yfinance — utilisé pour le logging.
            df:           DataFrame avec indicateurs pré-calculés.
                          Doit inclure "ADX" et "EMA50" (compute_indicators V7).
            macro_regime: "BULL_MARKET" | "BEAR_MARKET" | "CRASH_PANIC".

        Returns:
            Sous-liste de ALL_STRATEGIES adaptée au contexte.
        """
        # ── Règle 0 : Protection capital ───────────────────────────
        if macro_regime == "CRASH_PANIC":
            logger.info(f"[{ticker}] CRASH_PANIC → aucune stratégie sélectionnée")
            return []

        # ── Niveau 1 : Profilage ADX ────────────────────────────────
        adx_profile, avg_adx = StrategyFactory.get_ticker_profile(df)

        # ── Niveau 2 : Position par rapport à l'EMA50 ──────────────
        below_ema50 = False
        if "EMA50" in df.columns:
            ema50_series = df["EMA50"].dropna()
            if not ema50_series.empty:
                ema50_last = float(ema50_series.iloc[-1])
                close_last = float(df["Close"].iloc[-1])
                below_ema50 = close_last < ema50_last

        # ── Routing ─────────────────────────────────────────────────
        adx_label = f"ADX={avg_adx:.1f}" if not np.isnan(avg_adx) else "ADX=N/A"

        if adx_profile == "MOMENTUM":
            # Tendance forte confirmée : seuls les moteurs directionnels ont du sens.
            # Mean Reversion désactivée — on ne "pêche pas contre le courant".
            strategies = StrategyFactory.MOMENTUM_STRATEGIES
            logger.info(
                f"[{ticker}] {macro_regime} | {adx_label} > {ADX_THRESHOLD} (MOMENTUM) "
                f"→ moteurs directionnels : {strategies}"
            )

        elif adx_profile == "MEAN_REVERSION":
            # Marché en range : oscillateurs performants, tendance = whipsaws.
            # Exception : si BEAR structurel + sous EMA50, on force les directionnels
            # car même un range finit par céder en tendance baissière prolongée.
            if macro_regime == "BEAR_MARKET" and below_ema50:
                strategies = StrategyFactory.BEAR_DIRECTIONAL
                logger.info(
                    f"[{ticker}] {macro_regime} | {adx_label} ≤ {ADX_THRESHOLD} (RANGE) "
                    f"mais BEAR + sous EMA50 → override directionnel : {strategies}"
                )
            else:
                strategies = StrategyFactory.RANGING_STRATEGIES
                logger.info(
                    f"[{ticker}] {macro_regime} | {adx_label} ≤ {ADX_THRESHOLD} (MEAN_REVERSION) "
                    f"→ oscillateurs : {strategies}"
                )

        else:
            # ADX indisponible → fallback sur la logique V6 (régime + EMA50)
            if macro_regime == "BEAR_MARKET" and below_ema50:
                strategies = StrategyFactory.BEAR_DIRECTIONAL
                logger.info(
                    f"[{ticker}] {macro_regime} | ADX indisponible + sous EMA50 "
                    f"→ fallback BEAR_DIRECTIONAL : {strategies}"
                )
            elif macro_regime == "BULL_MARKET":
                strategies = StrategyFactory.BULL_STRATEGIES
                logger.info(
                    f"[{ticker}] {macro_regime} | ADX indisponible "
                    f"→ fallback BULL : {strategies}"
                )
            else:
                strategies = StrategyFactory.ALL_STRATEGIES
                logger.info(
                    f"[{ticker}] {macro_regime} | ADX indisponible (contexte mixte) "
                    f"→ toutes les stratégies : {strategies}"
                )

        return strategies
