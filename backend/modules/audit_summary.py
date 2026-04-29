"""Audit summary — verdict global "TITAN bat-il le marché ?" + checklist.

Audit S1 (2026-04-27) — diagnostic dashboard
=============================================
Ce module agrège quatre sources de vérité du moteur TITAN :
  1. Backtest historique (alpha vs SPY) — `modules.backtest`
  2. Calibration walk-forward — `data/wfo_weights.json`
  3. Registry delisted — `data/delisted.json`
  4. Profondeur historique — `modules.universe_history`

Et produit deux artefacts :
  - une liste de **checks** structurés ({name, status, value, threshold, msg})
    chacun étiqueté `ok` / `warn` / `fail` / `na`.
  - un **verdict global** parmi BAT_LE_MARCHÉ / INCERTAIN / NE_BAT_PAS /
    DONNÉES_INSUFFISANTES, dérivé des checks par règles déterministes.

Le backtest est lancé par `get_latest_backtest()` avec un cache JSON 24h
(`data/audit_backtest_cache.json`). Refresh forcé via `refresh=True`.

Endpoint : `GET /api/audit/full` (router audit).
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from modules import backtest as bt_mod
from modules import delisted as delisted_mod
from modules import universe_history, wfo_calibration, wfo_monitor
from modules.log import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BACKTEST_CACHE_PATH = _PROJECT_ROOT / "data" / "audit_backtest_cache.json"
_CACHE_LOCK = Lock()

# Backtest defaults — alignés avec config production.
_BT_TOP_N = 20
_BT_SLIPPAGE_BPS = 5.0           # mid-cap réaliste
_BT_COMMISSION_PER_SHARE = 0.0   # Alpaca = 0
_BT_BENCHMARK = "SPY"

# Cache TTL — 24h pour les runs OK (le backtest est déterministe à snapshot
# fixe, on ne le relance que quand `universe_history` s'est densifié = 1×/jour
# via cron). 5 min pour les erreurs/insufficient_data afin d'éviter qu'un
# hoquet transitoire (yfinance down 30s) ne fige la page audit pendant 24h.
_CACHE_TTL_OK_SECONDS    = 24 * 3600
_CACHE_TTL_ERROR_SECONDS = 5 * 60

# Seuils des checks. Documentés ici en un seul endroit pour faciliter le tuning.
_TH = {
    # Backtest performance
    "alpha_min":                0.0,    # alpha net > 0 = bat le marché
    "sharpe_min":               0.5,    # sharpe annualisé minimum acceptable
    "hit_rate_min":             0.50,   # > 50% des périodes positives
    "max_drawdown_max":         0.25,   # ≤ 25% drawdown
    "min_periods_for_trust":    20,     # < 20 périodes = stat-non-significatif
    # Audit S4 (Lot 16) — gating verdict global. Tant qu'on n'a pas 60 périodes
    # OOS, le verdict ne peut pas être robuste — un alpha sur 10 trades ne
    # signifie statistiquement rien. On force DONNÉES_INSUFFISANTES sous ce
    # seuil même quand le backtest tourne.
    "min_periods_for_verdict":  60,
    # Modèle (WFO)
    "wfo_weights_drift_max":    0.05,   # |Δ| par pilier
    "wfo_ic_min":               0.02,   # IC test mort sous ça
    "wfo_min_folds":            3,      # < 3 folds = peu fiable
    # Données
    "min_snapshots_history":    60,     # 60j ≈ 3 mois pour WFO solide
}


# ─────────────────────────────────────────────────────────────────
# Backtest cache
# ─────────────────────────────────────────────────────────────────

def _load_cache() -> dict[str, Any] | None:
    if not _BACKTEST_CACHE_PATH.exists():
        return None
    try:
        return json.loads(_BACKTEST_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"[audit_summary] cache corrompu : {e}")
        return None


def _save_cache(payload: dict[str, Any]) -> None:
    _BACKTEST_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _BACKTEST_CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    tmp.replace(_BACKTEST_CACHE_PATH)


def _latest_snapshot_mtime() -> float | None:
    """mtime du snapshot universe_history le plus récent. None si aucun.

    Sert à invalider le cache backtest dès qu'un snapshot plus frais est posé
    (cron daily à 06:00 ou rescoring API au cours de la journée), sans
    attendre l'expiration du TTL 24h.
    """
    try:
        snaps = sorted(
            universe_history.HISTORY_DIR.glob("snapshot_*.json.gz")
        )
    except OSError:
        return None
    if not snaps:
        return None
    try:
        return snaps[-1].stat().st_mtime
    except OSError:
        return None


def _is_fresh(payload: dict[str, Any]) -> bool:
    ts = payload.get("_cached_at")
    if not isinstance(ts, (int, float)):
        return False
    # TTL plus court pour les erreurs : permet le retry rapide après un
    # hoquet transitoire (yfinance, FMP).
    ttl = (_CACHE_TTL_OK_SECONDS
            if payload.get("_status") == "ok"
            else _CACHE_TTL_ERROR_SECONDS)
    if (time.time() - ts) >= ttl:
        return False
    # Invalidation event-driven : si un snapshot universe_history plus récent
    # que `_cached_at` existe, on considère le cache stale même si le TTL
    # n'est pas écoulé. Évite la fenêtre où le cron 06:00 produit la donnée
    # du jour mais le cache reste figé sur celle d'hier jusqu'au prochain
    # tick TTL.
    snap_mtime = _latest_snapshot_mtime()
    if snap_mtime is not None and snap_mtime > ts:
        return False
    return True


def _fetch_benchmark_curve(dates: list[str], ticker: str = "SPY") -> list[dict[str, Any]] | None:
    """Récupère la série SPY (close, normalisée à 1.0 au 1er jour) sur les
    dates de l'equity curve TITAN. Retourne None si yfinance échoue.

    Permet d'afficher dans l'UI une **vraie** courbe SPY au lieu d'une
    interpolation linéaire trompeuse.
    """
    if not dates:
        return None
    try:
        import yfinance as yf
        first, last = dates[0], dates[-1]
        # Buffer de 1j en aval pour garantir que `last` soit inclus (yfinance
        # `end` est exclusive).
        from datetime import datetime, timedelta
        end_buf = (datetime.fromisoformat(last) + timedelta(days=2)).date().isoformat()
        df = yf.download(ticker, start=first, end=end_buf,
                          progress=False, auto_adjust=True)
        if df is None or df.empty or "Close" not in df.columns:
            return None
        # Normalise à 1.0 au 1er close dispo dans la fenêtre.
        # `df["Close"]` peut être un DataFrame multi-index (yfinance >= 0.2.50)
        # quand `auto_adjust=True` ; on fait un `.squeeze` qui dégénère vers
        # une Series 1-D si une seule colonne ticker est présente.
        closes = df["Close"]
        if hasattr(closes, "squeeze"):
            closes = closes.squeeze("columns") if hasattr(closes, "columns") else closes
        closes = closes.dropna()
        if closes.empty:
            return None
        first_close = float(closes.iloc[0].item() if hasattr(closes.iloc[0], "item") else closes.iloc[0])
        if first_close <= 0:
            return None
        # On reprojette sur les dates TITAN : pour chaque date d'equity,
        # on prend le close SPY le plus récent ≤ d (forward-fill style).
        date_to_norm: list[dict[str, Any]] = []
        for d in dates:
            try:
                row = closes.loc[:d]
                if row.empty:
                    date_to_norm.append({"date": d, "spy": None})
                    continue
                last = row.iloc[-1]
                last_v = float(last.item() if hasattr(last, "item") else last)
                date_to_norm.append({"date": d, "spy": round(last_v / first_close, 5)})
            except Exception:
                date_to_norm.append({"date": d, "spy": None})
        return date_to_norm
    except Exception as e:
        logger.warning(f"[audit_summary] benchmark curve fetch failed: {e}")
        return None


def get_latest_backtest(*, refresh: bool = False) -> dict[str, Any]:
    """Retourne le dernier backtest TITAN top-N (cache 24h).

    Si `refresh=True` ou cache expiré/absent, relance `run_titan_top_n`
    avec les défauts production (top-N 20, slippage 5 bps, point-in-time).

    Renvoie le dict to_dict() du BacktestResult, augmenté de `_cached_at`
    (epoch) et `_status` ∈ {"ok", "error", "insufficient_data"} +
    `_error` si pertinent.
    """
    with _CACHE_LOCK:
        cached = _load_cache()
        if not refresh and cached and _is_fresh(cached):
            return cached

        try:
            result = bt_mod.run_titan_top_n(
                top_n=_BT_TOP_N,
                benchmark=_BT_BENCHMARK,
                weighting="equal",
                slippage_bps=_BT_SLIPPAGE_BPS,
                commission_per_share=_BT_COMMISSION_PER_SHARE,
                point_in_time=True,
                publication_lag_days=0,
            )
            payload = result.to_dict()
            payload["_status"] = "ok"
            # Fetch real SPY daily curve aligned on TITAN equity dates →
            # remplace l'interpolation linéaire trompeuse côté frontend.
            equity_dates = [row[0] for row in (payload.get("equity_curve") or [])]
            payload["benchmark_curve"] = _fetch_benchmark_curve(
                equity_dates, ticker=_BT_BENCHMARK,
            )
        except ValueError as e:
            # Cas typique : pas assez de snapshots dans universe_history.
            payload = {
                "_status": "insufficient_data",
                "_error": str(e),
            }
        except Exception as e:
            logger.warning(f"[audit_summary] backtest échec : {e}",
                            exc_info=True)
            payload = {"_status": "error", "_error": str(e)}

        payload["_cached_at"] = time.time()
        payload["_cached_at_iso"] = datetime.now(UTC).isoformat()
        _save_cache(payload)
        return payload


# ─────────────────────────────────────────────────────────────────
# Helpers checks
# ─────────────────────────────────────────────────────────────────

def _check(
    *,
    name: str,
    category: str,
    status: str,
    label: str,
    value: Any,
    threshold: Any = None,
    message: str = "",
    hint: str = "",
) -> dict[str, Any]:
    """Format unifié d'un check. `status` ∈ {ok, warn, fail, na}."""
    return {
        "name":      name,
        "category":  category,
        "status":    status,
        "label":     label,
        "value":     value,
        "threshold": threshold,
        "message":   message,
        "hint":      hint,
    }


def _build_backtest_checks(bt: dict[str, Any]) -> list[dict[str, Any]]:
    """Génère les checks de la catégorie 'Performance' depuis le backtest."""
    out: list[dict[str, Any]] = []
    cat = "performance"

    if bt.get("_status") != "ok":
        msg = bt.get("_error") or "Backtest non exécutable."
        # Tous les checks performance basculent en `na` (données insuffisantes).
        for name, label in (
            ("alpha_net",     "Alpha vs SPY (net de coûts)"),
            ("sharpe_annual", "Sharpe annualisé"),
            ("hit_rate",      "Hit rate (% périodes positives)"),
            ("max_drawdown",  "Drawdown max"),
            ("coverage",      "Couverture statistique"),
        ):
            out.append(_check(
                name=name, category=cat, status="na",
                label=label, value=None, threshold=None,
                message=msg,
                hint="Le backtest a besoin d'au moins 2 snapshots dans "
                     "`universe_history`. Plus de données arrivent chaque "
                     "jour via le cron `run_titan.sh`.",
            ))
        return out

    # 1. Alpha net (vs SPY)
    alpha = bt.get("alpha")
    bench = bt.get("benchmark_return")
    total = bt.get("total_return")
    if alpha is None:
        out.append(_check(
            name="alpha_net", category=cat, status="na",
            label="Alpha vs SPY (net de coûts)", value=None,
            threshold=_TH["alpha_min"],
            message="Benchmark SPY indisponible (yfinance fail ?).",
        ))
    else:
        if alpha > _TH["alpha_min"]:
            status = "ok"
            msg = f"TITAN ({total*100:+.2f}%) bat SPY ({bench*100:+.2f}%)."
        elif alpha > -0.005:
            status = "warn"
            msg = (f"TITAN ({total*100:+.2f}%) ≈ SPY ({bench*100:+.2f}%) — "
                    f"écart {alpha*100:+.2f}% non significatif.")
        else:
            status = "fail"
            msg = (f"TITAN ({total*100:+.2f}%) sous-performe SPY "
                    f"({bench*100:+.2f}%) de {alpha*100:.2f}%.")
        out.append(_check(
            name="alpha_net", category=cat, status=status,
            label="Alpha vs SPY (net de coûts)",
            value=round(alpha, 5), threshold=_TH["alpha_min"],
            message=msg,
            hint=("Inclut slippage 5bps + point-in-time. "
                  "Si négatif : revoir piliers, top-N, ou slippage."),
        ))

    # 2. Sharpe annualisé
    sharpe = bt.get("sharpe_annual")
    if sharpe is None:
        out.append(_check(
            name="sharpe_annual", category=cat, status="na",
            label="Sharpe annualisé", value=None,
            threshold=_TH["sharpe_min"],
            message="Sharpe non calculable (< 2 périodes ou σ=0).",
        ))
    else:
        if sharpe >= _TH["sharpe_min"]:
            status = "ok"
        elif sharpe > 0:
            status = "warn"
        else:
            status = "fail"
        out.append(_check(
            name="sharpe_annual", category=cat, status=status,
            label="Sharpe annualisé", value=round(sharpe, 2),
            threshold=_TH["sharpe_min"],
            message=f"Sharpe = {sharpe:.2f} (seuil {_TH['sharpe_min']}).",
            hint=">1.0 = excellent, 0.5-1.0 = correct, <0.5 = peu rémunérateur.",
        ))

    # 3. Hit rate
    hr = bt.get("hit_rate")
    if hr is None:
        out.append(_check(
            name="hit_rate", category=cat, status="na",
            label="Hit rate", value=None,
            threshold=_TH["hit_rate_min"]))
    else:
        status = "ok" if hr >= _TH["hit_rate_min"] else (
            "warn" if hr >= 0.45 else "fail")
        out.append(_check(
            name="hit_rate", category=cat, status=status,
            label="Hit rate", value=round(hr, 3),
            threshold=_TH["hit_rate_min"],
            message=f"{hr*100:.1f}% des périodes finissent positives.",
            hint="50% = pile/face. Compense des amplitudes asymétriques mais "
                 "<45% deux trimestres de suite = signal cassé.",
        ))

    # 4. Max drawdown
    dd = bt.get("max_drawdown")
    if dd is None:
        out.append(_check(
            name="max_drawdown", category=cat, status="na",
            label="Drawdown max", value=None,
            threshold=_TH["max_drawdown_max"]))
    else:
        if dd <= 0.10:
            status = "ok"
        elif dd <= _TH["max_drawdown_max"]:
            status = "warn"
        else:
            status = "fail"
        out.append(_check(
            name="max_drawdown", category=cat, status=status,
            label="Drawdown max",
            value=round(dd, 4), threshold=_TH["max_drawdown_max"],
            message=f"DD max observé : -{dd*100:.2f}%.",
            hint="< 10% = confortable, 10-25% = acceptable, > 25% = risqué.",
        ))

    # 5. Couverture statistique (n_periods)
    n_periods = bt.get("n_periods", 0)
    needed = _TH["min_periods_for_trust"]
    if n_periods >= needed:
        status, msg = "ok", f"{n_periods} périodes — résultats fiables."
    elif n_periods >= 5:
        status, msg = ("warn",
                        f"{n_periods}/{needed} périodes — significativité "
                        "statistique limitée.")
    else:
        status, msg = ("fail",
                        f"{n_periods}/{needed} périodes — résultats à prendre "
                        "avec des pincettes.")
    out.append(_check(
        name="coverage", category=cat, status=status,
        label="Couverture statistique", value=n_periods,
        threshold=needed, message=msg,
        hint="L'historique se densifie 1×/jour via le cron `run_titan.sh`.",
    ))

    return out


def _build_data_checks(
    delisted_payload: dict[str, Any],
    n_snapshots: int,
    bt: dict[str, Any],
) -> list[dict[str, Any]]:
    """Catégorie 'Données' — santé des inputs du backtest.

    Note : le check `publication_lag` a été retiré — il auditait une constante
    de config (`publication_lag_days=0` hardcodé dans `get_latest_backtest`)
    plutôt qu'un état de santé. Les snapshots étant créés à l'instant T avec
    les fondamentaux *visibles à T* (yfinance/FMP point-in-time), il n'y a pas
    de leak temporel à signaler.
    """
    out: list[dict[str, Any]] = []
    cat = "data"

    # 1. Survivorship filter (delisted registry)
    n_delisted = delisted_payload.get("n_delisted", 0)
    if n_delisted == 0:
        out.append(_check(
            name="survivorship_filter", category=cat, status="warn",
            label="Filtre survivorship", value=0, threshold=1,
            message="Registry delisted vide — backtests sur univers actuel "
                    "(légèrement gonflé). Le registre s'enrichit à chaque "
                    "rebuild d'univers.",
            hint="Un index reshuffle SP500 typique = 5-10 tickers/an.",
        ))
    else:
        out.append(_check(
            name="survivorship_filter", category=cat, status="ok",
            label="Filtre survivorship",
            value=n_delisted, threshold=1,
            message=f"{n_delisted} tickers retirés tracés. Backtest "
                    f"point-in-time activé.",
        ))

    # 2. Profondeur historique (snapshots)
    needed = _TH["min_snapshots_history"]
    if n_snapshots >= needed:
        status = "ok"
        msg = f"{n_snapshots} snapshots — base solide."
    elif n_snapshots >= 20:
        status = "warn"
        msg = f"{n_snapshots}/{needed} snapshots — historique en construction."
    else:
        status = "fail"
        msg = f"{n_snapshots}/{needed} snapshots — historique trop court."
    out.append(_check(
        name="history_depth", category=cat, status=status,
        label="Profondeur snapshots universe", value=n_snapshots,
        threshold=needed, message=msg,
        hint="Le cron daily `run_titan.sh` step 6 ajoute 1 snapshot/jour.",
    ))

    return out


def _build_model_checks(
    wfo: dict[str, Any] | None,
    history_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Catégorie 'Modèle' — santé des poids piliers et du signal."""
    out: list[dict[str, Any]] = []
    cat = "model"

    if not wfo:
        # Pas de WFO encore lancée → 3 checks `na`.
        for name, label in (
            ("weights_drift", "Drift poids piliers (prod vs OOS)"),
            ("signal_alive",  "Signal vivant (IC test)"),
            ("wfo_folds",     "Robustesse WFO (n folds)"),
        ):
            out.append(_check(
                name=name, category=cat, status="na",
                label=label, value=None, threshold=None,
                message="WFO jamais exécutée.",
                hint="Lancer : `python -m modules.wfo_calibration` "
                     "(le cron mensuel le fait automatiquement, step 3b).",
            ))
        return out

    n_folds = wfo.get("n_folds", 0)
    th_min_folds = _TH["wfo_min_folds"]
    # Gating : sous le seuil de folds, drift et IC sont du bruit statistique
    # (avec 1 fold les poids optimaux saturent à 0/1, l'IC dépend d'1 paire
    # train/test). On marque ces 2 checks `na` plutôt que de cracher des
    # `fail` qui contrediraient `wfo_folds: warn`.
    folds_unreliable = n_folds < th_min_folds

    # 1. Drift poids piliers (max |Δ|)
    deltas = wfo.get("weight_deltas") or {}
    if deltas:
        max_delta = max((abs(v) for v in deltas.values()), default=0.0)
        worst_pillar = max(deltas.items(), key=lambda kv: abs(kv[1]),
                            default=(None, 0.0))[0]
    else:
        # Calcul à la volée si l'endpoint n'a pas pré-calculé.
        avg = wfo.get("avg_weights") or {}
        try:
            from modules.sector_metrics import _scoring
            prod = {
                "quality_score":   _scoring._W_TITAN_QUALITY,
                "value_score":     _scoring._W_TITAN_VALUE,
                "risk_score":      _scoring._W_TITAN_RISK,
                "sentiment_score": _scoring._W_TITAN_SENTIMENT,
                "momentum_score":  _scoring._W_TITAN_MOMENTUM,
                "piotroski_score": _scoring._W_TITAN_PIOTROSKI,
                "growth_score":    _scoring._W_TITAN_GROWTH,
            }
        except Exception:
            prod = {}
        deltas = {p: round(avg.get(p, 0) - prod.get(p, 0), 4)
                    for p in (set(avg) | set(prod))}
        max_delta = max((abs(v) for v in deltas.values()), default=0.0)
        worst_pillar = max(deltas.items(), key=lambda kv: abs(kv[1]),
                            default=(None, 0.0))[0]

    th = _TH["wfo_weights_drift_max"]
    if folds_unreliable:
        out.append(_check(
            name="weights_drift", category=cat, status="na",
            label="Drift poids piliers (prod vs OOS)",
            value=round(max_delta, 4), threshold=th,
            message=f"Non évaluable : {n_folds} fold(s) — avec moins de "
                    f"{th_min_folds} folds, les poids optimaux WFO saturent "
                    "et le drift est du bruit, pas un signal.",
            hint="Re-évalué automatiquement quand l'historique aura "
                 "produit ≥ 3 folds.",
        ))
    elif max_delta < th:
        out.append(_check(
            name="weights_drift", category=cat, status="ok",
            label="Drift poids piliers (prod vs OOS)",
            value=round(max_delta, 4), threshold=th,
            message=f"Max drift {max_delta:.3f} (pilier {worst_pillar}).",
            hint="Δ > 0.05 = poids prod incohérent avec ce qui prédit le mieux "
                 "OOS sur l'historique.",
        ))
    elif max_delta < th * 2:
        out.append(_check(
            name="weights_drift", category=cat, status="warn",
            label="Drift poids piliers (prod vs OOS)",
            value=round(max_delta, 4), threshold=th,
            message=f"Drift {max_delta:.3f} sur {worst_pillar} — surveiller.",
            hint="Δ > 0.05 = poids prod incohérent avec ce qui prédit le mieux "
                 "OOS sur l'historique.",
        ))
    else:
        out.append(_check(
            name="weights_drift", category=cat, status="fail",
            label="Drift poids piliers (prod vs OOS)",
            value=round(max_delta, 4), threshold=th,
            message=f"Drift {max_delta:.3f} sur {worst_pillar} — rebalancer "
                    "prod ou justifier l'écart.",
            hint="Δ > 0.05 = poids prod incohérent avec ce qui prédit le mieux "
                 "OOS sur l'historique.",
        ))

    # 2. Signal vivant (IC test moyen)
    ic = wfo.get("avg_ic_test")
    if folds_unreliable:
        out.append(_check(
            name="signal_alive", category=cat, status="na",
            label="Signal vivant (IC test)",
            value=round(ic, 4) if ic is not None else None,
            threshold=_TH["wfo_ic_min"],
            message=f"Non évaluable : {n_folds} fold(s) — l'IC sur 1 paire "
                    "train/test est dominé par le bruit d'échantillonnage.",
            hint="Le seuil IC ≥ 0.02 ne devient significatif qu'à partir "
                 f"de {th_min_folds}+ folds.",
        ))
    elif ic is None:
        out.append(_check(
            name="signal_alive", category=cat, status="na",
            label="Signal vivant (IC test)", value=None,
            threshold=_TH["wfo_ic_min"]))
    else:
        if ic >= 0.05:
            status = "ok"
            msg = f"IC = {ic:.4f} — signal robuste."
        elif ic >= _TH["wfo_ic_min"]:
            status = "warn"
            msg = f"IC = {ic:.4f} — signal marginal."
        else:
            status = "fail"
            msg = (f"IC = {ic:.4f} sous {_TH['wfo_ic_min']} — "
                    "le composite ne prédit plus.")
        out.append(_check(
            name="signal_alive", category=cat, status=status,
            label="Signal vivant (IC test)", value=round(ic, 4),
            threshold=_TH["wfo_ic_min"],
            message=msg,
            hint="IC = corrélation rang Spearman score↔forward return OOS.",
        ))

    # 3. Robustesse WFO (n folds) — sert de gate aux 2 checks au-dessus.
    if n_folds >= th_min_folds:
        status = "ok"
        msg = f"{n_folds} folds — agrégat fiable."
    elif n_folds >= 1:
        status = "warn"
        msg = (f"{n_folds}/{th_min_folds} folds — drift et IC ne sont pas "
                "encore évaluables (plus d'historique nécessaire).")
    else:
        status = "fail"
        msg = "Aucun fold — WFO non concluante."
    out.append(_check(
        name="wfo_folds", category=cat, status=status,
        label="Robustesse WFO (n folds)",
        value=n_folds, threshold=th_min_folds,
        message=msg,
        hint="Plus de folds = moins d'overfit. Visez ≥ 6 folds en prod.",
    ))

    return out


# ─────────────────────────────────────────────────────────────────
# Verdict global
# ─────────────────────────────────────────────────────────────────

def _compute_global_verdict(
    checks: list[dict[str, Any]],
    bt: dict[str, Any],
) -> dict[str, Any]:
    """Dérive un verdict synthétique à partir des checks.

    Règles :
      • Si backtest `na` → DONNÉES_INSUFFISANTES (priorité absolue).
      • Sinon, si alpha_net ok ET aucun check 'fail' (toutes catégories) ET
        coverage ok → BAT_LE_MARCHÉ.
      • Si alpha_net fail → NE_BAT_PAS.
      • Tout le reste (warns, alpha proche zéro, coverage faible…) → INCERTAIN.
    """
    by_name = {c["name"]: c for c in checks}
    alpha_chk = by_name.get("alpha_net", {})
    coverage_chk = by_name.get("coverage", {})

    if bt.get("_status") != "ok" or alpha_chk.get("status") == "na":
        return {
            "verdict": "DONNÉES_INSUFFISANTES",
            "label":   "Pas assez de données pour conclure",
            "color":   "muted",
            "summary": ("Le backtest n'a pas pu tourner sur l'historique "
                         "actuel. Reviens dans quelques jours."),
        }

    alpha = alpha_chk.get("value") or 0.0
    bench = bt.get("benchmark_return")
    total = bt.get("total_return")
    n_periods = bt.get("n_periods", 0)

    # Audit S4 (Lot 16) — gate de robustesse statistique. Tant qu'on n'a pas
    # `min_periods_for_verdict` périodes OOS, on REFUSE de conclure même si
    # alpha apparaît positif : 4 périodes ne disent rien sur un univers de 500.
    min_periods = _TH.get("min_periods_for_verdict", 60)
    if n_periods < min_periods:
        progress_pct = round(100.0 * n_periods / max(1, min_periods), 1)
        return {
            "verdict": "DONNÉES_INSUFFISANTES",
            "label":   "Statistiquement prématuré",
            "color":   "muted",
            "summary": (
                f"Backtest sur {n_periods}/{min_periods} période(s) requises "
                f"({progress_pct} %). Verdict suspendu — un alpha sur < "
                f"{min_periods} périodes n'est pas distinguable du bruit. "
                "Le cron `run_titan.sh` accumule un snapshot/jour ; "
                "reviens dans quelques semaines."
            ),
            "progress_pct": progress_pct,
            "n_periods":    n_periods,
            "n_periods_required": min_periods,
        }

    n_fail = sum(1 for c in checks if c["status"] == "fail")
    n_warn = sum(1 for c in checks if c["status"] == "warn")

    if alpha_chk.get("status") == "fail":
        return {
            "verdict": "NE_BAT_PAS",
            "label":   "TITAN sous-performe SPY",
            "color":   "danger",
            "summary": (
                f"Sur {n_periods} période(s) : TITAN {total*100:+.2f}% vs "
                f"SPY {bench*100:+.2f}% (alpha {alpha*100:+.2f}%). "
                f"{n_fail} check(s) en échec."
            ),
        }

    if (alpha_chk.get("status") == "ok"
            and coverage_chk.get("status") == "ok"
            and n_fail == 0):
        return {
            "verdict": "BAT_LE_MARCHÉ",
            "label":   "TITAN bat le marché",
            "color":   "success",
            "summary": (
                f"Sur {n_periods} période(s) : TITAN {total*100:+.2f}% vs "
                f"SPY {bench*100:+.2f}% (alpha {alpha*100:+.2f}%). "
                f"Tous les checks fondamentaux sont au vert."
                + (f" {n_warn} warn(s) à surveiller." if n_warn else "")
            ),
        }

    return {
        "verdict": "INCERTAIN",
        "label":   "Verdict incertain",
        "color":   "warning",
        "summary": (
            f"Sur {n_periods} période(s) : TITAN {total*100:+.2f}% vs "
            f"SPY {bench*100:+.2f}% (alpha {alpha*100:+.2f}%). "
            f"{n_fail} fail / {n_warn} warn — voir checklist."
        ),
    }


# ─────────────────────────────────────────────────────────────────
# Loaders annexes
# ─────────────────────────────────────────────────────────────────

def _load_wfo() -> dict[str, Any] | None:
    """Lit `wfo_weights.json` + injecte `weight_deltas` (cohérent /api/wfo)."""
    if not wfo_calibration.WFO_OUTPUT_PATH.exists():
        return None
    try:
        payload = json.loads(
            wfo_calibration.WFO_OUTPUT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"[audit_summary] wfo corrompu : {e}")
        return None

    try:
        from modules.sector_metrics import _scoring
        prod = {
            "quality_score":   _scoring._W_TITAN_QUALITY,
            "value_score":     _scoring._W_TITAN_VALUE,
            "risk_score":      _scoring._W_TITAN_RISK,
            "sentiment_score": _scoring._W_TITAN_SENTIMENT,
            "momentum_score":  _scoring._W_TITAN_MOMENTUM,
            "piotroski_score": _scoring._W_TITAN_PIOTROSKI,
            "growth_score":    _scoring._W_TITAN_GROWTH,
        }
        avg = payload.get("avg_weights") or {}
        payload["prod_weights"]  = prod
        payload["weight_deltas"] = {
            p: round(avg.get(p, 0) - prod.get(p, 0), 4)
            for p in (set(avg) | set(prod))
        }
    except Exception:
        payload.setdefault("weight_deltas", {})
    return payload


def _history_summary() -> dict[str, Any]:
    """Compte rapide du nombre de snapshots universe_history disponibles."""
    snaps = universe_history.list_snapshots()
    if not snaps:
        return {"n_snapshots": 0, "first": None, "last": None}
    return {
        "n_snapshots": len(snaps),
        "first":       snaps[0].isoformat(),
        "last":        snaps[-1].isoformat(),
    }


def _load_history_ic() -> dict[str, Any]:
    """Résumé de wfo_history.jsonl pour le payload audit/full."""
    entries = wfo_monitor._read_history(last_n=50)
    return {
        "n_total":      len(entries),
        "latest":       entries[-1] if entries else None,
        "threshold_ic": wfo_monitor._IC_DEGRADATION_THRESHOLD,
    }


# ─────────────────────────────────────────────────────────────────
# API publique
# ─────────────────────────────────────────────────────────────────

def compute_full_audit(*, refresh: bool = False) -> dict[str, Any]:
    """Construit le payload complet de la page Audit.

    Sortie :
        {
          "verdict":  {verdict, label, color, summary},
          "checks":   [...],         # tous les checks groupables par category
          "backtest": {...},         # cache backtest (avec _status, _cached_at)
          "wfo":      {...} | None,
          "delisted": {...},
          "history":  {n_snapshots, first, last},
          "ic_history": {n_total, latest, threshold_ic},
          "thresholds": _TH,
        }
    """
    bt = get_latest_backtest(refresh=refresh)
    wfo = _load_wfo()
    delisted_payload = _delisted_payload()
    hist = _history_summary()
    ic_hist = _load_history_ic()

    checks = (
        _build_backtest_checks(bt)
        + _build_data_checks(delisted_payload, hist["n_snapshots"], bt)
        + _build_model_checks(wfo, hist)
    )
    verdict = _compute_global_verdict(checks, bt)

    return {
        "verdict":    verdict,
        "checks":     checks,
        "backtest":   bt,
        "wfo":        wfo,
        "delisted":   delisted_payload,
        "history":    hist,
        "ic_history": ic_hist,
        "thresholds": _TH,
    }


def _delisted_payload() -> dict[str, Any]:
    """Reformate delisted.load_registry() pour match /api/delisted."""
    registry = delisted_mod.load_registry()
    tickers_meta: dict[str, Any] = registry.get("tickers") or {}
    delisted_rows = delisted_mod.list_delisted()
    n_active = sum(1 for m in tickers_meta.values()
                    if not m.get("removed_at"))
    return {
        "n_total":    len(tickers_meta),
        "n_delisted": len(delisted_rows),
        "n_active":   n_active,
        "delisted":   delisted_rows,
    }
