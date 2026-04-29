"""Peer Comparison — top-N tickers comparables (sector + market_cap proche).

Approche simple, déterministe, pure Python — pas de cluster ML. Le critère
"comparable" suit la pratique sell-side :
  1. même sector (obligatoire)
  2. industry match en bonus (poids 2× sur le ranking)
  3. market_cap dans [0.3×, 3×] (cap-similar = même catégorie SMID/large/mega)

Score de proximité (plus bas = plus proche) :
  dist = log10(mcap_self / mcap_peer) ** 2 + (industry_mismatch × 0.5)

Renvoie aussi un mini-tableau comparatif sur les KPIs clés pour la factsheet.
"""
from __future__ import annotations

import math
from typing import Any

# KPIs présents dans le tableau peer-comparison de l'UI.
PEER_COMPARE_FIELDS: tuple[str, ...] = (
    "titan_composite_score",
    "trailing_pe",
    "forward_pe",
    "ev_to_ebitda",
    "return_on_equity",
    "operating_margin",
    "revenue_growth",
    "earnings_growth",
    "debt_to_equity",
    "dividend_yield",
)


def _safe(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _normalize_sector(s: str | None) -> str:
    if not s:
        return ""
    norm = s.strip()
    aliases = {
        "Financial Services": "Financials",
        "Financial": "Financials",
    }
    return aliases.get(norm, norm)


def find_peers(
    target: str,
    universe: dict[str, dict[str, Any]],
    *,
    n: int = 5,
    mcap_band: tuple[float, float] = (0.3, 3.0),
) -> list[dict[str, Any]]:
    """Retourne jusqu'à `n` peers comparables au `target`. Liste vide si target
    introuvable / sans secteur / sans market_cap.

    Args:
      target: ticker UPPER.
      universe: scored universe map (idéalement post `_score_universe`).
      n: nb de peers à retourner.
      mcap_band: bande [low, high] sur le ratio mcap_peer/mcap_self.
    """
    target = target.upper()
    self_row = universe.get(target)
    if not self_row:
        return []
    self_sector = _normalize_sector(self_row.get("sector"))
    self_industry = (self_row.get("industry") or "").strip()
    self_mcap = _safe(self_row.get("market_cap"))
    if not self_sector or self_mcap is None or self_mcap <= 0:
        return []

    candidates: list[tuple[float, str, dict[str, Any]]] = []
    for ticker, row in universe.items():
        if ticker == target:
            continue
        sec = _normalize_sector(row.get("sector"))
        if sec != self_sector:
            continue
        mcap = _safe(row.get("market_cap"))
        if mcap is None or mcap <= 0:
            continue
        ratio = mcap / self_mcap
        if ratio < mcap_band[0] or ratio > mcap_band[1]:
            continue
        ind = (row.get("industry") or "").strip()
        industry_mismatch = 0.0 if (self_industry and ind == self_industry) else 1.0
        log_dist = math.log10(ratio) ** 2
        dist = log_dist + 0.5 * industry_mismatch
        candidates.append((dist, ticker, row))

    candidates.sort(key=lambda x: x[0])
    out: list[dict[str, Any]] = []
    for _, t, row in candidates[:n]:
        peer = {
            "ticker": t,
            "name": row.get("name"),
            "industry": row.get("industry"),
            "market_cap": row.get("market_cap"),
        }
        for f in PEER_COMPARE_FIELDS:
            peer[f] = row.get(f)
        out.append(peer)
    return out


def build_peer_table(
    target: str, universe: dict[str, dict[str, Any]], *, n: int = 5,
) -> dict[str, Any]:
    """Construit la structure consommée par l'UI : self + peers + médiane.

    Returns:
      {
        "target":       {ticker, name, industry, market_cap, kpis...},
        "peers":        [{ticker, ...}],
        "sector_median": {kpi: value},
        "n_peers":      int,
      }
    """
    target = target.upper()
    self_row = universe.get(target) or {}
    peers = find_peers(target, universe, n=n)

    # Médianes sur self+peers, KPI par KPI.
    median: dict[str, float | None] = {}
    pool = [self_row, *(p for p in peers)]
    for f in PEER_COMPARE_FIELDS:
        vals = [_safe(p.get(f)) for p in pool]
        valid = sorted(v for v in vals if v is not None)
        if not valid:
            median[f] = None
            continue
        n_valid = len(valid)
        if n_valid % 2 == 1:
            median[f] = valid[n_valid // 2]
        else:
            median[f] = 0.5 * (valid[n_valid // 2 - 1] + valid[n_valid // 2])

    target_block = {
        "ticker": target,
        "name": self_row.get("name"),
        "industry": self_row.get("industry"),
        "sector": self_row.get("sector"),
        "market_cap": self_row.get("market_cap"),
    }
    for f in PEER_COMPARE_FIELDS:
        target_block[f] = self_row.get(f)

    return {
        "target": target_block,
        "peers": peers,
        "sector_median": median,
        "n_peers": len(peers),
    }
