"""Endpoint /api/ticker_analysis/{ticker} — factsheet structurée d'un ticker.

Pas d'IA, pas d'appel externe. Tout est lu depuis :
  - get_scored_universe() — 69 champs fundamentals + TITAN scoring (cache mtime)
  - market_db.read_ohlcv() — OHLCV pour price action
  - universe_history — drift TITAN entry → now (si position OPEN)
  - support_score — niveau de support technique

Structure de la réponse (minimale mais riche) :
  - identity   : ticker, name, sector, industry, market_cap
  - titan      : composite + 6 piliers + tilts + f-score breakdown
  - valuation  : P/E, EV/EBITDA, P/B, PEG, dividend
  - quality    : ROE, ROA, margins, FCF
  - health     : debt, ratios courants
  - growth     : revenue/earnings growth, Y/Y comparisons
  - analysts   : consensus + targets
  - price      : cours, MA50/MA200, drawdown, momentum
  - support    : badge support score
  - drift      : si position OPEN, comparaison entry vs now
  - flags      : règles simples (no IA) — debt élevée, P/E excessif, etc.
  - meta       : source_provider, fetched_at, data_quality
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Security

from modules import api_core, signal_qualification
from modules.auto_proposer import _days_until_earnings
from modules.buy_signal import compute_buy_signal
from modules.dividend_safety import compute_dividend_safety
from modules.earnings_surprise import compute_earnings_surprise_score
from modules.factor_grades import compute_factor_grades, quant_rating_letter
from modules.log import logger
from modules.market_db import read_ohlcv
from modules.sector_metrics import get_scored_universe
from modules.support_score import compute_support_score
from modules.thesis_stop import compute_thesis_status

router = APIRouter(prefix="/api", tags=["ticker_analysis"])


def _safe_float(v: Any) -> float | None:
    try:
        f = float(v)
        if f != f:
            return None
        return f
    except (ValueError, TypeError):
        return None


def _compute_price_action(ticker: str) -> tuple[dict[str, Any], Any]:
    """Lit OHLCV depuis DuckDB (fallback yfinance si vide). Retourne MA50/MA200,
    52w high/low, current price, drawdown.

    Renvoie toujours un tuple (payload, history) — l'appelant unpacke les deux.
    history vaut None quand les données sont indisponibles (available=False)."""
    history = None
    try:
        history = read_ohlcv(ticker, days=300)
    except Exception:
        pass
    if history is None or history.empty:
        try:
            import yfinance as yf
            df = yf.download(ticker, period="14mo", interval="1d",
                             progress=False, auto_adjust=True, threads=False)
            if df is not None and not df.empty:
                if hasattr(df.columns, "get_level_values"):
                    try:
                        df.columns = df.columns.get_level_values(0)
                    except Exception:
                        pass
                history = df
        except Exception:
            history = None

    if history is None or history.empty or "Close" not in history.columns:
        return {"available": False}, None

    closes = history["Close"].dropna().astype(float).to_numpy()
    if len(closes) < 20:
        return {"available": False}, None

    current = float(closes[-1])
    ma50 = float(np.mean(closes[-50:])) if len(closes) >= 50 else None
    ma200 = float(np.mean(closes[-200:])) if len(closes) >= 200 else None
    high_52w = float(np.max(closes[-252:])) if len(closes) >= 50 else float(np.max(closes))
    low_52w = float(np.min(closes[-252:])) if len(closes) >= 50 else float(np.min(closes))
    drawdown_pct = (current - high_52w) / high_52w * 100.0 if high_52w > 0 else None

    # Momentum 6 mois — sécurisé par len(closes) >= 126
    momentum_6m_pct = None
    if len(closes) >= 126:
        momentum_6m_pct = (current - float(closes[-126])) / float(closes[-126]) * 100.0

    return {
        "available": True,
        "current_price": round(current, 2),
        "ma50": round(ma50, 2) if ma50 is not None else None,
        "ma200": round(ma200, 2) if ma200 is not None else None,
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "drawdown_from_high_pct": round(drawdown_pct, 2) if drawdown_pct is not None else None,
        "momentum_6m_pct": round(momentum_6m_pct, 2) if momentum_6m_pct is not None else None,
        "history_days": len(closes),
    }, history


def _compute_drift(ticker: str, scored: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Si une position OPEN existe, compare TITAN à l'entrée vs maintenant
    et calcule le thesis_status (cassure de thèse fondamentale)."""
    try:
        from pathlib import Path

        journal_path = Path(__file__).resolve().parent.parent / "data" / "trade_journal.csv"
        if not journal_path.exists():
            return None
        df = pd.read_csv(journal_path, dtype={"Ticker": str})
        position = df[(df["Ticker"].str.upper() == ticker.upper()) & (df["Status"] == "OPEN")]
        if position.empty:
            return None
        row = position.iloc[0]
        entry_titan = _safe_float(row.get("Titan_Score_Entry"))
        entry_price = _safe_float(row.get("Entry"))
        entry_date = str(row.get("Date", ""))[:10]
        f_entry_raw = row.get("F_Score_Entry")

        # Phase 2 — thesis_status (cassure de thèse fondamentale)
        thesis = None
        if scored is not None:
            try:
                thesis = compute_thesis_status(
                    entry={
                        "Titan_Score_Entry": entry_titan,
                        "Quality_Entry":     _safe_float(row.get("Quality_Entry")),
                        "Value_Entry":       _safe_float(row.get("Value_Entry")),
                        "Risk_Entry":        _safe_float(row.get("Risk_Entry")),
                        "Momentum_Entry":    _safe_float(row.get("Momentum_Entry")),
                        "Piotroski_Entry":   _safe_float(row.get("Piotroski_Entry")),
                        "Growth_Entry":      _safe_float(row.get("Growth_Entry")),
                        "F_Score_Entry":     f_entry_raw,
                        "Tilt_Flags_Entry":  row.get("Tilt_Flags_Entry") or "",
                    },
                    current=scored,
                )
            except Exception as e:
                logger.debug(f"[ticker_analysis] thesis_status failed for {ticker}: {e}")

        if entry_titan is None:
            return {
                "is_open": True,
                "entry_date": entry_date,
                "entry_price": entry_price,
                "entry_titan": None,
                "warning": "Position ouverte avant la capture des entry scores",
                "thesis": thesis,
            }
        return {
            "is_open": True,
            "entry_date": entry_date,
            "entry_price": entry_price,
            "entry_titan": entry_titan,
            "entry_f_score": _safe_float(f_entry_raw),
            "thesis": thesis,
        }
    except Exception as e:
        logger.debug(f"[ticker_analysis] drift compute failed for {ticker}: {e}")
        return None


def _classify_flags_to_cases(flags: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    """Sépare les flags en bull (level=ok) / bear (level=danger/warn).

    Pratique : la factsheet expose deux listes Bull/Bear inspirées Seeking Alpha
    "Bullish vs Bearish Case". Les flags 'info' restent en 'neutral'.
    """
    bull: list[dict[str, str]] = []
    bear: list[dict[str, str]] = []
    neutral: list[dict[str, str]] = []
    for f in flags:
        lv = (f.get("level") or "").lower()
        if lv == "ok":
            bull.append(f)
        elif lv in ("danger", "warn"):
            bear.append(f)
        else:
            neutral.append(f)
    return {"bull": bull, "bear": bear, "neutral": neutral}


def _build_bull_bear_cases(
    scored: dict[str, Any],
    price_action: dict[str, Any],
    revisions_data: dict[str, Any] | None,
    surprise_data: dict[str, Any] | None,
) -> dict[str, list[str]]:
    """Bullish/Bearish bullets dérivés des scores TITAN + métriques. Trois lignes
    max chaque côté pour un format compact lisible.
    """
    bull: list[str] = []
    bear: list[str] = []

    composite = _safe_float(scored.get("titan_composite_score"))
    if composite is not None:
        if composite >= 75:
            bull.append(f"TITAN composite élevé ({composite:.0f}/100) — top quartile multi-factor.")
        elif composite < 35:
            bear.append(f"TITAN composite faible ({composite:.0f}/100) — bottom quartile multi-factor.")

    quality = _safe_float(scored.get("quality_score"))
    value = _safe_float(scored.get("value_score"))
    if quality is not None and quality >= 75 and value is not None and value >= 70:
        bull.append(f"QARP — Quality {quality:.0f} + Value {value:.0f} (Novy-Marx alpha durable).")
    elif quality is not None and value is not None and quality < 35 and value > 75:
        bear.append("Cheap-junk pattern — Value haut + Quality bas = piège classique.")

    momentum = _safe_float(scored.get("momentum_score"))
    if momentum is not None and momentum >= 80:
        bull.append(f"Momentum 12M-1M très fort (rank {momentum:.0f}) — anomalie Jegadeesh-Titman.")
    elif momentum is not None and momentum < 25:
        bear.append(f"Momentum dégradé (rank {momentum:.0f}) — vent contraire de tendance.")

    growth = _safe_float(scored.get("growth_score"))
    if growth is not None and growth >= 80:
        bull.append(f"Growth top sectoriel (rank {growth:.0f}) — revenus/earnings best-in-class.")

    f_score = scored.get("f_score")
    f_max = scored.get("f_score_max") or 9
    if f_score is not None and f_max:
        try:
            fs = int(f_score)
            if fs >= 8:
                bull.append(f"Piotroski F-Score {fs}/{f_max} — bilan + cash flow excellents.")
            elif fs <= 3:
                bear.append(f"Piotroski F-Score {fs}/{f_max} — multiple red flags comptables.")
        except (TypeError, ValueError):
            pass

    revisions = _safe_float(scored.get("revisions_score"))
    if revisions is not None and revisions >= 75:
        bull.append(f"Revisions analystes positives (rank {revisions:.0f}) — momentum analyst.")
    elif revisions is not None and revisions < 30:
        bear.append(f"Revisions négatives (rank {revisions:.0f}) — downgrade momentum.")

    # Lot 17 — Insider smart money.
    insider = _safe_float(scored.get("insider_score"))
    if scored.get("insider_cluster_buying"):
        bull.append("Insider cluster buying — 3+ filings Form 4 dans une fenêtre 7j (smart money).")
    elif insider is not None and insider >= 75:
        bull.append(f"Activité insider positive (score {insider:.0f}) — buys C-level récents.")
    elif insider is not None and insider < 30:
        bear.append(f"Activité insider négative (score {insider:.0f}) — sell-off C-level.")

    if surprise_data and surprise_data.get("level") == "STRONG_BEAT":
        bull.append("Earnings beat streak — PEAD signal positif.")
    elif surprise_data and surprise_data.get("level") in ("MISS", "STRONG_MISS"):
        bear.append("Earnings miss(s) récents — PEAD signal négatif.")

    de = _safe_float(scored.get("debt_to_equity"))
    if de is not None and de > 200:
        bear.append(f"Levier élevé — D/E {de:.0f} %.")

    pm = _safe_float(scored.get("profit_margin"))
    if pm is not None and pm < 0:
        bear.append(f"Marge nette négative ({pm * 100:.1f} %) — non rentable TTM.")

    if price_action.get("available"):
        dd = price_action.get("drawdown_from_high_pct")
        mom6 = price_action.get("momentum_6m_pct")
        if dd is not None and dd < -30:
            bear.append(f"Drawdown profond ({dd:.0f} % vs 52w high).")
        elif dd is not None and -15 <= dd <= -8:
            bull.append(f"Pullback sain ({dd:.0f} % du 52w high) — zone d'entrée typique.")
        if (
            dd is not None and dd > -3.0
            and mom6 is not None and mom6 > 50.0
        ):
            bear.append(
                f"Entrée à l'ATH ({dd:+.1f} % du 52w high) + momentum 6M {mom6:+.0f} % — "
                "mean reversion due, R/R défavorable."
            )

    return {
        "bull": bull[:5],
        "bear": bear[:5],
    }


def _build_entry_plan(
    price_action: dict[str, Any],
    support: dict[str, Any],
    buy_signal: dict[str, Any],
) -> dict[str, Any]:
    """Plan d'entrée fractionné selon la qualité du support + extension du prix.

    Logique :
      - Si verdict ∈ {SKIP, EARNINGS_BLACKOUT, CHEAP_JUNK, FALLING_KNIFE, NO_DATA}
        ou price_action indisponible → entry_plan désactivé (recommendation=SKIP).
      - ON_SUPPORT      → 100% au market (entry timing optimal)
      - NEAR_SUPPORT    → 50% market + 50% limit -5%
      - OFF_SUPPORT     → 30% market + 40% limit -8% + 30% limit -15%
      - OFF + ATH ext.  → 0% market + 40% limit -8% + 40% limit -15% + 20% post-earnings

    Toutes les limites sont ancrées sur current_price ; les pourcentages sont
    indicatifs et le user peut ajuster en UI.
    """
    if not price_action.get("available"):
        return {"available": False, "reason": "Pas de price_action"}

    verdict = buy_signal.get("verdict") or "SKIP"
    skip_verdicts = {"SKIP", "EARNINGS_BLACKOUT", "CHEAP_JUNK", "FALLING_KNIFE",
                     "NO_DATA"}
    if verdict in skip_verdicts:
        return {"available": False, "reason": f"Verdict {verdict}"}

    cur = _safe_float(price_action.get("current_price"))
    if cur is None or cur <= 0:
        return {"available": False, "reason": "Current price invalide"}

    sup_level = support.get("level")
    dd = _safe_float(price_action.get("drawdown_from_high_pct"))
    mom6 = _safe_float(price_action.get("momentum_6m_pct"))
    is_ath_extended = (
        sup_level == "OFF_SUPPORT"
        and dd is not None and dd > -3.0
        and mom6 is not None and mom6 > 50.0
    )

    def _tier(pct_off: float, weight: float, label: str) -> dict[str, Any]:
        return {
            "label": label,
            "weight_pct": round(weight * 100.0, 1),
            "limit_price": round(cur * (1.0 + pct_off / 100.0), 2),
            "discount_pct": pct_off,
        }

    if is_ath_extended:
        tiers = [
            _tier(0.0, 0.0, "Market (désactivé — entrée à l'ATH)"),
            _tier(-8.0, 0.40, "Limit -8% (pullback technique)"),
            _tier(-15.0, 0.40, "Limit -15% (zone MA50/swing)"),
            {
                "label": "Réserve post-earnings",
                "weight_pct": 20.0,
                "limit_price": None,
                "discount_pct": None,
                "note": "Décision après le prochain catalyst",
            },
        ]
        rationale = "OFF_SUPPORT + entrée à l'ATH + momentum parabolique → wait pullback"
        recommendation = "WAIT_PULLBACK"
    elif sup_level == "OFF_SUPPORT":
        tiers = [
            _tier(0.0, 0.30, "Market (entry partiel)"),
            _tier(-8.0, 0.40, "Limit -8% (pullback)"),
            _tier(-15.0, 0.30, "Limit -15% (support dynamique)"),
        ]
        rationale = "OFF_SUPPORT — étaler l'entrée pour absorber un mauvais timing"
        recommendation = "SPLIT_3"
    elif sup_level == "NEAR_SUPPORT":
        tiers = [
            _tier(0.0, 0.50, "Market"),
            _tier(-5.0, 0.50, "Limit -5% (test de support)"),
        ]
        rationale = "NEAR_SUPPORT — moitié market, moitié limite si pullback"
        recommendation = "SPLIT_2"
    elif sup_level == "ON_SUPPORT":
        tiers = [_tier(0.0, 1.00, "Market (full size)")]
        rationale = "ON_SUPPORT — entry timing optimal"
        recommendation = "MARKET_FULL"
    else:
        tiers = [_tier(0.0, 1.00, "Market")]
        rationale = "Support indéterminé — entrée standard"
        recommendation = "MARKET_FULL"

    return {
        "available": True,
        "recommendation": recommendation,
        "rationale": rationale,
        "anchor_price": round(cur, 2),
        "support_level": sup_level,
        "is_ath_extended": is_ath_extended,
        "tiers": tiers,
    }


def _compute_flags(scored: dict[str, Any], price_action: dict[str, Any]) -> list[dict[str, str]]:
    """Règles simples (no AI) — repère les points d'attention sans interprétation."""
    flags: list[dict[str, str]] = []

    pe = _safe_float(scored.get("trailing_pe"))
    if pe is not None and pe > 50:
        flags.append({"level": "warn", "label": "P/E élevé", "detail": f"P/E TTM = {pe:.1f} (>50)"})
    elif pe is not None and pe < 0:
        flags.append({"level": "warn", "label": "P/E négatif", "detail": "Société non rentable sur 12m"})

    de = _safe_float(scored.get("debt_to_equity"))
    if de is not None and de > 200:
        flags.append({"level": "danger", "label": "Dette élevée", "detail": f"D/E = {de:.0f}% (>200%)"})

    cr = _safe_float(scored.get("current_ratio"))
    if cr is not None and cr < 1.0:
        flags.append({"level": "warn", "label": "Liquidité tendue", "detail": f"Current ratio = {cr:.2f} (<1)"})
    elif cr is not None and cr > 2.5:
        flags.append({"level": "ok", "label": "Bilan robuste", "detail": f"Current ratio = {cr:.2f}"})

    pm = _safe_float(scored.get("profit_margin"))
    if pm is not None and pm < 0:
        flags.append({"level": "danger", "label": "Marge nette négative",
                      "detail": f"Net margin = {pm * 100:.1f}%"})

    f_score = scored.get("f_score")
    f_max = scored.get("f_score_max") or 9
    if f_score is not None and f_max:
        try:
            if int(f_score) >= 8:
                flags.append({"level": "ok", "label": "Piotroski excellent",
                              "detail": f"F-Score {f_score}/{f_max}"})
            elif int(f_score) <= 3:
                flags.append({"level": "danger", "label": "Piotroski faible",
                              "detail": f"F-Score {f_score}/{f_max}"})
        except (TypeError, ValueError):
            pass

    rec = scored.get("recommendation_key")
    if rec in ("strong_buy", "buy"):
        flags.append({"level": "ok", "label": "Analystes positifs", "detail": rec.replace("_", " ").title()})
    elif rec in ("sell", "strong_sell"):
        flags.append({"level": "danger", "label": "Analystes négatifs", "detail": rec.replace("_", " ").title()})

    if price_action.get("available"):
        dd = price_action.get("drawdown_from_high_pct")
        if dd is not None and dd < -30:
            flags.append({"level": "warn", "label": "Drawdown profond",
                          "detail": f"{dd:.0f}% sous le 52w high"})

        # Extended price : à <3% du 52w high + momentum 6M > 50% (parabolique).
        # Mean reversion statistiquement due, R/R défavorable même fondamentaux A+.
        mom6 = price_action.get("momentum_6m_pct")
        if (
            dd is not None and dd > -3.0
            and mom6 is not None and mom6 > 50.0
        ):
            flags.append({
                "level": "warn",
                "label": "Entrée à l'ATH",
                "detail": f"{dd:+.1f}% du 52w high + momentum 6M {mom6:+.0f}% — mean reversion due",
            })

    sector = scored.get("sector") or ""
    if sector in ("Healthcare", "Biotechnology"):
        flags.append({"level": "info", "label": "Risque pipeline/brevet",
                      "detail": "Secteur biotech — vérifier pipeline et patent cliffs"})
    elif sector == "Energy":
        flags.append({"level": "info", "label": "Risque cyclique",
                      "detail": "Secteur énergie — exposition aux prix matières premières"})

    return flags


@router.get("/ticker_analysis/{ticker}")
def ticker_analysis(ticker: str, _auth: None = Security(api_core.require_auth)) -> dict[str, Any]:
    """Factsheet structurée d'un ticker — 100 % données existantes, no IA."""
    ticker = ticker.upper().strip()
    if not ticker or not ticker.isalnum():
        raise HTTPException(status_code=400, detail="Ticker invalide")

    universe = get_scored_universe()
    scored = universe.get(ticker)
    if not scored:
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' absent de l'univers scoré")

    price_action, history = _compute_price_action(ticker)
    drift = _compute_drift(ticker, scored=scored)

    support: dict[str, Any] = {}
    try:
        cur_price = price_action.get("current_price") if price_action.get("available") else None
        sr = compute_support_score(ticker=ticker, current_price=cur_price, history=history)
        support = sr.to_dict()
    except Exception as e:
        logger.debug(f"[ticker_analysis] support failed: {e}")

    flags = _compute_flags(scored, price_action)

    # Lot 16 — modules dérivés (revisions, earnings surprise, dividend safety,
    # factor grades, quant rating, bull/bear cases). Tous fail-open.
    surprise_data = compute_earnings_surprise_score(scored).to_dict()
    dividend_data = compute_dividend_safety(scored).to_dict()
    factor_grades = compute_factor_grades(scored)
    composite = scored.get("titan_composite_score")
    quant_rating = quant_rating_letter(composite)
    revisions_block = {
        "score":               scored.get("revisions_score"),
        "components":          scored.get("revisions_components"),
        "data_quality":        scored.get("revisions_data_quality"),
        "upgrades_30d":        scored.get("upgrades_30d"),
        "downgrades_30d":      scored.get("downgrades_30d"),
        "upgrades_90d":        scored.get("upgrades_90d"),
        "downgrades_90d":      scored.get("downgrades_90d"),
        "revisions_net_score": scored.get("revisions_net_score"),
        "upgrade_downgrade_log": scored.get("upgrade_downgrade_log"),
        "analyst_actions": scored.get("finnhub_analyst_actions"),
    }
    # Lot 17 — Insider (smart money) block.
    insider_block = {
        "score":              scored.get("insider_score"),
        "components":         scored.get("insider_components"),
        "data_quality":       scored.get("insider_data_quality"),
        "n_filings":          scored.get("insider_n_filings"),
        "buy_count_30d":      scored.get("insider_buy_count_30d"),
        "distinct_30d":       scored.get("insider_distinct_30d"),
        "cluster_buying":     scored.get("insider_cluster_buying"),
        "most_recent":        scored.get("insider_most_recent"),
    }
    bull_bear = _build_bull_bear_cases(scored, price_action, revisions_block, surprise_data)
    flags_classified = _classify_flags_to_cases(flags)

    # Lot 18 — Buy Signal verdict (BUY/STRONG_BUY/WATCH/SKIP).
    # On enrichit le row avec support + price_action pour que les garde-fous
    # (sizing × support, ATH-extended) puissent se déclencher.
    enriched_row = {**scored, "support": support, "price_action": price_action}
    buy_signal_block = compute_buy_signal(enriched_row).to_dict()
    entry_plan_block = _build_entry_plan(price_action, support, buy_signal_block)

    # Étape 15 roadmap — qualification du signal (Étape 1) sur la fiche ticker.
    # Lecture seule, mêmes ingrédients que `auto_proposer.plan_proposals` (alloc)
    # pour ce même ticker. Fail-open : une erreur de qualification ne doit
    # jamais faire échouer la factsheet elle-même.
    try:
        qualification = signal_qualification.qualify_proposal(
            ticker,
            scored_row=scored,
            context_ingredients={
                "buy_signal": buy_signal_block,
                "f_score": scored.get("f_score"),
                "f_score_max": scored.get("f_score_max"),
                "momentum_score": scored.get("momentum_score"),
                "support": support,
                "revisions_score": scored.get("revisions_score"),
                "days_until_earnings": _days_until_earnings(scored.get("next_earnings_date")),
                "titan_tilt_flags": scored.get("titan_tilt_flags") or [],
            },
        )
    except Exception as e:
        logger.warning(f"[ticker_analysis] signal_qualification failed for {ticker}: {e}")
        qualification = None

    return {
        "ticker": ticker,
        "identity": {
            "name": scored.get("name"),
            "sector": scored.get("sector"),
            "industry": scored.get("industry"),
            "country": scored.get("country"),
            "currency": scored.get("currency"),
            "exchange": scored.get("exchange"),
            "market_cap": scored.get("market_cap"),
            "shares_outstanding": scored.get("shares_outstanding"),
        },
        "titan": {
            "composite": scored.get("titan_composite_score"),
            "composite_raw": scored.get("titan_composite_raw"),
            "quality": scored.get("quality_score"),
            "value": scored.get("value_score"),
            "risk": scored.get("risk_score"),
            "momentum": scored.get("momentum_score"),
            "growth": scored.get("growth_score"),
            "piotroski": scored.get("piotroski_score"),
            "f_score": scored.get("f_score"),
            "f_score_max": scored.get("f_score_max"),
            "f_score_breakdown": scored.get("f_score_breakdown"),
            "tilt_flags": scored.get("titan_tilt_flags") or [],
            "tilt_adjust": scored.get("titan_tilt_adjust"),
        },
        "valuation": {
            "trailing_pe": scored.get("trailing_pe"),
            "forward_pe": scored.get("forward_pe"),
            "ev_to_ebitda": scored.get("ev_to_ebitda"),
            "ev_to_revenue": scored.get("ev_to_revenue"),
            "price_to_book": scored.get("price_to_book"),
            "peg_ratio": scored.get("peg_ratio"),
            "dividend_yield": scored.get("dividend_yield"),
        },
        "quality": {
            "return_on_equity": scored.get("return_on_equity"),
            "return_on_assets": scored.get("return_on_assets"),
            "return_on_assets_prev_year": scored.get("return_on_assets_prev_year"),
            "profit_margin": scored.get("profit_margin"),
            "operating_margin": scored.get("operating_margin"),
            "gross_margin": scored.get("gross_margin"),
            "gross_margin_prev_year": scored.get("gross_margin_prev_year"),
            "operating_cash_flow": scored.get("operating_cash_flow"),
            "free_cash_flow": scored.get("free_cash_flow"),
            "net_income": scored.get("net_income"),
        },
        "health": {
            "debt_to_equity": scored.get("debt_to_equity"),
            "debt_to_equity_prev_year": scored.get("debt_to_equity_prev_year"),
            "current_ratio": scored.get("current_ratio"),
            "current_ratio_prev_year": scored.get("current_ratio_prev_year"),
            "quick_ratio": scored.get("quick_ratio"),
        },
        "growth": {
            "revenue_growth": scored.get("revenue_growth"),
            "earnings_growth": scored.get("earnings_growth"),
            "earnings_quarterly_growth": scored.get("earnings_quarterly_growth"),
            "shares_outstanding_prev_year": scored.get("shares_outstanding_prev_year"),
        },
        "analysts": {
            "num_analysts": scored.get("num_analysts"),
            "recommendation_key": scored.get("recommendation_key"),
            "recommendation_mean": scored.get("recommendation_mean"),
            "price_target_mean": scored.get("price_target_mean"),
            "price_target_high": scored.get("price_target_high"),
            "price_target_low": scored.get("price_target_low"),
        },
        "risk": {
            "beta": scored.get("beta"),
            "volatility_pct": scored.get("momentum_volatility_pct"),
            "momentum_return_pct": scored.get("momentum_return_pct"),
            "momentum_risk_adjusted": scored.get("momentum_risk_adjusted"),
            "momentum_high_52w_ratio": scored.get("momentum_high_52w_ratio"),
        },
        "price_action": price_action,
        "support": support,
        "drift": drift,
        "flags": flags,
        # Lot 16 — Seeking Alpha-inspired blocks.
        "factor_grades":   factor_grades,
        "quant_rating":    quant_rating,
        "buy_signal":      buy_signal_block,
        "entry_plan":      entry_plan_block,
        "qualification":   qualification,
        "revisions":       revisions_block,
        "insider":         insider_block,
        "earnings_surprise": surprise_data,
        "dividend_safety": dividend_data,
        "next_earnings_date": scored.get("next_earnings_date"),
        "next_earnings_eps_estimate": scored.get("next_earnings_eps_estimate"),
        "bull_bear_cases": bull_bear,
        "flags_classified": flags_classified,
        "meta": {
            "source_provider": scored.get("source_provider"),
            "fetched_at": scored.get("fetched_at"),
            "data_quality": scored.get("data_quality"),
            "data_quality_coef": scored.get("data_quality_coef"),
            "backfill_fields": scored.get("backfill_fields"),
        },
    }
