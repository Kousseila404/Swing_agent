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
            from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

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

            # Lot 14 — Fix bracket DAY expiry :
            # AVANT : LimitOrderRequest(TIF=DAY) → si limit_price jamais touché
            # avant market close, ordre expire sans fill → CSV = OPEN fantôme.
            # (Exactement ce qui est arrivé aux 6 ordres du 14-15/04.)
            # MAINTENANT : MarketOrderRequest(TIF=DAY) + bracket → fill au market
            # open ou immédiat en heures de marché. Si soumis weekend → queue
            # pour la prochaine ouverture (pas d'expiry silencieux).
            order_data = MarketOrderRequest(
                symbol        = scan.ticker,
                qty           = scan.position_size,
                side          = side,
                time_in_force = TimeInForce.DAY,
                order_class   = "bracket",
                take_profit   = TakeProfitRequest(limit_price=round(scan.take_profit, 2)),
                stop_loss     = StopLossRequest(stop_price=round(scan.stop_loss, 2)),
            )

            order = client.submit_order(order_data=order_data)
            order_id  = str(order.id)
            filled_at = float(order.filled_avg_price or 0)
            order_status = str(getattr(order, "status", "")).lower()

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
                    writer.writerow({
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
                    })
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

    def update_stop_loss(
        self,
        ticker: str,
        new_sl: float,
        direction: str = "LONG",
        update_csv: bool = True,
    ) -> bool:
        """
        Remplace le Stop Loss de l'ordre bracket existant.
        """
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest, ReplaceOrderRequest

            client = self._get_client()
            request = GetOrdersRequest(
                status=QueryOrderStatus.OPEN,
                symbols=[ticker],
            )
            open_orders = client.get_orders(filter=request)
            for order in open_orders:
                order_type = str(getattr(order, "type", "")).lower()
                if "stop" in order_type:
                    replacement = ReplaceOrderRequest(stop_price=round(new_sl, 2))
                    client.replace_order_by_id(str(order.id), replacement)
                    logger.info(f"[AlpacaBroker] Bracket SL {ticker} remplacé par {new_sl:.2f}")

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
            return False
        except Exception as exc:
            logger.warning(f"[AlpacaBroker] update_stop_loss {ticker} : {exc}")
            return False

    def sync_fills_from_alpaca(self) -> int:
        """
        Synchronise les positions clôturées automatiquement par Alpaca (bracket SL/TP)
        avec le CSV local.

        Logique :
          - Récupère tous les ordres filled des 7 derniers jours
          - Pour chaque ticker OPEN dans le CSV, vérifie si Alpaca a une position fermée
          - Si Alpaca dit que la position est fermée → met à jour le CSV

        Returns:
            Nombre de positions synchronisées.
        """
        import pandas as pd
        from filelock import FileLock

        synced = 0
        try:
            client = self._get_client()

            # Positions encore ouvertes dans Alpaca + map qty pour partial-fill detect
            alpaca_open_map = {str(p.symbol).upper(): float(p.qty) for p in client.get_all_positions()}
            alpaca_open = set(alpaca_open_map.keys())

            with FileLock(str(CSV_LOCK_PATH), timeout=10):
                ensure_csv_schema(CSV_PATH)
                df = pd.read_csv(CSV_PATH, dtype=str)
                open_mask = df["Status"] == "OPEN"
                csv_open  = df[open_mask]["Ticker"].str.upper().tolist()

                for ticker in csv_open:
                    if ticker in alpaca_open:
                        # Ticker OK côté broker — vérifier Lot 14 : PARTIAL FILL
                        # ou entry qty différente (rare mais possible sur IPO freshly
                        # listed ou liquidité faible). On ajuste le CSV sur la vraie
                        # qty du broker.
                        try:
                            alpaca_qty = alpaca_open_map[ticker]
                            row = df[(df["Ticker"] == ticker) & (df["Status"] == "OPEN")].iloc[0]
                            csv_qty = float(row.get("Size") or 0)
                            if abs(alpaca_qty - csv_qty) >= 1.0:  # différence significative
                                idx = df[(df["Ticker"] == ticker) & (df["Status"] == "OPEN")].index[0]
                                df.at[idx, "Size"] = str(int(alpaca_qty))
                                logger.warning(
                                    f"[AlpacaBroker][Sync] {ticker} partial fill détecté : "
                                    f"CSV={csv_qty} → Alpaca={alpaca_qty}. CSV corrigé."
                                )
                                synced += 1
                        except Exception as exc:
                            logger.debug(f"[AlpacaBroker][Sync] partial-fill check {ticker} : {exc}")
                        continue

                    # Alpaca n'a plus cette position. Deux cas :
                    #   A. Bracket hit (SL/TP fillé) → WIN/LOSS
                    #   B. Ordre d'entrée expired/canceled → CANCELED (jamais ouvert)
                    try:
                        from alpaca.trading.enums import QueryOrderStatus
                        from alpaca.trading.requests import GetOrdersRequest
                        req = GetOrdersRequest(
                            status=QueryOrderStatus.CLOSED,
                            symbols=[ticker],
                            limit=10,
                        )
                        closed_orders = client.get_orders(filter=req)
                        exit_price: float | None = None
                        status_code = "WIN"
                        any_filled = False
                        any_expired = False
                        order_type = ""
                        for o in sorted(closed_orders, key=lambda x: str(x.filled_at or x.submitted_at or ""), reverse=True):
                            o_status = str(getattr(o, "status", "")).lower()
                            if "expired" in o_status or "canceled" in o_status:
                                any_expired = True
                            if o.filled_avg_price and float(o.filled_avg_price) > 0:
                                any_filled = True
                                exit_price = float(o.filled_avg_price)
                                order_type = str(getattr(o, "type", "")).lower()
                                status_code = "LOSS" if "stop" in order_type else "WIN"
                                break

                        idx = df[(df["Ticker"] == ticker) & (df["Status"] == "OPEN")].index[0]

                        # Cas B : aucun fill, uniquement des expired/canceled
                        # → bracket d'entrée jamais exécuté, on marque CANCELED.
                        if not any_filled and any_expired:
                            df.at[idx, "Status"] = "CANCELED"
                            df.at[idx, "Exit_Price"] = df.at[idx, "Entry"]  # PnL=0
                            df.at[idx, "Exit_Date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                            df.at[idx, "Close_Reason"] = "ENTRY_EXPIRED"
                            synced += 1
                            logger.warning(
                                f"[AlpacaBroker][Sync] {ticker} → CANCELED "
                                f"(bracket d'entrée expired/canceled — jamais ouvert)"
                            )
                            continue

                        if exit_price is None:
                            logger.debug(
                                f"[AlpacaBroker][Sync] {ticker} : pas d'order closed "
                                f"ou prix non exploitable, skip."
                            )
                            continue

                        # Raffinement WIN/LOSS réel par direction et P&L.
                        row = df[(df["Ticker"] == ticker) & (df["Status"] == "OPEN")].iloc[0]
                        entry_price = float(row.get("Entry", exit_price))
                        direction   = str(row.get("Direction", "LONG")).upper()
                        if direction == "LONG":
                            status_code = "WIN" if exit_price > entry_price else "LOSS"
                        else:
                            status_code = "WIN" if exit_price < entry_price else "LOSS"

                        # Close_Reason best-effort depuis le type d'ordre broker —
                        # ce chemin réconcilie soit un bracket SL/TP fillé nativement
                        # côté Alpaca, soit une clôture evaluate_trades dont le fill
                        # n'a pas confirmé dans la fenêtre d'attente (cf. close_position).
                        # Dans les deux cas, mieux vaut une raison approximative que
                        # Close_Reason vide (bug corrigé 2026-07-16).
                        if "stop" in order_type:
                            close_reason = "SL_HIT"
                        elif "limit" in order_type:
                            close_reason = "TP_HIT"
                        else:
                            close_reason = "BROKER_SYNC"

                        df.at[idx, "Status"]     = status_code
                        df.at[idx, "Exit_Price"] = str(round(exit_price, 6))
                        df.at[idx, "Exit_Date"]  = datetime.now().strftime("%Y-%m-%d %H:%M")
                        df.at[idx, "Close_Reason"] = close_reason
                        synced += 1
                        logger.info(
                            f"[AlpacaBroker][Sync] {ticker} → {status_code} @ {exit_price:.4f} "
                            f"(bracket fermé par Alpaca, reason={close_reason})"
                        )
                    except Exception as exc:
                        logger.warning(f"[AlpacaBroker][Sync] Erreur {ticker} : {exc}")

                if synced > 0:
                    df.to_csv(CSV_PATH, index=False)

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
        """
        Importe les positions Alpaca actuellement ouvertes dans trade_journal.csv.
        Utilisé par --alpaca-sync pour migrer depuis paper trading.

        Skips les tickers déjà OPEN dans le CSV.
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

            with FileLock(str(CSV_LOCK_PATH), timeout=10):
                ensure_csv_schema(CSV_PATH)
                df = pd.read_csv(CSV_PATH, dtype=str)
                existing_open = set(df[df["Status"] == "OPEN"]["Ticker"].str.upper())

                rows_to_add = []
                for p in alpaca_positions:
                    if p.ticker.upper() in existing_open:
                        logger.info(f"[AlpacaBroker][Import] {p.ticker} déjà OPEN dans CSV — ignoré")
                        continue

                    rows_to_add.append({
                        "Date":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "Ticker":      p.ticker,
                        "Direction":   p.direction,
                        "Entry":       round(p.entry, 4),
                        "Stop_Loss":   "",
                        "Take_Profit": "",
                        "Size":        p.size,
                        "RR":          "",
                        "Status":      "OPEN",
                        "Exit_Price":  "",
                        "Exit_Date":   "",
                        "Order_ID":    f"ALPACA-IMPORT-{p.ticker}",
                    })
                    imported += 1
                    logger.info(f"[AlpacaBroker][Import] {p.ticker} {p.direction} ×{p.size} @ {p.entry:.2f}")

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
