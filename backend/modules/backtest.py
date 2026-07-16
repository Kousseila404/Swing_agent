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
from typing import Any, TypedDict

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
    alpha:           float | None = None           # total_return - benchmark_return (brut)
    # Phase 3 audit — alpha CAPM = excès de retour ajusté du beta. None si
    # < 5 périodes ou benchmark indisponible. Voir _capm_alpha_beta.
    diagnostics_capm: dict[str, float] | None = None
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
            "diagnostics_capm": self.diagnostics_capm,
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


<<<<<<< Updated upstream
def _count_total_tickers(snapshot: dict[str, Any]) -> int:
    return len(snapshot.get("tickers") or {})


def _count_low_dq_tickers(snapshot: dict[str, Any]) -> int:
    """Phase 3 audit — nombre de tickers sans titan_composite_score (NaN/None)."""
    snap_tickers = snapshot.get("tickers") or {}
    n = 0
    for row in snap_tickers.values():
        score = (row or {}).get("titan_composite_score")
        if score is None or (isinstance(score, float) and not math.isfinite(score)):
            n += 1
    return n


=======
>>>>>>> Stashed changes
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
<<<<<<< Updated upstream
    period_days: float = 7.0,
    risk_free_rate_annual: float = 0.045,
=======
>>>>>>> Stashed changes
) -> PeriodResult:
    """Simule un rebalance : rank top-N sur t, applique les poids choisis,
    mesure return brut, ponctionne les coûts proportionnels au turnover.

    Coûts modélisés :
<<<<<<< Updated upstream
      • slippage_bps : coût en basis points en one-way (par entrée OU sortie).
        Phase 3 audit (2026-05-06) — fix : on charge `turnover_two_way` au lieu
        de `turnover_one_way` pour comptabiliser entrées + sorties. Avant, un
        full rotation ne payait que la moitié des frais (sortie attribuée à
        la période suivante mais jamais facturée). Round-trip = 2× slippage_bps
        × position_size, naturellement.
=======
      • slippage_bps : coût en basis points sur la fraction du book qui
        tourne (entrée OU sortie). Round-trip = 2× slippage si le ticker
        est entré ce rebalance ET sortira au prochain.
>>>>>>> Stashed changes
      • commission_per_share : coût $ par action. Modélisé en bps en
        divisant par le prix moyen de l'entrée (approximation : 0.005 $/sh
        sur action à 100 $ ≈ 0.5 bp). Faute de connaître la taille du book
        en $, on capitalise sur 1.0 USD de book → c'est l'estimation
        relative qu'on cherche.
<<<<<<< Updated upstream
      • risk_free_rate_annual : taux money market sur la fraction non investie
        (cash drag). Phase 3 audit — avant, la fraction uninvested rapportait
        0% → Sharpe surestimé. Maintenant on crédite (1 - Σw) × rf × period.
=======
>>>>>>> Stashed changes
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
<<<<<<< Updated upstream
    for t, _w in weights.items():
=======
    for t, w in weights.items():
>>>>>>> Stashed changes
        if t in px_t and t in px_t1:
            returns[t] = (px_t1[t] / px_t[t]) - 1.0

    # Return brut = Σ_i w_i × r_i (pondéré, pas une simple moyenne).
    portfolio_return_gross = sum(w * returns.get(t, 0.0) for t, w in weights.items())

    # Turnover one-way : somme des changements absolus / 2.
    prev = prev_weights or {}
    all_keys = set(weights) | set(prev)
    turnover_two_way = sum(abs(weights.get(t, 0.0) - prev.get(t, 0.0)) for t in all_keys)
<<<<<<< Updated upstream
    turnover = turnover_two_way / 2.0  # one-way (reporté pour le diagnostic)

    # Phase 3 audit (2026-05-06) — coût slippage = bps × turnover_two_way :
    # entrée ET sortie sont chacune facturées en one-way, donc somme = two_way.
    # Avant : seulement turnover_one_way → Sharpe surestimé ~10-20% sur
    # stratégies haute rotation.
    slip_cost = (slippage_bps / 10_000.0) * turnover_two_way

    # Commission : approximation. Sans modèle de book size en $, on fixe par
    # convention 1 unité de book = 1 USD ; commission_per_share / px = bps
    # par dollar tourné → multipliée par turnover_two_way (entrées + sorties).
=======
    turnover = turnover_two_way / 2.0  # one-way

    # Coût total : slippage + commission proportionnels au turnover.
    # Convention : turnover=1.0 (full rotation) ponctionne 1× slippage_bps en
    # one-way. La sortie au rebalance suivant est attribuée à la période
    # suivante, donc on n'inclut pas le round-trip ici.
    slip_cost = (slippage_bps / 10_000.0) * turnover

    # Commission : approximation. Sans modèle de book size en $, on fixe par
    # convention 1 unité de book = 1 USD ; commission_per_share / px = bps
    # par dollar tourné → multipliée par turnover.
>>>>>>> Stashed changes
    if commission_per_share > 0 and px_t:
        # Average price of tickers we touched ce rebalance (un proxy raisonnable).
        touched = [px_t[t] for t in all_keys if t in px_t]
        avg_px = sum(touched) / len(touched) if touched else 0.0
<<<<<<< Updated upstream
        comm_cost = (commission_per_share / avg_px) * turnover_two_way if avg_px > 0 else 0.0
=======
        comm_cost = (commission_per_share / avg_px) * turnover if avg_px > 0 else 0.0
>>>>>>> Stashed changes
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

<<<<<<< Updated upstream
    # Phase 3 audit (2026-05-06) — cash drag : la fraction non investie
    # (Σw < 1 car certains tickers droppés faute de px_t) doit rapporter le
    # taux money market, pas 0%. Avant : Sharpe surestimé +200-500 bps/an.
    invested_fraction = sum(weights.values())  # déjà renormalisé à 1 si tout OK
    cash_fraction = max(0.0, 1.0 - invested_fraction)
    cash_period_return = (
        ((1.0 + risk_free_rate_annual) ** (period_days / 365.0)) - 1.0
        if cash_fraction > 0 and risk_free_rate_annual > 0
        else 0.0
    )
    cash_yield = cash_fraction * cash_period_return

    cost_pct = slip_cost + comm_cost + impact_cost
    portfolio_return = portfolio_return_gross + cash_yield - cost_pct
=======
    cost_pct = slip_cost + comm_cost + impact_cost
    portfolio_return = portfolio_return_gross - cost_pct
>>>>>>> Stashed changes

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


class _StatsDict(TypedDict):
    """Sortie de `_compute_stats` — sharpe_* nullable, le reste toujours float."""
    total_return: float
    avg_period_return: float
    avg_daily_return: float
    sharpe_period: float | None
    sharpe_daily: float | None
    sharpe_annual: float | None
    max_drawdown: float
    hit_rate: float
    annualization_factor: float


def _compute_stats(
    periods: list[PeriodResult],
    *,
    period_days: float = 7.0,
) -> _StatsDict:
    """Agrège stats à partir des returns par période.

    Phase 3 audit (2026-05-06) — Sharpe annualisé adapté à la fréquence des
    périodes : facteur = √(365 / period_days) au lieu de √252 hardcodé. Sur
    snapshots hebdo, factor=√52 ≈ 7.2 (vs √252 ≈ 15.9 = ~2.2× inflation).
    Sur mensuel, √12 ≈ 3.5 (vs √252 ≈ ~4.6× inflation).
    """
    rets = [p.portfolio_return for p in periods]
    if not rets:
        return {
            "total_return": 0.0,
            "avg_period_return": 0.0,
            "avg_daily_return": 0.0,            # legacy alias
            "sharpe_period": None,
            "sharpe_daily": None,                # legacy alias
            "sharpe_annual": None,
            "max_drawdown": 0.0,
            "hit_rate": 0.0,
            "annualization_factor": 1.0,
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
    sharpe_period: float | None = None
    if len(rets) >= 2:
        sigma = statistics.stdev(rets)
        if sigma > 0 and math.isfinite(sigma):
            sharpe_period = avg / sigma
    # Fréquence-aware : √(périodes par an). period_days = 7 → ~52/an → √52 ≈ 7.2.
    annualization_factor = (
        math.sqrt(365.0 / period_days) if period_days > 0 else math.sqrt(252)
    )
    sharpe_annual = (
        sharpe_period * annualization_factor if sharpe_period is not None else None
    )

    return {
        "total_return":         equity - 1.0,
        "avg_period_return":    avg,
        "avg_daily_return":     avg,  # legacy alias pour clients existants
        "sharpe_period":        sharpe_period,
        "sharpe_daily":         sharpe_period,  # legacy alias (mauvais nom historique)
        "sharpe_annual":        sharpe_annual,
        "max_drawdown":         max_dd,
        "hit_rate":             hit,
        "annualization_factor": round(annualization_factor, 3),
    }


def _benchmark_return(start: date, end: date, ticker: str = "SPY") -> float | None:
    """Return buy-and-hold du benchmark entre start et end (yfinance).

    Fail-open : retourne None si yfinance KO (pas bloquant pour le backtest).

    NB : `auto_adjust=True` ajuste pour splits + dividendes (proxy total return),
    mais yfinance peut introduire un lag 1-2j sur les corrections splits — sur
    horizons multi-année cela vaut ~20-40 bps/an d'erreur sur SPY. Pour un
    backtest production-grade, préférer un index dividendes-réinvestis officiel.
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


def _benchmark_period_returns(
    period_endpoints: list[tuple[str, str]],
    ticker: str = "SPY",
) -> list[float] | None:
    """Phase 3 audit — returns du benchmark par-période, alignés sur les bornes
    `(signal_date, next_date)` du backtest. Permet la régression CAPM.

    Returns None en cas d'échec yfinance (CAPM disabled).
    """
    if not period_endpoints:
        return None
    try:
        from datetime import date as _date
        from datetime import timedelta

        import yfinance as yf
        all_dates = sorted({d for pair in period_endpoints for d in pair})
        if len(all_dates) < 2:
            return None
        start = _date.fromisoformat(all_dates[0])
        end = _date.fromisoformat(all_dates[-1]) + timedelta(days=3)
        df = yf.download(
            ticker,
            start=start.isoformat(),
            end=end.isoformat(),
            progress=False, auto_adjust=True, threads=False,
        )
        if df is None or df.empty:
            return None
        if hasattr(df.columns, "get_level_values"):
            closes = df["Close"]
            if hasattr(closes, "columns"):
                closes = closes.iloc[:, 0]
        else:
            closes = df["Close"]
        # Indexe par date ISO pour lookup rapide ; pour chaque période, prend le
        # dernier close ≤ date (gère les weekends).
        idx_by_iso: dict[str, float] = {}
        for ts, val in closes.items():
            try:
                iso = ts.strftime("%Y-%m-%d")
                idx_by_iso[iso] = float(val)
            except Exception:
                continue
        sorted_isos = sorted(idx_by_iso.keys())

        def _last_close_on_or_before(iso: str) -> float | None:
            # Recherche dichotomique simple : on prend le plus grand iso ≤ cible.
            chosen: str | None = None
            for d_iso in sorted_isos:
                if d_iso <= iso:
                    chosen = d_iso
                else:
                    break
            return idx_by_iso.get(chosen) if chosen else None

        out: list[float] = []
        for d0_iso, d1_iso in period_endpoints:
            p0 = _last_close_on_or_before(d0_iso)
            p1 = _last_close_on_or_before(d1_iso)
            if p0 is None or p1 is None or p0 <= 0:
                return None
            out.append((p1 / p0) - 1.0)
        return out
    except Exception as e:
        logger.warning(f"[Backtest] CAPM benchmark {ticker} per-period fetch failed: {e}")
        return None


def _capm_alpha_beta(
    port_rets: list[float],
    bench_rets: list[float],
    *,
    period_days: float,
    risk_free_rate_annual: float = 0.045,
) -> dict[str, float]:
    """Régression OLS : (port - rf) = α + β × (bench - rf). Annualise α.

    Phase 3 audit (2026-05-06) — avant, alpha = total_return - benchmark_return
    sans ajustement beta → un portfolio high-beta en bull market apparaît
    avec alpha apparent qui n'est en fait que de la prime de risque marché.
    """
    n = min(len(port_rets), len(bench_rets))
    if n < 5:
        return {"alpha_annual": float("nan"), "beta": float("nan"), "n": n}
    rf_period = ((1.0 + risk_free_rate_annual) ** (period_days / 365.0)) - 1.0
    xs = [b - rf_period for b in bench_rets[:n]]
    ys = [p - rf_period for p in port_rets[:n]]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False)) / n
    var_x = sum((x - mean_x) ** 2 for x in xs) / n
    if var_x <= 0:
        return {"alpha_annual": float("nan"), "beta": float("nan"), "n": n}
    beta = cov_xy / var_x
    alpha_period = mean_y - beta * mean_x
    periods_per_year = 365.0 / period_days if period_days > 0 else 252
    alpha_annual = ((1.0 + alpha_period) ** periods_per_year) - 1.0
    return {
        "alpha_annual": round(alpha_annual, 5),
        "alpha_period": round(alpha_period, 5),
        "beta":         round(beta, 4),
        "n":            n,
    }


def _resample_snapshots(
    snapshots: list[tuple[date, dict[str, Any]]],
    min_period_days: float,
) -> list[tuple[date, dict[str, Any]]]:
    """Sous-échantillonne les snapshots pour espacer deux dates retenues d'au
    moins `min_period_days` jours.

    Audit 2026-07-16 — `universe_scheduler` ne rafraîchit `current_price` et
    le momentum que pour ~100 tickers/jour (budget quota, rotation ~7j) : sur
    des snapshots QUOTIDIENS, ~65-70 % des tickers d'un rebalance donné ont un
    prix figé (0 % de retour artificiel) tandis que les ~35 % rafraîchis ce
    jour-là encaissent d'un coup jusqu'à 7 jours de mouvement réel, comptés
    comme "1 jour" dans le calcul du retour ET dans l'annualisation Sharpe
    (`√365`). Résultat observé en prod : +51 % / 3 mois, Sharpe annualisé
    7.25, alpha annualisé +615 % — non crédible.

    En resamplant à ~7j (durée du cycle de rotation complet), chaque période
    couvre une fenêtre où la quasi-totalité de l'univers a été rafraîchie au
    moins une fois, ce qui aligne le backtest sur l'hypothèse déjà documentée
    dans `_compute_stats` ("snapshots hebdo, factor=√52").

    Glouton : garde toujours le premier ET le dernier snapshot disponibles ;
    entre les deux, avance jusqu'à la prochaine date qui dépasse le seuil.
    `min_period_days <= 0` désactive (comportement legacy = tous les
    snapshots consécutifs).
    """
    if min_period_days <= 0 or len(snapshots) < 2:
        return snapshots
    out = [snapshots[0]]
    for d, s in snapshots[1:]:
        if (d - out[-1][0]).days >= min_period_days:
            out.append((d, s))
    if out[-1][0] != snapshots[-1][0]:
        out.append(snapshots[-1])
    return out


def run_titan_top_n(
    top_n: int = 20,
    benchmark: str | None = "SPY",
    *,
    weighting: str = "equal",
    slippage_bps: float = 0.0,
    commission_per_share: float = 0.0,
    point_in_time: bool = True,
<<<<<<< Updated upstream
    publication_lag_days: int = 5,
    book_size_usd: float = 100_000.0,
    impact_coef: float = 0.0,
    fallback_turnover_ratio: float = 0.005,
    restrict_to: list[str] | None = None,
    min_period_days: float = 7.0,
=======
    publication_lag_days: int = 0,
    book_size_usd: float = 100_000.0,
    impact_coef: float = 0.0,
    fallback_turnover_ratio: float = 0.005,
>>>>>>> Stashed changes
) -> BacktestResult:
    """Backtest complet du pipeline TITAN sur l'historique disponible.

    Args:
        top_n: taille du portefeuille (défaut 20).
        benchmark: ticker du benchmark (défaut SPY). None = pas de comparaison.
        weighting: equal | score | risk_parity (défaut equal).
        slippage_bps: bps de slippage one-way par turnover (défaut 0).
        commission_per_share: $ par action sur les ordres (défaut 0).
        publication_lag_days: décale le ranking de N jours pour éviter le
<<<<<<< Updated upstream
            lookahead fondamentaux (défaut 5 = lag minimum FMP/yfinance T+2 à
            T+5). 90 j = lag 10-K typique pour un backtest production-grade.
            À signal_date d_i, on utilisera le snapshot le plus récent < d_i
            - lag pour ranker, mais le return reste mesuré sur (d_i, d_{i+1}).
        min_period_days: espacement minimum entre deux rebalances (défaut 7 —
            audit 2026-07-16, cf. `_resample_snapshots`). 0/1 = legacy
            (rebalance sur chaque snapshot quotidien disponible).
=======
            lookahead fondamentaux (défaut 0 = pas de lag, comportement
            historique). 90 j = lag 10-K typique, recommandé pour un
            backtest production-grade. À signal_date d_i, on utilisera
            le snapshot le plus récent < d_i - lag pour ranker, mais le
            return reste mesuré sur (d_i, d_{i+1}).
>>>>>>> Stashed changes
    """
    dates = universe_history.list_snapshots()
    if len(dates) < 2:
        raise ValueError(
            f"Au moins 2 snapshots requis pour 1 période de rebalance. "
            f"Disponibles : {[d.isoformat() for d in dates]}"
        )

    snapshots: list[tuple[date, dict[str, Any]]] = []
    n_bootstrap = 0
    for d in dates:
        snap = universe_history.read_snapshot(d)
        if snap:
            snapshots.append((d, snap))
            # Détecte si le snapshot vient du bootstrap rétroactif (fundamentals
            # figés à aujourd'hui = lookahead). Cf. universe_history_bootstrap.py.
            if (snap.get("macro") or {}).get("_bootstrap"):
                n_bootstrap += 1
    if len(snapshots) < 2:
        raise ValueError("Snapshots illisibles")
    if n_bootstrap > 0:
        logger.warning(
            f"[Backtest] LOOKAHEAD WARNING: {n_bootstrap}/{len(snapshots)} "
            f"snapshots viennent du bootstrap rétroactif — fundamentals figés "
            f"à aujourd'hui (Q/V/R/S/P/G/RV/IN). Seuls Momentum + current_price "
            f"sont vrais point-in-time. Les métriques alpha/IC sont biaisées "
            f"vers le haut. Pour un backtest as-reported, utiliser des snapshots "
            f"matérialisés en live (cron quotidien)."
        )

    # Audit 2026-07-16 — resample AVANT le reste du pipeline (point-in-time,
    # lag, période médiane) pour qu'un rebalance ne tombe jamais entre deux
    # jours où la majorité de l'univers n'a pas été rafraîchie (cf.
    # `_resample_snapshots`).
    n_snapshots_raw = len(snapshots)
    snapshots = _resample_snapshots(snapshots, min_period_days)

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

    # Phase 3 audit (2026-05-06) — fréquence des snapshots, utilisé pour le
    # cash drag par-période et pour annualiser le Sharpe correctement (au
    # lieu du √252 hardcodé).
    period_spans = [
        (snapshots[i + 1][0] - snapshots[i][0]).days
        for i in range(len(snapshots) - 1)
    ]
    median_period_days = (
        float(sorted(period_spans)[len(period_spans) // 2])
        if period_spans else 7.0
    )
    if median_period_days <= 0:
        median_period_days = 7.0

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

    periods: list[PeriodResult] = []
    prev_w: dict[str, float] = {}
    n_skipped_periods = 0
<<<<<<< Updated upstream
    n_low_quality_snapshots = 0
    for (d0, s0), (d1, s1) in zip(snapshots[:-1], snapshots[1:], strict=False):
=======
    for (d0, s0), (d1, s1) in zip(snapshots[:-1], snapshots[1:]):
>>>>>>> Stashed changes
        active = active_per_date.get(d0)
        # Si le registry est vide pour cette date (premier run, pas d'historique
        # de delisting) ⇒ active=set vide ⇒ on désactive le filtre pour ne pas
        # vider artificiellement le ranking.
        active_filter = active if active else None
<<<<<<< Updated upstream
        # Intersection avec la whitelist demandée (si fournie).
        if restrict_set is not None:
            active_filter = (active_filter & restrict_set) if active_filter else restrict_set
=======

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
>>>>>>> Stashed changes

        ranking_snapshot = _ranking_snapshot_for(d0) if publication_lag_days > 0 else s0
        if ranking_snapshot is None:
            # Lag plus large que l'historique disponible avant d0 → skip cette
            # période (impossible de ranker sans lookahead).
            n_skipped_periods += 1
            continue

        # Phase 3 audit — data quality screen : si ≥ 30% des tickers du snapshot
        # ranking ont un titan_composite_score=None, on log + skip cette
        # période (impossible de ranker fiablement). Avant : ranking dégénéré
        # silencieux avec n_eff=7-8 sur 20 cibles.
        n_dq_low = _count_low_dq_tickers(ranking_snapshot)
        n_dq_total = _count_total_tickers(ranking_snapshot)
        if n_dq_total > 0 and (n_dq_low / n_dq_total) >= 0.30:
            n_low_quality_snapshots += 1
            logger.warning(
                f"[Backtest] {d0.isoformat()} : {n_dq_low}/{n_dq_total} tickers "
                f"({n_dq_low/n_dq_total*100:.0f}%) sans titan_composite_score "
                f"— période skippée (data quality < 70%)."
            )
            continue

        # Période effective entre d0 et d1 (peut différer de median pour la
        # dernière période ou les gaps weekend).
        actual_days = max(1.0, float((d1 - d0).days))

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
            period_days=actual_days,
        )
        # Override les dates : signal_date doit refléter d0, pas la date
        # du snapshot ranking (sinon l'equity curve est désalignée).
        period.signal_date = d0.isoformat()
        period.next_date = d1.isoformat()
        periods.append(period)
        prev_w = period.weights

    stats = _compute_stats(periods, period_days=median_period_days)

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

    # Benchmark (SPY buy&hold sur la même période).
    # Phase 3 audit (2026-05-06) — alpha brut + alpha CAPM (excès via beta).
    if benchmark:
        bench_start = snapshots[0][0]
        bench_end   = snapshots[-1][0]
        bench_ret = _benchmark_return(bench_start, bench_end, benchmark)
        if bench_ret is not None:
            result.benchmark_return = bench_ret
            result.alpha = result.total_return - bench_ret
            # CAPM alpha : régression OLS portfolio_returns vs benchmark_returns
            # par-période, puis annualise. Demande au moins 5 périodes pour être
            # statistiquement utile.
            if len(periods) >= 5:
                bench_period_returns = _benchmark_period_returns(
                    [(p.signal_date, p.next_date) for p in periods],
                    benchmark,
                )
                if bench_period_returns and len(bench_period_returns) == len(periods):
                    capm = _capm_alpha_beta(
                        port_rets=[p.portfolio_return for p in periods],
                        bench_rets=bench_period_returns,
                        period_days=median_period_days,
                        risk_free_rate_annual=0.045,
                    )
                    result.diagnostics_capm = capm

    avg_turnover = (
        sum(p.turnover for p in periods) / len(periods) if periods else 0.0
    )
    total_costs = sum(p.cost_pct for p in periods)
    result.diagnostics = {
        "n_snapshots":          len(snapshots),
<<<<<<< Updated upstream
        "n_snapshots_raw":      n_snapshots_raw,
        "min_period_days":      min_period_days,
        "n_periods":            len(periods),
        "n_skipped_lag":        n_skipped_periods,
        "n_skipped_low_dq":     n_low_quality_snapshots,
        "n_bootstrap_snapshots": n_bootstrap,
        "lookahead_warning":     n_bootstrap > 0,
=======
        "n_periods":            len(periods),
        "n_skipped_lag":        n_skipped_periods,
>>>>>>> Stashed changes
        "benchmark":            benchmark,
        "weighting":            weighting,
        "slippage_bps":         slippage_bps,
        "commission_per_share": commission_per_share,
        "publication_lag_days": publication_lag_days,
        "book_size_usd":        book_size_usd,
        "impact_coef":          impact_coef,
        "avg_turnover":         round(avg_turnover, 4),
        "total_costs_pct":      round(total_costs, 5),
<<<<<<< Updated upstream
        "median_period_days":   round(median_period_days, 1),
        "annualization_factor": stats.get("annualization_factor", 1.0),
=======
>>>>>>> Stashed changes
        "date_range": {
            "start": snapshots[0][0].isoformat(),
            "end":   snapshots[-1][0].isoformat(),
        },
    }
    if result.diagnostics_capm:
        result.diagnostics["capm"] = result.diagnostics_capm
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
<<<<<<< Updated upstream
    p.add_argument("--publication-lag-days", type=int, default=5,
                   help="Décalage du ranking en jours (anti-lookahead "
                        "fondamentaux). Défaut 5 j = lag minimum FMP/yf T+2 à T+5. "
                        "0 = pas de lag (legacy, lookahead implicite). "
                        "90 = lag 10-K production-grade. "
=======
    p.add_argument("--publication-lag-days", type=int, default=0,
                   help="Décalage du ranking en jours (anti-lookahead "
                        "fondamentaux). 0 = pas de lag (défaut, comportement "
                        "historique). 90 = recommandé production, lag 10-K. "
>>>>>>> Stashed changes
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
<<<<<<< Updated upstream
    p.add_argument("--min-period-days", type=float, default=7.0,
                   help="Espacement minimum entre deux rebalances (défaut 7). "
                        "Audit 2026-07-16 : universe_scheduler ne rafraîchit "
                        "current_price/momentum que pour ~100 tickers/jour "
                        "(rotation 7j) — un rebalance quotidien mélange des "
                        "prix figés et des sauts de plusieurs jours comptés "
                        "comme '1 jour', ce qui fausse Sharpe/alpha. "
                        "0 ou 1 = legacy (un rebalance par snapshot dispo).")
=======
>>>>>>> Stashed changes
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
<<<<<<< Updated upstream
            min_period_days=args.min_period_days,
=======
>>>>>>> Stashed changes
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
        if result.alpha is not None:
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
