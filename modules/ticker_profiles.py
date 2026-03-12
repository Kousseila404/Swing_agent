"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE — TICKER PROFILES V2                                    ║
║  Gestion de la persistance et du classement des profils.        ║
║                                                                  ║
║  Structure data/profiles.json :                                  ║
║    { "TICKER": { "version": 2, "tier": "A", ... }, ... }        ║
║                                                                  ║
║  Tiers :                                                         ║
║    A → Confiance ≥ 70, tradable                                  ║
║    B → Confiance ≥ 50, tradable                                  ║
║    C → Confiance ≥ 30, tradable (avec prudence)                  ║
║    UNTRADABLE → Confiance < 30 ou non tradable                  ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

from modules.backtester import BacktestParams
from modules.log import logger


# ─────────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────────

PROFILES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "profiles.json",
)
PROFILE_VERSION = 2


# ─────────────────────────────────────────────────────────────────
# CLASSIFICATION EN TIERS
# ─────────────────────────────────────────────────────────────────

def classify_tier(confidence_score: int, tradable: bool) -> str:
    """
    Classe un ticker dans un Tier selon son score de confiance.

    Tiers :
        A → score ≥ 70, tradable
        B → score ≥ 50, tradable
        C → score ≥ 30, tradable
        UNTRADABLE → score < 30 ou non tradable

    Args:
        confidence_score: Score de confiance (0–100, capé à 100).
        tradable:         True si la stratégie est tradable.

    Returns:
        Tier parmi {A, B, C, UNTRADABLE}.
    """
    score = min(int(confidence_score), 100)
    if not tradable or score < 30:
        return "UNTRADABLE"
    if score >= 70:
        return "A"
    if score >= 50:
        return "B"
    return "C"


# ─────────────────────────────────────────────────────────────────
# LECTURE / ÉCRITURE
# ─────────────────────────────────────────────────────────────────

def load_all_profiles() -> dict:
    """
    Charge tous les profils depuis data/profiles.json.

    Returns:
        Dict {ticker: profile_dict}. Vide si le fichier n'existe pas.
    """
    if not os.path.exists(PROFILES_PATH):
        return {}
    try:
        with open(PROFILES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Erreur lecture profiles.json : {e}")
        return {}


def save_profile(ticker: str, profile: dict) -> bool:
    """
    Sauvegarde ou met à jour le profil d'un ticker dans data/profiles.json.

    Ajoute automatiquement :
        - version, last_optimized, tier

    Args:
        ticker:  Symbole boursier.
        profile: Dict profil (issu de ai_strategy_advisor.advise_ticker).

    Returns:
        True si la sauvegarde a réussi, False sinon.
    """
    try:
        os.makedirs(os.path.dirname(PROFILES_PATH), exist_ok=True)
        all_profiles = load_all_profiles()

        # Ajouter les métadonnées de versionning
        confidence  = profile.get("confidence_score", 0)
        tradable    = profile.get("tradable", False)
        tier        = classify_tier(confidence, tradable)

        profile["version"]        = PROFILE_VERSION
        profile["last_optimized"] = datetime.now().isoformat()
        profile["tier"]           = tier

        # Conserver l'historique des optimisations (3 dernières)
        existing = all_profiles.get(ticker, {})
        history  = existing.get("optimization_history", [])
        if existing.get("last_optimized"):
            history.append({
                "date":             existing.get("last_optimized"),
                "confidence_score": existing.get("confidence_score", 0),
                "tier":             existing.get("tier", "UNTRADABLE"),
            })
        profile["optimization_history"] = history[-3:]  # Garder 3 entrées max

        all_profiles[ticker] = profile

        with open(PROFILES_PATH, "w", encoding="utf-8") as f:
            json.dump(all_profiles, f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"[{ticker}] Profil V{PROFILE_VERSION} sauvegardé — Tier {tier} | Confiance {confidence}%")
        return True

    except Exception as e:
        logger.error(f"[{ticker}] Erreur sauvegarde profil : {e}")
        return False


def load_profile(ticker: str) -> Optional[dict]:
    """
    Charge le profil d'un ticker spécifique.

    Args:
        ticker: Symbole boursier.

    Returns:
        Dict profil ou None si aucun profil n'existe.
    """
    profile = load_all_profiles().get(ticker)
    if profile is None:
        logger.debug(f"[{ticker}] Aucun profil trouvé")
    return profile


def has_profile(ticker: str) -> bool:
    """Retourne True si un profil existe pour ce ticker."""
    return ticker in load_all_profiles()


def delete_profile(ticker: str) -> bool:
    """
    Supprime le profil d'un ticker.

    Args:
        ticker: Symbole boursier.

    Returns:
        True si suppression réussie ou si le profil n'existait pas.
    """
    try:
        all_profiles = load_all_profiles()
        if ticker in all_profiles:
            del all_profiles[ticker]
            with open(PROFILES_PATH, "w", encoding="utf-8") as f:
                json.dump(all_profiles, f, indent=2, ensure_ascii=False)
            logger.info(f"[{ticker}] Profil supprimé")
        return True
    except Exception as e:
        logger.error(f"[{ticker}] Erreur suppression profil : {e}")
        return False


# ─────────────────────────────────────────────────────────────────
# INTÉGRATION SCANNER LIVE
# ─────────────────────────────────────────────────────────────────

def get_backtest_params(ticker: str) -> BacktestParams:
    """
    Retourne les BacktestParams optimisés pour un ticker.

    Si un profil V2 existe avec des best_params, les utilise.
    Sinon, retourne les paramètres globaux de config.py.

    Args:
        ticker: Symbole boursier.

    Returns:
        BacktestParams optimaux ou paramètres par défaut.
    """
    profile = load_profile(ticker)

    if profile is None:
        return BacktestParams.from_config()

    # Essayer les optimal_params (IA) puis best_params (optimizer brut)
    raw_params = profile.get("optimal_params") or profile.get("best_params")

    if not raw_params:
        logger.debug(f"[{ticker}] Profil sans params — utilisation des params globaux")
        return BacktestParams.from_config()

    try:
        params = BacktestParams.from_dict(raw_params)
        logger.debug(
            f"[{ticker}] Params optimisés : RSI<{params.rsi_oversold} "
            f"| Stop {params.stop_method} | TP {params.tp_ratio}x "
            f"| Trail {params.trailing_stop}"
        )
        return params
    except Exception as e:
        logger.warning(f"[{ticker}] Erreur lecture params ({e}) — params globaux")
        return BacktestParams.from_config()


# ─────────────────────────────────────────────────────────────────
# LISTES & CLASSEMENT
# ─────────────────────────────────────────────────────────────────

def list_profiles_by_tier() -> dict[str, list[dict]]:
    """
    Retourne les profils organisés par Tier, triés par confiance décroissante.

    Returns:
        Dict { "A": [...], "B": [...], "C": [...], "UNTRADABLE": [...] }
    """
    all_p   = load_all_profiles()
    tiers: dict[str, list[dict]] = {"A": [], "B": [], "C": [], "UNTRADABLE": []}

    for ticker, profile in all_p.items():
        tier   = profile.get("tier", "UNTRADABLE")
        if tier not in tiers:
            tier = "UNTRADABLE"
        summary = _profile_summary(ticker, profile)
        tiers[tier].append(summary)

    # Trier chaque tier par confiance décroissante
    for tier in tiers:
        tiers[tier].sort(key=lambda x: x["confidence"], reverse=True)

    return tiers


def list_profiles() -> list[dict]:
    """
    Retourne tous les profils sous forme de liste, triés par confiance décroissante.

    Returns:
        Liste de dicts résumés (ticker, tier, confidence, strategy_type, ...).
    """
    all_p = load_all_profiles()
    summaries = [_profile_summary(t, p) for t, p in all_p.items()]
    return sorted(summaries, key=lambda x: x["confidence"], reverse=True)


def _profile_summary(ticker: str, profile: dict) -> dict:
    """
    Extrait les champs essentiels d'un profil pour l'affichage.

    Args:
        ticker:  Symbole boursier.
        profile: Dict profil complet.

    Returns:
        Dict résumé avec les champs d'affichage.
    """
    bp = profile.get("optimal_params") or profile.get("best_params") or {}
    avs = profile.get("avg_val_stats") or {}
    last_opt = profile.get("last_optimized", "")[:10] if profile.get("last_optimized") else "N/A"

    return {
        "ticker":          ticker,
        "tier":            profile.get("tier", "UNTRADABLE"),
        "strategy_type":   profile.get("strategy_type", "N/A"),
        "confidence":      profile.get("confidence_score", 0),
        "tradable":        profile.get("tradable", False),
        "rsi_oversold":    bp.get("rsi_oversold", "N/A"),
        "stop_method":     bp.get("stop_method", "N/A"),
        "tp_ratio":        bp.get("tp_ratio", "N/A"),
        "trailing_stop":   bp.get("trailing_stop", "N/A"),
        "val_trades":      avs.get("total_trades", 0),
        "val_wr":          avs.get("win_rate", 0.0),
        "val_return":      avs.get("total_return_pct", 0.0),
        "val_pf":          avs.get("profit_factor", 0.0),
        "val_sharpe":      avs.get("sharpe_ratio", 0.0),
        "val_dd":          avs.get("max_drawdown_pct", 0.0),
        "reliability":     profile.get("reliability_status", "N/A"),
        "overfitting":     profile.get("overfitting_detected", False),
        "last_optimized":  last_opt,
        "reasoning":       profile.get("reasoning", ""),
        "risk_warnings":   profile.get("risk_warnings", []),
    }


def profile_age_days(ticker: str) -> Optional[int]:
    """
    Retourne l'âge du profil en jours calendaires.

    Args:
        ticker: Symbole boursier.

    Returns:
        Nombre de jours depuis la dernière optimisation, ou None.
    """
    profile = load_profile(ticker)
    if profile is None:
        return None

    last_str = profile.get("last_optimized")
    if not last_str:
        return None

    try:
        last_dt = datetime.fromisoformat(str(last_str)[:19])
        return (datetime.now() - last_dt).days
    except (ValueError, TypeError):
        return None
