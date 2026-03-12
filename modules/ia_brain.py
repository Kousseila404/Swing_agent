"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE 3 — CERVEAU IA (LLM ANALYSIS)                         ║
║  Envoie le contexte (données techniques + news) à l'API LLM.  ║
║  Reçoit un verdict structuré en JSON.                          ║
╚══════════════════════════════════════════════════════════════════╝
"""
import json
from typing import Optional
import httpx
from dataclasses import dataclass

import config
from modules.scanner import ScanResult
from modules.log import logger


@dataclass
class AIVerdict:
    """Structure du verdict de l'IA."""
    sentiment: str        # HAUSSIER / BAISSIER / NEUTRE
    confidence: int       # 0-100
    reasoning: str        # Explication courte
    action: str           # ACHETER / SURVEILLER / ÉVITER
    risk_level: str       # FAIBLE / MOYEN / ÉLEVÉ
    target_horizon: str   # Ex: "3-5 jours", "1-2 semaines"


# Prompt système qui cadre strictement la réponse de l'IA
SYSTEM_PROMPT = """Tu es un Analyste Quantitatif Senior, froid, rationnel et pragmatique, \
spécialisé dans le swing trading sur les actions françaises (Euronext Paris). \
Ton rôle est de valider ou rejeter un signal technique avec précision.

RÈGLES STRICTES :
1. Réponds UNIQUEMENT en JSON valide, sans aucun texte avant ou après.
2. Le JSON doit avoir exactement cette structure :
{
    "sentiment": "HAUSSIER" | "BAISSIER" | "NEUTRE",
    "confidence": 0-100,
    "reasoning": "Explication en 2-3 phrases maximum",
    "action": "ACHETER" | "SURVEILLER" | "ÉVITER",
    "risk_level": "FAIBLE" | "MOYEN" | "ÉLEVÉ",
    "target_horizon": "durée estimée de détention"
}
3. Tu dois arrêter de te réfugier systématiquement dans le verdict NEUTRE. \
Si les données fondamentales ou les news sont absentes, appuie-toi sur la solidité \
du signal technique (ADX fort, RSI pertinent, volume) et le régime macro-économique.
4. Si les mathématiques sont excellentes (signal fort, volume élevé, RSI pertinent), \
n'hésite pas à donner un avis HAUSSIER avec action ACHETER.
5. Si le setup est bancal (signal faible, volume anémique, contexte négatif), \
donne un avis BAISSIER avec action ÉVITER.
6. Le verdict NEUTRE avec action SURVEILLER est réservé aux situations véritablement \
ambiguës où les signaux se contredisent — pas à l'absence de news.
7. Un RSI en survente + volume élevé SANS news négative = signal d'achat fort → HAUSSIER.
8. Un RSI en momentum + volume élevé + news positive = confirmation haussière solide → HAUSSIER.
9. Toute news liée à un profit warning, perte de contrat, scandale = BAISSIER / ÉVITER.
"""


def analyze_signal(scan: ScanResult, news_text: str) -> Optional[AIVerdict]:
    """
    Envoie les données d'un signal au LLM et retourne un AIVerdict structuré.
    Retourne None si l'appel échoue.
    """
    user_prompt = _build_user_prompt(scan, news_text)

    try:
        verdict_json = _call_anthropic_api(user_prompt)
        verdict = _parse_verdict(verdict_json)
        logger.info(
            f"[{scan.ticker}] IA → {verdict.sentiment} "
            f"(confiance: {verdict.confidence}%) → {verdict.action}"
        )
        return verdict

    except Exception as e:
        logger.error(f"[{scan.ticker}] Erreur cerveau IA : {e}")
        return None


def _build_user_prompt(scan: ScanResult, news_text: str) -> str:
    """Construit le prompt utilisateur avec toutes les données contextuelles."""
    return f"""Analyse cette action détectée par mon scanner :

DONNÉES TECHNIQUES :
- Ticker : {scan.ticker} ({scan.name})
- Prix actuel : {scan.price} €
- Variation jour : {scan.change_pct:+.2f}%
- Volume : {scan.volume:,} (ratio vs moy. 20j : {scan.volume_ratio}x)
- RSI(14) : {scan.rsi}
- Signal détecté : {scan.signal}

ACTUALITÉS :
{news_text}

CONTEXTE : Capital de {config.CAPITAL:,} €, swing trading (quelques jours à semaines).
Donne ton verdict en JSON."""


def _call_anthropic_api(user_prompt: str) -> dict:
    """
    Appelle l'API Anthropic Messages et retourne le JSON parsé.
    Utilise httpx pour un contrôle fin (timeout, headers).
    """
    headers = {
        "x-api-key": config.LLM_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    payload = {
        "model": config.LLM_MODEL,
        "max_tokens": 500,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": user_prompt}
        ],
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()

    data = response.json()

    # Extraire le texte de la réponse
    text_content = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text_content += block["text"]

    # Nettoyer les éventuels backticks markdown
    text_content = text_content.strip()
    if text_content.startswith("```"):
        text_content = text_content.split("\n", 1)[-1]  # enlever ```json
    if text_content.endswith("```"):
        text_content = text_content.rsplit("```", 1)[0]
    text_content = text_content.strip()

    return json.loads(text_content)


def _parse_verdict(data: dict) -> AIVerdict:
    """Parse le dict JSON en un objet AIVerdict avec des valeurs par défaut sûres."""
    return AIVerdict(
        sentiment=data.get("sentiment", "NEUTRE"),
        confidence=int(data.get("confidence", 50)),
        reasoning=data.get("reasoning", "Analyse indisponible."),
        action=data.get("action", "SURVEILLER"),
        risk_level=data.get("risk_level", "MOYEN"),
        target_horizon=data.get("target_horizon", "N/A"),
    )
