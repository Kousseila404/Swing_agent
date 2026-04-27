"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE RISK — GESTION DU RISQUE V1 (Prop Firm Edition)         ║
║  Calcul du Position Sizing dynamique et du Daily Drawdown        ║
║                                                                  ║
║  Deux fonctions publiques :                                      ║
║    calculate_position_size() — Taille de position basée ATR     ║
║    calculate_drawdown()      — Drawdown intraday en %           ║
╚══════════════════════════════════════════════════════════════════╝

Philosophie Prop Firm :
  - Risque constant de 0.25% du capital par trade, quelle que soit
    la volatilité ou le prix de l'action.
  - Si l'ATR est élevé → distance au SL grande → moins d'actions achetées.
  - Le risque absolu en dollars reste identique ($250 sur 100k$).
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────────
# POSITION SIZING DYNAMIQUE
# ─────────────────────────────────────────────────────────────────

def calculate_position_size(
    entry: float,
    sl: float,
    risk_pct: float,
    current_equity: float,
) -> int:
    """
    Calcule le nombre d'actions à acheter/shorter pour risquer exactement
    `risk_pct` du capital sur ce trade.

    Formule :
        risk_amount   = current_equity × risk_pct
        distance_sl   = |entry − sl|
        position_size = ⌊risk_amount / distance_sl⌋   (arrondi inférieur)

    Garanties :
        - Retourne au minimum 1 (une action).
        - Lève ValueError si distance_sl ≤ 0 (SL impossible ou identique à l'entrée).
        - Lève ValueError si l'un des paramètres est négatif ou nul.

    Args:
        entry:          Prix d'entrée (ex : 150.00 $).
        sl:             Prix du Stop-Loss (ex : 147.00 $).
        risk_pct:       Fraction du capital risquée (ex : 0.0025 = 0.25 %).
        current_equity: Capital total actuel en dollars (ex : 100_000).

    Returns:
        Nombre entier d'actions (≥ 1).

    Raises:
        ValueError: Si les paramètres rendent le calcul impossible.

    Exemples :
        >>> calculate_position_size(150.0, 147.0, 0.0025, 100_000)
        83
        >>> calculate_position_size(5.0, 4.5, 0.0025, 100_000)
        500
        >>> calculate_position_size(1000.0, 990.0, 0.0025, 100_000)
        25
    """
    if current_equity <= 0:
        raise ValueError(f"current_equity doit être > 0, reçu : {current_equity}")
    if risk_pct <= 0 or risk_pct >= 1:
        raise ValueError(f"risk_pct doit être dans ]0, 1[, reçu : {risk_pct}")
    if entry <= 0:
        raise ValueError(f"entry doit être > 0, reçu : {entry}")

    distance_sl = abs(entry - sl)
    if distance_sl <= 0:
        raise ValueError(
            f"distance_sl doit être > 0 (entry={entry}, sl={sl}). "
            "Le Stop-Loss ne peut pas être identique au prix d'entrée."
        )

    risk_amount = current_equity * risk_pct   # ex : 100_000 × 0.0025 = 250 $
    raw_size    = risk_amount / distance_sl   # ex : 250 / 3.0 = 83.33

    # ── Cap absolu : ne jamais allouer plus de 100% du capital ────────
    # Sans ce cap, un ATR très faible (SL ultra-serré) peut produire des
    # tailles astronomiques sur les penny stocks ou actions volatiles.
    max_affordable = int(current_equity / entry)   # shares achetables au prix actuel
    return max(1, min(int(raw_size), max_affordable))


# ─────────────────────────────────────────────────────────────────
# CALCUL DU DAILY DRAWDOWN
# ─────────────────────────────────────────────────────────────────

def calculate_drawdown(current_equity: float, starting_equity: float) -> float:
    """
    Calcule le drawdown intraday en pourcentage (valeur négative si en perte).

    Formule :
        drawdown_pct = (current_equity − starting_equity) / starting_equity × 100

    Args:
        current_equity:  Équité actuelle = Balance + Unrealized_PnL.
        starting_equity: Équité enregistrée au début de la journée (à 00h00).

    Returns:
        Drawdown en % (ex : −3.5 signifie −3.5 %, ex : +1.2 signifie +1.2 %).

    Raises:
        ValueError: Si starting_equity ≤ 0.

    Exemples :
        >>> calculate_drawdown(96_000, 100_000)
        -4.0
        >>> calculate_drawdown(101_500, 100_000)
        1.5
    """
    if starting_equity <= 0:
        raise ValueError(
            f"starting_equity doit être > 0, reçu : {starting_equity}"
        )
    return ((current_equity - starting_equity) / starting_equity) * 100.0


# ─────────────────────────────────────────────────────────────────
# RÉGIME — AJUSTEMENT DU RISQUE PAR TRADE (TIER 1)
# ─────────────────────────────────────────────────────────────────

def regime_adjusted_risk(
    base_risk_pct: float,
    regime: str,
    vix: float = 20.0,
) -> float:
    """
    Ajuste le pourcentage de risque par trade selon le régime macro et le VIX.

    Règles :
    - CRASH_PANIC : 0.0 (aucun trade)
    - BEAR_MARKET : 50% de la taille normale
    - VIX > 25    : 75% de la taille normale
    - VIX > 20    : 90% de la taille normale
    - BULL_MARKET calme (VIX < 20) : 100%
    """
    if regime == "CRASH_PANIC":
        return 0.0
    if regime == "BEAR_MARKET":
        return base_risk_pct * 0.5
    # BULL_MARKET — scale by VIX
    if vix > 25.0:
        return base_risk_pct * 0.75
    if vix > 20.0:
        return base_risk_pct * 0.90
    return base_risk_pct


# ─────────────────────────────────────────────────────────────────
# CIRCUIT BREAKER DRAWDOWN PROGRESSIF (TIER 3)
# ─────────────────────────────────────────────────────────────────

class DrawdownCircuitBreaker:
    """
    Circuit Breaker de drawdown progressif.

    Seuils (vs peak capital) :
    -2% → taille réduite à 75%
    -3% → taille réduite à 50%
    -4% → pause totale 5 jours de trading

    Utilisation :
        cb = DrawdownCircuitBreaker(peak_equity)
        multiplier = cb.get_size_multiplier(current_equity)
        if cb.is_paused():
            # skip new entries
    """

    def __init__(self, peak_equity: float, pause_days: int = 5):
        self.peak_equity = peak_equity
        self.pause_days = pause_days
        self._pause_remaining = 0   # jours de pause restants

    def update(self, current_equity: float) -> None:
        """Met à jour le peak et décrémente le compteur de pause."""
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
        if self._pause_remaining > 0:
            self._pause_remaining -= 1

    def get_size_multiplier(self, current_equity: float) -> float:
        """Retourne le multiplicateur de taille (0.0 si en pause)."""
        if self._pause_remaining > 0:
            return 0.0
        dd_pct = (current_equity - self.peak_equity) / self.peak_equity * 100
        if dd_pct <= -4.0:
            self._pause_remaining = self.pause_days
            return 0.0
        if dd_pct <= -3.0:
            return 0.5
        if dd_pct <= -2.0:
            return 0.75
        return 1.0

    def is_paused(self) -> bool:
        return self._pause_remaining > 0


# ─────────────────────────────────────────────────────────────────
# FILTRE ADTV — LIQUIDITÉ INSTITUTIONNELLE (TIER 1)
# ─────────────────────────────────────────────────────────────────

def apply_adtv_cap(
    position_size: int,
    entry: float,
    avg_volume: float,
    max_adtv_pct: float = 0.05,
) -> int:
    """
    Plafonne la taille de position à max_adtv_pct% du volume journalier moyen en dollars.

    Évite de prendre des positions qui représentent une fraction trop importante
    du volume traité — ce qui causerait du slippage significatif en live.

    Exemple : ADTV = 2M$ → position max = 100,000$ si max_adtv_pct = 0.05
              Si position calculée = 150,000$ → plafonnée à 100,000$

    Args:
        position_size:  Nombre d'actions calculé par risk formula.
        entry:          Prix d'entrée.
        avg_volume:     Volume moyen 20j en nombre d'actions.
        max_adtv_pct:   Fraction max de l'ADTV autorisée (défaut 5%).

    Returns:
        Nombre d'actions plafonné (≥ 1 si position_size > 0).
    """
    if avg_volume <= 0 or entry <= 0 or position_size <= 0:
        return position_size

    adtv_dollars = avg_volume * entry          # Volume journalier en $
    max_dollars  = adtv_dollars * max_adtv_pct # Seuil max position $
    max_shares   = int(max_dollars / entry)    # Conversion en actions

    if max_shares > 0 and position_size > max_shares:
        return max(1, max_shares)

    return position_size


# ─────────────────────────────────────────────────────────────────
# KELLY ROLLING — SIZING DYNAMIQUE SUR N DERNIERS TRADES
# ─────────────────────────────────────────────────────────────────

def kelly_rolling(
    trades_df,   # pd.DataFrame — pandas importé dans la fonction (imports tardifs legacy)
    n_last: int = 30,
    fraction: float = 0.25,
    min_trades: int = 10,
) -> float:
    """
    Calcule la fraction Kelly sur les N derniers trades clôturés.

    Kelly = (WR * RR - (1 - WR)) / RR   (version simplifiée)
    Fraction Kelly = Kelly × fraction  (quarter-Kelly par défaut = conservateur)

    Args:
        trades_df:  DataFrame du journal (colonnes Status, Entry, Exit_Price, Direction).
        n_last:     Nombre de derniers trades à considérer (défaut 30).
        fraction:   Facteur de fractionnement Kelly (défaut 0.25 = quart-Kelly).
        min_trades: Nombre minimum de trades pour un calcul fiable (retourne 0.25% si insuffisant).

    Returns:
        Fraction du capital à risquer par trade (entre 0.001 et 0.01).
        Retourne config.RISK_PER_TRADE si pas assez de trades.
    """
    import config

    default = float(getattr(config, "RISK_PER_TRADE", 0.0025))
    try:
        closed = trades_df[trades_df["Status"].isin(["WIN", "LOSS"])].tail(n_last)
        if len(closed) < min_trades:
            return default

        wins   = closed[closed["Status"] == "WIN"]
        losses = closed[closed["Status"] == "LOSS"]
        if len(losses) == 0:
            return min(default * 2, 0.005)   # Tout WIN — légèrement plus agressif

        win_rate = len(wins) / len(closed)

        # Calcul avg_win% et avg_loss%
        def _pct(row):
            try:
                e = float(row["Entry"])
                x = float(row["Exit_Price"])
                return (x - e) / e if str(row.get("Direction","LONG")).upper() == "LONG" else (e - x) / e
            except Exception:
                return 0.0

        avg_win  = abs(wins.apply(_pct, axis=1).mean())
        avg_loss = abs(losses.apply(_pct, axis=1).mean())
        if avg_loss <= 0:
            return default

        rr = avg_win / avg_loss
        kelly_full = (win_rate * rr - (1 - win_rate)) / rr
        if kelly_full <= 0:
            return max(default * 0.5, 0.001)   # Edge négatif → réduire

        kelly_frac = kelly_full * fraction
        # Bornes de sécurité : min 0.1% / max 1%
        return float(max(0.001, min(kelly_frac, 0.01)))

    except Exception:
        return default


# ─────────────────────────────────────────────────────────────────
# CONCENTRATION SECTORIELLE — max N positions par secteur GICS
# ─────────────────────────────────────────────────────────────────
#
# Source de vérité : data/universe.json, clé `tickers[TICKER].sector`
# (déjà classifié GICS par FMP / yfinance lors du rebuild universe).
# Le mapping est cacheé par mtime pour éviter de relire 500 tickers à
# chaque appel tout en reflètant un rebuild universe sans redémarrage.

from pathlib import Path as _Path

_UNIVERSE_PATH = _Path(__file__).resolve().parent.parent / "data" / "universe.json"
_sector_map_cache: tuple[float, dict[str, str]] | None = None


def _load_ticker_sector_map() -> dict[str, str]:
    """Retourne {TICKER_UPPER: sector_name} depuis universe.json.

    Cache mtime-keyed : re-lit seulement si le fichier a changé. Fail-open
    (retourne {} si fichier absent / corrompu) pour que check_sector_concentration
    ne crash jamais une décision de sizing sur IO locale.
    """
    global _sector_map_cache
    try:
        mtime = _UNIVERSE_PATH.stat().st_mtime
    except OSError:
        return {}
    if _sector_map_cache is not None and _sector_map_cache[0] == mtime:
        return _sector_map_cache[1]

    import json as _json
    try:
        with open(_UNIVERSE_PATH, encoding="utf-8") as fh:
            data = _json.load(fh)
    except (OSError, ValueError):
        return {}
    tickers = data.get("tickers") if isinstance(data, dict) else None
    out: dict[str, str] = {}
    if isinstance(tickers, dict):
        for tk, meta in tickers.items():
            if isinstance(meta, dict):
                sec = meta.get("sector")
                if isinstance(sec, str) and sec:
                    out[str(tk).upper()] = sec
    _sector_map_cache = (mtime, out)
    return out


def check_sector_concentration(
    new_ticker: str,
    open_tickers: list[str],
    max_per_sector: int = 2,
) -> bool:
    """
    Vérifie qu'on ne dépasse pas `max_per_sector` positions dans le même secteur.

    Returns True si OK pour entrer, False si le secteur est saturé.
    Fail-open si ticker inconnu ou si universe.json indisponible
    (ne bloque jamais une décision sur un problème d'IO local).
    """
    sector_map = _load_ticker_sector_map()
    sector = sector_map.get(new_ticker.upper())
    if sector is None:
        return True   # ticker inconnu → on laisse passer

    sector_count = sum(
        1 for t in open_tickers
        if sector_map.get(t.upper()) == sector
    )
    if sector_count >= max_per_sector:
        return False
    return True


# ─────────────────────────────────────────────────────────────────
# TESTS UNITAIRES INTÉGRÉS
# ─────────────────────────────────────────────────────────────────

def _run_tests() -> None:
    """
    Batterie de tests unitaires. Lancé via : python -m modules.risk
    Couvre les cas nominaux et les cas extrêmes (edge cases).
    """
    print("=" * 60)
    print("  modules/risk.py — Tests unitaires")
    print("=" * 60)

    # ── Tests calculate_position_size ────────────────────────────

    # Cas 1 : Trade standard (action à 150$, SL à 3$)
    result = calculate_position_size(150.0, 147.0, 0.0025, 100_000)
    assert result == 83, f"[FAIL] Cas 1 → attendu 83, reçu {result}"
    print(f"  [OK] Cas 1 — Trade standard              : {result} actions")

    # Cas 2 : Action penny (5$ avec SL à 0.50$)
    result = calculate_position_size(5.0, 4.5, 0.0025, 100_000)
    assert result == 500, f"[FAIL] Cas 2 → attendu 500, reçu {result}"
    print(f"  [OK] Cas 2 — Action penny ($5)            : {result} actions")

    # Cas 3 : Action chère (1000$ avec SL à 10$)
    result = calculate_position_size(1000.0, 990.0, 0.0025, 100_000)
    assert result == 25, f"[FAIL] Cas 3 → attendu 25, reçu {result}"
    print(f"  [OK] Cas 3 — Action chère ($1000)         : {result} actions")

    # Cas 4 : SL très serré (0.01$) → raw_size = 24999 mais CAP à equity/price
    # Avec entry=100$, equity=100k$ → max_affordable = 100_000/100 = 1000 shares
    # Le cap protège contre l'allocation > 100% du capital (comportement corrigé V2)
    result = calculate_position_size(100.0, 99.99, 0.0025, 100_000)
    max_affordable = int(100_000 / 100.0)  # 1000
    assert result == max_affordable, f"[FAIL] Cas 4 → attendu {max_affordable} (cap 100%), reçu {result}"
    print(f"  [OK] Cas 4 — SL ultra-serré → cap 100%    : {result} actions (max={max_affordable})")

    # Cas 5 : SL large (20$) → taille minimale
    result = calculate_position_size(200.0, 180.0, 0.0025, 100_000)
    assert result == 12, f"[FAIL] Cas 5 → attendu 12, reçu {result}"
    print(f"  [OK] Cas 5 — SL large ($20)               : {result} actions")

    # Cas 6 : Résultat brut < 1 → retourne 1 (floor minimum)
    result = calculate_position_size(1000.0, 1.0, 0.0025, 100_000)
    assert result == 1, f"[FAIL] Cas 6 → attendu 1 (minimum), reçu {result}"
    print(f"  [OK] Cas 6 — Résultat brut < 1 → floor 1  : {result} action")

    # Cas 7 : SHORT (sl > entry — distance identique au LONG)
    result_long  = calculate_position_size(150.0, 147.0, 0.0025, 100_000)
    result_short = calculate_position_size(147.0, 150.0, 0.0025, 100_000)
    assert result_long == result_short, (
        f"[FAIL] Cas 7 — LONG vs SHORT : {result_long} ≠ {result_short}"
    )
    print(f"  [OK] Cas 7 — LONG == SHORT (abs distance)  : {result_short} actions")

    # Cas 8 : distance_sl = 0 → doit lever ValueError
    try:
        calculate_position_size(150.0, 150.0, 0.0025, 100_000)
        print("  [FAIL] Cas 8 — Aucune exception levée pour SL=Entry !")
    except ValueError as e:
        print(f"  [OK] Cas 8 — ValueError capturée (SL=Entry) : {e}")

    # Cas 9 : capital nul → doit lever ValueError
    try:
        calculate_position_size(150.0, 147.0, 0.0025, 0)
        print("  [FAIL] Cas 9 — Aucune exception levée pour capital=0 !")
    except ValueError as e:
        print(f"  [OK] Cas 9 — ValueError capturée (capital=0) : {e}")

    print()

    # ── Tests calculate_drawdown ─────────────────────────────────

    # Cas 10 : Drawdown exactement à −4% (seuil killswitch)
    dd = calculate_drawdown(96_000, 100_000)
    assert dd == -4.0, f"[FAIL] Cas 10 → attendu -4.0, reçu {dd}"
    print(f"  [OK] Cas 10 — Drawdown −4% exact          : {dd:.2f}%")

    # Cas 11 : Gain intraday → valeur positive
    dd = calculate_drawdown(101_500, 100_000)
    assert dd == 1.5, f"[FAIL] Cas 11 → attendu 1.5, reçu {dd}"
    print(f"  [OK] Cas 11 — Gain intraday +1.5%         : {dd:.2f}%")

    # Cas 12 : Pas de variation (flat) → 0%
    dd = calculate_drawdown(100_000, 100_000)
    assert dd == 0.0, f"[FAIL] Cas 12 → attendu 0.0, reçu {dd}"
    print(f"  [OK] Cas 12 — Journée flat (0%)           : {dd:.2f}%")

    # Cas 13 : starting_equity = 0 → ValueError
    try:
        calculate_drawdown(100_000, 0)
        print("  [FAIL] Cas 13 — Aucune exception pour starting=0 !")
    except ValueError as e:
        print(f"  [OK] Cas 13 — ValueError capturée (starting=0) : {e}")

    print()
    print("  Tous les tests passent ✅")
    print("=" * 60)


if __name__ == "__main__":
    _run_tests()
