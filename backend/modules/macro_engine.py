"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE — MACRO ENGINE V1                                       ║
║  Détermination du Régime Macro-Économique Global                ║
║                                                                  ║
║  Sources :                                                       ║
║    ^GSPC  → S&P 500 (tendance macro, EMA 200 jours)             ║
║    ^VIX   → CBOE Volatility Index (peur du marché)              ║
║                                                                  ║
║  Régimes :                                                       ║
║    BULL_MARKET  → S&P500 > EMA200 ET VIX < 25                  ║
║                   Autorise uniquement les trades LONG            ║
║    BEAR_MARKET  → S&P500 < EMA200 ET VIX < 35                  ║
║                   Autorise uniquement les trades SHORT           ║
║    CRASH_PANIC  → VIX ≥ 35 (panique extrême)                   ║
║                   BLOQUE tous les trades — protection capital    ║
║                                                                  ║
║  Anti-biais strict :                                             ║
║    - Utilise uniquement la dernière barre COMPLÈTE (no look-     ║
║      ahead), calculée après la fermeture du marché.             ║
║    - Alignement date-par-date des séries VIX / S&P500           ║
║      via inner join sur l'index commun.                          ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import config
from data_providers.base import MarketDataProviderBase
from data_providers.yfinance_provider import YFinanceProvider
from modules.log import logger

# Fichier d'état pour la confirmation de régime sur N jours consécutifs
_MACRO_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "macro_state.json"

# ─────────────────────────────────────────────────────────────────
# PROVIDER MARKET DATA (défaut yfinance — Polygon utilise un autre
# format de ticker pour les indices, ^GSPC/^VIX ne sont pas portables)
# ─────────────────────────────────────────────────────────────────

_default_market_provider: MarketDataProviderBase | None = None


def _get_default_market_provider() -> MarketDataProviderBase:
    """Instance paresseuse partagée pour éviter les coûts d'init répétés."""
    global _default_market_provider
    if _default_market_provider is None:
        _default_market_provider = YFinanceProvider()
    return _default_market_provider


# ─────────────────────────────────────────────────────────────────
# CONFIRMATION DE RÉGIME — Persistance inter-cycles
# ─────────────────────────────────────────────────────────────────

def _load_macro_state() -> dict:
    """Charge l'état de confirmation de régime depuis macro_state.json."""
    if not _MACRO_STATE_PATH.exists():
        return {}
    try:
        with open(_MACRO_STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_macro_state(state: dict) -> None:
    """Persiste l'état de confirmation de régime."""
    _MACRO_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_MACRO_STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def _apply_regime_confirmation(raw_regime: str) -> str:
    """
    Applique la confirmation sur N jours consécutifs (REGIME_CONFIRMATION_DAYS)
    avant de valider un changement de régime. Évite les whipsaws lors de
    passages VIX autour des seuils.

    CRASH_PANIC est immédiat (protection capital — pas de délai).

    Returns:
        Le régime confirmé (stable) ou le régime précédent si en période d'attente.
    """
    confirmation_days = int(getattr(config, "REGIME_CONFIRMATION_DAYS", 3))
    today = datetime.now().date().isoformat()

    # CRASH_PANIC → immédiat, sans confirmation
    if raw_regime == "CRASH_PANIC":
        _save_macro_state({
            "confirmed_regime": "CRASH_PANIC",
            "candidate_regime": "CRASH_PANIC",
            "candidate_since":  today,
            "last_update":      today,
        })
        logger.info("[MacroEngine] ⚡ CRASH_PANIC — confirmation immédiate (capital protégé)")
        return "CRASH_PANIC"

    state     = _load_macro_state()
    confirmed = state.get("confirmed_regime", raw_regime)
    candidate = state.get("candidate_regime", raw_regime)
    c_since   = state.get("candidate_since",  today)

    if raw_regime == confirmed:
        # Régime stable — réinitialise le candidat
        _save_macro_state({
            "confirmed_regime": confirmed,
            "candidate_regime": confirmed,
            "candidate_since":  today,
            "last_update":      today,
        })
        return confirmed

    if candidate != raw_regime:
        # Nouveau candidat détecté
        _save_macro_state({
            "confirmed_regime": confirmed,
            "candidate_regime": raw_regime,
            "candidate_since":  today,
            "last_update":      today,
        })
        logger.info(
            f"[MacroEngine] ⏳ Nouveau candidat : {raw_regime} "
            f"(confirmé : {confirmed}). Attente de {confirmation_days}j consécutifs."
        )
        return confirmed

    # Même candidat — compter les jours OUVRÉS depuis candidate_since
    # (calendar days faussent le décompte sur les weekends/jours fériés)
    try:
        import pandas as _pd
        since_date = datetime.fromisoformat(c_since).date()
        today_date = datetime.now().date()
        days_since = len(_pd.bdate_range(str(since_date), str(today_date)))
    except Exception:
        days_since = 0

    if days_since >= confirmation_days:
        logger.info(
            f"[MacroEngine] ✅ Changement de régime confirmé après {days_since}j : "
            f"{confirmed} → {raw_regime}"
        )
        _save_macro_state({
            "confirmed_regime": raw_regime,
            "candidate_regime": raw_regime,
            "candidate_since":  today,
            "last_update":      today,
        })
        return raw_regime
    else:
        remaining = confirmation_days - days_since
        logger.info(
            f"[MacroEngine] ⏳ Candidat {raw_regime} depuis {days_since}j "
            f"({remaining}j restant(s)). Régime actuel maintenu : {confirmed}"
        )
        _save_macro_state({
            "confirmed_regime": confirmed,
            "candidate_regime": raw_regime,
            "candidate_since":  c_since,
            "last_update":      today,
        })
        return confirmed


# ─────────────────────────────────────────────────────────────────
# CACHE EN MÉMOIRE  (évite les appels API répétés dans le dashboard)
# ─────────────────────────────────────────────────────────────────

_REGIME_CACHE: dict = {}
_CACHE_TTL_SECONDS = 4 * 3600   # 4 heures


# ─────────────────────────────────────────────────────────────────
# INDICATEURS SECONDAIRES — Crédit + Breadth (composite regime V2)
# ─────────────────────────────────────────────────────────────────

def _download_secondary_indicators(
    market_provider: MarketDataProviderBase | None = None,
) -> dict:
    """
    Télécharge les indicateurs secondaires pour le régime composite.

    Sources :
      HYG  → iShares iBoxx $ High Yield ETF    (crédit risqué)
      LQD  → iShares iBoxx $ Investment Grade  (crédit sûr)
      RSP  → Invesco S&P 500 Equal Weight ETF  (breadth du marché)

    Retourne un dict avec les séries de clôture sur 100j ou None si erreur.
    """
    provider = market_provider or _get_default_market_provider()
    result: dict = {}

    for sym in ("HYG", "LQD", "RSP"):
        try:
            series = provider.get_daily_history(sym, days=120)  # 120j → 50j de MA propre
            if series is not None and len(series) >= 30:
                series = series.dropna()
                # Normalise l'index en datetime naïf pour permettre les joins avec SP/VIX
                series.index = pd.to_datetime(series.index).tz_localize(None)
                result[sym] = series
            else:
                result[sym] = None
                logger.debug(f"[MacroEngine] {sym} données insuffisantes")
        except Exception as e:
            result[sym] = None
            logger.debug(f"[MacroEngine] {sym} téléchargement échoué : {e}")

    return result


def _gmm_regime_signal(
    sp500_close: pd.Series,
    vix_close: pd.Series,
) -> str:
    """
    Signal de régime probabiliste via Gaussian Mixture Model (3 états).

    Entraîné sur les 252 dernières barres avec 3 features :
      1. Rendement journalier S&P500
      2. Niveau VIX normalisé
      3. Momentum 5j S&P500

    Les états sont labellisés automatiquement selon leur rendement moyen :
      État à rendement le plus élevé  → BULL
      État à rendement le plus faible → BEAR
      Troisième état                  → TRANSITION (→ UNCERTAIN)

    Filtre de confiance : retourne UNCERTAIN si la proba max < 55%
    pour éviter les signaux ambigus.

    Returns: "BULL" | "BEAR" | "UNCERTAIN"
    """
    try:
        import numpy as np
        from sklearn.mixture import GaussianMixture

        common_idx = sp500_close.index.intersection(vix_close.index)
        if len(common_idx) < 120:
            return "UNCERTAIN"

        sp_a  = sp500_close.reindex(common_idx)
        vix_a = vix_close.reindex(common_idx)

        returns    = sp_a.pct_change().dropna()
        vix_norm   = (vix_a - 15.0) / 20.0          # centré VIX=15
        momentum_5 = sp_a.pct_change(5).dropna()

        common2 = returns.index.intersection(vix_norm.index).intersection(momentum_5.index)
        if len(common2) < 80:
            return "UNCERTAIN"

        feat = np.column_stack([
            returns.reindex(common2).values,
            vix_norm.reindex(common2).values,
            momentum_5.reindex(common2).values,
        ])
        feat = feat[-252:]   # 252 dernières barres (≈ 1 an de trading)

        model = GaussianMixture(
            n_components=3, covariance_type="diag",
            n_init=5, random_state=42, max_iter=300,
        )
        model.fit(feat)
        states = model.predict(feat)

        # Labellisation par rendement moyen de chaque état
        state_returns: dict[int, float] = {}
        for s in range(3):
            mask = states == s
            if mask.sum() > 5:
                state_returns[s] = float(feat[mask, 0].mean())

        if len(state_returns) < 2:
            return "UNCERTAIN"

        sorted_states = sorted(state_returns.items(), key=lambda x: x[1])
        bear_state = sorted_states[0][0]    # plus faible rendement moyen
        bull_state = sorted_states[-1][0]   # plus fort rendement moyen

        current_state = int(states[-1])
        max_prob      = float(model.predict_proba(feat[-1:].reshape(1, -1))[0].max())

        if max_prob < 0.55:   # pas assez confiant → UNCERTAIN
            return "UNCERTAIN"

        if current_state == bull_state:
            logger.debug(f"[MacroEngine] GMM: état BULL (prob={max_prob:.2f})")
            return "BULL"
        if current_state == bear_state:
            logger.debug(f"[MacroEngine] GMM: état BEAR (prob={max_prob:.2f})")
            return "BEAR"
        return "UNCERTAIN"

    except Exception as e:
        logger.debug(f"[MacroEngine] GMM erreur : {e}")
        return "UNCERTAIN"


def _compute_composite_override(
    secondary: dict,
    sp500_close: pd.Series | None = None,
    vix_close: pd.Series | None   = None,
) -> str | None:
    """
    Détermine si les indicateurs secondaires justifient un downgrade BULL→BEAR.

    Logique conservatrice : downgrade UNIQUEMENT si ≥ 2 signaux sur 3
    sont simultanément bearish.

    Indicateurs :
      1. Credit Spread (HYG/LQD ratio < MA50)
      2. Market Breadth (RSP < MA50)
      3. GMM Regime Signal (Gaussian Mixture Model 3 états)

    Returns:
        "BEAR_MARKET" si override déclenché (downgrade BULL→BEAR)
        None si pas d'override (régime primaire maintenu)
    """
    bearish_signals = 0

    # ── Signal 1 : Credit Spread HYG/LQD ────────────────────────
    hyg = secondary.get("HYG")
    lqd = secondary.get("LQD")
    if hyg is not None and lqd is not None and len(hyg) >= 50 and len(lqd) >= 50:
        try:
            common = hyg.index.intersection(lqd.index)
            if len(common) >= 50:
                ratio = hyg.reindex(common) / lqd.reindex(common)
                ratio_ma50 = ratio.rolling(50).mean()
                if float(ratio.iloc[-1]) < float(ratio_ma50.iloc[-1]):
                    bearish_signals += 1
                    logger.debug("[MacroEngine] ⚠️  Credit spread HYG/LQD bearish (ratio < MA50)")
        except Exception as e:
            logger.debug(f"[MacroEngine] Credit spread calc erreur : {e}")

    # ── Signal 2 : Market Breadth RSP ────────────────────────────
    rsp = secondary.get("RSP")
    if rsp is not None and len(rsp) >= 50:
        try:
            rsp_ma50 = rsp.rolling(50).mean()
            if float(rsp.iloc[-1]) < float(rsp_ma50.iloc[-1]):
                bearish_signals += 1
                logger.debug("[MacroEngine] ⚠️  Market breadth RSP bearish (RSP < MA50)")
        except Exception as e:
            logger.debug(f"[MacroEngine] Market breadth calc erreur : {e}")

    # ── Signal 3 : GMM Régime probabiliste ───────────────────────
    if sp500_close is not None and vix_close is not None:
        try:
            gmm_signal = _gmm_regime_signal(sp500_close, vix_close)
            if gmm_signal == "BEAR":
                bearish_signals += 1
                logger.debug("[MacroEngine] ⚠️  GMM signal: BEAR")
            elif gmm_signal == "BULL":
                logger.debug("[MacroEngine] ✅ GMM signal: BULL")
            else:
                logger.debug("[MacroEngine] ℹ️  GMM signal: UNCERTAIN (non compté)")
        except Exception as e:
            logger.debug(f"[MacroEngine] GMM calc erreur : {e}")

    if bearish_signals >= 2:
        logger.info(
            f"[MacroEngine] 🔴 Override composite : {bearish_signals}/3 signaux bearish "
            "(crédit + breadth + GMM) → downgrade BULL_MARKET → BEAR_MARKET"
        )
        return "BEAR_MARKET"

    if bearish_signals == 1:
        logger.info("[MacroEngine] ⚠️  1/3 signaux secondaires bearish — régime primaire BULL maintenu")

    return None


# ─────────────────────────────────────────────────────────────────
# TÉLÉCHARGEMENT DES DONNÉES MACRO
# ─────────────────────────────────────────────────────────────────

def _download_macro_data(
    years: int = 2,
    market_provider: MarketDataProviderBase | None = None,
) -> tuple[pd.Series | None, pd.Series | None]:
    """
    Télécharge les closes ajustés du S&P500 et du VIX via un
    MarketDataProviderBase (par défaut yfinance).

    On télécharge 2 ans pour disposer d'au moins 200 barres de trading
    nécessaires au calcul de l'EMA 200 jours du S&P500.

    Anti-biais : le provider renvoie toutes les barres closes jusqu'à
    la dernière disponible. La sélection de la "dernière barre complète"
    est faite dans _align_and_compute() via iloc[-1] — jamais via une
    date future.

    Note : Polygon utilise un format de ticker différent pour les indices
    (I:SPX, I:VIX) — le défaut yfinance reste le plus portable. On peut
    injecter un provider custom pour les tests.

    Returns:
        Tuple (sp500_close, vix_close) — Series indexées DatetimeIndex
        (naïf), ou (None, None) si erreur réseau.
    """
    provider = market_provider or _get_default_market_provider()
    days = years * 365 + 90   # +90j de marge pour l'EMA200

    try:
        sp_series = provider.get_daily_history(config.MACRO_INDEX, days=days)
        if sp_series is None or len(sp_series) == 0:
            logger.error(f"[MacroEngine] Données {config.MACRO_INDEX} indisponibles")
            return None, None
        sp_series = sp_series.dropna()
        sp_series.index = pd.to_datetime(sp_series.index).tz_localize(None)
    except Exception as e:
        logger.error(f"[MacroEngine] Erreur téléchargement {config.MACRO_INDEX} : {e}")
        return None, None

    try:
        vix_series = provider.get_daily_history(config.VIX_INDEX, days=days)
        if vix_series is None or len(vix_series) == 0:
            logger.error(f"[MacroEngine] Données {config.VIX_INDEX} indisponibles")
            return None, None
        vix_series = vix_series.dropna()
        vix_series.index = pd.to_datetime(vix_series.index).tz_localize(None)
    except Exception as e:
        logger.error(f"[MacroEngine] Erreur téléchargement {config.VIX_INDEX} : {e}")
        return None, None

    return sp_series, vix_series


# ─────────────────────────────────────────────────────────────────
# ALIGNEMENT & CALCUL DE L'EMA 200
# ─────────────────────────────────────────────────────────────────

def _align_and_compute(
    sp_series: pd.Series,
    vix_series: pd.Series,
) -> tuple[float, float, float]:
    """
    Aligne les séries S&P500 et VIX sur leurs dates communes,
    calcule l'EMA 200 jours sur le S&P500, et extrait les 3 valeurs
    critiques de la dernière barre complète.

    Alignement par inner join sur l'index de date :
      - Les jours fériés US peuvent décaler VIX/SP500 d'un jour.
      - L'inner join garantit qu'on ne compare que des barres simultanées.
      - Aucun NaN résiduel n'entre dans la prise de décision.

    Anti-biais strict :
      - iloc[-1] = dernière barre complète (clôture passée connue).
      - On n'utilise pas shift(1) car la dernière barre est déjà formée.

    Args:
        sp_series:  Série des closes ajustés S&P500.
        vix_series: Série des closes ajustés VIX.

    Returns:
        Tuple (sp500_close, ema200_value, vix_close) — tous positifs.

    Raises:
        ValueError: Si les données alignées sont insuffisantes.
    """
    # Calcul de l'EMA200 sur la série complète du S&P500 — convention Wilder
    # alpha = 1/200 (identique au filtre EMA200 utilisé dans generate_signal_mask)
    ema200_series = sp_series.ewm(alpha=1.0 / 200, adjust=False).mean()

    if ema200_series.dropna().empty:
        raise ValueError(
            "[MacroEngine] Impossible de calculer l'EMA200 — "
            "données S&P500 insuffisantes (besoin de 200+ barres)"
        )

    # Construction d'un DataFrame combiné avec inner join sur les dates
    df_combined = (
        pd.DataFrame({"sp500": sp_series, "ema200": ema200_series})
        .join(pd.DataFrame({"vix": vix_series}), how="inner")
        .dropna()
    )

    if len(df_combined) < 10:
        raise ValueError(
            f"[MacroEngine] Seulement {len(df_combined)} barres après alignement "
            "SP500/VIX — données insuffisantes pour le régime."
        )

    # Lecture de la DERNIÈRE barre complète (aucun look-ahead)
    last = df_combined.iloc[-1]
    sp500 = float(last["sp500"])
    ema200 = float(last["ema200"])
    vix    = float(last["vix"])

    return sp500, ema200, vix


# ─────────────────────────────────────────────────────────────────
# DÉCISION DE RÉGIME — logique pure, sans IO
# ─────────────────────────────────────────────────────────────────

def _regime_decision(sp500: float, ema200: float, vix: float) -> str:
    """
    Applique les règles de décision du régime à partir des trois valeurs clés.

    Extraction pure (sans download, sans cache) partagée par
    get_market_regime() et get_regime_details().

    Ordre de priorité strict :
      1. VIX ≥ VIX_PANIC_MIN            → CRASH_PANIC
      2. SP500 > EMA200 ET VIX < BULL_MAX → BULL_MARKET
      3. Tout autre cas                  → BEAR_MARKET (prudence max)
    """
    if vix >= config.VIX_PANIC_MIN:
        return "CRASH_PANIC"
    if sp500 > ema200 and vix < config.VIX_BULL_MAX:
        return "BULL_MARKET"
    return "BEAR_MARKET"


# ─────────────────────────────────────────────────────────────────
# FONCTION PRINCIPALE — RÉGIME MACRO
# ─────────────────────────────────────────────────────────────────

def get_market_regime(
    use_cache: bool = True,
    market_provider: MarketDataProviderBase | None = None,
) -> str:
    """
    Détermine et retourne le Régime Macro-Économique Actuel.

    Logique de décision (ordre de priorité strict) :
      1. VIX ≥ 35                        → CRASH_PANIC  (aucun trade)
      2. S&P500 > EMA200 ET VIX < 25    → BULL_MARKET  (longs uniquement)
      3. S&P500 < EMA200 ET VIX < 35    → BEAR_MARKET  (shorts uniquement)
      4. Zone ambiguë (SP>EMA200, 25≤VIX<35) → BEAR_MARKET  (prudence max)

    Args:
        use_cache: Si True (défaut), utilise le cache mémoire de 4h
                   pour éviter des appels API répétés depuis le dashboard.
        market_provider: Fournisseur de données market optionnel. Par
                   défaut, yfinance (seul à gérer nativement ^GSPC/^VIX).
                   Utile en tests pour stub les appels réseau.

    Returns:
        "BULL_MARKET" | "BEAR_MARKET" | "CRASH_PANIC"
    """
    # ── Vérification du cache ──────────────────────────────────────
    if use_cache:
        cached = _REGIME_CACHE.get("regime")
        if cached:
            ts, regime = cached
            if time.time() - ts < _CACHE_TTL_SECONDS:
                logger.debug(f"[MacroEngine] Régime depuis cache : {regime}")
                return regime

    try:
        sp_series, vix_series = _download_macro_data(years=2, market_provider=market_provider)

        if sp_series is None or vix_series is None:
            logger.warning(
                "[MacroEngine] Données macro indisponibles — "
                "BEAR_MARKET activé par défaut (fail-safe)"
            )
            return "BEAR_MARKET"

        sp500, ema200, vix = _align_and_compute(sp_series, vix_series)

        logger.info(
            f"[MacroEngine] S&P500={sp500:,.2f} | EMA200={ema200:,.2f} | "
            f"VIX={vix:.2f} | Δ EMA={(sp500 / ema200 - 1) * 100:+.1f}%"
        )

        raw_regime = _regime_decision(sp500, ema200, vix)

        logger.info(
            f"[MacroEngine] 📊 Régime brut : {raw_regime} "
            f"| VIX_BULL_MAX={config.VIX_BULL_MAX} | VIX_PANIC_MIN={config.VIX_PANIC_MIN}"
        )

        # ── Confirmation N jours consécutifs (anti-whipsaw) ───────
        regime = _apply_regime_confirmation(raw_regime)
        logger.info(f"[MacroEngine] ✦ Régime primaire confirmé : {regime} ✦")

        # ── Enrichissement macro_state avec VIX + SP500 ───────────
        try:
            _mstate = _load_macro_state()
            _mstate["vix"]    = round(vix, 2)
            _mstate["sp500"]  = round(sp500, 2)
            _mstate["ema200"] = round(ema200, 2)
            _save_macro_state(_mstate)
        except Exception:
            pass

        # ── Override composite (crédit + breadth + GMM) — BULL uniquement ──
        # On ne downgrade qu'un BULL en BEAR, jamais l'inverse.
        # CRASH_PANIC et BEAR ne sont pas concernés (déjà restrictifs).
        if regime == "BULL_MARKET":
            try:
                secondary = _download_secondary_indicators(market_provider=market_provider)
                override  = _compute_composite_override(
                    secondary,
                    sp500_close=sp_series,
                    vix_close=vix_series,
                )

                # ── Sauvegarde des scores secondaires pour le dashboard ──
                try:
                    _mstate = _load_macro_state()
                    hyg = secondary.get("HYG")
                    lqd = secondary.get("LQD")
                    rsp = secondary.get("RSP")
                    if hyg is not None and len(hyg) >= 50:
                        hyg_ma50 = hyg.rolling(50).mean()
                        _mstate["hyg_score"] = round(
                            min(100.0, max(0.0, float(hyg.iloc[-1] / hyg_ma50.iloc[-1]) * 50)), 1
                        )
                    if lqd is not None and len(lqd) >= 50:
                        lqd_ma50 = lqd.rolling(50).mean()
                        _mstate["lqd_score"] = round(
                            min(100.0, max(0.0, float(lqd.iloc[-1] / lqd_ma50.iloc[-1]) * 50)), 1
                        )
                    if rsp is not None and len(rsp) >= 50:
                        rsp_ma50 = rsp.rolling(50).mean()
                        _mstate["rsp_score"] = round(
                            min(100.0, max(0.0, float(rsp.iloc[-1] / rsp_ma50.iloc[-1]) * 50)), 1
                        )
                    _save_macro_state(_mstate)
                except Exception:
                    pass

                if override is not None:
                    regime = override
                    logger.info(f"[MacroEngine] ✦ Régime final (composite) : {regime} ✦")
                else:
                    logger.info("[MacroEngine] ✦ Override composite : aucun downgrade — BULL confirmé ✦")
            except Exception as e:
                logger.warning(f"[MacroEngine] Override composite échoué ({e}) — régime primaire maintenu")

        # ── Mise en cache ──────────────────────────────────────────
        if use_cache:
            _REGIME_CACHE["regime"] = (time.time(), regime)

        return regime

    except Exception as e:
        logger.error(f"[MacroEngine] Erreur critique : {e}", exc_info=True)
        # Fail-safe : BEAR_MARKET = pas de longs incontrôlés en cas d'erreur
        return "BEAR_MARKET"


# ─────────────────────────────────────────────────────────────────
# DÉTAILS COMPLETS DU RÉGIME (pour le Dashboard)
# ─────────────────────────────────────────────────────────────────

def get_regime_details(
    market_provider: MarketDataProviderBase | None = None,
) -> dict:
    """
    Retourne un dictionnaire complet des métriques macro pour
    l'affichage dans le Dashboard Streamlit.

    Returns:
        Dict contenant :
          - regime             : "BULL_MARKET" | "BEAR_MARKET" | "CRASH_PANIC"
          - sp500              : Dernière clôture S&P500
          - ema200             : Valeur EMA 200j du S&P500
          - sp500_vs_ema200_pct: Écart relatif S&P500/EMA200 en %
          - vix                : Dernière clôture VIX
          - description        : Texte descriptif du régime
          - allowed_directions : Liste ["LONG"] | ["SHORT"] | []
          - emoji              : Emoji représentatif du régime
    """
    _REGIME_DESCRIPTIONS = {
        "BULL_MARKET": (
            "S&P500 au-dessus de l'EMA200, volatilité maîtrisée (VIX<25). "
            "Tendance haussière confirmée — Longs autorisés uniquement."
        ),
        "BEAR_MARKET": (
            "S&P500 sous l'EMA200 ou VIX élevé. "
            "Pression vendeuse structurelle — Shorts autorisés uniquement."
        ),
        "CRASH_PANIC": (
            "VIX ≥ 35 — Panique extrême sur les marchés. "
            "TOUS les trades sont bloqués — Protection du capital activée."
        ),
    }
    _REGIME_EMOJIS = {
        "BULL_MARKET": "🟢",
        "BEAR_MARKET": "🔴",
        "CRASH_PANIC": "🚨",
    }
    _ALLOWED_DIRECTIONS = {
        "BULL_MARKET": ["LONG"],
        "BEAR_MARKET": ["SHORT"],
        "CRASH_PANIC": [],
    }

    try:
        sp_series, vix_series = _download_macro_data(years=2, market_provider=market_provider)

        if sp_series is None or vix_series is None:
            return _fallback_regime_details()

        sp500, ema200, vix = _align_and_compute(sp_series, vix_series)

        # ── Régime calculé depuis les données déjà téléchargées ───────
        # Évite le second appel réseau que get_market_regime(use_cache=False)
        # déclencherait (SP500 + VIX + HYG/LQD/RSP re-téléchargés inutilement).
        raw_regime = _regime_decision(sp500, ema200, vix)
        regime     = _apply_regime_confirmation(raw_regime)

        # ── Indicateurs secondaires — un seul téléchargement ──────────
        secondary      = _download_secondary_indicators(market_provider=market_provider)
        secondary_info: dict = {}
        hyg = secondary.get("HYG")
        lqd = secondary.get("LQD")
        rsp = secondary.get("RSP")

        if hyg is not None and lqd is not None and len(hyg) >= 50 and len(lqd) >= 50:
            common = hyg.index.intersection(lqd.index)
            if len(common) >= 50:
                ratio      = hyg.reindex(common) / lqd.reindex(common)
                ratio_ma50 = ratio.rolling(50).mean()
                secondary_info["credit_spread_ratio"] = round(float(ratio.iloc[-1]), 4)
                secondary_info["credit_spread_ma50"]  = round(float(ratio_ma50.iloc[-1]), 4)
                secondary_info["credit_spread_ok"]    = bool(ratio.iloc[-1] >= ratio_ma50.iloc[-1])

        if rsp is not None and len(rsp) >= 50:
            rsp_ma50 = rsp.rolling(50).mean()
            secondary_info["rsp_price"]  = round(float(rsp.iloc[-1]), 2)
            secondary_info["rsp_ma50"]   = round(float(rsp_ma50.iloc[-1]), 2)
            secondary_info["breadth_ok"] = bool(rsp.iloc[-1] >= rsp_ma50.iloc[-1])

        # ── Override composite BULL → BEAR (même logique que get_market_regime) ──
        if regime == "BULL_MARKET":
            override = _compute_composite_override(
                secondary,
                sp500_close=sp_series,
                vix_close=vix_series,
            )
            if override is not None:
                regime = override

        # Mise à jour du cache pour cohérence avec get_market_regime()
        _REGIME_CACHE["regime"] = (time.time(), regime)

        return {
            "regime":              regime,
            "sp500":               round(sp500, 2),
            "ema200":              round(ema200, 2),
            "sp500_vs_ema200_pct": round((sp500 / ema200 - 1) * 100, 2),
            "vix":                 round(vix, 2),
            "description":         _REGIME_DESCRIPTIONS.get(regime, "Régime inconnu."),
            "allowed_directions":  _ALLOWED_DIRECTIONS.get(regime, []),
            "emoji":               _REGIME_EMOJIS.get(regime, "⚪"),
            "composite":           secondary_info,
        }

    except Exception as e:
        logger.error(f"[MacroEngine] get_regime_details() erreur : {e}")
        return _fallback_regime_details()


# ─────────────────────────────────────────────────────────────────
# CALENDRIER MACRO — Zones de blackout (FOMC, CPI, NFP)
# ─────────────────────────────────────────────────────────────────

_MACRO_CALENDAR_PATH = Path(__file__).resolve().parent.parent / "data" / "macro_calendar.json"
_macro_calendar_cache: list = []


def _load_macro_calendar() -> list:
    """Charge le calendrier macro depuis data/macro_calendar.json. Retourne [] si absent."""
    global _macro_calendar_cache
    if _macro_calendar_cache:
        return _macro_calendar_cache
    if not _MACRO_CALENDAR_PATH.exists():
        return []
    try:
        with open(_MACRO_CALENDAR_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        _macro_calendar_cache = data.get("events", [])
        logger.info(f"[MacroEngine] Calendrier macro chargé : {len(_macro_calendar_cache)} événements")
        return _macro_calendar_cache
    except Exception as e:
        logger.warning(f"[MacroEngine] Impossible de charger le calendrier macro : {e}")
        return []


def is_blackout_zone(
    check_date: datetime | date | None = None,
    days_before: int = 1,
    days_after: int = 0,
) -> tuple[bool, str]:
    """
    Vérifie si la date donnée est dans une zone de blackout macro.

    Une zone de blackout = J-days_before à J+days_after d'un événement FOMC/CPI/NFP.
    Les nouvelles entrées sont bloquées pendant ces périodes pour éviter
    les gaps violents liés aux surprises macro.

    Args:
        check_date: Date à vérifier (datetime.date ou None = aujourd'hui)
        days_before: Jours avant l'événement à bloquer (défaut 1)
        days_after: Jours après l'événement à bloquer (défaut 0)

    Returns:
        Tuple (is_blackout: bool, event_label: str)
        - is_blackout=True → bloquer les nouvelles entrées
        - event_label → nom de l'événement le plus proche
    """
    from datetime import timedelta as _td
    # Normalise vers un `date` pur (isinstance plutôt que hasattr pour que mypy
    # narrow correctement et écarte le None).
    if check_date is None:
        day = datetime.now().date()
    elif isinstance(check_date, datetime):
        day = check_date.date()
    else:
        day = check_date

    events = _load_macro_calendar()
    for evt in events:
        try:
            evt_date = datetime.strptime(evt["date"], "%Y-%m-%d").date()
            window_start = evt_date - _td(days=days_before)
            window_end   = evt_date + _td(days=days_after)
            if window_start <= day <= window_end:
                label = evt.get("label", evt.get("type", "Macro Event"))
                logger.info(f"[MacroEngine] Zone de blackout : {label} ({evt['date']})")
                return True, label
        except Exception:
            continue
    return False, ""


def _fallback_regime_details() -> dict:
    """Retourne des détails par défaut en cas d'erreur de téléchargement."""
    return {
        "regime":              "BEAR_MARKET",
        "sp500":               None,
        "ema200":              None,
        "sp500_vs_ema200_pct": None,
        "vix":                 None,
        "description":         "Données macro indisponibles — mode prudent activé par défaut.",
        "allowed_directions":  ["SHORT"],
        "emoji":               "⚠️",
    }
