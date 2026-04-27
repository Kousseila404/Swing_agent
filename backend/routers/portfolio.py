"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — PORTFOLIO                                              ║
║  GET  /api/portfolio                                             ║
║  GET  /api/equity_curve                                          ║
║  GET  /api/performance_metrics                                   ║
║  GET  /api/portfolio/recommendations   (warm par cron)           ║
║                                                                  ║
║  Le bulk-buy (POST /api/portfolio/execute) a été remplacé par    ║
║  POST /api/proposals/approve_batch (2026-04-24).                 ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from filelock import FileLock

import config
from modules import api_core
from modules.api_schemas import (
    EquityCurveResponse,
    PerformanceMetricsResponse,
    PortfolioResponse,
)
from modules.duckdb_journal import journal_mtime, read_journal_df
from modules.log import logger

router = APIRouter(prefix="/api", tags=["portfolio"])

# Plafond secteur utilisé pour les warnings de sur-exposition dans les recos.
# Aligné sur DEFAULT_SECTOR_CAP du risk parity (30 %) → l'UI avertit quand
# un achat ferait dépasser ce seuil, l'utilisateur peut override.
_SECTOR_EXPOSURE_CAP_PCT = 30.0


def _journal_etag() -> str | None:
    """ETag combiné journal + equity. Change dès qu'un des deux fichiers bouge.
    Utilisé par /portfolio, /equity_curve, /performance_metrics — ces trois
    endpoints sont tous dérivés du journal + equity_state."""
    mt_journal = journal_mtime()
    try:
        mt_equity = (
            api_core.EQUITY_PATH.stat().st_mtime
            if api_core.EQUITY_PATH.exists() else None
        )
    except OSError:
        mt_equity = None
    if mt_journal is None and mt_equity is None:
        return None
    return f'W/"{int(mt_journal or 0)}:{int(mt_equity or 0)}"'


@router.get("/portfolio", response_model=PortfolioResponse)
def get_portfolio(request: Request, response: Response):
    """Journal CSV complet + equity state + positions ouvertes."""
    etag = _journal_etag()
    if etag:
        if api_core.etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers={"ETag": etag})
        response.headers["ETag"] = etag

    equity  = api_core.load_equity()
    journal = api_core.load_journal()

    open_pos = [t for t in journal if t.get("Status") == "OPEN"]
    closed   = [t for t in journal if t.get("Status") in ("WIN", "LOSS", "CLOSED", "TP", "SL")]
    wins     = [t for t in closed if t.get("Status") in ("WIN", "TP")]
    losses   = [t for t in closed if t.get("Status") in ("LOSS", "SL")]
    win_rate = len(wins) / len(closed) * 100 if closed else 0

    return {
        "equity":          equity,
        "journal":         journal,
        "open_positions":  open_pos,
        "closed_trades":   closed,
        "stats": {
            "total_trades":    len(closed),
            "wins":            len(wins),
            "losses":          len(losses),
            "win_rate":        round(win_rate, 1),
            "realized_pnl":    equity.get("realized_pnl", 0.0),
            "unrealized_pnl":  equity.get("unrealized_pnl", 0.0),
            "account_equity":  equity.get("current_equity", api_core.INITIAL_CAPITAL),
        },
    }


@router.get("/equity_curve", response_model=EquityCurveResponse)
def get_equity_curve(request: Request, response: Response):
    """Courbe d'équité calculée depuis le journal des trades clôturés."""
    etag = _journal_etag()
    if etag:
        if api_core.etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers={"ETag": etag})
        response.headers["ETag"] = etag

    journal = api_core.load_journal()
    curve, equity = [], api_core.INITIAL_CAPITAL
    closed = [t for t in journal if t.get("Status") in ("WIN", "LOSS", "TP", "SL", "CLOSED") and t.get("Exit_Price")]
    closed.sort(key=lambda t: t.get("Exit_Date", ""))
    for t in closed:
        try:
            entry = float(t.get("Entry", 0))
            exit_p = float(t.get("Exit_Price", 0))
            size = int(t.get("Size", 0))
            direction = t.get("Direction", "LONG")
            if entry > 0 and exit_p > 0 and size > 0:
                pnl = (exit_p - entry) * size if direction == "LONG" else (entry - exit_p) * size
                equity += pnl
                curve.append({
                    "date": t.get("Exit_Date", ""), "ticker": t.get("Ticker", ""),
                    "pnl": round(pnl, 2), "equity": round(equity, 2), "status": t.get("Status", ""),
                })
        except Exception:
            continue
    return {"curve": curve, "final_equity": round(equity, 2), "initial_equity": api_core.INITIAL_CAPITAL}


@router.get("/performance_metrics", response_model=PerformanceMetricsResponse)
def get_performance_metrics(request: Request, response: Response):
    """Agrégats PnL réalisés + ratios (Sharpe/Sortino/Calmar/DD/Expectancy)."""
    etag = _journal_etag()
    if etag:
        if api_core.etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers={"ETag": etag})
        response.headers["ETag"] = etag

    try:
        from modules.perf_metrics import compute_metrics
        with FileLock(str(api_core.CSV_LOCK_PATH), timeout=10):
            df = read_journal_df()
    except Exception as e:
        logger.error(f"[API /performance_metrics] read failed: {e}", exc_info=True)
        raise HTTPException(500, f"Lecture journal impossible: {e}") from e

    m = compute_metrics(df, initial_capital=api_core.INITIAL_CAPITAL)
    # Force les types attendus par le schéma (compute_metrics peut renvoyer
    # des numpy types via pandas) et sérialise equity_by_date en list[list].
    equity_by_date = [[d, v] for d, v in (m.get("equity_by_date") or [])]
    return {
        "capital":          float(m.get("capital", api_core.INITIAL_CAPITAL)),
        "pnl_total":        float(m.get("pnl_total", 0.0)),
        "wins":             int(m.get("wins", 0)),
        "losses":           int(m.get("losses", 0)),
        "total_closed":     int(m.get("total_closed", 0)),
        "win_rate":         float(m.get("win_rate", 0.0)),
        "profit_factor":    float(m.get("profit_factor", 0.0)),
        "open_count":       int(m.get("open_count", 0)),
        "sharpe":           float(m.get("sharpe", 0.0)),
        "sortino":          float(m.get("sortino", 0.0)),
        "calmar":           float(m.get("calmar", 0.0)),
        "max_drawdown_pct": float(m.get("max_drawdown_pct", 0.0)),
        "avg_win":          float(m.get("avg_win", 0.0)),
        "avg_loss":         float(m.get("avg_loss", 0.0)),
        "expectancy":       float(m.get("expectancy", 0.0)),
        "streak":           int(m.get("streak", 0)),
        "avg_holding_days": float(m.get("avg_holding_days", 0.0)),
        "equity_by_date":   equity_by_date,
        "pnl_raw":          [float(x) for x in (m.get("pnl_raw") or [])],
        "pnl_series":       [float(x) for x in (m.get("pnl_series") or [])],
        "daily_pnl":        {str(k): float(v) for k, v in (m.get("daily_pnl") or {}).items()},
        "per_ticker_pnl":   m.get("per_ticker_pnl", {}),
    }


@router.get("/portfolio/recommendations")
def get_portfolio_recommendations(
    total_capital: float = 100_000.0,
    max_holdings: int = 20,
    allow_fractional_shares: bool = False,
    sector: str | None = None,
):
    """
    Plan d'achat recommandé — top `max_holdings` actions en tendance haussière
    pondérées en risk parity (inverse-vol) sur le capital fourni.

    Pipeline :
      1. Charge les tickers scorés (cache mtime-keyed de sector_metrics).
      2. Filtre bullish (momentum 6M > 0).
      3. Top N par titan_composite_score.
      4. Poids W_i = (1/σ_i) / Σ(1/σ_j) via volatilité 6M (Polygon/yf).
      5. Montants $ + shares (entières sauf allow_fractional_shares=true).

    Query params :
      total_capital            : float, capital total à allouer (défaut 100k).
      max_holdings             : int, concentration (défaut 20).
      allow_fractional_shares  : bool, actions fractionnelles (défaut false).
      sector                   : str, restreint l'univers à un secteur GICS.

    Réponse : {allocations: {TICKER: {weight_pct, amount_usd, shares,
              titan_score, momentum_pct, volatility_pct, imputed_vol, ...}},
              invested_usd, cash_remaining_usd, n_kept, ...}.

    Cas des volatilités manquantes (< 60 bars d'historique) :
      - ≥ 3 tickers avec σ valide → imputation médiane cross-portfolio,
        flag imputed_vol=true sur les tickers concernés.
      - < 3 valid → equal_weight_fallback=true (signal insuffisant pour RP).
    """
    if total_capital <= 0:
        raise HTTPException(400, "total_capital doit être > 0")
    if max_holdings <= 0 or max_holdings > 100:
        raise HTTPException(400, "max_holdings doit être dans ]0, 100]")

    try:
        from data_providers import YFinanceProvider, get_providers
        from modules.portfolio import (
            compute_current_exposure,
            read_open_positions,
            suggest_trade_levels,
        )
        from modules.portfolio_engine import PortfolioManager
        from modules.risk import regime_adjusted_risk
        from modules.sector_metrics import get_scored_universe
        scored = get_scored_universe()
    except Exception as e:
        logger.error(f"[API /portfolio/recommendations] scoring failed: {e}", exc_info=True)
        raise HTTPException(500, f"Scoring indisponible: {e}") from e

    # ── Current portfolio awareness ─────────────────────────────────────
    # 1. Lire positions OPEN → budget dispo = total_capital − déjà investi.
    # 2. Utilisé pour flagger les recos qui poussent un secteur > 30 %.
    # 3. Flag `already_held` si le ticker est déjà en OPEN → l'UI peut afficher
    #    un badge "top-up" plutôt qu'un achat neuf.
    open_positions = read_open_positions()
    current_exposure = compute_current_exposure(open_positions)
    already_invested = current_exposure["total_invested_usd"]
    tickers_open_set = set(current_exposure["tickers_open"])
    current_sector_usd = dict(current_exposure["sector_usd"])
    current_shares_map = {
        p["ticker"]: int(p["size"]) if p["size"] is not None else 0
        for p in open_positions
    }

    # Budget réellement dispo pour de nouvelles positions (jamais négatif).
    # total_capital = taille cible du portefeuille, pas "argent frais à déployer".
    available_budget = max(0.0, total_capital - already_invested)

    # Fix #12 — Régime macro gating.
    # On centralise la logique d'ajustement taille sur `regime_adjusted_risk`
    # (même source que le trading engine legacy → cohérence backtest/paper/live).
    # En cas de macro_state.json manquant/illisible, on degrade à multiplier=1.0
    # mais on flag `regime_unknown=True` pour que l'UI sache pas ignorer.
    macro_raw = api_core.load_macro()
    regime_label: str | None = None
    vix_value: float | None = None
    regime_multiplier = 1.0
    if isinstance(macro_raw, dict) and macro_raw:
        regime_label = (
            macro_raw.get("confirmed_regime")
            or macro_raw.get("regime")
            or macro_raw.get("candidate_regime")
        )
        try:
            vix_value = float(macro_raw.get("vix")) if macro_raw.get("vix") is not None else None
        except (TypeError, ValueError):
            vix_value = None
        if regime_label:
            regime_multiplier = regime_adjusted_risk(
                1.0, regime_label, vix_value if vix_value is not None else 20.0,
            )

    # Circuit breaker drawdown progressif (persisté par le tracker). Compose
    # multiplicativement avec regime_multiplier pour un sizing cohérent entre
    # /recommendations, auto_proposer et tracker. Source de vérité unique.
    cb_multiplier = 1.0
    cb_is_paused = False
    try:
        from modules.tracker.state import read_circuit_breaker_state
        _cb_state = read_circuit_breaker_state()
        cb_is_paused = bool(_cb_state.get("is_paused"))
        cb_multiplier = float(_cb_state.get("size_multiplier", 1.0) or 1.0)
    except Exception as e:
        logger.debug(f"[API /portfolio/recommendations] cb state read failed: {e}")

    effective_multiplier = max(0.0, min(1.0, regime_multiplier * cb_multiplier))
    if cb_is_paused:
        effective_multiplier = 0.0

    # Fix #13 — Staleness globale du run universe.
    # Un run universe > 24h est warning (peut refléter data market fermé week-end),
    # > 48h est severe (rebuild probablement cassé, les sélections sont obsolètes).
    # Exposé dans la réponse pour que l'UI affiche un bandeau visible.
    universe_updated_at: str | None = None
    universe_age_hours: float | None = None
    try:
        if api_core.UNIVERSE_QUANTAMENTAL_PATH.exists():
            _u_raw = json.loads(api_core.UNIVERSE_QUANTAMENTAL_PATH.read_text(encoding="utf-8"))
            universe_updated_at = _u_raw.get("updated_at") if isinstance(_u_raw, dict) else None
            _, _u_age_days = api_core.parse_updated_at(universe_updated_at)
            universe_age_hours = round(_u_age_days * 24.0, 2) if _u_age_days is not None else None
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[API /portfolio/recommendations] universe staleness read failed: {e}")

    # Providers — deux rôles distincts sur le chemin /recommendations :
    #   • market_provider : snapshot prix live (Polygon 1-call ou yfinance batch)
    #     — injecté pour que `shares = floor(amount / price_live)`.
    #   • momentum_provider : batch history 180 jours pour refresh momentum
    #     AVANT le filtre bullish. Polygon free tier = 5 req/min séquentiel,
    #     donc inadapté au batch (60 tickers × 12 s = 12 min). On force
    #     yfinance pour ce rôle (yf.download natif batch, 1 call).
    try:
        _, market_provider = get_providers()
    except Exception as e:
        logger.warning(f"[API /portfolio/recommendations] market provider unavailable: {e}")
        market_provider = None

    if market_provider is not None and market_provider.name == "yfinance":
        momentum_provider = market_provider  # partage le circuit-breaker yf
    else:
        try:
            momentum_provider = YFinanceProvider()
        except Exception as e:
            logger.warning(
                f"[API /portfolio/recommendations] momentum_provider init failed: {e}"
            )
            momentum_provider = None

    if not scored:
        return {
            "allocations":        {},
            "total_capital":      total_capital,
            "invested_usd":       0.0,
            "cash_remaining_usd": total_capital,
            "n_candidates":       0,
            "n_bullish":          0,
            "n_kept":             0,
            "equal_weight_fallback": False,
            "diagnostics": {"reason": "empty_universe"},
        }

    # Filtre secteur optionnel
    if sector:
        target = [t for t, row in scored.items()
                  if (row.get("sector") or "Unknown") == sector.strip()]
    else:
        target = list(scored.keys())

    # Vol-targeting activé en prod : garde σ portefeuille ≈ 14 % annualisée.
    from modules.portfolio._vol_target import DEFAULT_TARGET_VOL_PCT
    pm = PortfolioManager(
        scored,
        market_provider=market_provider,
        momentum_provider=momentum_provider,
        target_vol_pct=DEFAULT_TARGET_VOL_PCT,
    )
    # Si `available_budget` ≤ 0 (déjà full investi) → court-circuit : on renvoie
    # le plan vide explicite plutôt que de laisser PortfolioManager planter sur
    # total_capital=0. Le payload garde les sections macro/exposure pour l'UI.
    if available_budget <= 0.0:
        return _empty_recommendations_payload(
            total_capital=total_capital,
            already_invested=already_invested,
            reason="budget_exhausted_by_open_positions",
            current_exposure=current_exposure,
            regime_label=regime_label,
            vix_value=vix_value,
            regime_multiplier=regime_multiplier,
            universe_updated_at=universe_updated_at,
            universe_age_hours=universe_age_hours,
        )

    try:
        result = pm.calculate_allocations(
            target_tickers=target,
            total_capital=available_budget,
            max_holdings=max_holdings,
            allow_fractional_shares=allow_fractional_shares,
            regime_multiplier=effective_multiplier,
            regime_label=regime_label,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        logger.error(f"[API /portfolio/recommendations] allocation failed: {e}", exc_info=True)
        raise HTTPException(500, f"Allocation impossible: {e}") from e

    # ── Alignement prix live (fix 2026-04-23) ───────────────────────────
    # Le price retourné par le pipeline de scoring vient du market_provider
    # (yfinance snapshot). Le tracker, lui, utilise `get_current_price` qui
    # en BROKER_MODE=alpaca consulte Alpaca temps réel → Alpaca voit le
    # pre-market, yfinance pas toujours. Écart = delta artificiel sur le PnL
    # à l'ouverture. On resynchronise ici sur la SAME source que le tracker
    # pour que shares/amount_usd/SL/TP affichés correspondent à ce que
    # l'execute va réellement poser (plus aucun glissement au click).
    try:
        from modules.alpaca_data import get_latest_prices_batch
        live_prices = get_latest_prices_batch(list(result.allocations.keys()))
    except Exception as e:
        logger.warning(f"[/recommendations] live price batch failed: {e}")
        live_prices = {}

    for t, alloc in result.allocations.items():
        reco_price = alloc.get("price")
        live_px = live_prices.get(t)
        if (reco_price is None or reco_price <= 0
                or live_px is None or live_px <= 0):
            continue
        rel_diff = abs(live_px - reco_price) / reco_price
        if rel_diff <= 0.005:
            continue
        # Resync : entry = live, amount/shares recalculés en gardant le même
        # target_usd (ce que le PortfolioManager a décidé d'allouer).
        target_usd = alloc.get("target_usd") or alloc.get("amount_usd") or 0.0
        if allow_fractional_shares:
            new_shares = round(target_usd / live_px, 4) if live_px > 0 else 0
        else:
            new_shares = int(target_usd // live_px) if live_px > 0 else 0
        new_amount = new_shares * live_px
        alloc["price"] = round(live_px, 4)
        alloc["shares"] = new_shares
        alloc["amount_usd"] = round(new_amount, 2)
        # Traçabilité UI : badge "recalé au live" visible côté front.
        alloc["price_reco_was"] = round(reco_price, 4)
        alloc["price_live_delta_pct"] = round((live_px - reco_price) / reco_price * 100.0, 2)
        alloc["price_source"] = "alpaca_live" if getattr(config, "BROKER_MODE", "paper") == "alpaca" else "yfinance_live"

    # ── Enrichissement post-sizing ──────────────────────────────────────
    # Pour chaque allocation : SL/TP σ-scaled, drapeau already_held, et
    # sector_warning si ajouter cette position pousse le secteur > cap.
    # Le total projeté (current + new) sert de dénominateur pour la %.
    projected_total = already_invested + result.invested_usd
    projected_sector_usd: dict[str, float] = dict(current_sector_usd)
    for alloc in result.allocations.values():
        sec = alloc.get("sector") or "Unknown"
        amount = alloc.get("amount_usd") or 0.0
        projected_sector_usd[sec] = projected_sector_usd.get(sec, 0.0) + amount

    for t, alloc in result.allocations.items():
        # 1. SL/TP σ-scaled à partir de la vol déjà calculée (aucun I/O).
        levels = suggest_trade_levels(
            alloc.get("price"),
            alloc.get("volatility_pct"),
            direction="LONG",
        )
        alloc["suggested_sl"]     = levels["sl"]
        alloc["suggested_tp"]     = levels["tp"]
        alloc["suggested_sl_pct"] = levels["sl_pct"]
        alloc["suggested_tp_pct"] = levels["tp_pct"]
        alloc["levels_method"]    = levels["method"]

        # 2. Flag already_held — si un OPEN existe déjà, l'UI affiche "top-up".
        alloc["already_held"]   = t in tickers_open_set
        alloc["current_shares"] = current_shares_map.get(t, 0)

        # 3. Sector over-exposure warning — pct projeté vs. cap.
        sec = alloc.get("sector") or "Unknown"
        sec_projected = projected_sector_usd.get(sec, 0.0)
        sec_pct = (sec_projected / projected_total * 100.0) if projected_total > 0 else 0.0
        sec_current = current_sector_usd.get(sec, 0.0)
        sec_current_pct = (sec_current / projected_total * 100.0) if projected_total > 0 else 0.0
        alloc["sector_exposure"] = {
            "current_usd":      round(sec_current, 2),
            "current_pct":      round(sec_current_pct, 2),
            "projected_usd":    round(sec_projected, 2),
            "projected_pct":    round(sec_pct, 2),
            "cap_pct":          _SECTOR_EXPOSURE_CAP_PCT,
            "over_cap":         sec_pct > _SECTOR_EXPOSURE_CAP_PCT,
        }

    # Seuils heures (Fix #13) — contrairement à /api/universe qui monitore les
    # rebuilds hebdomadaires (_UNIVERSE_STALE_THRESHOLD_DAYS=7), ici on raisonne
    # daily : le cron universe_scheduler tourne tous les jours à 06:00 UTC.
    UNIVERSE_STALE_HOURS_WARNING = 24.0
    UNIVERSE_STALE_HOURS_SEVERE = 48.0
    universe_stale = (universe_age_hours is not None
                      and universe_age_hours >= UNIVERSE_STALE_HOURS_WARNING)
    universe_severe = (universe_age_hours is not None
                       and universe_age_hours >= UNIVERSE_STALE_HOURS_SEVERE)

    payload = result.to_dict()
    # Le champ `total_capital` retourné par AllocationResult est le budget
    # passé à PortfolioManager (= available_budget). On surcharge pour exposer
    # à l'UI le *total* visé par l'utilisateur + le détail de ce qui reste.
    payload["total_capital"] = total_capital
    payload["budget_available_usd"] = round(available_budget, 2)
    payload["already_invested_usd"] = round(already_invested, 2)
    payload["cash_remaining_usd"]   = round(
        total_capital - already_invested - result.invested_usd, 2,
    )
    payload["current_portfolio"] = {
        "n_open_positions":    current_exposure["n_open_positions"],
        "total_invested_usd":  current_exposure["total_invested_usd"],
        "sector_usd":          current_exposure["sector_usd"],
        "tickers_open":        current_exposure["tickers_open"],
    }
    payload["macro"] = {
        "regime":      regime_label,
        "vix":         vix_value,
        "multiplier":  regime_multiplier,
        "blocks_new_entries": effective_multiplier == 0.0,
        "regime_unknown": regime_label is None,
    }
    payload["circuit_breaker"] = {
        "size_multiplier": cb_multiplier,
        "is_paused":       cb_is_paused,
    }
    payload["effective_multiplier"] = effective_multiplier
    payload["universe_staleness"] = {
        "updated_at":              universe_updated_at,
        "age_hours":               universe_age_hours,
        "stale":                   universe_stale,
        "severe":                  universe_severe,
        "warning_threshold_hours": UNIVERSE_STALE_HOURS_WARNING,
        "severe_threshold_hours":  UNIVERSE_STALE_HOURS_SEVERE,
    }
    return payload


# ─────────────────────────────────────────────────────────────────────
# HELPERS — shape empty response + execute bulk trades
# ─────────────────────────────────────────────────────────────────────

def _empty_recommendations_payload(
    *,
    total_capital: float,
    already_invested: float,
    reason: str,
    current_exposure: dict[str, Any],
    regime_label: str | None,
    vix_value: float | None,
    regime_multiplier: float,
    universe_updated_at: str | None,
    universe_age_hours: float | None,
) -> dict[str, Any]:
    """Réponse vide (budget épuisé / univers vide / régime bloquant) mais
    structurée comme un succès — évite des branches spéciales côté frontend.
    """
    return {
        "allocations":        {},
        "total_capital":      total_capital,
        "budget_available_usd": round(max(0.0, total_capital - already_invested), 2),
        "already_invested_usd": round(already_invested, 2),
        "invested_usd":       0.0,
        "cash_remaining_usd": round(max(0.0, total_capital - already_invested), 2),
        "n_candidates":       0,
        "n_bullish":          0,
        "n_kept":             0,
        "equal_weight_fallback": False,
        "diagnostics":        {"reason": reason},
        "current_portfolio": {
            "n_open_positions":    current_exposure["n_open_positions"],
            "total_invested_usd":  current_exposure["total_invested_usd"],
            "sector_usd":          current_exposure["sector_usd"],
            "tickers_open":        current_exposure["tickers_open"],
        },
        "macro": {
            "regime":      regime_label,
            "vix":         vix_value,
            "multiplier":  regime_multiplier,
            "blocks_new_entries": regime_multiplier == 0.0,
            "regime_unknown": regime_label is None,
        },
        "universe_staleness": {
            "updated_at":              universe_updated_at,
            "age_hours":               universe_age_hours,
            "stale":                   False,
            "severe":                  False,
            "warning_threshold_hours": 24.0,
            "severe_threshold_hours":  48.0,
        },
    }


