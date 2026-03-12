"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE — AI STRATEGY ADVISOR V2                                ║
║  Analyse IA des résultats Walk-Forward via API Anthropic.       ║
║                                                                  ║
║  Input  : JSON des métriques OptimizationResult                 ║
║  Output : Verdict structuré JSON (strategy_type, confidence,    ║
║           reasoning, risk_warnings, etc.)                        ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

import config
from modules.strategy_optimizer import OptimizationResult
from modules.log import logger


# ─────────────────────────────────────────────────────────────────
# PROMPT SYSTÈME
# ─────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Tu es un analyste quantitatif institutionnel spécialisé dans le swing trading \
intraday sur actions et cryptos US (timeframe 1H). Tu analyses les résultats d'une optimisation \
Walk-Forward rigoureuse et fournis un verdict d'expert sur la tradabilité de la stratégie.

CONTEXTE SYSTÈME (TITAN V7 — ADX Asset Profiling) :
Le système utilise un routeur ADX pour sélectionner les stratégies AVANT l'optimisation :
- ADX > 25 (MOMENTUM) → seuls trend_breakout et trend_following sont testés.
  La Mean Reversion est désactivée car elle "pêche contre le courant" en tendance forte.
- ADX ≤ 25 (MEAN_REVERSION) → seuls mean_reversion (RSI) et breakout (Bollinger) sont testés.
  Les stratégies directionnelles génèrent des whipsaws en marché sans direction.
Tu dois prendre en compte ce profil dans ton raisonnement et expliquer pourquoi la stratégie
sélectionnée est COHÉRENTE (ou non) avec le comportement historique du ticker.

RÈGLES ABSOLUES :
1. Réponds UNIQUEMENT en JSON valide, sans texte avant ou après.
2. Le JSON doit avoir EXACTEMENT cette structure (tous les champs requis) :
{
  "ticker": "...",
  "strategy_type": "MEAN_REVERSION|MOMENTUM|BREAKOUT|MIXED|UNTRADABLE",
  "confidence_score": <entier 0-100>,
  "tradable": <true|false>,
  "optimal_params": {
    "rsi_oversold": <int>,
    "volume_spike": <float>,
    "stop_method": "atr_1.5x|atr_2x|support|percent_5",
    "tp_ratio": <float>,
    "trailing_stop": <bool>
  },
  "reasoning": "Analyse en 3-5 phrases. Citer le profil ADX, les métriques clés et leur cohérence. Être honnête.",
  "market_regime_advice": "Dans quel régime de marché cette stratégie performe-t-elle ?",
  "seasonal_notes": "Observations saisonnières si pertinentes, sinon 'Aucune observation particulière'.",
  "risk_warnings": ["Warning 1", "Warning 2"],
  "position_sizing_advice": "Recommandation concrète sur la taille des positions."
}

3. CRITÈRES DE TRADABILITÉ :
   - confidence_score >= 60 ET profit_factor > 1.5 ET sharpe > 0.5 → tradable: true
   - Sinon → tradable: false
   - Si reliability_status = "DONNÉES INSUFFISANTES" → strategy_type = "UNTRADABLE", tradable: false

4. HONNÊTETÉ ABSOLUE : Si les métriques montrent une stratégie perdante ou non fiable,
   dis-le clairement dans reasoning. Ne jamais embellir les résultats.

5. Le confidence_score dans ta réponse doit CORRESPONDRE au score calculé dans les métriques
   (± 5 pts d'ajustement qualitatif autorisé).

6. COHÉRENCE ADX : Si le profil est MOMENTUM mais que la stratégie gagnante est mean_reversion,
   signale cette incohérence dans risk_warnings — elle indique un possible overfitting.
"""


# ─────────────────────────────────────────────────────────────────
# CONSTRUCTION DU PROMPT UTILISATEUR
# ─────────────────────────────────────────────────────────────────

def _build_user_prompt(opt_result: OptimizationResult) -> str:
    """
    Construit le prompt utilisateur à partir des métriques walk-forward.

    Args:
        opt_result: Résultat complet de l'optimisation.

    Returns:
        Prompt JSON-enrichi décrivant la performance de la stratégie.
    """
    d    = opt_result.to_dict()
    avs  = d.get("avg_val_stats", {})
    best = d.get("best_params", {})

    # Profil comportemental ADX (V7 Asset Profiling)
    ticker_profile = getattr(opt_result, "ticker_profile", "UNKNOWN")
    avg_adx        = getattr(opt_result, "avg_adx", 0.0)
    if ticker_profile == "MOMENTUM":
        profile_explanation = (
            f"ADX moyen = {avg_adx:.1f} > 25 → ticker en TENDANCE FORTE. "
            "Seuls les moteurs directionnels (trend_breakout, trend_following) "
            "ont été testés. Les stratégies Mean Reversion ont été désactivées "
            "pour éviter de 'pêcher contre le courant'."
        )
    elif ticker_profile == "MEAN_REVERSION":
        profile_explanation = (
            f"ADX moyen = {avg_adx:.1f} ≤ 25 → ticker en RANGE / CONSOLIDATION. "
            "Seuls les oscillateurs (mean_reversion RSI, breakout Bollinger) "
            "ont été testés. Les stratégies de tendance génèrent des whipsaws "
            "en marché sans direction."
        )
    else:
        profile_explanation = (
            "ADX indisponible → sélection des stratégies par régime macro + EMA50 (mode fallback V6)."
        )

    lines = [
        f"Analyse la stratégie de swing trading pour le ticker : {opt_result.ticker}",
        "",
        "=== PROFIL COMPORTEMENTAL DU TICKER (ADX Asset Profiling — TITAN V7) ===",
        f"Profil détecté             : {ticker_profile}",
        f"ADX moyen (50 bougies 1H)  : {avg_adx:.1f}" if avg_adx > 0 else "ADX moyen (50 bougies 1H)  : N/A",
        f"Logique de sélection       : {profile_explanation}",
        "",
        "=== RÉSULTATS WALK-FORWARD ===",
        f"Score de confiance calculé : {d['confidence_score']}%",
        f"Statut de fiabilité        : {d['reliability_status']}",
        f"Overfitting détecté        : {d['overfitting_detected']}",
        f"Fenêtres walk-forward      : {d['n_windows']}",
        f"Combinaisons testées       : {d['total_combos_tested']}",
        "",
        "=== MÉTRIQUES DE VALIDATION (moyennes pondérées) ===",
        f"Trades en validation       : {avs.get('total_trades', 0)}",
        f"Win rate                   : {avs.get('win_rate', 0):.1%}",
        f"Profit Factor              : {avs.get('profit_factor', 0):.2f}",
        f"Rendement total val        : {avs.get('total_return_pct', 0):+.1f}%",
        f"CAGR                       : {avs.get('cagr_pct', 0):+.1f}%",
        f"Sharpe ratio               : {avs.get('sharpe_ratio', 0):.2f}",
        f"Sortino ratio              : {avs.get('sortino_ratio', 0):.2f}",
        f"Max Drawdown               : {avs.get('max_drawdown_pct', 0):.1f}%",
        f"Calmar ratio               : {avs.get('calmar_ratio', 0):.2f}",
        f"Expectancy                 : {avs.get('expectancy_pct', 0):+.2f}%/trade",
        f"Avg Win                    : {avs.get('avg_win_pct', 0):+.2f}%",
        f"Avg Loss                   : {avs.get('avg_loss_pct', 0):+.2f}%",
        f"Benchmark B&H              : {avs.get('benchmark_return_pct', 0):+.1f}%",
        "",
        "=== MEILLEURS PARAMÈTRES (sélectionnés par walk-forward) ===",
        f"RSI oversold seuil         : {best.get('rsi_oversold', 'N/A')}",
        f"Volume spike ratio         : {best.get('volume_spike', 'N/A')}x",
        f"Stop method                : {best.get('stop_method', 'N/A')}",
        f"TP ratio                   : {best.get('tp_ratio', 'N/A')}",
        f"Trailing stop              : {best.get('trailing_stop', 'N/A')}",
        "",
        "=== RÉSULTATS PAR FENÊTRE ===",
    ]

    for w in d.get("windows", []):
        lines.append(
            f"  Fenêtre {w['window']} : Train {w['train_score']:.0f}pts "
            f"→ Valid {w['val_score']:.0f}pts | V/T ratio {w['val_train_ratio']:.2f} "
            f"| {w['val_trades']} trades | WR {w['val_wr']:.1%} | "
            f"Ret {w['val_return']:+.1f}% | DD {w['val_dd']:.1f}%"
        )

    lines += [
        "",
        f"Capital de référence : {config.CAPITAL:,} €",
        "Frais réels simulés  : 0.1% commission + 0.05% slippage par exécution",
        "",
        "Fournis ton analyse JSON selon le format requis.",
    ]

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# APPEL API ANTHROPIC
# ─────────────────────────────────────────────────────────────────

def _call_anthropic(prompt: str) -> dict:
    """
    Appelle l'API Anthropic Claude et retourne le JSON parsé.

    Args:
        prompt: Prompt utilisateur complet.

    Returns:
        Dict JSON du verdict IA.

    Raises:
        RuntimeError: Si l'API échoue ou si le JSON est invalide.
    """
    headers = {
        "x-api-key":         config.LLM_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }
    payload = {
        "model":      config.LLM_MODEL,
        "max_tokens": 1024,
        "system":     _SYSTEM_PROMPT,
        "messages":   [{"role": "user", "content": prompt}],
    }

    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()

    data = response.json()

    # Extraire le texte de la réponse
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block["text"]

    # Nettoyer les backticks markdown éventuels
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    return json.loads(text)


# ─────────────────────────────────────────────────────────────────
# VALIDATION & NORMALISATION DU VERDICT
# ─────────────────────────────────────────────────────────────────

_VALID_STRATEGY_TYPES = {"MEAN_REVERSION", "MOMENTUM", "BREAKOUT", "MIXED", "UNTRADABLE"}
_VALID_STOP_METHODS   = {"atr_1.5x", "atr_2x", "support", "percent_5"}


def _normalize_verdict(raw: dict, opt_result: OptimizationResult) -> dict:
    """
    Valide et normalise le verdict IA.

    Ajoute des valeurs par défaut pour les champs manquants et corrige
    les incohérences évidentes.

    Args:
        raw:        Dict brut retourné par l'API.
        opt_result: Résultat d'optimisation original (pour les valeurs de fallback).

    Returns:
        Dict normalisé conforme au format attendu.
    """
    best = opt_result.best_config()
    best_p = best.params.to_dict() if best else {}

    strategy_type = raw.get("strategy_type", "MEAN_REVERSION")
    if strategy_type not in _VALID_STRATEGY_TYPES:
        strategy_type = "MEAN_REVERSION"

    confidence = int(raw.get("confidence_score", opt_result.confidence_score))
    confidence  = max(0, min(100, confidence))

    optimal_params = raw.get("optimal_params", best_p)
    if not isinstance(optimal_params, dict):
        optimal_params = best_p
    # Valider stop_method
    if optimal_params.get("stop_method") not in _VALID_STOP_METHODS:
        optimal_params["stop_method"] = best_p.get("stop_method", "atr_1.5x")

    risk_warnings = raw.get("risk_warnings", [])
    if not isinstance(risk_warnings, list):
        risk_warnings = [str(risk_warnings)]

    return {
        "ticker":                 opt_result.ticker,
        "strategy_type":          strategy_type,
        "confidence_score":       confidence,
        "tradable":               bool(raw.get("tradable", confidence >= 60)),
        "optimal_params":         optimal_params,
        "reasoning":              str(raw.get("reasoning", "Analyse indisponible.")),
        "market_regime_advice":   str(raw.get("market_regime_advice", "Non disponible.")),
        "seasonal_notes":         str(raw.get("seasonal_notes", "Aucune observation particulière.")),
        "risk_warnings":          risk_warnings,
        "position_sizing_advice": str(raw.get("position_sizing_advice", "Taille standard (2% du capital).")),
        # Champs additionnels pour ticker_profiles
        "confidence_score_raw":   opt_result.confidence_score,
        "reliability_status":     opt_result.reliability_status,
        "best_params":            best_p,
        "overfitting_detected":   opt_result.overfitting_detected,
        # Profil ADX (Asset Profiling V7)
        "ticker_profile":         getattr(opt_result, "ticker_profile", "UNKNOWN"),
        "avg_adx":                getattr(opt_result, "avg_adx", 0.0),
    }


# ─────────────────────────────────────────────────────────────────
# FALLBACK (sans appel API)
# ─────────────────────────────────────────────────────────────────

def _build_fallback_advice(opt_result: OptimizationResult) -> dict:
    """
    Construit un profil de base sans appel IA (mode --no-ai).

    Détermine strategy_type et tradable à partir des métriques calculées.

    Args:
        opt_result: Résultat d'optimisation.

    Returns:
        Dict profil au format normalisé.
    """
    best           = opt_result.best_config()
    best_p         = best.params.to_dict() if best else {}
    avs            = opt_result.avg_val_stats
    conf           = opt_result.confidence_score
    ticker_profile = getattr(opt_result, "ticker_profile", "UNKNOWN")

    # Déduire le type de stratégie en tenant compte du profil ADX
    if opt_result.reliability_status == "DONNÉES INSUFFISANTES":
        strategy_type = "UNTRADABLE"
        tradable      = False
    elif ticker_profile == "MOMENTUM":
        strategy_type = "MOMENTUM" if avs and avs.profit_factor > 1.5 else "UNTRADABLE"
        tradable      = conf >= 60
    elif avs and avs.win_rate > 0.55 and avs.profit_factor > 1.5:
        strategy_type = "MEAN_REVERSION"
        tradable      = conf >= 60
    else:
        strategy_type = "UNTRADABLE" if conf < 40 else "MEAN_REVERSION"
        tradable      = conf >= 60

    warnings = []
    if opt_result.overfitting_detected:
        warnings.append("Overfitting probable détecté (ratio V/T < 0.3 ou variance élevée)")
    if avs and avs.max_drawdown_pct > 20:
        warnings.append(f"Drawdown max élevé : {avs.max_drawdown_pct:.1f}%")
    if avs and avs.total_trades < config.MIN_TRADES_FOR_CONFIDENCE:
        warnings.append("Nombre de trades insuffisant pour une analyse statistiquement fiable")

    avg_adx = getattr(opt_result, "avg_adx", 0.0)
    return {
        "ticker":                 opt_result.ticker,
        "strategy_type":          strategy_type,
        "confidence_score":       conf,
        "tradable":               tradable,
        "optimal_params":         best_p,
        "reasoning":              (
            f"Profil généré sans IA. Profil ADX : {ticker_profile} "
            f"(ADX moyen = {avg_adx:.1f}). "
            f"Score {conf}% — {opt_result.reliability_status}."
        ),
        "market_regime_advice":   "Analyse IA désactivée.",
        "seasonal_notes":         "Aucune observation particulière.",
        "risk_warnings":          warnings or ["Aucun warning spécifique."],
        "position_sizing_advice": "Taille standard : 2% du capital risqué par trade.",
        "confidence_score_raw":   conf,
        "reliability_status":     opt_result.reliability_status,
        "best_params":            best_p,
        "overfitting_detected":   opt_result.overfitting_detected,
        "ticker_profile":         ticker_profile,
        "avg_adx":                avg_adx,
    }


# ─────────────────────────────────────────────────────────────────
# POINT D'ENTRÉE PRINCIPAL
# ─────────────────────────────────────────────────────────────────

def advise_ticker(
    opt_result: OptimizationResult,
    use_ai: bool = True,
) -> dict:
    """
    Génère un profil de stratégie structuré pour un ticker.

    En mode IA (use_ai=True) : appelle l'API Anthropic Claude avec les métriques
    walk-forward et retourne le verdict structuré.
    En mode --no-ai : génère un profil de base à partir des métriques.

    Args:
        opt_result: Résultat de l'optimisation walk-forward.
        use_ai:     Si True, appelle l'API Anthropic.

    Returns:
        Dict conforme au format de sortie spécifié (ticker, strategy_type,
        confidence_score, tradable, optimal_params, reasoning, etc.).
    """
    if not use_ai:
        return _build_fallback_advice(opt_result)

    if not config.LLM_API_KEY:
        logger.warning(f"[{opt_result.ticker}] LLM_API_KEY absent — fallback sans IA")
        return _build_fallback_advice(opt_result)

    try:
        prompt  = _build_user_prompt(opt_result)
        raw     = _call_anthropic(prompt)
        verdict = _normalize_verdict(raw, opt_result)

        logger.info(
            f"[{opt_result.ticker}] IA → {verdict['strategy_type']} "
            f"| Confiance {verdict['confidence_score']}% "
            f"| Tradable: {verdict['tradable']}"
        )
        return verdict

    except httpx.HTTPStatusError as e:
        logger.error(f"[{opt_result.ticker}] API Anthropic HTTP {e.response.status_code} : {e}")
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"[{opt_result.ticker}] JSON invalide de l'IA : {e}")
    except Exception as e:
        logger.error(f"[{opt_result.ticker}] Erreur advisor IA : {e}", exc_info=True)

    # Fallback si l'API échoue
    return _build_fallback_advice(opt_result)
