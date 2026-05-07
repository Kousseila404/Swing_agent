"""Module — auto-proposer.

Génère un lot de propositions d'achat à partir du moteur d'allocation TITAN,
sous condition que **toutes** les gates de sécurité soient au vert :

    1. Killswitch nuclear non posé
    2. Circuit breaker drawdown pas en pause
    3. Régime macro ∈ liste autorisée (défaut ["BULL_MARKET"])
    4. Multiplier macro > 0 (donc pas CRASH_PANIC)
    5. Univers pas en stale severe (> 48 h)
    6. Slots libres = max_holdings − len(open_positions) ≥ min_free_slots
    7. Cash dispo ≥ MIN_PROPOSAL_USD

Si une gate échoue → aucune proposition générée, audit retourné avec la raison.
Sinon → top-N par titan_score parmi les bullish, hors `already_held` et hors
`sector_over_cap`, jusqu'à combler les slots libres disponibles.

Pure : pas d'I/O HTTP. Persiste les propositions via `proposals.enqueue_batch`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from modules import api_core, proposals
from modules.buy_signal import compute_buy_signal
from modules.log import logger
from modules.support_score import compute_support_score


def _days_until_earnings(next_earnings_date: Any) -> int | None:
    """Retourne le nombre de jours d'ici le prochain earnings (peut être négatif
    si la date est passée et pas encore mise à jour). None si parsing échoue.
    """
    if not next_earnings_date:
        return None
    s = str(next_earnings_date)[:10]
    from datetime import date as _date
    try:
        d = _date.fromisoformat(s)
    except ValueError:
        return None
    return (d - _date.today()).days


def _compute_support_for_ticker(ticker: str, price: float | None) -> dict[str, Any]:
    """Wrapper safe pour le calcul du support score à la volée.

    Ne JAMAIS lever d'exception : un échec de lecture OHLCV ne doit pas bloquer
    la génération de proposition. Retourne un dict vide-équivalent dans ce cas.
    """
    try:
        from modules.market_db import read_ohlcv
        history = read_ohlcv(ticker, days=300)
        # Fallback yfinance si DuckDB vide (mêmes 300j que le module Lot 7).
        if history is None or history.empty:
            try:
                import yfinance as yf
                df = yf.download(ticker, period="14mo", interval="1d",
                                 progress=False, auto_adjust=True, threads=False)
                if df is not None and not df.empty:
                    if isinstance(df.columns, type(df.columns)) and hasattr(df.columns, "get_level_values"):
                        try:
                            df.columns = df.columns.get_level_values(0)
                        except Exception:
                            pass
                    history = df
            except Exception:
                history = None
        result = compute_support_score(
            ticker=ticker,
            current_price=price,
            history=history,
        )
        return result.to_dict()
    except Exception as e:
        logger.debug(f"[support_score] {ticker} échec : {e}")
        return {"score": 0.0, "level": "INSUFFICIENT_DATA", "method": "insufficient_data"}

# ─────────────────────────────────────────────────────────────────
# CONSTANTES — defaults conservateurs
# ─────────────────────────────────────────────────────────────────
DEFAULT_ALLOWED_REGIMES = ("BULL_MARKET",)
DEFAULT_MAX_HOLDINGS = 20
DEFAULT_MIN_FREE_SLOTS = 1
DEFAULT_MIN_PROPOSAL_USD = 250.0  # plancher : ne propose pas en-dessous
DEFAULT_TOTAL_CAPITAL = 100_000.0
UNIVERSE_SEVERE_HOURS = 48.0

# Top-N mode — pilote le nombre de candidats retenus :
#   "free_slots"   → min(max_holdings - n_open, n_bullish) — cron quotidien (défaut).
#   "max_holdings" → jusqu'à max_holdings candidats (rebalance complet UI).
DEFAULT_TOP_N_MODE = "free_slots"

# Earnings calendar gate (Lot 16) — skip un ticker dont les earnings tombent
# dans < EARNINGS_BLACKOUT_DAYS jours. Un earnings = catalyst binaire (gap
# ±10-20 % fréquent), incompatible avec une thèse Quantamental LT à conviction.
# 0 ou None = gate désactivée (test/legacy).
EARNINGS_BLACKOUT_DAYS = 7

# Weighting method par défaut pour les propositions cron : "hrp" intègre la
# corrélation inter-tickers dans l'allocation (López de Prado 2016) au lieu de
# la traiter en post-hoc haircut comme le faisait le 1/σ legacy. Fallback
# automatique vers risk_parity si scipy/data manquent (cf. _hrp.py).
DEFAULT_WEIGHTING_METHOD = "hrp"


@dataclass
class GateResult:
    name: str
    ok: bool
    detail: str
    value: Any = None


@dataclass
class ProposerResult:
    """Résultat d'un run du proposer (avant ou après enqueue)."""
    ok: bool
    gates: list[GateResult]
    proposals: list[dict[str, Any]]  # list of asdict(Proposal)
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "gates": [
                {"name": g.name, "ok": g.ok, "detail": g.detail, "value": g.value}
                for g in self.gates
            ],
            "proposals": self.proposals,
            "diagnostics": self.diagnostics,
        }


# ─────────────────────────────────────────────────────────────────
# GATES
# ─────────────────────────────────────────────────────────────────

def _gate_killswitch() -> GateResult:
    try:
        from modules.tracker.killswitch import is_trading_allowed
        allowed = is_trading_allowed()
    except Exception as e:
        logger.warning(f"[AutoProposer] killswitch check failed: {e}")
        # Fail-CLOSED : si on ne peut pas vérifier, on bloque.
        return GateResult("killswitch", False, f"check_failed: {e}")
    return GateResult(
        "killswitch", allowed,
        "trading_allowed" if allowed else "TRADING_BLOCKED — nuclear stop",
    )


def _gate_circuit_breaker() -> GateResult:
    try:
        from modules.tracker.state import read_circuit_breaker_state
        state = read_circuit_breaker_state()
    except Exception as e:
        return GateResult("circuit_breaker", True, f"check_failed (fail-open): {e}")
    if state.get("is_paused"):
        return GateResult(
            "circuit_breaker", False,
            f"PAUSED — drawdown vs peak (peak=${state.get('peak_equity', 0):,.0f})",
            value=state,
        )
    mult = float(state.get("size_multiplier", 1.0) or 1.0)
    return GateResult(
        "circuit_breaker", True,
        f"ok (size_multiplier={mult:.2f})",
        value={**state, "size_multiplier": mult},
    )


def _gate_regime(allowed_regimes: tuple[str, ...]) -> tuple[GateResult, dict[str, Any]]:
    """Retourne (gate, macro_meta) — macro_meta réutilisé pour le sizing."""
    macro_raw = api_core.load_macro()
    regime_label = None
    vix = None
    if isinstance(macro_raw, dict) and macro_raw:
        regime_label = (
            macro_raw.get("confirmed_regime")
            or macro_raw.get("regime")
            or macro_raw.get("candidate_regime")
        )
        try:
            vix = float(macro_raw.get("vix")) if macro_raw.get("vix") is not None else None
        except (TypeError, ValueError):
            vix = None

    meta = {"regime_label": regime_label, "vix": vix}

    if not regime_label:
        return GateResult(
            "macro_regime", False,
            "regime inconnu (macro_state.json absent ou incomplet)",
        ), meta
    if regime_label not in allowed_regimes:
        return GateResult(
            "macro_regime", False,
            f"{regime_label} ∉ {list(allowed_regimes)}",
            value=regime_label,
        ), meta
    return GateResult(
        "macro_regime", True,
        f"{regime_label} (allowed)",
        value=regime_label,
    ), meta


def _gate_regime_multiplier(macro_meta: dict[str, Any]) -> tuple[GateResult, float]:
    from modules.risk import regime_adjusted_risk
    regime = macro_meta.get("regime_label")
    vix = macro_meta.get("vix") or 20.0
    if not regime:
        return GateResult("regime_multiplier", False, "regime inconnu"), 0.0
    mult = regime_adjusted_risk(1.0, regime, vix)
    if mult <= 0.0:
        return GateResult(
            "regime_multiplier", False,
            f"multiplier={mult} (régime {regime} bloque les nouvelles entrées)",
            value=mult,
        ), mult
    return GateResult(
        "regime_multiplier", True,
        f"multiplier={mult:.2f}",
        value=mult,
    ), mult


def _gate_universe_freshness() -> GateResult:
    try:
        if not api_core.UNIVERSE_QUANTAMENTAL_PATH.exists():
            return GateResult(
                "universe_freshness", False, "universe.json absent"
            )
        raw = json.loads(
            api_core.UNIVERSE_QUANTAMENTAL_PATH.read_text(encoding="utf-8")
        )
        updated_at = raw.get("updated_at") if isinstance(raw, dict) else None
        _, age_days = api_core.parse_updated_at(updated_at)
        age_hours = age_days * 24.0 if age_days is not None else None
    except (json.JSONDecodeError, OSError) as e:
        return GateResult("universe_freshness", False, f"read_failed: {e}")

    if age_hours is None:
        return GateResult("universe_freshness", False, "updated_at non parsable")
    if age_hours >= UNIVERSE_SEVERE_HOURS:
        return GateResult(
            "universe_freshness", False,
            f"stale severe ({age_hours:.1f}h ≥ {UNIVERSE_SEVERE_HOURS}h)",
            value=age_hours,
        )
    return GateResult(
        "universe_freshness", True,
        f"age={age_hours:.1f}h",
        value=age_hours,
    )


def _gate_free_slots(
    n_open: int, max_holdings: int, min_free_slots: int,
) -> tuple[GateResult, int]:
    free = max(0, max_holdings - n_open)
    if free < min_free_slots:
        return GateResult(
            "free_slots", False,
            f"{free}/{max_holdings} libres (< min={min_free_slots})",
            value=free,
        ), free
    return GateResult(
        "free_slots", True,
        f"{free}/{max_holdings} libres",
        value=free,
    ), free


def _gate_cash(available_budget: float, min_proposal_usd: float) -> GateResult:
    if available_budget < min_proposal_usd:
        return GateResult(
            "cash_available", False,
            f"${available_budget:,.0f} < min ${min_proposal_usd:,.0f}",
            value=available_budget,
        )
    return GateResult(
        "cash_available", True,
        f"${available_budget:,.0f} dispo",
        value=available_budget,
    )


# ─────────────────────────────────────────────────────────────────
# PROPOSAL BUILDING
# ─────────────────────────────────────────────────────────────────

def _build_proposal_from_alloc(
    ticker: str,
    alloc: dict[str, Any],
    *,
    macro_meta: dict[str, Any],
    ttl_hours: float | None,
) -> proposals.Proposal | None:
    """Convertit une allocation enrichie (issue de PortfolioManager + suggested_levels)
    en Proposal prête à enqueuer. Retourne None si l'allocation est inutilisable
    (size=0, prix invalides, sl/tp non calculables).
    """
    size = int(alloc.get("shares") or 0)
    entry = alloc.get("price")
    sl = alloc.get("suggested_sl")
    tp = alloc.get("suggested_tp")
    if size <= 0 or not (entry and sl and tp):
        return None
    if not (sl < entry < tp):
        # Garde-fou : si suggest_trade_levels a renvoyé du bizarre, on skip.
        return None

    context = {
        "titan_score":        alloc.get("titan_score"),
        "weight_pct":         alloc.get("weight_pct"),
        "amount_usd":         alloc.get("amount_usd"),
        "momentum_pct":       alloc.get("momentum_pct"),
        "volatility_pct":     alloc.get("volatility_pct"),
        "imputed_vol":        alloc.get("imputed_vol"),
        "price_live":         alloc.get("price_live"),
        "price_source":       alloc.get("price_source"),
        "levels_method":      alloc.get("levels_method"),
        "suggested_sl_pct":   alloc.get("suggested_sl_pct"),
        "suggested_tp_pct":   alloc.get("suggested_tp_pct"),
        "support":            alloc.get("support") or {},
        "sector_exposure":    alloc.get("sector_exposure"),
        "already_held":       bool(alloc.get("already_held")),
        "current_shares":     int(alloc.get("current_shares") or 0),
        "fundamentals_age_days": alloc.get("fundamentals_age_days"),
        "fundamentals_stale": alloc.get("fundamentals_stale"),
        "macro_regime":       macro_meta.get("regime_label"),
        "vix":                macro_meta.get("vix"),
        # Lot 15 — piliers + flags pour la capture d'entrée et la corrélation
        # future outcome WIN/LOSS ↔ scores (performance_attribution, Phase 2).
        "quality_score":      alloc.get("quality_score"),
        "value_score":        alloc.get("value_score"),
        "risk_score":         alloc.get("risk_score"),
        "momentum_score":     alloc.get("momentum_score"),
        "piotroski_score":    alloc.get("piotroski_score"),
        "growth_score":       alloc.get("growth_score"),
        "f_score":            alloc.get("f_score"),
        "f_score_max":        alloc.get("f_score_max"),
        "titan_tilt_flags":   alloc.get("titan_tilt_flags") or [],
        "titan_tilt_adjust":  alloc.get("titan_tilt_adjust"),
        # Lot 16 — Revisions + Earnings calendar pour audit / UI.
        "revisions_score":    alloc.get("revisions_score"),
        "next_earnings_date": alloc.get("next_earnings_date"),
        "days_until_earnings": alloc.get("days_until_earnings"),
        # Lot 18 — Buy Signal verdict pour badge UI prominent.
        "buy_signal":         alloc.get("buy_signal"),
    }

    return proposals.make_proposal(
        ticker=ticker,
        direction="LONG",
        entry=float(entry),
        stop_loss=float(sl),
        take_profit=float(tp),
        size=size,
        sector=str(alloc.get("sector") or ""),
        signal="AUTO_PROPOSAL",
        context=context,
        ttl_hours=ttl_hours,
    )


# ─────────────────────────────────────────────────────────────────
# ORCHESTRATION
# ─────────────────────────────────────────────────────────────────

def plan_proposals(
    *,
    total_capital: float = DEFAULT_TOTAL_CAPITAL,
    max_holdings: int = DEFAULT_MAX_HOLDINGS,
    min_free_slots: int = DEFAULT_MIN_FREE_SLOTS,
    min_proposal_usd: float = DEFAULT_MIN_PROPOSAL_USD,
    allowed_regimes: tuple[str, ...] = DEFAULT_ALLOWED_REGIMES,
    ttl_hours: float | None = None,
    allow_fractional_shares: bool = False,
    include_held: bool = False,
    top_n_mode: str = DEFAULT_TOP_N_MODE,
    earnings_blackout_days: int | None = EARNINGS_BLACKOUT_DAYS,
    weighting_method: str = DEFAULT_WEIGHTING_METHOD,
) -> ProposerResult:
    """Évalue les gates et, si toutes OK, génère les propositions à enqueuer.

    NE PERSISTE RIEN — c'est le caller (router /refresh) qui appelle
    `proposals.enqueue_batch(result.proposals)` après inspection.

    Args:
      allow_fractional_shares: passe PortfolioManager.allow_fractional_shares.
        Utile pour "Générer un plan complet" côté UI quand le broker supporte
        les fractions. Par défaut False (cron → ordres entiers).
      include_held: si True, les tickers déjà OPEN restent dans la file avec
        le flag `already_held=True` et c'est l'UI/approve qui décide du top-up.
        Par défaut False (cron skip, comportement historique).
      top_n_mode: "free_slots" (défaut cron, cap à max_holdings-n_open) ou
        "max_holdings" (cap au max_holdings absolu, cible UI "rebalance complet").
        NB : le filtre sector_over_cap est toujours EXPOSÉ (jamais filtré) —
        `sector_exposure.over_cap=True` pousse le ticker dans la file et
        `approve_batch` refuse sans ack explicite.

    Retourne `ProposerResult` :
      - `ok=False` si une gate bloque (proposals=[]).
      - `ok=True` si gates passent, avec proposals = top-N candidats (incluant
        over_cap et éventuellement already_held selon include_held).
    """
    gates: list[GateResult] = []
    diagnostics: dict[str, Any] = {
        "params": {
            "total_capital":     total_capital,
            "max_holdings":      max_holdings,
            "min_free_slots":    min_free_slots,
            "min_proposal_usd":  min_proposal_usd,
            "allowed_regimes":   list(allowed_regimes),
            "ttl_hours":         ttl_hours,
            "allow_fractional_shares": allow_fractional_shares,
            "include_held":      include_held,
            "top_n_mode":        top_n_mode,
            "earnings_blackout_days": earnings_blackout_days,
            "weighting_method":  weighting_method,
        },
    }

    # Gates indépendantes des données portfolio — short-circuit dès qu'une bloque.
    g_ks = _gate_killswitch()
    gates.append(g_ks)
    if not g_ks.ok:
        return ProposerResult(False, gates, [], diagnostics)

    g_cb = _gate_circuit_breaker()
    gates.append(g_cb)
    if not g_cb.ok:
        return ProposerResult(False, gates, [], diagnostics)
    # Honorer le size_multiplier du circuit breaker (drawdown progressif) en
    # plus du régime macro — on multiplie les deux, minimum = 0.
    cb_multiplier = float((g_cb.value or {}).get("size_multiplier", 1.0) or 1.0)
    diagnostics["circuit_breaker_multiplier"] = cb_multiplier

    g_regime, macro_meta = _gate_regime(allowed_regimes)
    gates.append(g_regime)
    if not g_regime.ok:
        return ProposerResult(False, gates, [], diagnostics)

    g_mult, regime_multiplier = _gate_regime_multiplier(macro_meta)
    gates.append(g_mult)
    if not g_mult.ok:
        return ProposerResult(False, gates, [], diagnostics)

    g_univ = _gate_universe_freshness()
    gates.append(g_univ)
    if not g_univ.ok:
        return ProposerResult(False, gates, [], diagnostics)

    # Gates qui dépendent du portefeuille courant
    try:
        from modules.portfolio import (
            compute_current_exposure,
            read_open_positions,
            suggest_trade_levels,
        )
        from modules.portfolio._sector_cap import DEFAULT_SECTOR_CAP
        from modules.portfolio_engine import PortfolioManager
        from modules.sector_metrics import get_scored_universe
    except Exception as e:
        gates.append(GateResult("imports", False, f"failed: {e}"))
        return ProposerResult(False, gates, [], diagnostics)

    open_positions = read_open_positions()
    exposure = compute_current_exposure(open_positions)
    n_open = exposure["n_open_positions"]
    already_invested = exposure["total_invested_usd"]
    available_budget = max(0.0, total_capital - already_invested)
    diagnostics["portfolio"] = {
        "n_open_positions":     n_open,
        "already_invested_usd": already_invested,
        "available_budget_usd": round(available_budget, 2),
        "tickers_open":         exposure["tickers_open"],
    }

    g_slots, free_slots = _gate_free_slots(n_open, max_holdings, min_free_slots)
    gates.append(g_slots)
    if not g_slots.ok:
        return ProposerResult(False, gates, [], diagnostics)

    g_cash = _gate_cash(available_budget, min_proposal_usd)
    gates.append(g_cash)
    if not g_cash.ok:
        return ProposerResult(False, gates, [], diagnostics)

    # Toutes les gates passent — calcul des allocations.
    try:
        scored = get_scored_universe()
    except Exception as e:
        gates.append(GateResult("scored_universe", False, f"failed: {e}"))
        return ProposerResult(False, gates, [], diagnostics)

    if not scored:
        gates.append(GateResult("scored_universe", False, "univers vide"))
        return ProposerResult(False, gates, [], diagnostics)
    gates.append(GateResult(
        "scored_universe", True, f"{len(scored)} tickers scorés",
        value=len(scored),
    ))

    try:
        from data_providers import YFinanceProvider, get_providers
        try:
            _, market_provider = get_providers()
        except Exception as e:
            logger.warning(f"[AutoProposer] market provider unavailable: {e}")
            market_provider = None
        if market_provider is not None and market_provider.name == "yfinance":
            momentum_provider = market_provider
        else:
            try:
                momentum_provider = YFinanceProvider()
            except Exception as e:
                logger.warning(f"[AutoProposer] momentum_provider init failed: {e}")
                momentum_provider = None
    except Exception as e:
        logger.warning(f"[AutoProposer] providers import failed: {e}")
        market_provider = None
        momentum_provider = None

    from modules.portfolio._vol_target import DEFAULT_TARGET_VOL_PCT
    pm = PortfolioManager(
        scored,
        market_provider=market_provider,
        momentum_provider=momentum_provider,
        target_vol_pct=DEFAULT_TARGET_VOL_PCT,
        weighting_method=weighting_method,
    )
    # Effective multiplier = régime macro × circuit breaker progressif.
    # Un VIX>25 (macro 0.75) + un drawdown -3 % (cb 0.5) = 0.375 de déploiement.
    effective_multiplier = max(0.0, min(1.0, regime_multiplier * cb_multiplier))
    diagnostics["effective_multiplier"] = effective_multiplier

    try:
        result = pm.calculate_allocations(
            target_tickers=list(scored.keys()),
            total_capital=available_budget,
            max_holdings=max_holdings,
            allow_fractional_shares=allow_fractional_shares,
            regime_multiplier=effective_multiplier,
            regime_label=macro_meta.get("regime_label"),
        )
    except Exception as e:
        logger.error(f"[AutoProposer] allocation failed: {e}", exc_info=True)
        gates.append(GateResult("allocation", False, f"failed: {e}"))
        return ProposerResult(False, gates, [], diagnostics)

    diagnostics["allocation"] = {
        "n_candidates":         result.n_candidates,
        "n_bullish":            result.n_bullish,
        "n_kept":               result.n_kept,
        "invested_usd":         result.invested_usd,
        "equal_weight_fallback": result.equal_weight_fallback,
        "weight_method":        result.diagnostics.get("weight_method"),
    }

    if not result.allocations:
        gates.append(GateResult(
            "allocations", False,
            f"aucune allocation (reason={result.diagnostics.get('reason', 'unknown')})",
        ))
        return ProposerResult(True, gates, [], diagnostics)

    # Enrichissement post-sizing : exposure projeté + suggested levels + filtre
    # already_held / over_cap. On RECRÉE la projection localement plutôt que de
    # dépendre du router pour rester pure.
    tickers_open_set = set(exposure["tickers_open"])
    current_sector_usd = dict(exposure["sector_usd"])
    projected_total = already_invested + result.invested_usd
    projected_sector_usd = dict(current_sector_usd)
    for _ticker, alloc in result.allocations.items():
        sec = alloc.get("sector") or "Unknown"
        amount = alloc.get("amount_usd") or 0.0
        projected_sector_usd[sec] = projected_sector_usd.get(sec, 0.0) + amount

    # Tri par titan_score décroissant pour combler les slots libres en priorité.
    ranked = sorted(
        result.allocations.items(),
        key=lambda kv: kv[1].get("titan_score") or 0.0,
        reverse=True,
    )

    # Cap du nombre de candidats retenus — cron = free_slots (combler), UI
    # "rebalance complet" = max_holdings (tout le panier).
    if top_n_mode == "max_holdings":
        cap = max(0, max_holdings)
    else:
        cap = free_slots

    skipped: list[dict[str, Any]] = []
    proposals_to_enqueue: list[proposals.Proposal] = []

    for ticker, alloc in ranked:
        if len(proposals_to_enqueue) >= cap:
            break

        # Filtre 1 : déjà détenu — skippé uniquement en mode cron (include_held=False).
        # En mode UI (include_held=True), le ticker reste dans la file avec le
        # flag `already_held=True` → l'utilisateur décide du top-up via approve_batch.
        already_held = ticker in tickers_open_set
        if already_held and not include_held:
            skipped.append({"ticker": ticker, "reason": "already_held"})
            continue

        # Calcul projection secteur — utilise le total projeté pour la %. Avec
        # include_held=True on additionne les top-ups au secteur courant.
        sec = alloc.get("sector") or "Unknown"
        sec_proj = projected_sector_usd.get(sec, 0.0)
        sec_pct = (sec_proj / projected_total * 100.0) if projected_total > 0 else 0.0
        over_cap = sec_pct > DEFAULT_SECTOR_CAP * 100.0
        sec_current = current_sector_usd.get(sec, 0.0)
        sec_current_pct = (sec_current / projected_total * 100.0) if projected_total > 0 else 0.0

        # Compute SL/TP via le même helper que le router /recommendations.
        levels = suggest_trade_levels(
            alloc.get("price"),
            alloc.get("volatility_pct"),
            direction="LONG",
        )

        # Support score composite — informatif pour l'utilisateur (règle manuelle
        # "TITAN > 80 + support"). Lecture OHLCV depuis DuckDB ; fallback yfinance
        # via try/except interne pour ne PAS bloquer la génération de proposition.
        support = _compute_support_for_ticker(ticker, alloc.get("price"))
        current_shares = 0
        if already_held:
            try:
                current_shares = int(
                    next(
                        (p["size"] for p in open_positions if p.get("ticker") == ticker),
                        0,
                    )
                    or 0,
                )
            except (TypeError, ValueError):
                current_shares = 0

        # Lot 18 — Buy Signal calc (verdict prominent UI).
        # On enrichit avec scored[ticker] pour récupérer earnings/insider_score
        # qui peuvent ne pas être présents dans alloc tel quel.
        scored_row = scored.get(ticker) or {}
        buy_input = {
            **scored_row,
            "support": support,
            "next_earnings_date": scored_row.get("next_earnings_date"),
        }
        buy_signal_data = compute_buy_signal(buy_input).to_dict()

        alloc = {
            **alloc,
            "suggested_sl":     levels["sl"],
            "suggested_tp":     levels["tp"],
            "suggested_sl_pct": levels["sl_pct"],
            "suggested_tp_pct": levels["tp_pct"],
            "levels_method":    levels["method"],
            "support":          support,
            "buy_signal":       buy_signal_data,
            "already_held":     already_held,
            "current_shares":   current_shares,
            "sector_exposure":  {
                "current_usd":   round(sec_current, 2),
                "current_pct":   round(sec_current_pct, 2),
                "projected_usd": round(sec_proj, 2),
                "projected_pct": round(sec_pct, 2),
                "cap_pct":       DEFAULT_SECTOR_CAP * 100.0,
                "over_cap":      over_cap,
            },
        }

        # Filtre 2 : montant minimum (un trade < min_proposal_usd n'a pas de
        # sens vs slippage/frais). On garde ce filtre dur : une reco à 50$ est
        # toujours du bruit, pas besoin d'exposer à l'utilisateur.
        if (alloc.get("amount_usd") or 0.0) < min_proposal_usd:
            skipped.append({
                "ticker": ticker, "reason": "below_min_proposal_usd",
                "amount_usd": alloc.get("amount_usd"),
            })
            continue

        # Filtre 3 (Lot 16) : earnings blackout. Si le prochain earnings tombe
        # dans < earnings_blackout_days, on skip — un earnings est un catalyst
        # binaire (gap ±15 % fréquent) incompatible avec une thèse LT.
        next_earn = (scored.get(ticker) or {}).get("next_earnings_date")
        days_to_e = _days_until_earnings(next_earn)
        alloc["next_earnings_date"] = next_earn
        alloc["days_until_earnings"] = days_to_e
        if (
            earnings_blackout_days is not None
            and earnings_blackout_days > 0
            and days_to_e is not None
            and 0 <= days_to_e < earnings_blackout_days
        ):
            skipped.append({
                "ticker": ticker, "reason": "earnings_blackout",
                "days_until_earnings": days_to_e,
            })
            continue

        # `over_cap` n'est PLUS un filtre — le flag est exposé dans
        # context.sector_exposure.over_cap et `approve_batch` refuse sans ack
        # explicite côté UI. Ça permet à l'utilisateur de voir le top-N complet
        # et d'arbitrer (ex: override un sector cap si conviction forte).

        prop = _build_proposal_from_alloc(
            ticker, alloc, macro_meta=macro_meta, ttl_hours=ttl_hours,
        )
        if prop is None:
            skipped.append({"ticker": ticker, "reason": "alloc_invalid"})
            continue
        proposals_to_enqueue.append(prop)

    diagnostics["selection"] = {
        "free_slots":            free_slots,
        "cap":                   cap,
        "top_n_mode":            top_n_mode,
        "include_held":          include_held,
        "n_proposals":           len(proposals_to_enqueue),
        "n_skipped":             len(skipped),
        "skipped":               skipped[:20],  # cap l'audit
    }

    return ProposerResult(
        True, gates,
        [p.__dict__ for p in proposals_to_enqueue],
        diagnostics,
    )


def run_and_enqueue(**kwargs: Any) -> ProposerResult:
    """Convenience : `plan_proposals(...)` + `enqueue_batch` si proposals.

    Retourne le ProposerResult (proposals filtrées par dédup éventuelle dans
    enqueue_batch).
    """
    result = plan_proposals(**kwargs)
    if not result.proposals:
        return result

    # Reconstruit des Proposal depuis les dicts retournés par plan_proposals
    # pour appeler enqueue_batch (signature attend des Proposal).
    proposals_objs = [proposals.Proposal(**p) for p in result.proposals]
    inserted = proposals.enqueue_batch(proposals_objs)
    result.diagnostics["enqueued"] = {
        "n_planned":  len(proposals_objs),
        "n_inserted": len(inserted),
        "n_dedup":    len(proposals_objs) - len(inserted),
    }
    # Met à jour la liste retournée pour ne refléter que ce qui a été inséré.
    result.proposals = inserted
    return result
