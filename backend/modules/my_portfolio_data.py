"""Portefeuille personnel long terme — 10 positions manuelles.

Allocation statique définie par l'utilisateur, entièrement hors du moteur
TITAN (pas de scoring, pas de proposals, pas de trade_journal). Ces tickers
ne font pas partie de l'univers S&P500 scanné par TITAN — c'est un book
personnel distinct affiché dans une section séparée du dashboard.

`shares` = nombre d'actions réellement détenues (fourni par l'utilisateur,
2026-08-20) → sert de base au calcul du poids réel (prix live × shares)
et donc à la dérive vs poids cible.
"""
from __future__ import annotations

POSITIONS: list[dict] = [
    {
        "ticker": "BNP.PA", "target_weight_pct": 15.0, "target_amount": 300.0,
        "shares": 2.338323, "beta": 0.36,
        "reason": "Stabilisateur, corrélation max 0.22 avec le book",
        "sell_signal": "Beta >0.8 durable ou corrélation >0.40",
    },
    {
        "ticker": "FMX", "target_weight_pct": 14.0, "target_amount": 280.0,
        "shares": 2.29508, "beta": 0.37,
        "reason": "Seule exposition staples + Amérique latine",
        "sell_signal": "Dégradation durable OXXO/Coca volumes",
    },
    {
        "ticker": "PSX", "target_weight_pct": 11.5, "target_amount": 230.0,
        "shares": 0.32436, "beta": 0.72,
        "reason": "Décorrélation historique du marché (raffinage)",
        "sell_signal": "Retournement crack spreads 2-3 trimestres",
    },
    {
        "ticker": "DRH", "target_weight_pct": 11.0, "target_amount": 220.0,
        "shares": 17.40506, "beta": 1.03,
        "reason": "Seule exposition REIT/immobilier",
        "sell_signal": "Chute RevPAR durable, coupe dividende",
    },
    {
        "ticker": "CNC", "target_weight_pct": 11.0, "target_amount": 220.0,
        "shares": 3.27527, "beta": 0.19,
        "reason": "Beta le plus bas du book, quasi non corrélé",
        "sell_signal": "Guidance abaissée 2 trimestres consécutifs",
    },
    {
        "ticker": "HRTG", "target_weight_pct": 9.0, "target_amount": 180.0,
        "shares": 5.31915, "beta": 0.34,
        "reason": "Meilleur diversifiant mesuré (corr +0.037)",
        "sell_signal": "Corrélation moyenne >0.30 durable",
    },
    {
        "ticker": "LNVGY", "target_weight_pct": 7.0, "target_amount": 140.0,
        "shares": 0.0, "beta": 0.99,
        "reason": "Conviction WS la plus forte (5.00/5)",
        "sell_signal": "2 trimestres manqués consécutifs",
        "badge": "⏳ En attente earnings 21/08",
    },
    {
        "ticker": "MU", "target_weight_pct": 4.5, "target_amount": 90.0,
        "shares": 0.09659, "beta": 2.53,
        "reason": "Pari sur contrats NAND prix plancher",
        "sell_signal": "Retour cycle surcapacité classique",
    },
    {
        "ticker": "NUTX", "target_weight_pct": 4.0, "target_amount": 80.0,
        "shares": 0.41824, "beta": 1.69,
        "reason": "Décorrélé (max 0.18) malgré beta élevé",
        "sell_signal": "Effondrement revenus arbitrage",
    },
    {
        "ticker": "ERO", "target_weight_pct": 3.0, "target_amount": 60.0,
        "shares": 1.76367, "beta": 1.63,
        "reason": "Exposition matériaux/Brésil, réduit (corrélé MU)",
        "sell_signal": "Corrélation MU >0.50",
    },
]

CASH_RESERVE_PCT    = 10.0
CASH_RESERVE_AMOUNT = 200.0

# Watchlist pure — thèse cassée, aucune position, 0% cible.
WATCHLIST: list[dict] = [
    {"ticker": "SEZL", "target_weight_pct": 0.0, "note": "Thèse cassée, pas détenu"},
]

# Dérive relative (|réel - cible| / cible) au-delà de laquelle on affiche
# l'alerte de rééquilibrage visuel.
REBALANCE_DRIFT_THRESHOLD_PCT = 25.0
