"""Momentum math : conversion close series → (return, volatility, risk-adjusted).

Purement mathématique — aucun I/O, pas de cache, pas de provider. Les consumers
(compute_momentum_fresh, portfolio/_caches.refresh_momentum_live_cached) injectent
les séries pandas pré-fetchées.
"""
from __future__ import annotations

import math
from typing import Any

# Mapping GICS sector (tel que retourné par yfinance) → ETF SPDR proxy.
# Source : SPDR ETFs officiels (State Street). "Unknown" ignoré.
SECTOR_TO_ETF: dict[str, str] = {
    "Technology":             "XLK",
    "Industrials":            "XLI",
    "Financial Services":     "XLF",
    "Consumer Cyclical":      "XLY",
    "Healthcare":             "XLV",
    "Consumer Defensive":     "XLP",
    "Utilities":              "XLU",
    "Real Estate":            "XLRE",
    "Communication Services": "XLC",
    "Energy":                 "XLE",
    "Basic Materials":        "XLB",
}

# Jours de trading par an — conversion daily σ → annualisée via √252.
_TRADING_DAYS_PER_YEAR = 252

# Historique demandé au batch provider. ~12 mois civils = 252 jours ouvrés
# pour couvrir la fenêtre 12M-1M de Jegadeesh-Titman (l'anomalie momentum
# la mieux documentée). Buffer de 20j pour les jours fériés.
_MOMENTUM_HISTORY_DAYS = 272

# Skip les N derniers jours de la série pour retirer le short-term reversal
# (effet documenté par Jegadeesh-Titman 1993, Asness 1994). Le 12M-1M est le
# standard académique pour le momentum cross-sectionnel.
_MOMENTUM_SKIP_RECENT_DAYS = 21

# Minimum de jours dans la fenêtre utile (après skip) pour calculer le return.
_MOMENTUM_MIN_WINDOW_DAYS = 120

# Squelette d'une entrée momentum vide — factorise la création de "trous".
_EMPTY_MOMENTUM: dict[str, float | None] = {
    "return_pct":      None,
    "volatility_pct":  None,
    "risk_adjusted":   None,
    # Lot 14 — ratio prix actuel / plus haut 52 semaines.
    # Signal d'anti-reversal : un stock près de son 52wh (ratio → 1.0) monte
    # plus souvent ensuite (Grinblatt-Han 2002, Asness et al. 2013).
    # Décorrélé du return 12M-1M car 52wh capte l'état *final* pas la pente.
    "high_52w_ratio":  None,
}


def _extract_close_series(df: Any, ticker: str) -> Any | None:
    """MultiIndex-aware Close extraction — rend une Series pd ou None."""
    try:
        if hasattr(df.columns, "get_level_values") and ticker in df.columns.get_level_values(0):
            return df[ticker]["Close"].dropna()
        if "Close" in df.columns:
            return df["Close"].dropna()
    except Exception:
        return None
    return None


def _compute_momentum_stats(close_series: Any) -> dict[str, float | None]:
    """Pipe commun : série de cours ajustés → {return_pct, volatility_pct, risk_adjusted}.

    Momentum 12M-1M (Jegadeesh-Titman) : le return est mesuré entre iloc[0] et
    iloc[-21] pour retirer le short-term reversal du dernier mois. La σ est
    calculée sur la fenêtre ENTIÈRE (on veut capter le risque récent).

    Fallback : si la série est plus courte que _MOMENTUM_MIN_WINDOW_DAYS après
    skip, on désactive le skip (tickers IPO récents — mieux vaut un 6M brut
    que None).

    Garde stricte contre divisions dégénérées (prix initial ≤ 0, σ = 0, inf) :
    retourne _EMPTY_MOMENTUM plutôt que de propager un NaN/inf silencieux.
    """
    if close_series is None or len(close_series) < 2:
        return dict(_EMPTY_MOMENTUM)

    n = len(close_series)
    skip = _MOMENTUM_SKIP_RECENT_DAYS if n >= _MOMENTUM_MIN_WINDOW_DAYS + _MOMENTUM_SKIP_RECENT_DAYS else 0
    end_idx = -1 - skip if skip > 0 else -1

    try:
        p0 = float(close_series.iloc[0])
        p1 = float(close_series.iloc[end_idx])
    except Exception:
        return dict(_EMPTY_MOMENTUM)

    # Garde division par zéro / prix négatif (splits ajustés anormaux, glitches yfinance).
    if not math.isfinite(p0) or not math.isfinite(p1) or p0 <= 0:
        return dict(_EMPTY_MOMENTUM)

    ret_pct = (p1 / p0 - 1.0) * 100.0
    if not math.isfinite(ret_pct):
        return dict(_EMPTY_MOMENTUM)

    # pct_change peut émettre inf si un prix intraday est 0 → neutraliser avant std().
    # σ sur la fenêtre ENTIÈRE (incluant le dernier mois) — on veut le risque
    # récent, pas stable sur l'horizon du return.
    import numpy as _np
    daily = close_series.pct_change().replace([_np.inf, -_np.inf], _np.nan).dropna()
    if len(daily) < 20:
        return {"return_pct": ret_pct, "volatility_pct": None, "risk_adjusted": None}

    try:
        sigma_daily = float(daily.std())
    except Exception:
        return {"return_pct": ret_pct, "volatility_pct": None, "risk_adjusted": None}

    if not math.isfinite(sigma_daily) or sigma_daily <= 0:
        return {"return_pct": ret_pct, "volatility_pct": None, "risk_adjusted": None}

    vol_ann_pct = sigma_daily * math.sqrt(_TRADING_DAYS_PER_YEAR) * 100.0
    if not math.isfinite(vol_ann_pct) or vol_ann_pct <= 0:
        return {"return_pct": ret_pct, "volatility_pct": None, "risk_adjusted": None}

    # Approximation Sharpe 12M-1M : les deux termes sont en % → ratio sans unité.
    # Pas de taux sans risque soustrait — cohérent avec un Momentum Score pur.
    ra_val = ret_pct / vol_ann_pct
    ra: float | None = ra_val if math.isfinite(ra_val) else None

    # 52-week high ratio : prix actuel (iloc[-1]) / max rolling des dernières
    # ~252 closes. Proche de 1.0 = stock près de son plus haut → anti-reversal
    # bullish.  <0.7 = en drawdown profond.
    high_52w_ratio: float | None = None
    try:
        last_px = float(close_series.iloc[-1])
        high_52w = float(close_series.max())
        if math.isfinite(last_px) and math.isfinite(high_52w) and high_52w > 0:
            high_52w_ratio = last_px / high_52w
            if not math.isfinite(high_52w_ratio) or high_52w_ratio <= 0:
                high_52w_ratio = None
    except Exception:
        high_52w_ratio = None

    return {
        "return_pct":      ret_pct,
        "volatility_pct":  vol_ann_pct,
        "risk_adjusted":   ra,
        "high_52w_ratio":  high_52w_ratio,
    }
