"""Moteur de risque en tâche de fond — book `my_portfolio` (Upgrade 1).

3 `sell_signal` du book référencent des métriques jamais calculées (beta
recalculé, corrélation moyenne, corrélation vs un ticker précis — voir
`docs/UPGRADES_MY_PORTFOLIO.md` Upgrade 1). Ce module calcule ces métriques
en tâche de fond (job hebdo, `--recompute-portfolio-risk`) et les persiste
dans `data/my_portfolio_risk.json`, lu ensuite par `routers/my_portfolio.py`.

Porte la logique déjà écrite ailleurs, paramétrée pour ce book (10 tickers,
fenêtre 2 ans au lieu de 60j/panier TITAN) plutôt que de la réécrire :
  - `correlation_check._to_returns` (log-returns) et son seuil
    `_MIN_PAIRWISE_DAYS` — importés tels quels.
  - `backtest._capm_alpha_beta` — régression OLS béta (le terme risk-free
    s'annule mathématiquement dans le calcul du béta seul, on ignore
    `alpha_*`).

Devise des séries : les retours sont calculés sur les prix convertis en
USD (post-FX quotidien, `EURUSD=X`/`USDHKD=X` fetchés au même format que
les prix, alignés par date avec `ffill`), pas sur les prix natifs — le
risque de change fait partie du risque réellement subi par l'investisseur.

Pondération des agrégats portefeuille (beta pondéré, corrélation moyenne
pondérée, ratio de diversification) : la spec (`docs/UPGRADES_MY_PORTFOLIO.md`)
demande `real_weight_pct` (poids réel, dérivé du prix live). Ce module
tourne en job batch hebdomadaire hors contexte de requête HTTP — il utilise
`target_weight_pct` (poids cible statique, déjà connu sans fetch prix
supplémentaire) comme approximation : un snapshot de risque hebdo a
vocation à caractériser le profil de risque de l'allocation cible, pas
l'instantané exact de dérive du jour (qui, lui, est déjà exposé séparément
via `drift_pct`/`rebalance_alert`). Déviation documentée, pas une
contradiction cachée de la spec.

Fail-open à deux niveaux (même philosophie que `my_portfolio_earnings.py`) :
  1. Par ticker : historique indisponible/trop court → `data_quality` !=
     "ok", champs beta/corrélation à `None` (jamais de valeur hallucinée),
     ticker compté dans `n_tickers_missing`.
  2. Par run : si AUCUN ticker n'a de données exploitables (panne réseau
     totale), le snapshot précédent est conservé tel quel (jamais écrasé
     par un état vide) — voir `refresh_portfolio_risk`.

Persistance "durable" (streak hebdo) : réutilise le pattern de
`proposals.recurrence_streaks` (mémoire d'un compteur qui s'incrémente tant
que la condition est vraie, reset sinon) sans l'importer directement (celui-
ci lit `list_all()` du carnet de propositions, sans rapport avec ce book) —
le compteur est ici porté par le state JSON persisté par ce module.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from modules.backtest import _capm_alpha_beta
from modules.correlation_check import _MIN_PAIRWISE_DAYS, _to_returns
from modules.log import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATE_PATH = _PROJECT_ROOT / "data" / "my_portfolio_risk.json"

SCHEMA_VERSION = 1

BENCHMARK_TICKER = "^GSPC"
HISTORY_PERIOD = "2y"
TRADING_DAYS_PER_YEAR = 252

# Sous ce nombre de log-returns utilisables, le ticker est trop récent pour
# un beta/corrélation 2 ans fiable → "insufficient_history" (~3 mois de
# bourse, cohérent avec le seuil analogue de `correlation_check.py` mais
# plus strict car la fenêtre visée ici est 2 ans, pas 60j).
_MIN_RETURNS_FOR_ANALYSIS = 60

# Écart beta recalculé vs beta déclaré au-delà duquel on affiche le badge ⚠.
BETA_FLAG_THRESHOLD_PCT = 30.0

# Seuil absolu au-delà duquel un ticker cesse de jouer un rôle de
# "stabilisateur" bas-beta dans le book (ex: critère éditorial BNP.PA "Beta
# >0.8 durable ou corrélation >0.40", modules/my_portfolio_thesis.py
# REFERENCE_METRICS / sell_signals `auto_metric`). Calculé pour tous les
# tickers (pas seulement ceux avec `correlation_alert` configuré) — c'est un
# bloc générique, réutilisable par n'importe quel sell_signal qui s'y
# rattache via `auto_metric`, pas une notion propre à BNP.PA.
STABILIZER_BETA_THRESHOLD = 0.8
_DEFAULT_STABILIZER_PERSIST_WEEKS = 2

# Paire yfinance par devise native + sens d'application (même convention
# que `routers/my_portfolio.py::_usd_multiplier`, dupliquée ici en version
# "série historique" plutôt qu'importée — besoin différent, un historique
# 2 ans complet plutôt qu'un taux ponctuel).
_FX_PAIR_FOR_CURRENCY = {"EUR": "EURUSD=X", "HKD": "USDHKD=X"}

_MAX_CORRELATED_PAIRS = 5


@dataclass
class TickerReturns:
    ticker: str
    returns: pd.Series | None
    data_quality: str  # "ok" | "insufficient_history" | "fetch_failed"


def _fetch_close_series(symbol: str, period: str) -> pd.Series | None:
    try:
        hist = yf.Ticker(symbol).history(period=period)
    except Exception as e:
        logger.warning(f"[portfolio_risk] Historique {symbol} indisponible: {e}")
        return None
    if hist is None or hist.empty or "Close" not in hist.columns:
        return None
    s = hist["Close"].dropna().astype(float)
    if s.empty:
        return None
    # Normalise l'index en date calendaire nue (pas de tz, pas d'heure) —
    # nécessaire pour aligner des séries venant de marchés différents
    # (NYSE vs HKEX pour 0992.HK, tz-aware dans des fuseaux différents).
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s[~s.index.duplicated(keep="last")].sort_index()


def _usd_close_series(price_ticker: str, currency: str, period: str) -> pd.Series | None:
    native = _fetch_close_series(price_ticker, period)
    if native is None or native.empty:
        return None
    if currency == "USD":
        return native

    pair = _FX_PAIR_FOR_CURRENCY.get(currency)
    if pair is None:
        logger.error(f"[portfolio_risk] Devise {currency} sans paire FX configurée")
        return None
    fx = _fetch_close_series(pair, period)
    if fx is None or fx.empty:
        return None
    fx = fx.reindex(native.index).ffill()

    if currency == "EUR":
        usd = native * fx
    else:
        # USDHKD=X (et toute paire de la même forme "USD<devise>=X") cote
        # en devise native par USD → on inverse pour obtenir un prix USD.
        usd = native / fx
    return usd.dropna()


def _ticker_returns(price_ticker: str, currency: str, period: str) -> TickerReturns:
    series = _usd_close_series(price_ticker, currency, period)
    if series is None or series.empty:
        return TickerReturns(price_ticker, None, "fetch_failed")
    returns = _to_returns(series)
    if len(returns) < _MIN_RETURNS_FOR_ANALYSIS:
        return TickerReturns(price_ticker, returns if len(returns) else None, "insufficient_history")
    return TickerReturns(price_ticker, returns, "ok")


# Périodes yfinance exposées pour le graphique de la page détail ticker (Mon
# Portefeuille) — sous-ensemble sain des périodes yfinance valides.
PRICE_HISTORY_PERIODS = frozenset({"3mo", "6mo", "1y", "2y", "5y", "max"})


def get_price_history(
    price_ticker: str, currency: str, period: str = "1y",
) -> list[dict[str, Any]] | None:
    """Historique de prix USD pour la page détail ticker (Mon Portefeuille).

    Réutilise `_usd_close_series` — le même fetch/pipeline déjà utilisé pour
    le calcul beta/corrélation (Upgrade 1) — plutôt que d'ajouter une nouvelle
    source de données ou un nouveau calcul. `None` si l'historique est
    indisponible (fetch échoué, ticker/FX inconnu).
    """
    series = _usd_close_series(price_ticker, currency, period)
    if series is None or series.empty:
        return None
    return [
        {"date": idx.date().isoformat(), "price": round(float(val), 4)}
        for idx, val in series.items()
    ]


def _beta_recalculated(returns: pd.Series, bench_returns: pd.Series) -> float | None:
    common = returns.index.intersection(bench_returns.index)
    if len(common) < _MIN_PAIRWISE_DAYS:
        return None
    port = returns.loc[common].tolist()
    bench = bench_returns.loc[common].tolist()
    result = _capm_alpha_beta(port, bench, period_days=1)
    beta = result.get("beta")
    if beta is None or (isinstance(beta, float) and math.isnan(beta)):
        return None
    return float(beta)


def _per_ticker_avg_correlation(corr: pd.DataFrame, ticker: str) -> float | None:
    if ticker not in corr.columns:
        return None
    row = corr[ticker].drop(labels=[ticker], errors="ignore").dropna()
    if row.empty:
        return None
    return float(row.mean())


def _most_correlated_pairs(corr: pd.DataFrame, limit: int = _MAX_CORRELATED_PAIRS) -> list[dict[str, Any]]:
    cols = list(corr.columns)
    pairs: list[tuple[str, str, float]] = []
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            val = corr.loc[a, b]
            if val is None or (isinstance(val, float) and math.isnan(val)):
                continue
            pairs.append((a, b, float(val)))
    pairs.sort(key=lambda t: t[2], reverse=True)
    return [{"a": a, "b": b, "corr": round(v, 3)} for a, b, v in pairs[:limit]]


def _diversification_ratio(
    returns_map: dict[str, pd.Series], weights: dict[str, float]
) -> float | None:
    cols = list(returns_map.keys())
    if len(cols) < 2:
        return None
    df = pd.DataFrame(returns_map)
    cov = df.cov(min_periods=_MIN_PAIRWISE_DAYS) * TRADING_DAYS_PER_YEAR
    valid_cols = [t for t in cols if t in cov.columns and not math.isnan(cov.loc[t, t])]
    if len(valid_cols) < 2:
        return None
    sigma = np.array([math.sqrt(cov.loc[t, t]) for t in valid_cols])
    w = np.array([weights.get(t, 0.0) for t in valid_cols])
    sub_cov = cov.loc[valid_cols, valid_cols].to_numpy()
    sub_cov = np.nan_to_num(sub_cov, nan=0.0)
    numerator = float(np.dot(w, sigma))
    port_var = float(w @ sub_cov @ w)
    if port_var <= 0:
        return None
    return numerator / math.sqrt(port_var)


def _weighted_avg_correlation(corr: pd.DataFrame, weights: dict[str, float]) -> float | None:
    cols = list(corr.columns)
    num = 0.0
    den = 0.0
    for i, a in enumerate(cols):
        for j, b in enumerate(cols):
            if i == j:
                continue
            rho = corr.loc[a, b]
            if rho is None or (isinstance(rho, float) and math.isnan(rho)):
                continue
            wiwj = weights.get(a, 0.0) * weights.get(b, 0.0)
            num += wiwj * rho
            den += wiwj
    if den <= 0:
        return None
    return num / den


def _correlation_streak(
    *,
    ref_value: float | None,
    threshold: float,
    persist_weeks: int,
    prev_streak: int,
) -> tuple[int, bool]:
    """Incrémente/reset le streak hebdo. `ref_value=None` (data indisponible
    ce run) → streak conservé tel quel (fail-open, pas de reset artificiel).
    """
    if ref_value is None:
        streak = prev_streak
    elif ref_value > threshold:
        streak = prev_streak + 1
    else:
        streak = 0
    return streak, streak >= persist_weeks


def compute_portfolio_risk(
    positions: list[dict[str, Any]],
    *,
    cash_weight_pct: float = 0.0,
    benchmark: str = BENCHMARK_TICKER,
    history_period: str = HISTORY_PERIOD,
    prev_streaks: dict[str, int] | None = None,
    prev_beta_streaks: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Calcule le snapshot de risque complet pour `positions`.

    `positions` : items façon `my_portfolio_data.POSITIONS` (au minimum
    `ticker`, `beta`, `target_weight_pct`, `currency` optionnel, `
    price_ticker` optionnel, `correlation_alert` optionnel).
    Fonction pure (aucun accès disque) — la persistance est gérée par
    `refresh_portfolio_risk`.

    `prev_streaks`/`prev_beta_streaks` : deux compteurs indépendants
    (corrélation vs beta absolu, voir STABILIZER_BETA_THRESHOLD) — séparés
    plutôt que nichés dans un seul dict pour ne pas casser la signature
    existante de `prev_streaks` (déjà testée/appelée telle quelle).
    """
    prev_streaks = prev_streaks or {}
    prev_beta_streaks = prev_beta_streaks or {}
    bench_returns_all = _fetch_close_series(benchmark, history_period)
    bench_returns = _to_returns(bench_returns_all) if bench_returns_all is not None else pd.Series(dtype=float)

    per_ticker: dict[str, TickerReturns] = {}
    for p in positions:
        ticker = p["ticker"]
        price_ticker = p.get("price_ticker", ticker)
        currency = p.get("currency", "USD")
        per_ticker[ticker] = _ticker_returns(price_ticker, currency, history_period)

    returns_map: dict[str, pd.Series] = {
        t: r.returns for t, r in per_ticker.items()
        if r.data_quality == "ok" and r.returns is not None
    }
    corr = pd.DataFrame(returns_map).corr(min_periods=_MIN_PAIRWISE_DAYS) if len(returns_map) >= 2 else pd.DataFrame()

    weights: dict[str, float] = {p["ticker"]: p.get("target_weight_pct", 0.0) / 100.0 for p in positions}

    tickers_out: dict[str, Any] = {}
    beta_recalc_map: dict[str, float] = {}
    for p in positions:
        ticker = p["ticker"]
        tr = per_ticker[ticker]
        beta_static = p.get("beta")

        beta_recalc: float | None = None
        if tr.data_quality == "ok" and tr.returns is not None and not bench_returns.empty:
            beta_recalc = _beta_recalculated(tr.returns, bench_returns)
        if beta_recalc is not None:
            beta_recalc_map[ticker] = beta_recalc

        if beta_recalc is not None and beta_static:
            beta_diff_pct = (beta_recalc - beta_static) / beta_static * 100
            beta_flag = abs(beta_diff_pct) > BETA_FLAG_THRESHOLD_PCT
        else:
            beta_diff_pct = None
            beta_flag = False

        avg_corr = _per_ticker_avg_correlation(corr, ticker) if not corr.empty else None

        # Streak "beta stabilisateur" — générique, calculé pour tous les
        # tickers (pas seulement ceux avec `correlation_alert`), réutilise
        # `_correlation_streak` telle quelle malgré son nom (générique :
        # ref_value/threshold/persist_weeks/prev_streak, rien de spécifique
        # à la corrélation). `persist_weeks` vient de `correlation_alert` si
        # configuré (même fenêtre "durable" que le volet corrélation),
        # sinon la valeur par défaut.
        beta_alert_cfg = p.get("correlation_alert") or {}
        beta_persist_weeks = beta_alert_cfg.get("persist_weeks", _DEFAULT_STABILIZER_PERSIST_WEEKS)
        beta_over_threshold_streak, beta_over_threshold_triggered = _correlation_streak(
            ref_value=beta_recalc,
            threshold=STABILIZER_BETA_THRESHOLD,
            persist_weeks=beta_persist_weeks,
            prev_streak=prev_beta_streaks.get(ticker, 0),
        )

        alert_cfg = p.get("correlation_alert")
        correlation_vs_ref: float | None = None
        correlation_alert_triggered = False
        correlation_streak_weeks = 0
        if alert_cfg is not None:
            ref = alert_cfg.get("ref")
            threshold = alert_cfg["threshold"]
            persist_weeks = alert_cfg["persist_weeks"]
            if ref is not None:
                if ref in corr.columns and ticker in corr.columns:
                    val = corr.loc[ticker, ref]
                    correlation_vs_ref = float(val) if not math.isnan(val) else None
                ref_value = correlation_vs_ref
            else:
                ref_value = avg_corr
            correlation_streak_weeks, correlation_alert_triggered = _correlation_streak(
                ref_value=ref_value,
                threshold=threshold,
                persist_weeks=persist_weeks,
                prev_streak=prev_streaks.get(ticker, 0),
            )

        tickers_out[ticker] = {
            "beta_recalculated": round(beta_recalc, 4) if beta_recalc is not None else None,
            "beta_diff_pct": round(beta_diff_pct, 1) if beta_diff_pct is not None else None,
            "beta_flag": beta_flag,
            "avg_correlation": round(avg_corr, 3) if avg_corr is not None else None,
            "correlation_vs_ref": round(correlation_vs_ref, 3) if correlation_vs_ref is not None else None,
            "correlation_alert_triggered": correlation_alert_triggered,
            "correlation_streak_weeks": correlation_streak_weeks,
            "beta_over_threshold_triggered": beta_over_threshold_triggered,
            "beta_over_threshold_streak_weeks": beta_over_threshold_streak,
            # Rôle "stabilisateur bas-beta/faible corrélation" toujours
            # rempli : combine les deux volets. `None` (pas juste False) si
            # la donnée n'est pas exploitable ce run — un sell_signal qui
            # s'y rattache (`auto_metric`) ne doit jamais fabriquer un
            # statut à partir d'une donnée absente (voir routers/my_portfolio.py).
            "stabilizer_signal_triggered": (
                (beta_over_threshold_triggered or correlation_alert_triggered)
                if tr.data_quality == "ok" else None
            ),
            "data_quality": tr.data_quality,
        }

    n_tickers_ok = sum(1 for r in per_ticker.values() if r.data_quality == "ok")
    n_tickers_missing = len(positions) - n_tickers_ok

    portfolio_beta = sum(weights.get(t, 0.0) * b for t, b in beta_recalc_map.items())
    avg_weighted_correlation = _weighted_avg_correlation(corr, weights) if not corr.empty else None
    diversification_ratio = _diversification_ratio(returns_map, weights) if returns_map else None
    most_correlated_pairs = _most_correlated_pairs(corr) if not corr.empty else []

    today = date.today()
    return {
        "schema_version": SCHEMA_VERSION,
        "risk_snapshot": {
            "portfolio_beta": round(portfolio_beta, 4) if beta_recalc_map else None,
            "avg_weighted_correlation": round(avg_weighted_correlation, 3) if avg_weighted_correlation is not None else None,
            "diversification_ratio": round(diversification_ratio, 3) if diversification_ratio is not None else None,
            "most_correlated_pairs": most_correlated_pairs,
            "last_recalc_date": today.isoformat(),
            "next_recalc_date": (today + timedelta(days=7)).isoformat(),
            "n_tickers_ok": n_tickers_ok,
            "n_tickers_missing": n_tickers_missing,
        },
        "tickers": tickers_out,
        "fetched_at": time.time(),
    }


def _load_state() -> dict[str, Any]:
    if not _STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return {}
    return payload


def _save_state(state: dict[str, Any]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_PATH.with_suffix(_STATE_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(_STATE_PATH)
    except Exception as e:
        logger.warning(f"[portfolio_risk] state write failed: {e}")


def load_snapshot() -> dict[str, Any]:
    """Dernier snapshot persisté, lecture seule (pas de recalcul, pas de
    fetch réseau) — utilisé par `routers/my_portfolio.py`. Fail-open : dict
    vide si le fichier est absent, corrompu, ou d'un `schema_version`
    différent (même garde que `refresh_portfolio_risk`)."""
    return _load_state()


def refresh_portfolio_risk(
    positions: list[dict[str, Any]],
    *,
    cash_weight_pct: float = 0.0,
    benchmark: str = BENCHMARK_TICKER,
    history_period: str = HISTORY_PERIOD,
) -> dict[str, Any]:
    """Recalcule le snapshot, persiste, et retourne le nouvel état.

    Fail-open par run : si aucun ticker n'a de données exploitables ce run
    (panne réseau totale), l'état précédent est conservé tel quel — jamais
    écrasé par un snapshot vide. `last_recalc_date` reste alors daté du
    dernier run réussi (staleness visible plutôt que masquée).
    """
    previous = _load_state()
    prev_streaks = {
        t: v.get("correlation_streak_weeks", 0) for t, v in previous.get("tickers", {}).items()
    }
    prev_beta_streaks = {
        t: v.get("beta_over_threshold_streak_weeks", 0) for t, v in previous.get("tickers", {}).items()
    }

    result = compute_portfolio_risk(
        positions,
        cash_weight_pct=cash_weight_pct,
        benchmark=benchmark,
        history_period=history_period,
        prev_streaks=prev_streaks,
        prev_beta_streaks=prev_beta_streaks,
    )

    if result["risk_snapshot"]["n_tickers_ok"] == 0 and previous:
        logger.warning(
            "[portfolio_risk] Aucun ticker exploitable ce run — snapshot précédent conservé"
        )
        return previous

    _save_state(result)
    return result
