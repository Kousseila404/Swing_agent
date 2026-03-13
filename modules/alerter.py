"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE 4 — ALERTES TELEGRAM                                   ║
║  Formate les signaux en messages lisibles et les envoie         ║
║  sur votre canal Telegram privé.                                ║
╚══════════════════════════════════════════════════════════════════╝
"""
from typing import Optional
import csv
import math
import time
import httpx
from datetime import datetime
from pathlib import Path

import config
from modules.scanner import ScanResult, load_optimized_strategy
from modules.ia_brain import AIVerdict
from modules.log import logger


# Emojis par type de signal pour un scan rapide du message
_SIGNAL_EMOJI = {
    "SURVENTE":      "🔴",
    "MOMENTUM":      "🟢",
    "MOMENTUM_DIP":  "🟢",
    "MEAN_REVERSION":"🔵",
}

_ACTION_EMOJI = {
    "ACHETER": "🚀",
    "SURVEILLER": "👀",
    "ÉVITER": "⛔",
}

_RISK_EMOJI = {
    "FAIBLE": "🟢",
    "MOYEN": "🟡",
    "ÉLEVÉ": "🔴",
}

# Colonnes du journal CSV (V19)
_CSV_FIELDNAMES = ["Date", "Ticker", "Entry", "Stop_Loss", "Take_Profit", "Size", "RR", "Status"]
_CSV_PATH       = Path("data") / "trade_journal.csv"


def log_trade_to_csv(trade_data: dict) -> bool:
    """
    V19 — Journalise un trade dans data/trade_journal.csv.

    Crée le fichier (avec en-têtes) si inexistant. Ajoute une ligne à chaque
    appel. Les clés inconnues dans trade_data sont ignorées (extrasaction="ignore").

    Colonnes : Date, Ticker, Entry, Stop_Loss, Take_Profit, Size, RR, Status.

    Args:
        trade_data: Dict avec les valeurs du trade. Clés attendues :
                    Date, Ticker, Entry, Stop_Loss, Take_Profit, Size, RR, Status.

    Returns:
        True si journalisé avec succès, False en cas d'erreur I/O.
    """
    try:
        _CSV_PATH.parent.mkdir(parents=True, exist_ok=True)

        file_exists = _CSV_PATH.exists()

        with open(_CSV_PATH, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=_CSV_FIELDNAMES,
                extrasaction="ignore",
            )
            if not file_exists:
                writer.writeheader()

            row = {field: trade_data.get(field, "") for field in _CSV_FIELDNAMES}
            writer.writerow(row)

        logger.info(
            f"[Journal] {trade_data.get('Ticker', '?')} journalisé → {_CSV_PATH}"
        )
        return True

    except Exception as exc:
        logger.error(f"[Journal] Erreur écriture CSV : {exc}")
        return False


def send_alert(scan: ScanResult, verdict: Optional[AIVerdict]) -> bool:
    """
    Envoie une alerte formatée sur Telegram (V19).
    Journalise automatiquement le trade dans data/trade_journal.csv si succès.

    Returns:
        True si le message Telegram a été envoyé avec succès, False sinon.
    """
    message = _format_message(scan, verdict)

    try:
        _send_telegram_message(message)
        logger.info(f"[{scan.ticker}] Alerte Telegram envoyée ✅")

        # V19 — Journalisation CSV automatique
        _rr  = scan.rr_ratio
        log_trade_to_csv({
            "Date":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Ticker":      scan.ticker,
            "Entry":       scan.price,
            "Stop_Loss":   "" if math.isnan(scan.stop_loss)   else round(scan.stop_loss, 4),
            "Take_Profit": "" if math.isnan(scan.take_profit) else round(scan.take_profit, 4),
            "Size":        scan.position_size,
            "RR":          "" if math.isnan(_rr)              else round(_rr, 2),
            "Status":      "OPEN",
        })

        return True

    except Exception as e:
        logger.error(f"[{scan.ticker}] Échec envoi Telegram : {e}")
        return False


def send_summary(total_scanned: int, signals_found: int, alerts_sent: int) -> bool:
    """Envoie un résumé de fin de scan."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    message = (
        f"📊 <b>RÉSUMÉ DU SCAN — {now}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Actions scannées : {total_scanned}\n"
        f"• Signaux détectés : {signals_found}\n"
        f"• Alertes envoyées : {alerts_sent}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
    )

    if signals_found == 0:
        message += "💤 <i>Aucune anomalie détectée. Le marché est calme.</i>"
    else:
        message += "🔍 <i>Consultez les alertes ci-dessus pour le détail.</i>"

    try:
        _send_telegram_message(message)
        return True
    except Exception as e:
        logger.error(f"Échec envoi résumé Telegram : {e}")
        return False


def _format_plan_de_trade(scan: ScanResult, verdict: Optional[AIVerdict]) -> str:
    """
    V19 — Génère le bloc "Ordre de Mission" HTML (copiable-collable pour exécution manuelle).

    Format :
        🎯 PLAN DE TRADE : [TICKER]
        ──────────────────────────
        🛒 Action : ACHAT LIMIT / VENTE SHORT
        💰 Prix d'Entrée : $X.XX
        🛑 Stop-Loss     : $X.XX  (Risque : $XXX)
        🏁 Take-Profit   : $X.XX  (Gain potentiel : $XXX)
        📦 Quantité      : X actions
        ──────────────────────────
        🧠 Contexte : [reasoning IA ou nom du signal]

    Args:
        scan:    ScanResult enrichi avec les champs V17.
        verdict: AIVerdict optionnel (contexte IA).

    Returns:
        Bloc HTML formaté pour Telegram (parse_mode="HTML").
    """
    action_label = "ACHAT LIMIT" if scan.direction == "LONG" else "VENTE SHORT"

    def _fmt(value: float, decimals: int = 2) -> str:
        """Formate un float ou retourne 'N/A' si NaN."""
        return f"{value:.{decimals}f}" if not math.isnan(value) else "N/A"

    contexte = (
        verdict.reasoning
        if verdict and verdict.reasoning
        else f"Signal {scan.signal} détecté ({scan.direction})"
    )

    plan = (
        f"🎯 <b>PLAN DE TRADE : {scan.ticker}</b>\n"
        f"──────────────────────────\n"
        f"🛒 Action : <b>{action_label}</b>\n"
        f"💰 Prix d'Entrée : <b>${_fmt(scan.price, 4)}</b>\n"
        f"🛑 Stop-Loss     : <b>${_fmt(scan.stop_loss, 4)}</b>"
        f"  <i>(Risque : ${_fmt(scan.risk_amount)})</i>\n"
        f"🏁 Take-Profit   : <b>${_fmt(scan.take_profit, 4)}</b>"
        f"  <i>(Gain potentiel : ${_fmt(scan.gain_amount)})</i>\n"
        f"📦 Quantité      : <b>{scan.position_size} actions</b>\n"
        f"──────────────────────────\n"
        f"🧠 Contexte : <i>{contexte}</i>"
    )

    return plan


def _format_message(scan: ScanResult, verdict: Optional[AIVerdict]) -> str:
    """
    Construit le message Telegram complet avec HTML (V19).

    Structure :
        1. En-tête signal technique
        2. Données marché (prix, volume, RSI)
        3. 🎯 PLAN DE TRADE (Ordre de Mission V19)
        4. Analyse IA (verdict)
        5. Footer stratégie Optuna + horodatage

    Lisible en un coup d'œil sur mobile.
    """
    sig_emoji = _SIGNAL_EMOJI.get(scan.signal, "⚪")

    # --- En-tête ---
    msg = (
        f"{sig_emoji} <b>SIGNAL {scan.signal}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷️ <b>{scan.ticker}</b> — {scan.name}\n\n"
    )

    # --- Données techniques ---
    msg += (
        f"💰 Prix : <b>{scan.price} $</b> ({scan.change_pct:+.2f}%)\n"
        f"📊 Volume : <b>{scan.volume:,}</b> ({scan.volume_ratio}x moy.)\n"
        f"📈 RSI(14) : <b>{scan.rsi}</b>\n\n"
    )

    # --- Plan de Trade V19 (Ordre de Mission) ---
    msg += _format_plan_de_trade(scan, verdict)
    msg += "\n\n"

    # --- Verdict IA (si disponible) ---
    if verdict:
        act_emoji  = _ACTION_EMOJI.get(verdict.action,     "❓")
        risk_emoji = _RISK_EMOJI.get(verdict.risk_level,   "⚪")

        msg += (
            f"🧠 <b>ANALYSE IA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Sentiment : <b>{verdict.sentiment}</b>\n"
            f"Confiance : <b>{verdict.confidence}%</b>\n"
            f"Action : {act_emoji} <b>{verdict.action}</b>\n"
            f"Risque : {risk_emoji} <b>{verdict.risk_level}</b>\n"
            f"Horizon : <i>{verdict.target_horizon}</i>\n\n"
            f"💬 <i>{verdict.reasoning}</i>\n"
        )
    else:
        msg += "🧠 <i>Analyse IA indisponible pour ce signal.</i>\n"

    # --- Footer : stratégie Optuna active ---
    try:
        _strat = load_optimized_strategy()
        _strat_line = (
            f"⚙️ <i>Stratégie : "
            f"EMA {_strat['param_ema']}, "
            f"ADX {_strat['param_adx']:.1f}, "
            f"RSI {_strat['param_rsi']:.1f}</i>\n"
        )
    except Exception:
        _strat_line = ""

    msg += (
        f"\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"{_strat_line}"
        f"⏰ {datetime.now().strftime('%H:%M:%S')} "
        f"| SwingAgent V19"
    )

    return msg


def _send_telegram_message(text: str, max_retries: int = 3, retry_delay: float = 5.0) -> None:
    """
    Envoie un message via l'API Telegram Bot avec retry automatique.

    En cas d'échec réseau ou d'erreur HTTP transitoire (5xx), effectue
    jusqu'à max_retries tentatives avec un délai croissant (5s, 10s, 15s).

    Args:
        text:        Corps du message HTML.
        max_retries: Nombre maximum de tentatives (défaut : 3).
        retry_delay: Délai de base en secondes, multiplié par le numéro de tentative.

    Raises:
        RuntimeError: Si toutes les tentatives échouent.
    """
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": int(config.TELEGRAM_CHAT_ID),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
            return  # Succès — on sort immédiatement

        except httpx.HTTPStatusError as exc:
            # Erreur 4xx (token invalide, chat_id erroné…) : inutile de réessayer
            if exc.response.status_code < 500:
                raise RuntimeError(
                    f"[Telegram] Erreur HTTP {exc.response.status_code} (non-récupérable) : {exc}"
                ) from exc
            last_exc = exc

        except Exception as exc:
            last_exc = exc

        if attempt < max_retries:
            wait = retry_delay * attempt  # 5s, 10s, 15s
            logger.warning(
                f"[Telegram] Tentative {attempt}/{max_retries} échouée : {last_exc} "
                f"— Nouvel essai dans {wait:.0f}s"
            )
            time.sleep(wait)

    raise RuntimeError(
        f"[Telegram] Échec après {max_retries} tentatives. Dernière erreur : {last_exc}"
    )
