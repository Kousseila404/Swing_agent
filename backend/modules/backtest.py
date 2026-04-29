"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  BACKTEST — Top-N par TITAN score, weights paramétrables, costs modélisés   ║
║                                                                              ║
║  Source : snapshots universe_history (modules/universe_history.py).          ║
║                                                                              ║
║  Pour chaque paire consécutive de dates (t, t+1) :                           ║
║    1. Ranker tickers du snapshot t par `titan_composite_score` desc          ║
║    2. Prendre top N, allocation par `--weighting` :                          ║
║       • equal         → 1/N par ticker                                       ║
║       • score         → poids ∝ titan_composite_score (rebased min 0)        ║
║       • risk_parity   → poids ∝ 1/realized_vol_30d (fallback equal)          ║
║    3. Return brut période = Σ_i w_i × (px_t+1[i] / px_t[i] − 1)              ║
║    4. Frais : pour chaque turnover ticker (entrée/sortie), prélèvement       ║
║       slippage_bps × poids_ticker + commission_per_share × shares_ratio.     ║
║    5. Agréger return net en equity curve (initial = 1.0).                    ║
║                                                                              ║
║  CLI : python -m modules.backtest [--top-n 20] [--weighting equal|score|...] ║
║         [--slippage-bps 5] [--commission-per-share 0.005] [--benchmark SPY]  ║
║                                                                              ║
║  Audit S1.4 (2026-04-27) — addition de slippage/commission/weights réels :   ║
║    le backtest equal-weight sans coûts surestimait l'alpha vs production     ║
║    qui tourne risk-parity + frais Alpaca. On rapproche maintenant les deux.  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from modules import universe_history
from modules.log import logger


@dataclass
class PeriodResult:
    """Un rebalance : date de signal + returns brut/net + top-N tickers."""
    signal_date: str            # date du score utilisé pour ranker
    next_date: str              # date du prix de sortie
    top_tickers: list[str]
    weights: dict[str, float]   # poids appliqués (somme ≤ 1, dépend des prix dispo)
    returns: dict[str, float]   # par ticker, décimal (0.02 = +2%)
    portfolio_return_gross: float   # avant frais
    portfolio_return: float         # après frais (= net)
    cost_pct: float                 # coût total ponctionné cette période (décimal)
    turnover: float                 # somme |Δw| / 2 (one-way), 1.0 = full rotation
    n_valid: int                # tickers avec price_t et price_t+1 non-None
    n_skipped: int              # tickers skippés (price manquant t ou t+1)


@dataclass
class BacktestResult:
    strategy:        str                           # "titan_top_n"
    top_n:           int
    periods:         list[PeriodResult]
    equity_curve:    list[tuple[str, float]]       # (date, equity)
    total_return:    float                         # cumulative, décimal
    avg_daily_return: float                        # moyenne arithmétique des périodes
    sharpe_daily:    float | None
    sharpe_annual:   float | None
    max_drawdown:    float                         # décimal, positif (0.05 = -5%)
    hit_rate:        float                         # % périodes positives
    benchmark_return: float | None = None          # return SPY sur la même période
    alpha:           float | None = None           # total_return - benchmark_return
    diagnostics:     dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "top_n": self.top_n,
            "n_periods": len(self.periods),
            "equity_curve": self.equity_curve,
            "total_return": round(self.total_return, 5),
            "avg_daily_return": round(self.avg_daily_return, 5),
            "sharpe_daily": round(self.sharpe_daily, 3) if self.sharpe_daily is not None else None,
            "sharpe_annual": round(self.sharpe_annual, 3) if self.sharpe_annual is not None else None,
            "max_drawdown": round(self.max_drawdown, 5),
            "hit_rate": round(self.hit_rate, 3),
            "benchmark_return": round(self.benchmark_return, 5) if self.benchmark_return is not None else None,
            "alpha": round(self.alpha, 5) if self.alpha is not None else None,
            "periods": [
                {
                    "signal_date":            p.signal_date,
                    "next_date":              p.next_date,
                    "portfolio_return":       round(p.portfolio_return, 5),
                    "portfolio_return_gross": round(p.portfolio_return_gross, 5),
                    "cost_pct":               round(p.cost_pct, 5),
                    "turnover":               round(p.turnover, 4),
                    "n_valid":                p.n_valid,
                    "n_skipped":              p.n_skipped,
                    "top_tickers":            p.top_tickers[:10],  # cap pour le payload
                }
                for p in self.periods
            ],
            "diagnostics": self.diagnostics,
        }


def _rank_top_n(
    snapshot: dict[str, Any],
    top_n: int,
    *,
    active_filter: set[str] | None = None,
    score_field: str = "titan_composite_score",
) -> list[tuple[str, float]]:
    """Retourne top-N tickers (ticker, score) par `score_field` desc.

    `active_filter` (Audit S1.1) : si fourni, on ne ranke que les tickers
    qui étaient actifs à la date du snapshot (point-in-time). Évite qu'un
    re-build d'univers récent qui réintroduit un ticker (M&A reverse) ne
    fasse remonter ce ticker dans des snapshots historiques où il n'était
    pas investissable.

    `score_field` : permet de pointer un champ alternatif (ex: composite
    re-calculé à un publication-lag différent par le backtest).
    """
    tickers = snapshot.get("tickers") or {}
    candidates: list[tuple[str, float]] = []
    for t, row in tickers.items():
        if active_filter is not None and t not in active_filter:
            continue
        score = row.get(score_field)
        if score is None or not isinstance(score, (int, float)):
            continue
        if not math.isfinite(float(score)):
            continue
        candidates.append((t, float(score)))
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:top_n]


def _extract_prices(snapshot: dict[str, Any], tickers: list[str]) -> dict[str, float]:
    """Extrait current_price pour chaque ticker du snapshot (None → exclu)."""
    out: dict[str, float] = {}
    snap_tickers = snapshot.get("tickers") or {}
    for t in tickers:
        row = snap_tickers.get(t) or {}
        px = row.get("current_price")
        if px is not None and isinstance(px, (int, float)) and math.isfinite(float(px)) and float(px) > 0:
            out[t] = float(px)
    return out


def _compute_weights(
    snapshot: dict[str, Any],
    top: list[tuple[str, float]],
    weighting: str,
) -> dict[str, float]:
    """Construit le vecteur de poids (somme = 1) selon le mode demandé.

    • equal       : 1/N
    • score       : poids ∝ (score - min(scores) + 1) — rebase positif pour
                    éviter les poids ≤ 0 sur scores faibles ; +1 = floor.
    • risk_parity : poids ∝ 1/realized_vol_30d (champ snapshot, fallback equal
                    si la majorité des vols sont absentes — pas un proxy fiable
                    autrement).
    """
    if not top:
        return {}
    tickers = [t for t, _ in top]
    n = len(tickers)
    mode = (weighting or "equal").lower().strip()

    if mode == "equal":
        w = {t: 1.0 / n for t in tickers}

    elif mode == "score":
        scores = [s for _, s in top]
        floor = max(0.0, -min(scores)) + 1.0  # garantit raw > 0
        raw = {t: (s + floor) for (t, s) in top}
        total = sum(raw.values())
        w = {t: v / total for t, v in raw.items()} if total > 0 else {t: 1.0 / n for t in tickers}

    elif mode == "risk_parity":
        snap_tickers = snapshot.get("tickers") or {}
        invvol: dict[str, float] = {}
        for t in tickers:
            row = snap_tickers.get(t) or {}
            v = row.get("realized_vol_30d")
            if v is None:
                v = row.get("realized_vol")  # fallback nom alternatif
            try:
                v = float(v) if v is not None else None
            except (TypeError, ValueError):
                v = None
            if v is not None and math.isfinite(v) and v > 1e-6:
                invvol[t] = 1.0 / v
        # Si plus de la moitié des tickers n'a pas de vol → fallback equal honnête.
        if len(invvol) < (n + 1) // 2:
            w = {t: 1.0 / n for t in tickers}
        else:
            # tickers sans vol : reçoivent l'equal-weight 1/N (pas de bonus ni penalty)
            avg_inv = sum(invvol.values()) / len(invvol)
            for t in tickers:
                invvol.setdefault(t, avg_inv)
            total = sum(invvol.values())
            w = {t: invvol[t] / total for t in tickers}

    else:
        raise ValueError(f"weighting inconnu : {weighting!r} (equal|score|risk_parity)")

    # Sécurité numérique : renormalise (drift float)
    s = sum(w.values())
    if s > 0:
        w = {t: v / s for t, v in w.items()}
    return w


def _compute_period(
    snapshot_t: dict[str, Any],
    snapshot_t1: dict[str, Any],
    top_n: int,
    *,
    weighting: str = "equal",
    prev_weights: dict[str, float] | None = None,
    slippage_bps: float = 0.0,
    commission_per_share: float = 0.0,
    active_filter: set[str] | None = None,
    book_size_usd: float = 100_000.0,
    impact_coef: float = 0.0,
    fallback_turnover_ratio: float = 0.005,
) -> PeriodResult:
    """Simule un rebalance : rank top-N sur t, applique les poids choisis,
    mesure return brut, ponctionne les coûts proportionnels au turnover.

    Coûts modélisés :
      • slippage_bps : coût en basis points sur la fraction du book qui
        tourne (entrée OU sortie). Round-trip = 2× slippage si le ticker
        est entré ce rebalance ET sortira au prochain.
      • commission_per_share : coût $ par action. Modélisé en bps en
        divisant par le prix moyen de l'entrée (approximation : 0.005 $/sh
        sur action à 100 $ ≈ 0.5 bp). Faute de connaître la taille du book
        en $, on capitalise sur 1.0 USD de book → c'est l'estimation
        relative qu'on cherche.
    """
    top = _rank_top_n(snapshot_t, top_n, active_filter=active_filter)
    top_tickers = [t for t, _ in top]

    px_t  = _extract_prices(snapshot_t,  top_tickers)
    px_t1 = _extract_prices(snapshot_t1, top_tickers)

    # Poids cibles désirés. On laisse les poids définis sur tous les tickers
    # top (y compris ceux sans price_t) pour un calcul de turnover honnête,
    # mais le return ne crédite que les tickers à prix valides.
    weights_full = _compute_weights(snapshot_t, top, weighting)

    # Effective weights : on retire les tickers sans px_t et renormalise.
    # Sans px_t on ne peut pas exécuter l'ordre — exclusion réaliste.
    effective_raw = {t: w for t, w in weights_full.items() if t in px_t}
    total_eff = sum(effective_raw.values())
    if total_eff > 0:
        weights = {t: w / total_eff for t, w in effective_raw.items()}
    else:
        weights = {}

    returns: dict[str, float] = {}
    for t, w in weights.items():
        if t in px_t and t in px_t1:
            returns[t] = (px_t1[t] / px_t[t]) - 1.0

    # Return brut = Σ_i w_i × r_i (pondéré, pas une simple moyenne).
    portfolio_return_gross = sum(w * returns.get(t, 0.0) for t, w in weights.items())

    # Turnover one-way : somme des changements absolus / 2.
    prev = prev_weights or {}
    all_keys = set(weights) | set(prev)
    turnover_two_way = sum(abs(weights.get(t, 0.0) - prev.get(t, 0.0)) for t in all_keys)
    turnover = turnover_two_way / 2.0  # one-way

    # Coût total : slippage + commission proportionnels au turnover.
    # Convention : turnover=1.0 (full rotation) ponctionne 1× slippage_bps en
    # one-way. La sortie au rebalance suivant est attribuée à la période
    # suivante, donc on n'inclut pas le round-trip ici.
    slip_cost = (slippage_bps / 10_000.0) * turnover

    # Commission : approximation. Sans modèle de book size en $, on fixe par
    # convention 1 unité de book = 1 USD ; commission_per_share / px = bps
    # par dollar tourné → multipliée par turnover.
    if commission_per_share > 0 and px_t:
        # Average price of tickers we touched ce rebalance (un proxy raisonnable).
        touched = [px_t[t] for t in all_keys if t in px_t]
        avg_px = sum(touched) / len(touched) if touched else 0.0
        comm_cost = (commission_per_share / avg_px) * turnover if avg_px > 0 else 0.0
    else:
        comm_cost = 0.0

    # Audit S3.x — Modèle d'impact ADTV (pénalité quadratique).
    # Pour chaque ticker qui tourne (Δw ≠ 0), on calcule la part du book en
    # dollars qui passe par l'ordre, on la divise par l'ADTV en dollars du
    # ticker (proxy market_cap × fallback_turnover_ratio si avg_volume_3m
    # absent), puis on applique impact_coef × ratio² en bps.
    # Modèle conservateur (Almgren-Chriss simplifié, pas de racine carrée) —
    # 1 % de l'ADTV ⇒ pénalité = impact_coef × 0.0001 sur le book ; 5 % ⇒
    # ×25. impact_coef=10 produit ~10 bps sur 5 % ADTV. impact_coef=0 (default)
    # désactive complètement le modèle.
    impact_cost = 0.0
    if impact_coef > 0 and book_size_usd > 0:
        snap_t_tickers = snapshot_t.get("tickers") or {}
        for t in all_keys:
            dw = abs(weights.get(t, 0.0) - prev.get(t, 0.0))
            if dw <= 0 or t not in px_t:
                continue
            row = snap_t_tickers.get(t) or {}
            adv = row.get("avg_volume_3m")
            mcap = row.get("market_cap")
            try:
                adv = float(adv) if adv is not None else None
                mcap = float(mcap) if mcap is not None else None
            except (TypeError, ValueError):
                adv = mcap = None
            if adv is not None and adv > 0:
                adtv_dollars = adv * px_t[t]
            elif mcap is not None and mcap > 0:
                adtv_dollars = mcap * fallback_turnover_ratio
            else:
                continue
            order_dollars = dw * book_size_usd
            ratio = order_dollars / adtv_dollars
            # Décimal — impact_coef en bps mais le ratio²×coef se traduit
            # directement en bps de pénalité sur la fraction concernée du book.
            # On rapporte la pénalité au book entier (donc × dw pour pondérer).
            impact_cost += (impact_coef * 1e-4) * (ratio ** 2) * dw

    cost_pct = slip_cost + comm_cost + impact_cost
    portfolio_return = portfolio_return_gross - cost_pct

    return PeriodResult(
        signal_date=str(snapshot_t.get("snapshot_date") or ""),
        next_date=str(snapshot_t1.get("snapshot_date") or ""),
        top_tickers=top_tickers,
        weights=weights,
        returns=returns,
        portfolio_return_gross=portfolio_return_gross,
        portfolio_return=portfolio_return,
        cost_pct=cost_pct,
        turnover=turnover,
        n_valid=len(returns),
        n_skipped=len(top_tickers) - len(returns),
    )


def _compute_stats(periods: list[PeriodResult]) -> dict[str, float]:
    """Agrège daily stats à partir des returns par période."""
    rets = [p.portfolio_return for p in periods]
    if not rets:
        return {
            "total_return": 0.0, "avg_daily_return": 0.0,
            "sharpe_daily": None, "sharpe_annual": None,
            "max_drawdown": 0.0, "hit_rate": 0.0,
        }

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in rets:
        equity *= (1.0 + r)
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    avg = statistics.mean(rets)
    hit = sum(1 for r in rets if r > 0) / len(rets)

    # Sharpe : σ > 0 requis (≥ 2 périodes).
    sharpe_daily: float | None = None
    if len(rets) >= 2:
        sigma = statistics.stdev(rets)
        if sigma > 0 and math.isfinite(sigma):
            sharpe_daily = avg / sigma
    sharpe_annual = sharpe_daily * math.sqrt(252) if sharpe_daily is not None else None

    return {
        "total_return":     equity - 1.0,
        "avg_daily_return": avg,
        "sharpe_daily":     sharpe_daily,
        "sharpe_annual":    sharpe_annual,
        "max_drawdown":     max_dd,
        "hit_rate":         hit,
    }


def _benchmark_return(start: date, end: date, ticker: str = "SPY") -> float | None:
    """Return buy-and-hold du benchmark entre start et end (yfinance).

    Fail-open : retourne None si yfinance KO (pas bloquant pour le backtest).
    """
    try:
        # +1 jour de buffer à la fin pour garantir le close disponible.
        from datetime import timedelta

        import yfinance as yf
        df = yf.download(
            ticker,
            start=start.isoformat(),
            end=(end + timedelta(days=3)).isoformat(),
            progress=False, auto_adjust=True, threads=False,
        )
        if df is None or df.empty or len(df) < 2:
            return None
        # MultiIndex flatten
        if hasattr(df.columns, "get_level_values"):
            closes = df["Close"]
            if hasattr(closes, "columns"):
                closes = closes.iloc[:, 0]
        else:
            closes = df["Close"]
        p0 = float(closes.iloc[0])
        p1 = float(closes.iloc[-1])
        if p0 <= 0:
            return None
        return (p1 / p0) - 1.0
    except Exception as e:
        logger.warning(f"[Backtest] benchmark {ticker} fetch failed: {e}")
        return None


def run_titan_top_n(
    top_n: int = 20,
    benchmark: str | None = "SPY",
    *,
    weighting: str = "equal",
    slippage_bps: float = 0.0,
    commission_per_share: float = 0.0,
    point_in_time: bool = True,
    publication_lag_days: int = 0,
    book_size_usd: float = 100_000.0,
    impact_coef: float = 0.0,
    fallback_turnover_ratio: float = 0.005,
    restrict_to: list[str] | None = None,
) -> BacktestResult:
    """Backtest complet du pipeline TITAN sur l'historique disponible.

    Args:
        top_n: taille du portefeuille (défaut 20).
        benchmark: ticker du benchmark (défaut SPY). None = pas de comparaison.
        weighting: equal | score | risk_parity (défaut equal).
        slippage_bps: bps de slippage one-way par turnover (défaut 0).
        commission_per_share: $ par action sur les ordres (défaut 0).
        publication_lag_days: décale le ranking de N jours pour éviter le
            lookahead fondamentaux (défaut 0 = pas de lag, comportement
            historique). 90 j = lag 10-K typique, recommandé pour un
            backtest production-grade. À signal_date d_i, on utilisera
            le snapshot le plus récent < d_i - lag pour ranker, mais le
            return reste mesuré sur (d_i, d_{i+1}).
    """
    dates = universe_history.list_snapshots()
    if len(dates) < 2:
        raise ValueError(
            f"Au moins 2 snapshots requis pour 1 période de rebalance. "
            f"Disponibles : {[d.isoformat() for d in dates]}"
        )

    snapshots: list[tuple[date, dict[str, Any]]] = []
    for d in dates:
        snap = universe_history.read_snapshot(d)
        if snap:
            snapshots.append((d, snap))
    if len(snapshots) < 2:
        raise ValueError("Snapshots illisibles")

    # Audit S1.1 — filtre point-in-time : à chaque date, on ne ranke que
    # les tickers qui étaient actifs à ce moment (registry delisted). Sans
    # ça le backtest sur-estime systématiquement l'alpha (survivorship bias).
    active_per_date: dict[date, set[str]] = {}
    if point_in_time:
        try:
            from modules import delisted as _delisted
            for d, _ in snapshots:
                active_per_date[d] = _delisted.get_active_at(d)
        except Exception as e:
            logger.warning(f"[Backtest] point-in-time filter disabled: {e}")
            active_per_date = {}

    # Audit S3.x — Publication lag : pour chaque signal_date d_i, on cherche
    # le snapshot le plus récent dont la date est ≤ d_i - lag. C'est ce
    # snapshot ranking-source qui sera utilisé, mais le return reste mesuré
    # sur (d_i, d_{i+1}) — i.e. on ranke avec des fondamentaux "périmés"
    # pour éviter d'utiliser des publications postérieures à la décision.
    from datetime import timedelta as _td
    def _ranking_snapshot_for(signal_date: date) -> dict[str, Any] | None:
        if publication_lag_days <= 0:
            return None  # 0 = pas de shift, callers utiliseront le snapshot natif
        cutoff = signal_date - _td(days=publication_lag_days)
        chosen: dict[str, Any] | None = None
        for d_x, s_x in snapshots:
            if d_x <= cutoff:
                chosen = s_x
            else:
                break
        return chosen

    # Whitelist explicite (ex: backtest in-page sur la vue filtrée). On l'intersecte
    # avec le filtre point-in-time si actif, sinon elle l'écrase. None = pas de
    # restriction — comportement legacy.
    restrict_set: set[str] | None = None
    if restrict_to:
        restrict_set = {t.upper().strip() for t in restrict_to if t}
        if not restrict_set:
            restrict_set = None

    periods: list[PeriodResult] = []
    prev_w: dict[str, float] = {}
    n_skipped_periods = 0
    for (d0, s0), (d1, s1) in zip(snapshots[:-1], snapshots[1:]):
        active = active_per_date.get(d0)
        # Si le registry est vide pour cette date (premier run, pas d'historique
        # de delisting) ⇒ active=set vide ⇒ on désactive le filtre pour ne pas
        # vider artificiellement le ranking.
        active_filter = active if active else None
        # Intersection avec la whitelist demandée (si fournie).
        if restrict_set is not None:
            active_filter = (active_filter & restrict_set) if active_filter else restrict_set

        ranking_snapshot = _ranking_snapshot_for(d0) if publication_lag_days > 0 else s0
        if ranking_snapshot is None:
            # Lag plus large que l'historique disponible avant d0 → skip cette
            # période (impossible de ranker sans lookahead).
            n_skipped_periods += 1
            continue

        period = _compute_period(
            ranking_snapshot, s1, top_n,
            weighting=weighting,
            prev_weights=prev_w,
            slippage_bps=slippage_bps,
            commission_per_share=commission_per_share,
            active_filter=active_filter,
            book_size_usd=book_size_usd,
            impact_coef=impact_coef,
            fallback_turnover_ratio=fallback_turnover_ratio,
        )
        # Override les dates : signal_date doit refléter d0, pas la date
        # du snapshot ranking (sinon l'equity curve est désalignée).
        period.signal_date = d0.isoformat()
        period.next_date = d1.isoformat()
        periods.append(period)
        prev_w = period.weights

    stats = _compute_stats(periods)

    # Equity curve pour visualisation
    equity = 1.0
    curve: list[tuple[str, float]] = [(str(snapshots[0][0].isoformat()), 1.0)]
    for p in periods:
        equity *= (1.0 + p.portfolio_return)
        curve.append((p.next_date, round(equity, 5)))

    result = BacktestResult(
        strategy="titan_top_n",
        top_n=top_n,
        periods=periods,
        equity_curve=curve,
        total_return=stats["total_return"],
        avg_daily_return=stats["avg_daily_return"],
        sharpe_daily=stats["sharpe_daily"],
        sharpe_annual=stats["sharpe_annual"],
        max_drawdown=stats["max_drawdown"],
        hit_rate=stats["hit_rate"],
    )

    # Benchmark (SPY buy&hold sur la même période)
    if benchmark:
        bench_start = snapshots[0][0]
        bench_end   = snapshots[-1][0]
        bench_ret = _benchmark_return(bench_start, bench_end, benchmark)
        if bench_ret is not None:
            result.benchmark_return = bench_ret
            result.alpha = result.total_return - bench_ret

    avg_turnover = (
        sum(p.turnover for p in periods) / len(periods) if periods else 0.0
    )
    total_costs = sum(p.cost_pct for p in periods)
    result.diagnostics = {
        "n_snapshots":          len(snapshots),
        "n_periods":            len(periods),
        "n_skipped_lag":        n_skipped_periods,
        "benchmark":            benchmark,
        "weighting":            weighting,
        "slippage_bps":         slippage_bps,
        "commission_per_share": commission_per_share,
        "publication_lag_days": publication_lag_days,
        "book_size_usd":        book_size_usd,
        "impact_coef":          impact_coef,
        "avg_turnover":         round(avg_turnover, 4),
        "total_costs_pct":      round(total_costs, 5),
        "date_range": {
            "start": snapshots[0][0].isoformat(),
            "end":   snapshots[-1][0].isoformat(),
        },
    }
    return result


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def _main() -> int:
    import argparse
    import json
    p = argparse.ArgumentParser(prog="backtest",
        description="Backtest TITAN top-N sur universe_history (costs + weights).")
    p.add_argument("--top-n", type=int, default=20,
                   help="Taille du portefeuille (défaut 20)")
    p.add_argument("--benchmark", type=str, default="SPY",
                   help="Ticker benchmark (défaut SPY, 'none' pour skip)")
    p.add_argument("--weighting", type=str, default="equal",
                   choices=("equal", "score", "risk_parity"),
                   help="Schéma de pondération (défaut equal)")
    p.add_argument("--slippage-bps", type=float, default=0.0,
                   help="Slippage one-way en bps par turnover (défaut 0). "
                        "Ordre de grandeur réaliste mid-cap : 5-10 bps.")
    p.add_argument("--commission-per-share", type=float, default=0.0,
                   help="Commission $/action (défaut 0). "
                        "Alpaca = 0 ; Interactive Brokers = 0.005.")
    p.add_argument("--no-point-in-time", action="store_true",
                   help="Désactive le filtre point-in-time (registry delisted).")
    p.add_argument("--publication-lag-days", type=int, default=0,
                   help="Décalage du ranking en jours (anti-lookahead "
                        "fondamentaux). 0 = pas de lag (défaut, comportement "
                        "historique). 90 = recommandé production, lag 10-K. "
                        "Quand lag > historique → période skippée.")
    p.add_argument("--book-size-usd", type=float, default=100_000.0,
                   help="Taille du book simulé en USD (défaut 100k). Sert "
                        "uniquement au modèle d'impact ADTV.")
    p.add_argument("--impact-coef", type=float, default=0.0,
                   help="Coefficient d'impact ADTV (défaut 0 = désactivé). "
                        "Pénalité ≈ coef × (order_$ / ADTV_$)² × Δw, en bps. "
                        "10 ≈ ~10 bps sur ordres = 5 %% ADTV.")
    p.add_argument("--fallback-turnover", type=float, default=0.005,
                   help="Si avg_volume_3m absent du snapshot, on estime "
                        "ADTV_$ = market_cap × ratio (défaut 0.5 %%, "
                        "typique large-cap US).")
    p.add_argument("--json", action="store_true",
                   help="Sortie JSON brut (sinon : tableau lisible humain)")
    args = p.parse_args()

    bench = None if args.benchmark.lower() == "none" else args.benchmark.upper()
    try:
        result = run_titan_top_n(
            top_n=args.top_n,
            benchmark=bench,
            weighting=args.weighting,
            slippage_bps=args.slippage_bps,
            commission_per_share=args.commission_per_share,
            point_in_time=not args.no_point_in_time,
            publication_lag_days=args.publication_lag_days,
            book_size_usd=args.book_size_usd,
            impact_coef=args.impact_coef,
            fallback_turnover_ratio=args.fallback_turnover,
        )
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return 0

    # Pretty print
    diag = result.diagnostics
    print(f"=== TITAN Backtest — Top {result.top_n} ({diag['weighting']}) ===")
    print(f"Période : {diag['date_range']['start']} → {diag['date_range']['end']}")
    print(f"N snapshots : {diag['n_snapshots']} | N rebalances : {diag['n_periods']}")
    print(
        f"Frais : slippage={diag['slippage_bps']:.1f} bps  "
        f"commission=${diag['commission_per_share']:.4f}/sh  "
        f"avg_turnover={diag['avg_turnover']*100:.1f}%  "
        f"total_costs={diag['total_costs_pct']*100:.2f}%"
    )
    if diag.get("publication_lag_days", 0) > 0:
        print(
            f"Anti-lookahead : ranking décalé de {diag['publication_lag_days']}j  "
            f"({diag.get('n_skipped_lag', 0)} période(s) skippée(s) faute d'historique)"
        )
    if diag.get("impact_coef", 0) > 0:
        print(
            f"Impact ADTV : book=${diag['book_size_usd']:,.0f}  "
            f"coef={diag['impact_coef']}  "
            f"(fallback turnover {diag.get('avg_turnover', 0)*100:.1f}%)"
        )
    print()
    print(f"Total return      : {result.total_return*100:+.2f}%")
    print(f"Avg daily return  : {result.avg_daily_return*100:+.3f}%")
    if result.sharpe_daily is not None:
        print(f"Sharpe daily      : {result.sharpe_daily:.2f}  (annualized ~ {result.sharpe_annual:.2f})")
    else:
        print("Sharpe            : N/A (< 2 périodes ou σ=0)")
    print(f"Max drawdown      : {result.max_drawdown*100:.2f}%")
    print(f"Hit rate          : {result.hit_rate*100:.1f}%")
    if result.benchmark_return is not None:
        print(f"Benchmark {bench:<5}   : {result.benchmark_return*100:+.2f}%")
        print(f"Alpha             : {result.alpha*100:+.2f}% {'✅' if result.alpha > 0 else '🔴'}")
    print()
    print("=== Equity curve ===")
    for d, eq in result.equity_curve:
        bar = "█" * int((eq - 1.0) * 1000 + 0.5) if eq >= 1.0 else ""
        print(f"  {d}  {eq:7.4f}  {bar}")
    print()
    print("=== Périodes ===")
    for p_ in result.periods:
        top5 = ", ".join(p_.top_tickers[:5])
        print(f"  {p_.signal_date} → {p_.next_date}: {p_.portfolio_return*100:+.2f}% "
              f"({p_.n_valid}/{p_.n_valid + p_.n_skipped} valid) | top5: {top5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
