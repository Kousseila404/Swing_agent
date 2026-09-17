"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE — BROKER GATEWAY V2                                      ║
║  Couche d'abstraction entre SwingAgent et le broker réel.        ║
║                                                                  ║
║  Deux implémentations :                                          ║
║    PaperBroker  — comportement actuel (CSV + yfinance)           ║
║    AlpacaBroker — ordres réels via Alpaca Markets API            ║
║                                                                  ║
║  Passage de paper → live :                                       ║
║    1. Remplir ALPACA_API_KEY / ALPACA_SECRET_KEY dans .env       ║
║    2. Changer BROKER_MODE = "alpaca" dans .env                   ║
║    3. python main.py --alpaca-test  (valide la connexion)        ║
║    4. python main.py --alpaca-sync  (importe positions Alpaca)   ║
║                                                                  ║
║  Usage :                                                         ║
║    from modules.broker_gateway import get_broker                 ║
║    broker = get_broker()                                         ║
║    result = broker.submit_order(scan)                            ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import csv
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from modules.log import logger
from modules.utils import CSV_LOCK_PATH, CSV_PATH, CSV_SCHEMA, ensure_csv_schema

# ─────────────────────────────────────────────────────────────────
# STRUCTURES DE DONNÉES
# ─────────────────────────────────────────────────────────────────

@dataclass
class OrderResult:
    """Résultat d'une soumission d'ordre."""
    success:   bool
    ticker:    str
    order_id:  str    # "" pour paper, ID Alpaca pour live
    message:   str
    filled_at: float = 0.0   # Prix d'exécution réel (0 = non rempli)


# ─────────────────────────────────────────────────────────────────
# HELPERS — capture des scores TITAN à l'entrée
# ─────────────────────────────────────────────────────────────────

def _make_client_order_id(ticker: str) -> str:
    """ID client déterministe et unique : SQ-<TICKER>-<UTC yyyymmddHHMMSS>-<4 hex>.

    Alpaca impose ≤ 128 caractères et l'unicité par compte. Le préfixe `SQ-`
    permet de distinguer nos ordres d'ordres manuels passés dans le dashboard
    Alpaca lors de la réconciliation.
    """
    import uuid
    from datetime import UTC

    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"SQ-{str(ticker).upper()}-{stamp}-{uuid.uuid4().hex[:4]}"


# Fenêtre de grâce avant qu'une ligne OPEN fraîchement écrite puisse être
# réconciliée comme "fermée par le broker". Audit 2026-09-17 (P0-3) : à
# l'ouverture, le fill du parent et l'apparition de la position dans
# `get_all_positions` ont quelques secondes de latence ; un cycle tracker qui
# démarre dans la même minute voyait "pas de position" et clôturait la ligne
# avec le premier vieux fill trouvé (CF 17/08 clôturé au fill du 27/07).
SYNC_GRACE_MINUTES = 30

# Plancher catastrophe appliqué quand une position importée depuis Alpaca n'a
# aucun stop connu (ni journal, ni jambe bracket ouverte). Aligné sur
# `portfolio._trade_levels._MAX_SL_PCT`.
IMPORT_FALLBACK_SL_PCT = 0.35


def _cell(v, default: str = "") -> str:
    """Valeur CSV normalisée : None/NaN/'nan' → default. Les DataFrames lus avec
    dtype=str exposent les cellules vides comme float('nan'), qui est *truthy* —
    `row.get(x) or default` ne protège donc pas (bug trouvé par les tests
    de l'audit 2026-09-17)."""
    if v is None:
        return default
    try:
        if isinstance(v, float) and math.isnan(v):
            return default
    except Exception:
        pass
    sv = str(v).strip()
    return default if sv == "" or sv.lower() == "nan" else sv


def _fmt_score(v) -> str:
    """Formatage d'un score 0-100 pour CSV. Retourne '' si None/invalide."""
    if v is None:
        return ""
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return ""
        return f"{f:.2f}"
    except (TypeError, ValueError):
        return ""


def _entry_scores_dict(scan) -> dict[str, str]:
    """Extrait les scores TITAN depuis les attrs optionnels du scan.

    Le router `/proposals/approve_batch` enrichit le SimpleNamespace avec ces
    attrs avant submit_order. Les trades manuels (/trade/add) n'ont pas ces
    attrs → retourne des chaînes vides (schéma CSV préservé, pas de None).
    """
    conf = getattr(scan, "confidence_entry", None)
    return {
        "Titan_Score_Entry": _fmt_score(getattr(scan, "titan_score_entry",  None)),
        "Quality_Entry":     _fmt_score(getattr(scan, "quality_entry",      None)),
        "Value_Entry":       _fmt_score(getattr(scan, "value_entry",        None)),
        "Risk_Entry":        _fmt_score(getattr(scan, "risk_entry",         None)),
        "Momentum_Entry":    _fmt_score(getattr(scan, "momentum_entry",     None)),
        "Piotroski_Entry":   _fmt_score(getattr(scan, "piotroski_entry",    None)),
        "Growth_Entry":      _fmt_score(getattr(scan, "growth_entry",       None)),
        "F_Score_Entry":     str(getattr(scan, "f_score_entry", "") or ""),
        "Tilt_Flags_Entry":  ",".join(getattr(scan, "tilt_flags_entry", []) or []),
        # Phase 1 data hardening — confidence_score à l'entrée (entier 0-100).
        "Confidence_Entry":  str(int(conf)) if isinstance(conf, (int, float)) and conf is not None else "",
    }


@dataclass
class BrokerPosition:
    """Position ouverte telle que vue par le broker."""
    ticker:     str
    direction:  str       # "LONG" | "SHORT"
    entry:      float
    stop_loss:  float
    take_profit: float
    size:       int
    status:     str       # "OPEN" | "WIN" | "LOSS"
    entry_date: str
    order_id:   str = ""
    unrealized_pnl: float = 0.0


# ─────────────────────────────────────────────────────────────────
# CLASSE ABSTRAITE — INTERFACE COMMUNE
# ─────────────────────────────────────────────────────────────────

class BrokerGateway(ABC):
    """
    Interface abstraite du broker.
    Toute implémentation (Paper, Alpaca, IB…) doit implémenter ces méthodes.
    """

    @abstractmethod
    def submit_order(self, scan) -> OrderResult:
        """Envoie un ordre d'entrée au broker."""

    @abstractmethod
    def close_position(
        self,
        ticker: str,
        exit_price: float,
        status: str,
        direction: str = "LONG",
        update_csv: bool = True,
    ) -> bool:
        """Ferme une position (SL touché, TP atteint, ou timeout)."""

    @abstractmethod
    def update_stop_loss(
        self,
        ticker: str,
        new_sl: float,
        direction: str = "LONG",
        update_csv: bool = True,
    ) -> bool:
        """Met à jour le stop loss (trailing stop)."""

    @abstractmethod
    def get_open_positions(self) -> list[BrokerPosition]:
        """Retourne toutes les positions actuellement ouvertes."""

    @abstractmethod
    def get_account_equity(self) -> float:
        """Retourne l'équité totale du compte en dollars."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Nom lisible du broker."""


# ─────────────────────────────────────────────────────────────────
# PAPER BROKER — Comportement actuel (CSV + yfinance)
# ─────────────────────────────────────────────────────────────────

class PaperBroker(BrokerGateway):
    """
    Broker de papier — réplique exactement le comportement actuel du bot.

    Les ordres sont loggués dans data/trade_journal.csv.
    Les prix sont récupérés via yfinance (mode paper trading).
    Aucune connexion broker réelle.
    """

    @property
    def name(self) -> str:
        return "PaperBroker (CSV)"

    def ensure_protective_stops(self, open_rows: list[dict]) -> list[dict]:
        """Paper : aucun ordre broker → rien à ré-armer (parité d'interface)."""
        return []

    def submit_order(self, scan) -> OrderResult:
        """
        Journalise le trade dans trade_journal.csv.
        Équivalent à log_trade_to_csv() mais retourne un OrderResult structuré.
        """
        try:
            from filelock import FileLock
            _rr = getattr(scan, "rr_ratio", float("nan"))
            order_id = f"PAPER-{scan.ticker}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

            CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(CSV_LOCK_PATH), timeout=10):
                ensure_csv_schema(CSV_PATH)
                with open(CSV_PATH, "a", newline="", encoding="utf-8") as fh:
                    writer = csv.DictWriter(fh, fieldnames=CSV_SCHEMA, extrasaction="ignore")
                    _sl_val = "" if math.isnan(scan.stop_loss) else round(scan.stop_loss, 4)
                    writer.writerow({
                        "Date":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "Ticker":      scan.ticker,
                        "Direction":   scan.direction,
                        "Entry":       scan.price,
                        "Stop_Loss":   _sl_val,
                        "Initial_SL":  _sl_val,
                        "Take_Profit": "" if math.isnan(scan.take_profit) else round(scan.take_profit, 4),
                        "Size":        scan.position_size,
                        "RR":          "" if math.isnan(_rr)              else round(_rr, 2),
                        "Status":      "OPEN",
                        "Exit_Price":  "",
                        "Exit_Date":   "",
                        "Order_ID":    order_id,
                        "Signal":      getattr(scan, "signal",     ""),
                        "Sector":      getattr(scan, "sector_etf", ""),
                        **_entry_scores_dict(scan),
                    })

            logger.info(f"[PaperBroker] {scan.ticker} journalisé → {CSV_PATH.name}")
            return OrderResult(
                success=True,
                ticker=scan.ticker,
                order_id=order_id,
                message="Trade journalisé (paper trading)",
                filled_at=scan.price,
            )

        except Exception as exc:
            logger.error(f"[PaperBroker] Erreur soumission {scan.ticker} : {exc}")
            return OrderResult(
                success=False,
                ticker=scan.ticker,
                order_id="",
                message=str(exc),
            )

    def close_position(
        self,
        ticker: str,
        exit_price: float,
        status: str,
        direction: str = "LONG",
        update_csv: bool = True,
    ) -> bool:
        """
        Met à jour le statut de la position dans le CSV.
        Cherche la première ligne OPEN pour ce ticker et la clôture.
        """
        if not update_csv:
            return True

        import pandas as pd
        from filelock import FileLock

        try:
            with FileLock(str(CSV_LOCK_PATH), timeout=10):
                ensure_csv_schema(CSV_PATH)
                df = pd.read_csv(CSV_PATH, dtype=str)
                mask = (df["Ticker"] == ticker) & (df["Status"] == "OPEN")
                if not mask.any():
                    logger.warning(f"[PaperBroker] Aucune position OPEN trouvée pour {ticker}")
                    return False
                idx = df[mask].index[0]
                exit_date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                df.at[idx, "Status"]     = status
                df.at[idx, "Exit_Price"] = str(round(exit_price, 6))
                df.at[idx, "Exit_Date"]  = exit_date_str
                df.to_csv(CSV_PATH, index=False)

            from modules.duckdb_journal import shadow_update_status
            shadow_update_status(ticker, status, exit_price, exit_date_str)

            logger.info(f"[PaperBroker] {ticker} clôturé → {status} @ {exit_price:.4f}")
            return True

        except Exception as exc:
            logger.error(f"[PaperBroker] Erreur clôture {ticker} : {exc}")
            return False

    def update_stop_loss(
        self,
        ticker: str,
        new_sl: float,
        direction: str = "LONG",
        update_csv: bool = True,
    ) -> bool:
        if not update_csv:
            return True

        import pandas as pd
        from filelock import FileLock
        try:
            with FileLock(str(CSV_LOCK_PATH), timeout=10):
                ensure_csv_schema(CSV_PATH)
                df = pd.read_csv(CSV_PATH, dtype=str)
                mask = (df["Ticker"] == ticker) & (df["Status"] == "OPEN")
                if not mask.any():
                    return False
                idx = df[mask].index[0]
                df.at[idx, "Stop_Loss"] = str(round(new_sl, 4))
                df.to_csv(CSV_PATH, index=False)
            return True
        except Exception as exc:
            logger.error(f"[PaperBroker] Erreur update SL {ticker} : {exc}")
            return False

    def get_open_positions(self) -> list[BrokerPosition]:
        """Lit les positions OPEN depuis trade_journal.csv."""
        import pandas as pd

        positions: list[BrokerPosition] = []
        if not CSV_PATH.exists():
            return positions
        try:
            df = pd.read_csv(CSV_PATH, dtype=str)
            open_rows = df[df["Status"] == "OPEN"]
            for _, row in open_rows.iterrows():
                try:
                    positions.append(BrokerPosition(
                        ticker      = str(row["Ticker"]).strip(),
                        direction   = str(row.get("Direction", "LONG")).strip(),
                        entry       = float(row["Entry"]),
                        stop_loss   = float(row["Stop_Loss"]) if row.get("Stop_Loss") else float("nan"),
                        take_profit = float(row["Take_Profit"]) if row.get("Take_Profit") else float("nan"),
                        size        = int(float(row.get("Size", 1) or 1)),
                        status      = "OPEN",
                        entry_date  = str(row.get("Date", "")),
                        order_id    = str(row.get("Order_ID", "")),
                    ))
                except Exception:
                    continue
        except Exception as exc:
            logger.warning(f"[PaperBroker] Lecture positions : {exc}")
        return positions

    def get_account_equity(self) -> float:
        """Lit l'équité depuis equity_state.json."""
        import json
        equity_path = Path(__file__).resolve().parent.parent / "data" / "equity_state.json"
        try:
            if equity_path.exists():
                data = json.loads(equity_path.read_text())
                return float(data.get("current_equity", config.ACCOUNT_SIZE))
        except Exception:
            pass
        return float(config.ACCOUNT_SIZE)


# ─────────────────────────────────────────────────────────────────
# ALPACA BROKER — Ordres réels via Alpaca Markets API
# ─────────────────────────────────────────────────────────────────

class AlpacaBroker(BrokerGateway):
    """
    Broker Alpaca Markets — ordres bracket réels via API REST (alpaca-py).

    Prérequis :
        pip install alpaca-py  (déjà installé)
        .env → ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL

    Alpaca Paper  : ALPACA_BASE_URL=https://paper-api.alpaca.markets
    Alpaca Live   : ALPACA_BASE_URL=https://api.alpaca.markets

    Fonctionnalités :
        - Bracket orders (entry + SL automatique + TP automatique)
        - Annulation des ordres enfants avant clôture
        - Sync des fills Alpaca → CSV (positions clôturées par bracket auto)
        - Prix de fill réel (pas yfinance)
    """

    def __init__(self):
        self._api_key    = getattr(config, "ALPACA_API_KEY",    None)
        self._secret_key = getattr(config, "ALPACA_SECRET_KEY", None)
        self._base_url   = getattr(config, "ALPACA_BASE_URL",   "https://paper-api.alpaca.markets")
        self._client     = None   # TradingClient — initialisé lazily
        self._data_client = None  # StockHistoricalDataClient — pour les prix en temps réel

        if not self._api_key or not self._secret_key:
            raise RuntimeError(
                "[AlpacaBroker] ALPACA_API_KEY et ALPACA_SECRET_KEY manquants dans .env. "
                "Ajoutez vos clés et relancez."
            )

    @property
    def name(self) -> str:
        mode = "Paper" if "paper" in self._base_url else "Live"
        return f"AlpacaBroker ({mode})"

    @property
    def is_paper(self) -> bool:
        return "paper" in self._base_url

    def _get_client(self):
        """Initialise le TradingClient Alpaca lazily."""
        if self._client is not None:
            return self._client
        try:
            from alpaca.trading.client import TradingClient
            self._client = TradingClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
                paper=self.is_paper,
            )
            logger.info(f"[AlpacaBroker] TradingClient connecté → {self._base_url}")
            return self._client
        except ImportError as e:
            raise RuntimeError("[AlpacaBroker] alpaca-py non installé. Exécutez : pip install alpaca-py") from e

    def test_connection(self) -> dict:
        """
        Teste la connexion et retourne les infos du compte.
        Utilisé par --alpaca-test.
        """
        client = self._get_client()
        account = client.get_account()
        return {
            "status":       str(account.status),
            "equity":       float(account.equity),
            "buying_power": float(account.buying_power),
            "cash":         float(account.cash),
            "mode":         "paper" if self.is_paper else "live",
        }

    def market_status(self) -> dict:
        """Retourne l'état de l'horloge NYSE côté Alpaca (is_open, next_open, next_close)."""
        client = self._get_client()
        clock = client.get_clock()
        return {
            "is_open":    bool(clock.is_open),
            "next_open":  clock.next_open.isoformat()  if clock.next_open  else None,
            "next_close": clock.next_close.isoformat() if clock.next_close else None,
            "timestamp":  clock.timestamp.isoformat()  if clock.timestamp  else None,
        }

    def submit_order(self, scan) -> OrderResult:
        """
        Envoie un ordre bracket (entry + SL + TP) sur Alpaca.

        Type : market order avec bracket (OTO)
          - Entry  : market order (exécution immédiate)
          - SL     : stop order (LONG : prix ≤ stop_loss | SHORT : prix ≥ stop_loss)
          - TP     : limit order (LONG : prix ≥ take_profit | SHORT : prix ≤ take_profit)

        Gate horaire : refuse si NYSE fermée. Les brackets DAY soumis hors heures
        restent `accepted` en queue jusqu'à l'ouverture suivante, avec SL/TP
        calibrés sur un prix potentiellement stale (gap overnight). Fail-closed
        plutôt que laisser passer un ordre potentiellement mal calibré.
        """
        try:
            from alpaca.trading.enums import OrderSide, TimeInForce
            from alpaca.trading.requests import (
                LimitOrderRequest,
                MarketOrderRequest,
                StopLossRequest,
                TakeProfitRequest,
            )

            client = self._get_client()

            # Gate horaire fail-closed : refuse les soumissions hors heures NYSE.
            # Sans ce gate, Alpaca accepte le bracket DAY et le queue jusqu'à l'open
            # suivant, mais les SL/TP sont déjà figés sur scan.price (stale). En cas
            # de gap overnight → RR réel décalé, SL/TP mal calibrés. Mieux vaut
            # refuser et laisser l'utilisateur re-soumettre à l'ouverture avec un
            # live price frais (le flow /execute refait l'alignement à chaque retry).
            try:
                clock = client.get_clock()
                if not clock.is_open:
                    next_open = clock.next_open.isoformat() if clock.next_open else "?"
                    msg = f"NYSE fermée — prochaine ouverture : {next_open}"
                    logger.warning(f"[AlpacaBroker] {scan.ticker} refusé : {msg}")
                    return OrderResult(
                        success=False,
                        ticker=scan.ticker,
                        order_id="",
                        message=msg,
                    )
            except Exception as exc:
                logger.warning(f"[AlpacaBroker] get_clock a échoué : {exc} — on laisse passer")

            side = OrderSide.BUY if scan.direction == "LONG" else OrderSide.SELL

            # Exécution intelligente (2026-09-17) — gate de spread + entrée limite.
            quote = self.get_quote(scan.ticker)
            max_spread = float(getattr(config, "EXEC_MAX_SPREAD_PCT", 0.5))
            if quote and quote.get("spread_pct") is not None and quote["spread_pct"] > max_spread:
                msg = f"spread {quote['spread_pct']:.2f}% > {max_spread:.2f}% — entrée différée"
                logger.warning(f"[AlpacaBroker] {scan.ticker} refusé : {msg}")
                return OrderResult(success=False, ticker=scan.ticker, order_id="", message=msg)

            # Audit 2026-09-17 (P0-1) — bracket **GTC**, pas DAY.
            # AVANT : TimeInForce.DAY → le parent market fillait à l'ouverture,
            # puis les jambes SL/TP (elles aussi DAY) étaient annulées/expirées
            # par Alpaca à 16:00 NY le jour même. Preuve : 100 % des jambes
            # STOP depuis juillet ont `canceled_at = 20:0x UTC` ; aucun ordre
            # stop n'existait chez le broker sur 6 positions ouvertes.
            # Pour une détention de 60 j+, la protection doit survivre à la
            # clôture : GTC. Le parent market GTC hors séance reste en queue
            # jusqu'à l'ouverture (le gate horaire ci-dessus empêche ce cas).
            # `client_order_id` déterministe : permet à la réconciliation de
            # relier parent/enfants sans dépendre d'une recherche par symbole.
            client_order_id = _make_client_order_id(scan.ticker)
            entry_type = str(getattr(config, "EXEC_ENTRY_TYPE", "limit")).lower()
            ref_px = float((quote or {}).get("ask" if side == OrderSide.BUY else "bid") or 0) or float(scan.price or 0)
            order_data: Any
            if entry_type == "limit" and ref_px > 0:
                # Limite « marketable » : ask × (1 + offset) à l'achat — fill
                # immédiat en conditions normales, protège d'un pic à l'ouverture.
                # Non fillée après EXEC_LIMIT_MAX_AGE_MIN → sweep_unfilled_entries
                # la remplace par un market bracket.
                offset = float(getattr(config, "EXEC_LIMIT_OFFSET_PCT", 0.3)) / 100.0
                limit_px = round(ref_px * (1 + offset), 2) if side == OrderSide.BUY else round(ref_px * (1 - offset), 2)
                order_data = LimitOrderRequest(
                    symbol          = scan.ticker,
                    qty             = scan.position_size,
                    side            = side,
                    time_in_force   = TimeInForce.GTC,
                    order_class     = "bracket",
                    limit_price     = limit_px,
                    client_order_id = client_order_id,
                    take_profit     = TakeProfitRequest(limit_price=round(scan.take_profit, 2)),
                    stop_loss       = StopLossRequest(stop_price=round(scan.stop_loss, 2)),
                )
            else:
                order_data = MarketOrderRequest(
                    symbol          = scan.ticker,
                    qty             = scan.position_size,
                    side            = side,
                    time_in_force   = TimeInForce.GTC,
                    order_class     = "bracket",
                    client_order_id = client_order_id,
                    take_profit     = TakeProfitRequest(limit_price=round(scan.take_profit, 2)),
                    stop_loss       = StopLossRequest(stop_price=round(scan.stop_loss, 2)),
                )

            order = client.submit_order(order_data=order_data)
            order_id  = str(order.id)
            filled_at = float(order.filled_avg_price or 0)
            order_status = str(getattr(order, "status", "")).lower()

            # Vérification des jambes : un bracket sans jambe STOP est une
            # position nue. On log en ERROR (le tracker ré-armera via
            # ensure_protective_stops au cycle suivant, cf. cycle.py).
            try:
                legs = list(getattr(order, "legs", None) or [])
                leg_types = {str(getattr(leg, "type", "")).lower() for leg in legs}
                if legs and not any("stop" in t for t in leg_types):
                    logger.error(
                        f"[AlpacaBroker] {scan.ticker} bracket {order_id} sans jambe STOP "
                        f"(legs={sorted(leg_types)}) — ré-armement attendu au prochain cycle."
                    )
            except Exception as _leg_exc:
                logger.debug(f"[AlpacaBroker] legs check {scan.ticker} : {_leg_exc}")

            logger.info(
                f"[AlpacaBroker] Ordre soumis {scan.ticker} {scan.direction} "
                f"×{scan.position_size} | ID={order_id} | status={order_status}"
            )

            # Phase 2 audit (2026-05-06) — bracket parent fail-fast :
            # si Alpaca renvoie immédiatement un statut terminal négatif, on
            # NE journalise PAS un trade fantôme dans le CSV (avant : log
            # systématique → lignes OPEN orphelines à nettoyer manuellement).
            # On force aussi la cancellation des enfants au cas où.
            terminal_failures = {"rejected", "canceled", "expired", "suspended"}
            if order_status in terminal_failures:
                logger.warning(
                    f"[AlpacaBroker] Parent {order_id} a un statut terminal "
                    f"négatif ({order_status}) — pas de log CSV ; tentative "
                    f"cancellation enfants bracket."
                )
                self._cancel_bracket_children(scan.ticker)
                return OrderResult(
                    success=False,
                    ticker=scan.ticker,
                    order_id=order_id,
                    message=f"Parent bracket rejeté : status={order_status}",
                )

            # Journalise dans le CSV avec l'order_id Alpaca réel.
            # Note : on log si status=accepted/pending_new/new/filled/partially_filled
            # — l'alpaca-sync réconciliera les fills/expiries a posteriori.
            self._log_to_csv(scan, order_id, filled_at or scan.price)

            return OrderResult(
                success=True,
                ticker=scan.ticker,
                order_id=order_id,
                message=f"Ordre bracket Alpaca soumis — status={order_status}",
                filled_at=filled_at,
            )

        except Exception as exc:
            logger.error(f"[AlpacaBroker] Erreur ordre {scan.ticker} : {exc}")
            return OrderResult(
                success=False,
                ticker=scan.ticker,
                order_id="",
                message=str(exc),
            )

    def _log_to_csv(self, scan, order_id: str, fill_price: float) -> None:
        """Journalise un trade Alpaca dans trade_journal.csv."""
        import math

        from filelock import FileLock

        _rr = getattr(scan, "rr_ratio", float("nan"))
        try:
            CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(CSV_LOCK_PATH), timeout=10):
                ensure_csv_schema(CSV_PATH)
                with open(CSV_PATH, "a", newline="", encoding="utf-8") as fh:
                    writer = csv.DictWriter(fh, fieldnames=CSV_SCHEMA, extrasaction="ignore")
                    _sl_val = "" if math.isnan(scan.stop_loss) else round(scan.stop_loss, 4)
                    row = {
                        "Date":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "Ticker":      scan.ticker,
                        "Direction":   scan.direction,
                        "Entry":       fill_price,
                        "Stop_Loss":   _sl_val,
                        "Initial_SL":  _sl_val,
                        "Take_Profit": "" if math.isnan(scan.take_profit) else round(scan.take_profit, 4),
                        "Size":        scan.position_size,
                        "RR":          "" if math.isnan(_rr)              else round(_rr, 2),
                        "Status":      "OPEN",
                        "Exit_Price":  "",
                        "Exit_Date":   "",
                        "Order_ID":    order_id,
                        "Signal":      getattr(scan, "signal",     ""),
                        "Sector":      getattr(scan, "sector_etf", ""),
                        **_entry_scores_dict(scan),
                    }
                    writer.writerow(row)
            # Audit 2026-09-17 (P2-8) — miroir DuckDB dès l'ouverture (avant :
            # seules les clôtures y étaient répliquées, la DB ne contenait
            # que les lignes importées).
            try:
                from modules.duckdb_journal import shadow_insert
                shadow_insert({k: ("" if v is None else v) for k, v in row.items()})
            except Exception as _db_exc:
                logger.debug(f"[AlpacaBroker] shadow_insert {scan.ticker} : {_db_exc}")
        except Exception as exc:
            logger.error(f"[AlpacaBroker] Erreur CSV log {scan.ticker} : {exc}")

    def _cancel_bracket_children(self, ticker: str) -> None:
        """
        Annule les ordres enfants (SL/TP) d'un bracket order ouvert.
        Nécessaire avant de clôturer manuellement une position.
        """
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            client = self._get_client()
            request = GetOrdersRequest(
                status=QueryOrderStatus.OPEN,
                symbols=[ticker],
            )
            open_orders = client.get_orders(filter=request)
            for order in open_orders:
                try:
                    client.cancel_order_by_id(str(order.id))
                    logger.info(f"[AlpacaBroker] Ordre enfant annulé : {order.id} ({ticker})")
                except Exception as e:
                    logger.warning(f"[AlpacaBroker] Échec annulation {order.id} : {e}")
        except Exception as exc:
            logger.warning(f"[AlpacaBroker] _cancel_bracket_children {ticker} : {exc}")

    # Nombre de tentatives / délai pour confirmer le fill réel d'un ordre de
    # clôture avant de considérer la position comme close. Un market order
    # Alpaca fill quasi-instantanément en heures de marché (paper ou live) —
    # cette fenêtre couvre la latence normale sans bloquer le cycle tracker
    # (appelé toutes les 2 min, cf. crontab).
    _CLOSE_CONFIRM_ATTEMPTS = 5
    _CLOSE_CONFIRM_DELAY_SEC = 1.5

    def close_position(
        self,
        ticker: str,
        exit_price: float,
        status: str,
        direction: str = "LONG",
        update_csv: bool = True,
    ) -> bool:
        """
        Clôture une position Alpaca :
        1. Annule les ordres bracket enfants (SL/TP pending)
        2. Envoie un market order de clôture, puis attend la confirmation du fill
        3. Met à jour le CSV (optionnel)

        Retourne True seulement si la clôture est CONFIRMÉE (fill réel obtenu,
        ou position inexistante côté broker → CANCELED). Retourne False si
        l'ordre a été soumis mais n'a pas fillé dans la fenêtre d'attente —
        dans ce cas la position reste économiquement ouverte : l'appelant ne
        doit PAS la marquer clôturée dans le journal (sync_fills_from_alpaca
        la réconciliera au cycle suivant une fois le fill confirmé).

        Bug corrigé 2026-07-16 : avant ce fix, un fill non confirmé (filled_avg_price
        None) tombait silencieusement sur `exit_price` et la position était marquée
        close immédiatement — alors que les actions restaient parfois ouvertes côté
        Alpaca pendant plusieurs heures (ordre queued hors séance), créant une
        exposition réelle non comptabilisée + un "phantom re-import" au sync suivant.
        """
        import time

        import pandas as pd
        from filelock import FileLock

        try:
            # 1. Annuler les ordres bracket enfants (quoi qu'il arrive).
            self._cancel_bracket_children(ticker)

            # 2. Clôturer la position via API Alpaca.
            # Cas particulier : position inexistante côté broker (bracket expiré,
            # jamais fillé). Alpaca lève APIError 404. Dans ce cas on PATCH QUAND
            # MÊME le CSV pour marquer la ligne OPEN comme CANCELED — sinon on
            # reste désynchronisé comme avant Lot 14.
            client = self._get_client()
            real_exit = exit_price
            position_existed = True
            confirmed = True
            try:
                response = client.close_position(ticker)
                filled_px = getattr(response, "filled_avg_price", None)
                order_id = getattr(response, "id", None)

                if not filled_px and order_id is not None:
                    # Pas encore fillé dans la réponse immédiate — on poll
                    # l'ordre quelques secondes avant d'abandonner.
                    for attempt in range(self._CLOSE_CONFIRM_ATTEMPTS):
                        time.sleep(self._CLOSE_CONFIRM_DELAY_SEC)
                        try:
                            order = client.get_order_by_id(order_id)
                        except Exception as poll_exc:
                            logger.debug(
                                f"[AlpacaBroker] poll fill {ticker} tentative "
                                f"{attempt + 1} : {poll_exc}"
                            )
                            continue
                        filled_px = getattr(order, "filled_avg_price", None)
                        if filled_px:
                            break

                if filled_px:
                    real_exit = float(filled_px)
                    logger.info(
                        f"[AlpacaBroker] Position {ticker} clôturée → {status} @ {real_exit:.4f}"
                    )
                else:
                    confirmed = False
                    logger.warning(
                        f"[AlpacaBroker] Position {ticker} : ordre de clôture soumis "
                        f"mais fill non confirmé après {self._CLOSE_CONFIRM_ATTEMPTS * self._CLOSE_CONFIRM_DELAY_SEC:.0f}s "
                        "— position laissée OPEN, sera réconciliée par sync_fills_from_alpaca"
                    )
            except Exception as close_exc:
                msg = str(close_exc).lower()
                # APIError 404 = pas de position → on réconcilie le CSV en CANCELED.
                if "404" in msg or "position does not exist" in msg or "no position" in msg:
                    logger.warning(
                        f"[AlpacaBroker] Position {ticker} introuvable côté broker "
                        f"(bracket expired ?) → marque CSV comme CANCELED"
                    )
                    status = "CANCELED"
                    position_existed = False
                else:
                    raise  # autre erreur → remonte

            if not confirmed:
                # Ordre soumis, fill non confirmé : ne rien écrire, laisser
                # sync_fills_from_alpaca finaliser au prochain cycle.
                return False

            # 3. Mettre à jour le CSV (CLOSED ou CANCELED).
            if update_csv:
                exit_date_str: str | None = None
                with FileLock(str(CSV_LOCK_PATH), timeout=10):
                    ensure_csv_schema(CSV_PATH)
                    df = pd.read_csv(CSV_PATH, dtype=str)
                    mask = (df["Ticker"] == ticker) & (df["Status"] == "OPEN")
                    if mask.any():
                        idx = df[mask].index[0]
                        exit_date_str = datetime.now().strftime(
                            "%Y-%m-%d %H:%M"
                        )
                        df.at[idx, "Status"]     = status
                        # Si pas de position, exit = entry → PnL = 0 (canceled clean)
                        exit_val = real_exit if position_existed else float(df.at[idx, "Entry"] or real_exit)
                        df.at[idx, "Exit_Price"] = str(round(exit_val, 6))
                        df.at[idx, "Exit_Date"]  = exit_date_str
                        df.to_csv(CSV_PATH, index=False)
                if exit_date_str is not None:
                    from modules.duckdb_journal import shadow_update_status
                    shadow_update_status(
                        ticker, status, real_exit, exit_date_str
                    )

            return True

        except Exception as exc:
            logger.error(f"[AlpacaBroker] Erreur clôture {ticker} : {exc}")
            return False

    # ─────────────────────────────────────────────────────────────
    # Helpers ordres de protection (audit 2026-09-17, P0-1)
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def _exit_side_name(direction: str) -> str:
        return "sell" if str(direction or "LONG").upper() == "LONG" else "buy"

    def _open_orders_for(self, client, ticker: str) -> list:
        """Ordres ouverts (accepted/new/held…) pour un symbole, **jambes
        incluses** (nested=True puis aplatissement des `legs`). Fail-open → [].

        Sans l'aplatissement, la jambe STOP d'un OCO (enfant du LIMIT parent)
        était invisible → ré-armement à chaque cycle (bug vu en prod le
        17/09 : MU/EIX OCO annulé/recréé toutes les 2 min).
        """
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[ticker], limit=50, nested=True)
            top = list(client.get_orders(filter=req) or [])
        except Exception as exc:
            logger.warning(f"[AlpacaBroker] get_orders(OPEN) {ticker} : {exc}")
            return []
        flat: list = []
        for o in top:
            flat.append(o)
            for leg in (getattr(o, "legs", None) or []):
                st = str(getattr(leg, "status", "") or "").lower()
                if any(x in st for x in ("canceled", "expired", "filled", "rejected", "replaced")):
                    continue
                flat.append(leg)
        return flat

    @staticmethod
    def _order_side(order) -> str:
        return str(getattr(order, "side", "") or "").lower().replace("orderside.", "")

    @staticmethod
    def _order_type(order) -> str:
        return str(getattr(order, "type", "") or "").lower().replace("ordertype.", "")

    def _find_open_stop(self, orders: list, direction: str = "LONG"):
        """Retourne la jambe STOP (ou STOP_LIMIT) de sortie ouverte, sinon None."""
        exit_side = self._exit_side_name(direction)
        for o in orders:
            if "stop" in self._order_type(o) and self._order_side(o) == exit_side:
                return o
        return None

    def _find_open_limit(self, orders: list, direction: str = "LONG"):
        """Retourne la jambe LIMIT (take-profit) de sortie ouverte, sinon None."""
        exit_side = self._exit_side_name(direction)
        for o in orders:
            if self._order_type(o) == "limit" and self._order_side(o) == exit_side:
                return o
        return None

    def _arm_stop(
        self,
        client,
        ticker: str,
        stop_price: float,
        direction: str = "LONG",
        take_profit: float | None = None,
        qty: float | None = None,
    ) -> str | None:
        """Pose un stop GTC (OCO stop+limit si `take_profit`) sur une position.

        Annule d'abord une éventuelle jambe LIMIT orpheline (elle bloque la
        quantité disponible et ferait rejeter le stop pour
        « insufficient qty »). Retourne l'ID de l'ordre posé, None si échec.
        """
        try:
            from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
            from alpaca.trading.requests import (
                LimitOrderRequest,
                StopLossRequest,
                StopOrderRequest,
                TakeProfitRequest,
            )

            if qty is None:
                try:
                    pos = client.get_open_position(ticker)
                    qty = abs(float(pos.qty))
                except Exception as exc:
                    logger.warning(f"[AlpacaBroker] _arm_stop {ticker} : position introuvable ({exc})")
                    return None
            qty_int = int(qty)
            if qty_int <= 0:
                return None

            orders = self._open_orders_for(client, ticker)
            stale_limit = self._find_open_limit(orders, direction)
            if stale_limit is not None:
                if take_profit is None:
                    try:
                        take_profit = float(stale_limit.limit_price)
                    except (TypeError, ValueError):
                        take_profit = None
                try:
                    client.cancel_order_by_id(str(stale_limit.id))
                    logger.info(
                        f"[AlpacaBroker] {ticker} jambe LIMIT orpheline {stale_limit.id} annulée "
                        "avant ré-armement du stop"
                    )
                except Exception as exc:
                    logger.warning(f"[AlpacaBroker] annulation LIMIT {ticker} : {exc}")

            side = OrderSide.SELL if str(direction).upper() == "LONG" else OrderSide.BUY
            sp = round(float(stop_price), 2)
            req: Any
            if take_profit is not None and float(take_profit) > 0:
                tp_px = round(float(take_profit), 2)
                # OCO Alpaca : type=limit + take_profit.limit_price + stop_loss.
                req = LimitOrderRequest(
                    symbol=ticker, qty=qty_int, side=side,
                    time_in_force=TimeInForce.GTC,
                    order_class=OrderClass.OCO,
                    limit_price=tp_px,
                    take_profit=TakeProfitRequest(limit_price=tp_px),
                    stop_loss=StopLossRequest(stop_price=sp),
                    client_order_id=_make_client_order_id(f"{ticker}-OCO"),
                )
            else:
                req = StopOrderRequest(
                    symbol=ticker, qty=qty_int, side=side,
                    time_in_force=TimeInForce.GTC,
                    stop_price=sp,
                    client_order_id=_make_client_order_id(f"{ticker}-STOP"),
                )
            order = client.submit_order(order_data=req)
            logger.info(
                f"[AlpacaBroker] 🛡️ {ticker} stop GTC armé @ {sp:.2f}"
                + (f" + TP {float(take_profit):.2f} (OCO)" if take_profit else "")
                + f" | id={order.id}"
            )
            return str(order.id)
        except Exception as exc:
            logger.error(f"[AlpacaBroker] _arm_stop {ticker} @ {stop_price} : {exc}")
            return None

    def ensure_protective_stops(self, open_rows: list[dict]) -> list[dict]:
        """Garantit qu'une jambe STOP ouverte existe chez le broker pour chaque
        position OPEN du journal. Appelé à chaque cycle tracker.

        Pour chaque ligne : si Alpaca détient la position et qu'aucun ordre
        stop de sortie n'est ouvert → pose un stop GTC (OCO si TP connu) au
        `Stop_Loss` du journal, ou au plancher catastrophe
        `IMPORT_FALLBACK_SL_PCT` si le journal n'en a pas.

        Retourne la liste des actions `{ticker, stop_price, take_profit,
        order_id, fallback, ok}` — le tracker persiste le SL fallback dans le
        journal et alerte.
        """
        actions: list[dict] = []
        try:
            client = self._get_client()
            positions = {
                str(p.symbol).upper(): p for p in client.get_all_positions()
            }
        except Exception as exc:
            logger.warning(f"[AlpacaBroker] ensure_protective_stops : {exc}")
            return actions

        seen: set[str] = set()
        for row in open_rows:
            ticker = _cell(row.get("Ticker")).upper()
            if not ticker or ticker in seen or ticker not in positions:
                continue
            seen.add(ticker)
            direction = _cell(row.get("Direction"), "LONG").upper()
            def _f(v) -> float | None:
                try:
                    x = float(v)
                    return x if math.isfinite(x) and x > 0 else None
                except (TypeError, ValueError):
                    return None

            sl = _f(row.get("Stop_Loss"))
            tp = _f(row.get("Take_Profit"))
            entry = _f(row.get("Entry")) or _f(getattr(positions[ticker], "avg_entry_price", None))

            orders = self._open_orders_for(client, ticker)
            existing = self._find_open_stop(orders, direction)
            if existing is not None:
                # Dérive de quantité (rebalance / partial fill) : la jambe doit
                # couvrir toute la position → on la ré-arme à la bonne qty.
                try:
                    bq = float(getattr(existing, "qty", 0) or 0)
                    pq = abs(float(positions[ticker].qty))
                except (TypeError, ValueError):
                    bq, pq = 0.0, 0.0
                if bq > 0 and pq > 0 and abs(bq - pq) >= 1:
                    try:
                        client.cancel_order_by_id(str(existing.id))
                        oid = self._arm_stop(client, ticker, sl or float(getattr(existing, "stop_price", 0) or 0),
                                             direction, take_profit=tp, qty=pq)
                        actions.append({"ticker": ticker, "stop_price": sl, "take_profit": tp, "order_id": oid,
                                        "fallback": False, "ok": oid is not None, "realigned": True, "qty": pq})
                        logger.info(f"[AlpacaBroker] 🛡️ {ticker} stop qty {bq:g} → {pq:g} (rebalance)")
                    except Exception as exc:
                        logger.warning(f"[AlpacaBroker] réarmement qty {ticker} : {exc}")
                    continue
                # Dérive niveau journal ↔ broker (> 1 %) : après une
                # reconstruction du journal ou un édit manuel, le stop broker
                # doit suivre le journal (source de vérité du tracker).
                try:
                    bsp = float(getattr(existing, "stop_price", 0) or 0)
                except (TypeError, ValueError):
                    bsp = 0.0
                if sl is not None and bsp > 0 and abs(bsp - sl) / sl > 0.01:
                    try:
                        from alpaca.trading.requests import ReplaceOrderRequest
                        client.replace_order_by_id(str(existing.id), ReplaceOrderRequest(stop_price=round(sl, 2)))
                        logger.info(f"[AlpacaBroker] 🛡️ {ticker} stop broker {bsp:.2f} → {sl:.2f} (aligné journal)")
                        actions.append({"ticker": ticker, "stop_price": sl, "take_profit": tp,
                                        "order_id": str(existing.id), "fallback": False, "ok": True,
                                        "realigned": True})
                    except Exception as exc:
                        logger.warning(f"[AlpacaBroker] réalignement stop {ticker} : {exc}")
                continue

            fallback = False
            if sl is None:
                if entry is None:
                    logger.error(f"[AlpacaBroker] {ticker} sans SL ni prix d'entrée — impossible d'armer")
                    continue
                sl = round(entry * (1.0 - IMPORT_FALLBACK_SL_PCT), 2) if direction == "LONG" \
                    else round(entry * (1.0 + IMPORT_FALLBACK_SL_PCT), 2)
                fallback = True

            logger.warning(
                f"[AlpacaBroker] ⚠️ {ticker} position OPEN SANS stop chez le broker — "
                f"ré-armement @ {sl:.2f}" + (" (plancher catastrophe, SL journal absent)" if fallback else "")
            )
            qty = abs(float(positions[ticker].qty))
            oid = self._arm_stop(client, ticker, sl, direction, take_profit=tp, qty=qty)
            actions.append({
                "ticker": ticker, "stop_price": sl, "take_profit": tp,
                "order_id": oid, "fallback": fallback, "ok": oid is not None,
            })
        return actions

    # ─────────────────────────────────────────────────────────────
    # Exécution intelligente (2026-09-17, améliorations post-audit)
    # ─────────────────────────────────────────────────────────────

    def get_quote(self, ticker: str) -> dict | None:
        """{bid, ask, mid, spread_pct} via Alpaca Data ; None si indisponible."""
        try:
            from modules.alpaca_data import get_latest_quote
            return get_latest_quote(ticker)
        except Exception as exc:
            logger.debug(f"[AlpacaBroker] quote {ticker} : {exc}")
            return None

    def sweep_unfilled_entries(self, max_age_min: int | None = None) -> dict:
        """Réconcilie les entrées : corrige `Entry` avec le fill réel, remplace
        les ordres limites non fillés après `max_age_min` par un market bracket.

        Appelé à chaque cycle tracker. Le journal écrit `Entry=prix proposé` au
        moment du submit (fill inconnu) : sans ce sweep, le PnL du journal
        partait d'un prix faux (MU : 1003,58 journal vs 1007,74 réel).
        """
        import pandas as pd
        from filelock import FileLock

        max_age = int(max_age_min if max_age_min is not None else getattr(config, "EXEC_LIMIT_MAX_AGE_MIN", 60))
        result: dict = {"entry_fixed": [], "replaced": [], "canceled": []}
        try:
            client = self._get_client()
            try:
                market_open = bool(client.get_clock().is_open)
            except Exception:
                market_open = False
            with FileLock(str(CSV_LOCK_PATH), timeout=10):
                ensure_csv_schema(CSV_PATH)
                df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
                changed = False
                for idx in df.index[df["Status"] == "OPEN"]:
                    row = df.loc[idx]
                    oid = _cell(row.get("Order_ID"))
                    if not (len(oid) == 36 and oid.count("-") == 4):
                        continue
                    try:
                        parent = client.get_order_by_id(oid)
                    except Exception:
                        continue
                    status = str(getattr(parent, "status", "")).lower()
                    filled_qty = float(getattr(parent, "filled_qty", 0) or 0)
                    fill_px = float(getattr(parent, "filled_avg_price", 0) or 0)
                    ticker = _cell(row.get("Ticker")).upper()
                    if filled_qty > 0 and fill_px > 0:
                        try:
                            j_entry = float(_cell(row.get("Entry")) or 0)
                        except ValueError:
                            j_entry = 0.0
                        if abs(j_entry - fill_px) > 0.005:
                            df.at[idx, "Entry"] = f"{round(fill_px, 4):g}"
                            reco = _cell(row.get("Reco_Entry")) or (f"{j_entry:g}" if j_entry else "")
                            if reco and not _cell(row.get("Reco_Entry")):
                                df.at[idx, "Reco_Entry"] = reco
                            try:
                                r = float(reco) if reco else 0.0
                                if r > 0:
                                    df.at[idx, "Slippage_Bps"] = f"{(fill_px - r) / r * 10000:.1f}"
                            except ValueError:
                                pass
                            changed = True
                            result["entry_fixed"].append({"ticker": ticker, "journal": j_entry, "fill": fill_px})
                            logger.info(f"[AlpacaBroker][Sweep] {ticker} Entry {j_entry:g} → fill réel {fill_px:g}")
                        if "partially" in status and abs(filled_qty - float(_cell(row.get("Size")) or 0)) >= 1:
                            df.at[idx, "Size"] = str(int(filled_qty))
                            changed = True
                        continue
                    # Non fillé : ordre limite en attente ?
                    if not any(s in status for s in ("new", "accepted", "pending", "held")):
                        continue
                    submitted = getattr(parent, "submitted_at", None)
                    age_min = None
                    if submitted is not None:
                        from datetime import UTC as _UTC
                        sub = submitted if submitted.tzinfo else submitted.replace(tzinfo=_UTC)
                        age_min = (datetime.now(_UTC) - sub).total_seconds() / 60.0
                    if age_min is None or age_min < max_age or not market_open:
                        continue
                    # Remplacement par un market bracket (même SL/TP, même qty).
                    try:
                        from alpaca.trading.enums import OrderSide, TimeInForce
                        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest
                        client.cancel_order_by_id(oid)
                        qty = int(float(_cell(row.get("Size")) or 0))
                        sl = float(_cell(row.get("Stop_Loss")) or 0)
                        tp = float(_cell(row.get("Take_Profit")) or 0)
                        direction = _cell(row.get("Direction"), "LONG").upper()
                        if qty <= 0 or sl <= 0:
                            result["canceled"].append({"ticker": ticker, "reason": "qty/SL invalides"})
                            df.at[idx, "Status"] = "CANCELED"
                            df.at[idx, "Close_Reason"] = "ENTRY_EXPIRED"
                            changed = True
                            continue
                        req = MarketOrderRequest(
                            symbol=ticker, qty=qty,
                            side=OrderSide.BUY if direction == "LONG" else OrderSide.SELL,
                            time_in_force=TimeInForce.GTC, order_class="bracket",
                            client_order_id=_make_client_order_id(f"{ticker}-MKT"),
                            take_profit=TakeProfitRequest(limit_price=round(tp, 2)) if tp > 0 else None,
                            stop_loss=StopLossRequest(stop_price=round(sl, 2)),
                        )
                        new_order = client.submit_order(order_data=req)
                        df.at[idx, "Order_ID"] = str(new_order.id)
                        df.at[idx, "Date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        changed = True
                        result["replaced"].append({"ticker": ticker, "old": oid, "new": str(new_order.id), "age_min": round(age_min, 1)})
                        logger.warning(f"[AlpacaBroker][Sweep] {ticker} limite non fillée après {age_min:.0f} min → market bracket {new_order.id}")
                    except Exception as exc:
                        logger.error(f"[AlpacaBroker][Sweep] remplacement {ticker} : {exc}")
                if changed:
                    df.to_csv(CSV_PATH, index=False)
            if changed:
                try:
                    from modules.duckdb_journal import sync_from_csv
                    sync_from_csv(csv_path=CSV_PATH, db_path=CSV_PATH.with_name("trade_journal.duckdb"))
                except Exception:
                    pass
        except Exception as exc:
            logger.warning(f"[AlpacaBroker] sweep_unfilled_entries : {exc}")
        return result

    def adjust_position(self, ticker: str, delta_qty: int, direction: str = "LONG") -> dict:
        """Rebalance de poids : achète (delta>0) ou vend (delta<0) `|delta|` actions
        au marché, puis met à jour la ligne OPEN du journal (Size, Entry moyen
        pondéré pour un ajout) et journalise dans `data/rebalance_log.jsonl`.

        Les jambes stop existantes sont ré-ajustées en quantité par
        `ensure_protective_stops` au cycle suivant (drift de qty). Pour une
        réduction, on annule d'abord la jambe stop (elle bloque la quantité).
        """
        import json as _json

        import pandas as pd
        from filelock import FileLock

        out: dict = {"ticker": ticker, "delta": delta_qty, "ok": False}
        if delta_qty == 0:
            out["ok"] = True
            return out
        try:
            from alpaca.trading.enums import OrderSide, TimeInForce
            from alpaca.trading.requests import MarketOrderRequest
            client = self._get_client()
            is_long = str(direction).upper() == "LONG"
            selling = (delta_qty < 0) == is_long
            if selling:
                for o in self._open_orders_for(client, ticker):
                    if self._order_side(o) == self._exit_side_name(direction):
                        try:
                            client.cancel_order_by_id(str(o.id))
                        except Exception as exc:
                            logger.debug(f"[AlpacaBroker] cancel {o.id} : {exc}")
            req = MarketOrderRequest(
                symbol=ticker, qty=abs(int(delta_qty)),
                side=OrderSide.SELL if selling else OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                client_order_id=_make_client_order_id(f"{ticker}-REB"),
            )
            order = client.submit_order(order_data=req)
            fill_px = 0.0
            for _ in range(self._CLOSE_CONFIRM_ATTEMPTS):
                import time as _t
                _t.sleep(self._CLOSE_CONFIRM_DELAY_SEC)
                try:
                    o = client.get_order_by_id(str(order.id))
                    fill_px = float(getattr(o, "filled_avg_price", 0) or 0)
                    if fill_px > 0:
                        break
                except Exception:
                    continue
            out.update({"order_id": str(order.id), "fill": fill_px})
            with FileLock(str(CSV_LOCK_PATH), timeout=10):
                ensure_csv_schema(CSV_PATH)
                df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
                mask = (df["Ticker"].str.upper() == ticker.upper()) & (df["Status"] == "OPEN")
                if mask.any():
                    idx = df[mask].index[0]
                    old_qty = int(float(_cell(df.at[idx, "Size"]) or 0))
                    old_entry = float(_cell(df.at[idx, "Entry"]) or 0)
                    new_qty = old_qty + int(delta_qty)
                    if delta_qty > 0 and fill_px > 0 and old_qty > 0:
                        df.at[idx, "Entry"] = f"{round((old_entry * old_qty + fill_px * delta_qty) / new_qty, 4):g}"
                    df.at[idx, "Size"] = str(max(0, new_qty))
                    df.to_csv(CSV_PATH, index=False)
                    realized = (fill_px - old_entry) * abs(delta_qty) if (delta_qty < 0 and fill_px > 0) else 0.0
                    out.update({"old_qty": old_qty, "new_qty": new_qty, "realized_pnl": round(realized, 2)})
            try:
                log_path = CSV_PATH.with_name("rebalance_log.jsonl")
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(_json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **out}) + "\n")
                from modules.duckdb_journal import sync_from_csv
                sync_from_csv(csv_path=CSV_PATH, db_path=CSV_PATH.with_name("trade_journal.duckdb"))
            except Exception:
                pass
            out["ok"] = fill_px > 0
            logger.info(f"[AlpacaBroker] rebalance {ticker} {delta_qty:+d} @ {fill_px:.2f} → qty {out.get('new_qty')}")
        except Exception as exc:
            logger.error(f"[AlpacaBroker] adjust_position {ticker} {delta_qty:+d} : {exc}")
            out["error"] = str(exc)
        return out

    def update_stop_loss(
        self,
        ticker: str,
        new_sl: float,
        direction: str = "LONG",
        update_csv: bool = True,
    ) -> bool:
        """Remplace le Stop Loss de la jambe bracket/OCO existante.

        Audit 2026-09-17 : si aucune jambe stop n'est ouverte (bracket DAY
        expiré, import, OCO annulé…), on en **crée** une (GTC) au lieu de
        retourner False silencieusement — le trailing stop doit toujours
        finir chez le broker.
        """
        try:
            from alpaca.trading.requests import ReplaceOrderRequest

            client = self._get_client()
            orders = self._open_orders_for(client, ticker)
            stop_order = self._find_open_stop(orders, direction)
            if stop_order is not None:
                replacement = ReplaceOrderRequest(stop_price=round(new_sl, 2))
                client.replace_order_by_id(str(stop_order.id), replacement)
                logger.info(f"[AlpacaBroker] Bracket SL {ticker} remplacé par {new_sl:.2f}")
            else:
                if self._arm_stop(client, ticker, new_sl, direction) is None:
                    return False

            if update_csv:
                import pandas as pd
                from filelock import FileLock
                with FileLock(str(CSV_LOCK_PATH), timeout=10):
                    ensure_csv_schema(CSV_PATH)
                    df = pd.read_csv(CSV_PATH, dtype=str)
                    mask = (df["Ticker"] == ticker) & (df["Status"] == "OPEN")
                    if mask.any():
                        idx = df[mask].index[0]
                        df.at[idx, "Stop_Loss"] = str(round(new_sl, 4))
                        df.to_csv(CSV_PATH, index=False)
            return True
        except Exception as exc:
            logger.warning(f"[AlpacaBroker] update_stop_loss {ticker} : {exc}")
            return False

    def sync_fills_from_alpaca(self) -> int:
        """Réconcilie les lignes OPEN du journal avec l'état réel du broker.

        Réécrite le 2026-09-17 (audit P0-3). L'ancienne version cherchait
        « les 10 derniers ordres fermés du symbole » sans borne temporelle :
        un trade ouvert le matin était clôturé 2 min plus tard avec le fill de
        clôture d'un trade précédent (CF 17/08 → fill du 27/07), puis
        ré-importé sans stop. Règles maintenant :

          1. Fenêtre de grâce : une ligne écrite il y a < SYNC_GRACE_MINUTES
             n'est jamais réconciliée (latence fill/position à l'ouverture).
          2. Position toujours ouverte chez Alpaca → seul le partial-fill
             (qty) est corrigé.
          3. Position absente → on relit le **parent** par `Order_ID` (UUID
             Alpaca) : jamais fillé + canceled/expired/rejected → CANCELED.
          4. Sinon on cherche un fill de **sortie** (side opposé, filled_qty>0,
             filled_at > date d'entrée) fermé APRÈS l'entrée → WIN/LOSS avec
             Close_Reason déduit du type d'ordre. Aucun fill → on laisse OPEN
             et on log (jamais de clôture sur un fill ancien).

        Returns: nombre de lignes modifiées.
        """
        from datetime import UTC, timedelta

        import pandas as pd
        from filelock import FileLock

        synced = 0
        try:
            client = self._get_client()
            alpaca_open_map = {
                str(p.symbol).upper(): float(p.qty) for p in client.get_all_positions()
            }
            now_local = datetime.now()

            with FileLock(str(CSV_LOCK_PATH), timeout=10):
                ensure_csv_schema(CSV_PATH)
                df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
                open_idx = list(df.index[df["Status"] == "OPEN"])

                for idx in open_idx:
                    row = df.loc[idx]
                    ticker = _cell(row.get("Ticker")).upper()
                    direction = _cell(row.get("Direction"), "LONG").upper()
                    if not ticker:
                        continue

                    # 1. Fenêtre de grâce (Date du journal = heure locale du process).
                    try:
                        entry_local = datetime.strptime(str(row.get("Date"))[:19], "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        try:
                            entry_local = datetime.strptime(str(row.get("Date"))[:16], "%Y-%m-%d %H:%M")
                        except Exception:
                            entry_local = now_local - timedelta(days=365)
                    if (now_local - entry_local) < timedelta(minutes=SYNC_GRACE_MINUTES):
                        logger.debug(f"[AlpacaBroker][Sync] {ticker} : ligne < {SYNC_GRACE_MINUTES} min, skip")
                        continue
                    # Borne UTC pour les requêtes Alpaca (marge 1 h pour l'offset local).
                    entry_utc = (entry_local - timedelta(hours=1)).replace(tzinfo=UTC)

                    # 2. Position toujours ouverte → partial fill uniquement.
                    if ticker in alpaca_open_map:
                        try:
                            alpaca_qty = alpaca_open_map[ticker]
                            csv_qty = float(row.get("Size") or 0)
                            if abs(alpaca_qty - csv_qty) >= 1.0:
                                df.at[idx, "Size"] = str(int(alpaca_qty))
                                logger.warning(
                                    f"[AlpacaBroker][Sync] {ticker} partial fill détecté : "
                                    f"CSV={csv_qty} → Alpaca={alpaca_qty}. CSV corrigé."
                                )
                                synced += 1
                        except Exception as exc:
                            logger.debug(f"[AlpacaBroker][Sync] partial-fill check {ticker} : {exc}")
                        continue

                    # 3. Parent jamais fillé → CANCELED.
                    order_id = _cell(row.get("Order_ID"))
                    parent = None
                    if len(order_id) == 36 and order_id.count("-") == 4:
                        try:
                            parent = client.get_order_by_id(order_id)
                        except Exception as exc:
                            logger.debug(f"[AlpacaBroker][Sync] get_order_by_id {order_id} : {exc}")
                    if parent is not None:
                        p_status = str(getattr(parent, "status", "")).lower()
                        p_filled = float(getattr(parent, "filled_qty", 0) or 0)
                        if p_filled <= 0 and any(s in p_status for s in ("canceled", "expired", "rejected")):
                            df.at[idx, "Status"] = "CANCELED"
                            df.at[idx, "Exit_Price"] = df.at[idx, "Entry"]
                            df.at[idx, "Exit_Date"] = now_local.strftime("%Y-%m-%d %H:%M")
                            df.at[idx, "Close_Reason"] = "ENTRY_EXPIRED"
                            synced += 1
                            logger.warning(
                                f"[AlpacaBroker][Sync] {ticker} → CANCELED (parent {p_status}, jamais fillé)"
                            )
                            continue

                    # 4. Fill de sortie postérieur à l'entrée.
                    try:
                        from alpaca.trading.enums import QueryOrderStatus
                        from alpaca.trading.requests import GetOrdersRequest
                        req = GetOrdersRequest(
                            status=QueryOrderStatus.CLOSED,
                            symbols=[ticker],
                            after=entry_utc,
                            limit=100,
                        )
                        closed_orders = list(client.get_orders(filter=req) or [])
                    except Exception as exc:
                        logger.warning(f"[AlpacaBroker][Sync] get_orders(CLOSED) {ticker} : {exc}")
                        continue

                    exit_side = self._exit_side_name(direction)
                    fills = []
                    for o in closed_orders:
                        try:
                            if self._order_side(o) != exit_side:
                                continue
                            fq = float(getattr(o, "filled_qty", 0) or 0)
                            fp = float(getattr(o, "filled_avg_price", 0) or 0)
                            fa = getattr(o, "filled_at", None)
                            if fq <= 0 or fp <= 0 or fa is None:
                                continue
                            fa_utc = fa if fa.tzinfo else fa.replace(tzinfo=UTC)
                            if fa_utc <= entry_utc:
                                continue
                            fills.append((fa_utc, fp, self._order_type(o), fq))
                        except Exception as _fill_exc:
                            logger.debug(f"[AlpacaBroker][Sync] {ticker} fill ignoré : {_fill_exc!r}")
                            continue

                    if not fills:
                        logger.warning(
                            f"[AlpacaBroker][Sync] {ticker} : position absente chez Alpaca mais "
                            "aucun fill de sortie postérieur à l'entrée — ligne laissée OPEN "
                            "(vérifier manuellement)."
                        )
                        continue

                    fills.sort(key=lambda x: x[0])
                    _, exit_price, order_type, _ = fills[-1]
                    entry_price = float(row.get("Entry") or exit_price)
                    if direction == "LONG":
                        status_code = "WIN" if exit_price > entry_price else "LOSS"
                    else:
                        status_code = "WIN" if exit_price < entry_price else "LOSS"
                    if "stop" in order_type:
                        close_reason = "SL_HIT"
                    elif order_type == "limit":
                        close_reason = "TP_HIT"
                    else:
                        close_reason = "BROKER_SYNC"

                    df.at[idx, "Status"] = status_code
                    df.at[idx, "Exit_Price"] = str(round(exit_price, 6))
                    df.at[idx, "Exit_Date"] = now_local.strftime("%Y-%m-%d %H:%M")
                    df.at[idx, "Close_Reason"] = close_reason
                    synced += 1
                    logger.info(
                        f"[AlpacaBroker][Sync] {ticker} → {status_code} @ {exit_price:.4f} "
                        f"(fill {order_type} postérieur à l'entrée, reason={close_reason})"
                    )

                if synced > 0:
                    df.to_csv(CSV_PATH, index=False)
                    try:
                        from modules.duckdb_journal import sync_from_csv
                        sync_from_csv(csv_path=CSV_PATH, db_path=CSV_PATH.with_name("trade_journal.duckdb"))
                    except Exception as _db_exc:
                        logger.debug(f"[AlpacaBroker][Sync] duckdb sync : {_db_exc}")

        except Exception as exc:
            logger.error(f"[AlpacaBroker] sync_fills_from_alpaca : {exc}")

        return synced

    def get_open_positions(self) -> list[BrokerPosition]:
        """Récupère les positions ouvertes depuis l'API Alpaca."""
        positions = []
        try:
            client = self._get_client()
            alpaca_positions = client.get_all_positions()
            for p in alpaca_positions:
                direction = "LONG" if float(p.qty) > 0 else "SHORT"
                positions.append(BrokerPosition(
                    ticker         = str(p.symbol),
                    direction      = direction,
                    entry          = float(p.avg_entry_price),
                    stop_loss      = float("nan"),
                    take_profit    = float("nan"),
                    size           = abs(int(float(p.qty))),
                    status         = "OPEN",
                    entry_date     = "",
                    order_id       = "",
                    unrealized_pnl = float(p.unrealized_pl or 0),
                ))
        except Exception as exc:
            logger.warning(f"[AlpacaBroker] get_open_positions : {exc}")
        return positions

    def get_account_equity(self) -> float:
        """Récupère l'équité du compte Alpaca en temps réel."""
        try:
            client = self._get_client()
            account = client.get_account()
            return float(account.equity)
        except Exception as exc:
            logger.warning(f"[AlpacaBroker] get_account_equity : {exc}")
            return float(config.ACCOUNT_SIZE)

    def import_positions_to_csv(self) -> int:
        """Importe dans le journal les positions Alpaca absentes du CSV.

        Réécrit le 2026-09-17 (audit P2-7). Avant : ligne muette (pas de SL,
        pas de TP, pas de scores) → position jamais protégée par le tracker
        (`nan%→SL`) et invisible pour `thesis_stop`. Maintenant :
          • SL/TP récupérés depuis les jambes STOP/LIMIT ouvertes chez Alpaca ;
          • sinon SL = plancher catastrophe (IMPORT_FALLBACK_SL_PCT) + WARNING ;
          • `Signal=ALPACA_IMPORT`, `Order_ID` daté (unicité) ;
          • miroir DuckDB.
        Un import ne devrait jamais arriver en fonctionnement normal (chaque
        ordre passe par submit_order) : c'est un filet, et il est bruyant.

        Returns: nombre de positions importées.
        """
        import pandas as pd
        from filelock import FileLock

        imported = 0
        try:
            alpaca_positions = self.get_open_positions()
            if not alpaca_positions:
                logger.info("[AlpacaBroker] Aucune position ouverte à importer.")
                return 0

            client = self._get_client()
            with FileLock(str(CSV_LOCK_PATH), timeout=10):
                ensure_csv_schema(CSV_PATH)
                df = pd.read_csv(CSV_PATH, dtype=str)
                existing_open = set(df[df["Status"] == "OPEN"]["Ticker"].str.upper())

                rows_to_add = []
                for p in alpaca_positions:
                    ticker = p.ticker.upper()
                    if ticker in existing_open:
                        continue

                    orders = self._open_orders_for(client, ticker)
                    stop_o = self._find_open_stop(orders, p.direction)
                    limit_o = self._find_open_limit(orders, p.direction)
                    sl = None
                    tp = None
                    try:
                        if stop_o is not None and getattr(stop_o, "stop_price", None):
                            sl = round(float(stop_o.stop_price), 4)
                        if limit_o is not None and getattr(limit_o, "limit_price", None):
                            tp = round(float(limit_o.limit_price), 4)
                    except (TypeError, ValueError):
                        pass
                    fallback = False
                    if sl is None:
                        fallback = True
                        sl = round(p.entry * (1.0 - IMPORT_FALLBACK_SL_PCT), 4) if p.direction == "LONG" \
                            else round(p.entry * (1.0 + IMPORT_FALLBACK_SL_PCT), 4)

                    row: dict[str, Any] = {col: "" for col in CSV_SCHEMA}
                    row.update({
                        "Date":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "Ticker":      ticker,
                        "Direction":   p.direction,
                        "Entry":       round(p.entry, 4),
                        "Stop_Loss":   sl,
                        "Initial_SL":  sl,
                        "Take_Profit": "" if tp is None else tp,
                        "Size":        p.size,
                        "RR":          "",
                        "Status":      "OPEN",
                        "Order_ID":    f"ALPACA-IMPORT-{ticker}-{datetime.now().strftime('%Y%m%d')}",
                        "Signal":      "ALPACA_IMPORT",
                    })
                    rows_to_add.append(row)
                    imported += 1
                    level = logger.warning if fallback else logger.info
                    level(
                        f"[AlpacaBroker][Import] {ticker} {p.direction} ×{p.size} @ {p.entry:.2f} "
                        f"| SL={sl} TP={tp}"
                        + (" — AUCUN stop broker : plancher catastrophe appliqué" if fallback else "")
                    )
                    try:
                        from modules.duckdb_journal import shadow_insert
                        shadow_insert(row)
                    except Exception:
                        pass

                if rows_to_add:
                    new_df = pd.DataFrame(rows_to_add)
                    df = pd.concat([df, new_df], ignore_index=True)
                    df.to_csv(CSV_PATH, index=False)

        except Exception as exc:
            logger.error(f"[AlpacaBroker] import_positions_to_csv : {exc}")

        return imported

# ─────────────────────────────────────────────────────────────────
# FACTORY — Retourne le bon broker selon BROKER_MODE
# ─────────────────────────────────────────────────────────────────

_broker_instance: BrokerGateway | None = None


def get_broker(force_reinit: bool = False) -> BrokerGateway:
    """
    Factory — retourne le broker configuré (singleton par session).

    Lit BROKER_MODE dans config.py :
        "paper"  → PaperBroker  (défaut — zéro risque)
        "alpaca" → AlpacaBroker (ordres réels Alpaca Markets)

    Args:
        force_reinit: Si True, recrée l'instance (utile pour les tests).

    Returns:
        Instance du broker actif.
    """
    global _broker_instance

    if _broker_instance is not None and not force_reinit:
        return _broker_instance

    mode = getattr(config, "BROKER_MODE", "paper").lower().strip()

    if mode == "alpaca":
        _broker_instance = AlpacaBroker()
        logger.info(f"[BrokerGateway] Mode activé : {_broker_instance.name}")
    else:
        _broker_instance = PaperBroker()
        logger.info(f"[BrokerGateway] Mode activé : {_broker_instance.name}")

    return _broker_instance
