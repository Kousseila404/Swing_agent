"""
╔══════════════════════════════════════════════════════════════════╗
║  ROUTER — MON PORTEFEUILLE (book personnel, hors univers TITAN)  ║
║  GET  /api/my_portfolio                                          ║
║                                                                  ║
║  10 positions long terme statiques (voir modules/my_portfolio_   ║
║  data.py) + prix live pour calculer le poids réel et détecter la ║
║  dérive vs poids cible (rééquilibrage).                          ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from fastapi import APIRouter, Security

from modules import api_core
from modules.log import logger
from modules.my_portfolio_data import (
    CASH_RESERVE_AMOUNT,
    CASH_RESERVE_PCT,
    DEPLOYMENT_THRESHOLD_PCT,
    POSITIONS,
    REBALANCE_DRIFT_THRESHOLD_PCT,
    TOTAL_ENVELOPE_AMOUNT,
    WATCHLIST,
)
from modules.my_portfolio_earnings import get_earnings_snapshot
from modules.portfolio_risk import load_snapshot as load_risk_snapshot
from modules.tracker.market import get_current_price_detailed, get_fx_rate

router = APIRouter(prefix="/api", tags=["my_portfolio"])

# Valeurs par défaut quand le job hebdo (`--recompute-portfolio-risk`) n'a
# encore jamais tourné pour ce ticker — jamais halluciner un beta/corrélation,
# voir Upgrade 1 (docs/UPGRADES_MY_PORTFOLIO.md).
_RISK_FIELDS_DEFAULT: dict[str, Any] = {
    "beta_recalculated": None,
    "beta_diff_pct": None,
    "beta_flag": False,
    "avg_correlation": None,
    "correlation_vs_ref": None,
    "correlation_alert_triggered": False,
    "correlation_streak_weeks": 0,
    "data_quality": None,
}


def _risk_fields(ticker: str, risk_by_ticker: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return risk_by_ticker.get(ticker, _RISK_FIELDS_DEFAULT)

# Fenêtre d'affichage du badge earnings — voir docs/UPGRADES_MY_PORTFOLIO.md
# Upgrade 2 : "à venir" jusqu'à 14j avant, "résultats publiés" pendant les 5j
# qui suivent (état transitoire, évite la disparition instantanée du badge
# comme dans le bug initial LNVGY sans le laisser traîner indéfiniment).
_EARNINGS_UPCOMING_MAX_DAYS = 14
_EARNINGS_JUST_REPORTED_MIN_DAYS = -5


def _earnings_badge(days_until: int | None, next_date: str | None) -> str | None:
    if days_until is None:
        return None
    if 0 <= days_until <= _EARNINGS_UPCOMING_MAX_DAYS:
        return f"📅 Earnings dans {days_until} j"
    if _EARNINGS_JUST_REPORTED_MIN_DAYS <= days_until < 0:
        return f"✅ Résultats publiés le {next_date}"
    return None

# Paire yfinance à interroger par devise native, et comment l'appliquer pour
# obtenir un multiplicateur "× taux → USD" (EURUSD=X cote déjà en USD par
# EUR ; USDHKD=X cote en HKD par USD, donc on inverse).
_FX_PAIR_FOR_CURRENCY = {"EUR": "EURUSD=X", "HKD": "USDHKD=X"}


def _usd_multiplier(currency: str) -> float | None:
    if currency == "USD":
        return 1.0
    pair = _FX_PAIR_FOR_CURRENCY.get(currency)
    if pair is None:
        logger.error(f"[my_portfolio] Devise {currency} sans paire FX configurée")
        return None
    rate = get_fx_rate(pair)
    if rate is None or rate <= 0:
        return None
    return rate if currency == "EUR" else 1.0 / rate


def _safe_price(p: dict) -> tuple[float | None, str | None, float | None, float | None]:
    """Prix live d'une position, converti en USD, + timestamp source (as_of).

    Expose aussi le prix natif brut (`raw_price`, pré-ADR/pré-FX) et le
    multiplicateur FX utilisé (`fx`) — réutilisés par `_rebalance_order`
    pour exprimer un ordre dans la devise native sans double conversion
    silencieuse ni nouveau fetch réseau (voir Upgrade 3,
    docs/UPGRADES_MY_PORTFOLIO.md).
    """
    ticker       = p["ticker"]
    price_ticker = p.get("price_ticker", ticker)
    currency     = p.get("currency", "USD")
    shares_per_adr = p.get("shares_per_adr", 1)

    try:
        # use_alpaca=False : ce book n'est pas exécuté via Alpaca (positions
        # tenues sur un broker tiers) — le carnet IEX gratuit d'Alpaca dérive
        # de plusieurs % vs le NBBO consolidé sur les tickers peu liquides
        # (FMX/HRTG audités à ±7 %). yfinance (consolidé, retard ~15 min)
        # colle mieux au prix réellement affiché par le broker de l'utilisateur.
        raw_price, as_of, _fetched_at = get_current_price_detailed(price_ticker, use_alpaca=False)
    except Exception as exc:
        logger.warning(f"[my_portfolio] Prix indisponible pour {ticker} ({price_ticker}): {exc}")
        return None, None, None, None

    if raw_price is None:
        return None, None, None, None

    fx = _usd_multiplier(currency)
    if fx is None:
        logger.warning(f"[my_portfolio] Taux de change {currency} indisponible pour {ticker} — prix ignoré")
        return None, None, None, None

    return raw_price * shares_per_adr * fx, as_of, raw_price, fx


def _earnings_fields(p: dict, today: date, snapshot: dict[str, dict[str, Any]]) -> dict[str, Any]:
    symbol = p.get("price_ticker", p["ticker"]).upper()
    snap = snapshot.get(symbol, {"next_earnings_date": None, "source": None, "stale": False})
    next_date_str = snap["next_earnings_date"]
    days_until: int | None = None
    if next_date_str:
        try:
            days_until = (date.fromisoformat(next_date_str) - today).days
        except ValueError:
            days_until = None
    return {
        "next_earnings_date": next_date_str,
        "earnings_days_until": days_until,
        "earnings_source": snap["source"],
        "earnings_data_stale": snap["stale"],
        "badge": _earnings_badge(days_until, next_date_str),
    }


def _rebalance_order(r: dict[str, Any], raw_native_price: float | None, fx: float | None) -> dict[str, Any] | None:
    """Ordre BUY/SELL pour ramener une ligne dérivée à son poids cible.

    `None` si pas d'alerte de dérive, prix stale, ou FX indisponible — un
    ordre approximatif serait pire qu'aucun ordre (voir Upgrade 3,
    docs/UPGRADES_MY_PORTFOLIO.md, "Cas limites").
    """
    if not r["rebalance_alert"] or r["price_stale"] or raw_native_price is None or fx is None:
        return None

    # target_amount est déjà la valeur-cible en $ pour cette ligne (voir
    # commentaire TOTAL_ENVELOPE_AMOUNT) : delta_usd > 0 => acheter.
    delta_usd = r["target_amount"] - r["current_value"]
    direction = "BUY" if delta_usd > 0 else "SELL"
    amount_native = delta_usd / fx
    shares_native = abs(amount_native / raw_native_price)

    return {
        "direction": direction,
        "shares_native": round(shares_native, 6),
        "amount_native": round(amount_native, 2),
        "currency": r.get("currency", "USD"),
        "amount_usd": round(delta_usd, 2),
    }


@router.get("/my_portfolio")
def get_my_portfolio(_auth: None = Security(api_core.require_auth)) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=max(1, len(POSITIONS))) as ex:
        results = dict(zip(
            (p["ticker"] for p in POSITIONS), ex.map(_safe_price, POSITIONS), strict=True
        ))
    prices = {t: r[0] for t, r in results.items()}
    prices_as_of = {t: r[1] for t, r in results.items()}
    raw_native_prices = {t: r[2] for t, r in results.items()}
    fx_multipliers = {t: r[3] for t, r in results.items()}

    today = date.today()
    earnings_symbols = sorted({
        p.get("price_ticker", p["ticker"]).upper() for p in POSITIONS + WATCHLIST
    })
    earnings_snapshot = get_earnings_snapshot(earnings_symbols)
    risk_state = load_risk_snapshot()
    risk_by_ticker: dict[str, dict[str, Any]] = risk_state.get("tickers", {})

    rows: list[dict[str, Any]] = []
    for p in POSITIONS:
        shares = p["shares"]
        price  = prices.get(p["ticker"])

        if shares <= 0:
            # Pas encore acheté (position en attente d'ouverture) → 0 réel,
            # pas de fallback sur le montant cible.
            current_value, price_stale = 0.0, False
        elif price is not None:
            current_value, price_stale = shares * price, False
        else:
            # Prix live indisponible → fallback sur le montant cible pour
            # ne pas fausser le total (mieux qu'une position à 0$).
            current_value, price_stale = p["target_amount"], True

        # P&L — axe de tracking séparé du poids/dérive : "combien j'ai
        # gagné/perdu vs mon prix d'entrée réel", indépendant de l'allocation
        # cible. Nécessite un prix d'entrée connu (None pour une position pas
        # encore ouverte) et un prix live valide (pas de fallback stale).
        # entry_price est natif à `currency` (même devise que le prix brut
        # avant conversion) → reconverti au taux live actuel pour rester
        # cohérent avec `price` (déjà en USD). Voir docstring my_portfolio_data.
        entry_price = p["entry_price"]
        currency = p.get("currency", "USD")
        if entry_price and currency != "USD":
            fx = _usd_multiplier(currency)
            entry_price = entry_price * fx if fx is not None else None
        if entry_price and shares > 0 and price is not None:
            pnl_usd = (price - entry_price) * shares
            pnl_pct = (price / entry_price - 1) * 100
        else:
            pnl_usd, pnl_pct = None, None

        rows.append({
            **p,
            "current_price": price,
            "current_value": round(current_value, 2),
            "price_stale":   price_stale,
            "price_as_of":   prices_as_of.get(p["ticker"]),
            "is_pending":    shares <= 0,
            "pnl_usd":       round(pnl_usd, 2) if pnl_usd is not None else None,
            "pnl_pct":       round(pnl_pct, 1) if pnl_pct is not None else None,
            **_earnings_fields(p, today, earnings_snapshot),
            **_risk_fields(p["ticker"], risk_by_ticker),
        })

    # Valeur actuelle réelle du book — purement informative (tuile "Valeur
    # totale"). NE DOIT JAMAIS servir de dénominateur au poids réel : cette
    # somme varie avec le déploiement des positions (ex: PSX pas encore
    # pleinement investie), ce qui gonflerait artificiellement le poids
    # réel de toutes les autres lignes.
    total_current_value = sum(r["current_value"] for r in rows) + CASH_RESERVE_AMOUNT

    # P&L global — somme des P&L $ des lignes ouvertes avec un prix d'entrée
    # connu (une position sans entry_price, pas encore ouverte, est exclue).
    total_pnl_usd = sum(r["pnl_usd"] for r in rows if r["pnl_usd"] is not None)

    for r in rows:
        # Dénominateur FIXE — enveloppe totale $2000, jamais la somme
        # variable des valeurs actuellement investies (voir commentaire
        # TOTAL_ENVELOPE_AMOUNT dans my_portfolio_data.py).
        real_weight = r["current_value"] / TOTAL_ENVELOPE_AMOUNT * 100
        target = r["target_weight_pct"]
        target_amount = r["target_amount"]

        # % du montant cible effectivement déployé (prix live × shares vs
        # target_amount). Une position à 0 part (0%) est le cas extrême de
        # cette même logique — pas un cas à part.
        deployment_pct = (r["current_value"] / target_amount * 100) if target_amount else 100.0
        is_deploying = deployment_pct < DEPLOYMENT_THRESHOLD_PCT

        # Une dérive n'a de sens que sur une position pleinement déployée :
        # sous le seuil de déploiement, un écart au poids cible reflète un
        # DCA pas terminé, pas un besoin de rééquilibrage.
        if target and not is_deploying:
            drift_pct = (real_weight - target) / target * 100
        else:
            drift_pct = None

        r["real_weight_pct"]  = round(real_weight, 2)
        r["deployment_pct"]   = round(deployment_pct, 1)
        r["is_deploying"]     = is_deploying
        r["drift_pct"]        = round(drift_pct, 1) if drift_pct is not None else None
        r["rebalance_alert"]  = drift_pct is not None and abs(drift_pct) > REBALANCE_DRIFT_THRESHOLD_PCT
        r["rebalance_order"]  = _rebalance_order(
            r, raw_native_prices.get(r["ticker"]), fx_multipliers.get(r["ticker"])
        )

    cash_weight = CASH_RESERVE_AMOUNT / TOTAL_ENVELOPE_AMOUNT * 100

    watchlist_rows = [
        {**w, **_earnings_fields(w, today, earnings_snapshot)} for w in WATCHLIST
    ]

    return {
        "positions": rows,
        "cash_reserve": {
            "target_weight_pct": CASH_RESERVE_PCT,
            "amount":            CASH_RESERVE_AMOUNT,
            "real_weight_pct":   round(cash_weight, 2),
        },
        "watchlist":              watchlist_rows,
        "total_value":            round(total_current_value, 2),
        "total_pnl_usd":          round(total_pnl_usd, 2),
        "drift_threshold_pct":    REBALANCE_DRIFT_THRESHOLD_PCT,
        "deployment_threshold_pct": DEPLOYMENT_THRESHOLD_PCT,
        "risk_snapshot":          risk_state.get("risk_snapshot"),
    }
