"""CLI handlers divers TITAN — n'expose plus que `run_macro_mode`.

Les modes `run_update_cache_mode` et `run_report_mode` ont été retirés
lors du pivot quantamental (scanner/backtester legacy supprimés).
"""
from __future__ import annotations

from modules.log import logger


def run_macro_mode() -> None:
    """Affiche le régime macro-économique actuel avec toutes les métriques."""
    from modules.macro_engine import get_regime_details

    logger.info("=" * 60)
    logger.info("📊 MODE MACRO — Régime Macro-Économique Actuel")
    logger.info("=" * 60)

    try:
        details = get_regime_details()
        regime  = details.get("regime", "INCONNU")
        emoji   = details.get("emoji", "⚪")

        delta   = details.get("sp500_vs_ema200_pct")
        allowed = details.get("allowed_directions", [])
        logger.info("═" * 55)
        logger.info(f"  {emoji}  RÉGIME MACRO : {regime}")
        logger.info("═" * 55)
        logger.info(f"  S&P 500    : {details.get('sp500', 'N/A'):>12}")
        logger.info(f"  EMA 200j   : {details.get('ema200', 'N/A'):>12}")
        logger.info(
            f"  Δ EMA200   : "
            f"{f'{delta:+.1f}%' if delta is not None else 'N/A':>12}"
        )
        logger.info(f"  VIX        : {details.get('vix', 'N/A'):>12}")
        logger.info(
            f"  Directions : "
            f"{', '.join(allowed) if allowed else 'AUCUNE (CRASH PANIC)':>12}"
        )
        logger.info(f"  ℹ️  {details.get('description', '')}")
        logger.info("═" * 55)

    except Exception as e:
        logger.error(f"Erreur MacroEngine : {e}")
