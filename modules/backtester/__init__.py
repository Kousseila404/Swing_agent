"""
modules/backtester — Package Backtesting Swing Quant
======================================================
Ce package contient deux moteurs de backtest complémentaires :

  legacy.py    — Backtester V4/V8 original (itératif, barre par barre).
                  Calcule EMA / RSI / ATR / ADX / BB / MACD en Pandas pur
                  (zéro pandas_ta). Chargé systématiquement au démarrage.

  vector_engine.py — Moteur V11 (vectorbt + Optuna).
                      Nécessite vectorbt + numba — chargement DIFFÉRÉ.
                      Invoqué uniquement via `--vector-backtest` dans main.py.
                      Si vectorbt n'est pas disponible, l'import est silencieux
                      et seul ce mode échouera (le scanner reste fonctionnel).
"""

# ── Exports V4/V8 legacy ────────────────────────────────────────────────────
# Chargé à chaque démarrage. N'importe plus pandas_ta ni vectorbt.
# Requis par : ticker_profiles, strategy_optimizer, backtest_report, main.py.
from modules.backtester.legacy import (
    BacktestParams,
    BacktestResult,
    BacktestStats,
    TradeResult,
    compute_indicators,
    download_data,
    run_backtest,
)

# ── Exports V11 vectorisé (chargement différé — vectorbt/numba) ─────────────
# vectorbt dépend de numba, qui peut provoquer des conflits JIT au démarrage.
# On l'isole dans un try/except : si l'import échoue, seul --vector-backtest
# sera indisponible. Le scanner, le portfolio et le backtest legacy restent OK.
try:
    from modules.backtester.vector_engine import (
        load_cache_data,
        run_vectorbt_backtest,
        run_optimization,
    )
    _VECTOR_ENGINE_AVAILABLE = True
except Exception as _vbt_err:  # noqa: BLE001
    import warnings as _w
    _w.warn(
        f"[backtester] vector_engine non disponible ({_vbt_err}). "
        "Le mode --vector-backtest sera désactivé.",
        ImportWarning,
        stacklevel=2,
    )
    _VECTOR_ENGINE_AVAILABLE = False

    # Stubs pour éviter les NameError si quelqu'un tente d'importer ces noms
    def load_cache_data(*_a, **_kw):  # type: ignore[misc]
        raise RuntimeError("vector_engine non disponible — installez vectorbt.")

    def run_vectorbt_backtest(*_a, **_kw):  # type: ignore[misc]
        raise RuntimeError("vector_engine non disponible — installez vectorbt.")

    def run_optimization(*_a, **_kw):  # type: ignore[misc]
        raise RuntimeError("vector_engine non disponible — installez vectorbt.")


__all__ = [
    # V4/V8 legacy (toujours disponible)
    "BacktestParams",
    "BacktestResult",
    "BacktestStats",
    "TradeResult",
    "compute_indicators",
    "download_data",
    "run_backtest",
    # V11 vectorisé (disponible si vectorbt est installé)
    "load_cache_data",
    "run_vectorbt_backtest",
    "run_optimization",
    "_VECTOR_ENGINE_AVAILABLE",
]
