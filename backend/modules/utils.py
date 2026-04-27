"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE UTILS — UTILITAIRES PARTAGÉS V2                         ║
║  Source de vérité unique pour le schéma CSV et le chemin        ║
║  du journal. Importé par alerter.py ET tracker.py.              ║
╚══════════════════════════════════════════════════════════════════╝
"""
import math
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

import config

# ─────────────────────────────────────────────────────────────────
# SLIPPAGE ATR-ADAPTATIF — source unique de vérité
# ─────────────────────────────────────────────────────────────────

def atr_slippage(
    atr_value: float,
    price: float,
    base_pct: float | None = None,
) -> float:
    """
    Slippage adaptatif basé sur l'ATR du titre.

    Un slippage fixe sous-estime le coût réel sur les actions volatiles
    (NVDA ATR=3.5% → spread ~0.14%) et surestime sur les calmes
    (V ATR=0.5% → spread ~0.02%).

    Formule : slip = max(base_pct, atr_pct × 0.04)
      - base_pct : plancher  = config.SLIPPAGE_PCT (0.05%)
      - atr_pct  : atr_value / price
      - 0.04     : proxy empirique spread ≈ 4 % de l'ATR journalier

    Exemples :
      NVDA (ATR=3.5%, price=900) → max(0.05%, 0.14%) = 0.14%
      JNJ  (ATR=0.8%, price=155) → max(0.05%, 0.03%) = 0.05%

    Args:
        atr_value: ATR(14) en valeur absolue ($).
        price:     Prix d'entrée ($).
        base_pct:  Plancher de slippage (défaut config.SLIPPAGE_PCT).

    Returns:
        Fraction de slippage ∈ [base_pct, ∞[ à appliquer comme multiplicateur.
    """
    if base_pct is None:
        base_pct = config.SLIPPAGE_PCT
    if price <= 0 or math.isnan(atr_value) or atr_value <= 0:
        return base_pct
    return max(base_pct, (atr_value / price) * 0.04)


# ─────────────────────────────────────────────────────────────────
# CHEMINS CANONIQUES — absolus, indépendants du CWD de PM2/cron
# ─────────────────────────────────────────────────────────────────

# modules/utils.py → parent = modules/ → parent.parent = racine du projet
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

CSV_PATH      = _PROJECT_ROOT / "data" / "trade_journal.csv"
CSV_LOCK_PATH = _PROJECT_ROOT / "data" / "trade_journal.csv.lock"


# ─────────────────────────────────────────────────────────────────
# SCHÉMA CSV CANONIQUE
# Toute colonne ajoutée ici doit être gérée par alerter.py ET tracker.py
# ─────────────────────────────────────────────────────────────────

CSV_SCHEMA = [
    "Date",            # Horodatage d'entrée     ex: "2026-03-13 21:05:00"
    "Ticker",          # Symbole boursier        ex: "NVDA"
    "Direction",       # Sens du trade           "LONG" ou "SHORT"
    "Entry",           # Prix d'entrée           ex: 850.25
    "Stop_Loss",       # Prix stop-loss courant  ex: 835.00 (mis à jour par trailing stop)
    "Initial_SL",      # Prix stop-loss initial  ex: 835.00 (figé à l'entrée, jamais modifié)
    "Take_Profit",     # Prix take-profit        ex: 880.50
    "Size",            # Nombre d'actions        ex: 4
    "RR",              # Ratio Risk/Reward       ex: 2.01
    "Status",          # État courant            "OPEN" → "WIN" ou "LOSS"
    "Exit_Price",      # Prix de clôture         vide jusqu'à clôture par tracker
    "Exit_Date",       # Horodatage de clôture   vide jusqu'à clôture par tracker
    "Last_Alert_Pct",  # Dernier % de gain ayant déclenché une alerte (2.5/5.0/10.0)
    "Last_TS_Update",  # Date YYYY-MM-DD de la dernière mise à jour trailing stop (1 fois/jour max)
    "Order_ID",        # ID ordre broker (PAPER-xxx pour PaperBroker, UUID Alpaca pour live)
    "Signal",          # Type de signal   ex: "CHANDELIER_MOMENTUM" | "MOMENTUM_DIP" | "MEAN_REVERSION"
    "Sector",          # ETF proxy secteur ex: "SOXX" | "XLF" | "XLE" (attribution sectorielle)
    # Lot 13 — execution quality tracking (slippage réel vs reco).
    "Reco_Entry",      # Prix reco au moment de l'exec  ex: "150.42" (vide si ordre manuel)
    "Slippage_Bps",    # (fill - reco) / reco × 10000, signe = direction — permet de calibrer
                       # slippage_bps du backtest sur données réelles ex: "12.3" ou "-4.1"
    # ── Lot 15 — Capture scores TITAN à l'entrée (Phase 1 performance attribution).
    # Remplis par /proposals/approve_batch depuis proposal.context au moment où
    # le trade s'ouvre. Permet plus tard de corréler le score d'entrée avec
    # l'outcome WIN/LOSS (module performance_attribution à venir). Vides pour
    # les trades ouverts manuellement via /trade/add ou avant cette capture.
    "Titan_Score_Entry", # Composite score 0-100 ex: "82.5"
    "Quality_Entry",     # Pillar Q 0-100
    "Value_Entry",       # Pillar V 0-100
    "Risk_Entry",        # Pillar R 0-100
    "Momentum_Entry",    # Pillar M 0-100
    "Piotroski_Entry",   # Pillar P 0-100
    "Growth_Entry",      # Pillar G 0-100
    "F_Score_Entry",     # "8/9" — F-Score Piotroski absolu
    "Tilt_Flags_Entry",  # CSV des flags ex: "qarp,consistent" ou "cheap_junk"
]


# ─────────────────────────────────────────────────────────────────
# FONCTION PRINCIPALE
# ─────────────────────────────────────────────────────────────────

def get_open_tickers() -> set:
    """
    Retourne l'ensemble des tickers ayant actuellement une position OPEN.
    Utilisé par le pipeline de scan pour éviter les doublons quotidiens.
    """
    if not CSV_PATH.exists():
        return set()
    try:
        df = pd.read_csv(CSV_PATH, dtype=str)
        if "Status" not in df.columns or "Ticker" not in df.columns:
            return set()
        return set(df[df["Status"] == "OPEN"]["Ticker"].str.strip().str.upper())
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return set()


def get_recently_lost_tickers(cooldown_days: int = 14) -> set:
    """
    Retourne les tickers ayant eu une LOSS dans les `cooldown_days` derniers jours.

    Évite de re-rentrer sur un ticker qui vient de perdre — les conditions
    qui ont causé la perte (tendance baissière, secteur en retournement) sont
    souvent encore présentes. Un cooldown de 14 jours réduit les séries de
    pertes consécutives sur le même sous-jacent.

    Fail-open : retourne un set vide si le CSV est absent ou illisible.
    """
    if not CSV_PATH.exists():
        return set()
    try:
        df = pd.read_csv(CSV_PATH, dtype=str)
        required = {"Status", "Ticker", "Exit_Date"}
        if not required.issubset(df.columns):
            return set()

        losses = df[df["Status"] == "LOSS"].copy()
        if losses.empty:
            return set()

        losses["Exit_Date"] = pd.to_datetime(losses["Exit_Date"], errors="coerce")
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=cooldown_days)
        recent_losses = losses[losses["Exit_Date"] >= cutoff]
        return set(recent_losses["Ticker"].str.strip().str.upper().dropna())
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, KeyError):
        return set()


def ensure_csv_schema(csv_path: "str | Path" = CSV_PATH) -> None:
    """
    Garantit que trade_journal.csv existe et respecte le schéma canonique.

    Cette fonction est la réponse au bug "œuf et poule" (Bug H) : peu importe
    quel processus démarre en premier (alerter ou tracker), le fichier CSV sera
    toujours initialisé avec le même schéma complet.

    Comportements :
      - Fichier absent ou vide     → création avec les headers canoniques.
      - Colonnes manquantes        → ajout des colonnes absentes (valeur "").
      - Colonnes inconnues/extra   → conservées sans modification.
      - Fichier corrompu/illisible → recréation propre (données perdues, loguées).
      - Schéma déjà conforme       → aucune modification (idempotent).

    Args:
        csv_path: Chemin vers le fichier CSV. Défaut : data/trade_journal.csv.
    """
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Cas 1 : Fichier absent ou vide → initialisation propre
    if not path.exists() or path.stat().st_size == 0:
        pd.DataFrame(columns=CSV_SCHEMA).to_csv(path, index=False)
        return

    # Cas 2 : Fichier existant — lecture des colonnes uniquement (nrows=0)
    try:
        existing_cols = pd.read_csv(path, nrows=0).columns.tolist()
    except (OSError, UnicodeDecodeError, pd.errors.EmptyDataError, pd.errors.ParserError):
        # Fichier corrompu / illisible → backup horodaté avant recréation
        bak = path.with_suffix(f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        try:
            shutil.copy2(path, bak)
        except Exception:
            pass  # Si la copie échoue, on ne peut rien faire de plus
        pd.DataFrame(columns=CSV_SCHEMA).to_csv(path, index=False)
        return

    # Cas 3 : Schéma conforme → rien à faire
    missing = [col for col in CSV_SCHEMA if col not in existing_cols]
    if not missing:
        return

    # Cas 4 : Colonnes manquantes → migration en place
    try:
        df = pd.read_csv(path)
    except (OSError, UnicodeDecodeError, pd.errors.EmptyDataError, pd.errors.ParserError):
        # Corruption détectée au moment de relire les données (alors que la
        # lecture des colonnes avait réussi) → backup + recréation.
        bak = path.with_suffix(f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        try:
            shutil.copy2(path, bak)
        except Exception:
            pass
        pd.DataFrame(columns=CSV_SCHEMA).to_csv(path, index=False)
        return
    for col in missing:
        df[col] = ""

    # Réordonner : colonnes canoniques d'abord, puis les éventuelles colonnes extra
    extra_cols = [c for c in df.columns if c not in CSV_SCHEMA]
    df = df[CSV_SCHEMA + extra_cols]
    df.to_csv(path, index=False)
