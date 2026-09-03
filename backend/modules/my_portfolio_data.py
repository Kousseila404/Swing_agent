"""Portefeuille personnel long terme — 10 positions manuelles.

Allocation statique définie par l'utilisateur, entièrement hors du moteur
TITAN (pas de scoring, pas de proposals, pas de trade_journal). Ces tickers
ne font pas partie de l'univers S&P500 scanné par TITAN — c'est un book
personnel distinct affiché dans une section séparée du dashboard.

`shares` = nombre d'actions réellement détenues (fourni par l'utilisateur,
2026-08-20) → sert de base au calcul du poids réel (prix live × shares)
et donc à la dérive vs poids cible.

`entry_price` = prix d'entrée réel (colonne "Ouvert" eToro, fourni par
l'utilisateur, 2026-08-20) → sert UNIQUEMENT au calcul du P&L (axe
"combien j'ai gagné/perdu"), un tracking distinct et indépendant du
poids/dérive vs allocation cible. `None` = position pas encore ouverte
(LNVGY) → P&L non calculable.

`currency` (défaut "USD") = devise native de `price_ticker` sur son
marché de cotation. Quand ≠ "USD", le routeur convertit le prix ET
`entry_price` avec le taux live du jour (voir get_fx_rate) — les deux
legs du P&L doivent rester dans la même devise, sinon le calcul est
faux. Approximation acceptée : aucun historique FX n'est capturé, donc
`entry_price` (natif) est reconverti au taux ACTUEL plutôt qu'au taux du
jour d'achat — dérive FX résiduelle ignorée (hors scope d'un book perso).

`price_ticker` (défaut = `ticker`) = symbole réellement interrogé pour le
prix live, quand il diffère du ticker d'affichage (ex : ADR US illiquide
sans données yfinance fiables → on interroge la cotation locale).

`shares_per_adr` (défaut 1) = ratio de conversion si `price_ticker` cote
la valeur ordinaire sous-jacente plutôt que l'ADR (ex : LNVGY = 20 actions
ordinaires Lenovo/ADR — Yahoo/Nasdaq).

Les anciens champs texte libres `reason`/`sell_signal` ont été retirés
(2026-09-03) et migrés vers `modules/my_portfolio_thesis.py` — thèse
structurée éditable via `PATCH /api/my_portfolio/{ticker}/thesis`, persistée
dans `data/my_portfolio_thesis.json` (survit aux redéploiements, contrairement
à ce fichier). Migration initiale : `scripts/seed_my_portfolio_thesis.py`.
"""
from __future__ import annotations

POSITIONS: list[dict] = [
    {
        "ticker": "BNP.PA", "target_weight_pct": 15.0, "target_amount": 300.0,
        "shares": 2.338323, "entry_price": 110.64, "beta": 0.36,
        "currency": "EUR",
        "correlation_alert": {"ref": None, "threshold": 0.40, "persist_weeks": 2},
    },
    {
        "ticker": "FMX", "target_weight_pct": 14.0, "target_amount": 280.0,
        "shares": 2.29508, "entry_price": 122.00, "beta": 0.37,
    },
    {
        "ticker": "PSX", "target_weight_pct": 11.5, "target_amount": 230.0,
        "shares": 0.32436, "entry_price": 246.64, "beta": 0.72,
    },
    {
        "ticker": "DRH", "target_weight_pct": 11.0, "target_amount": 220.0,
        "shares": 17.40506, "entry_price": 12.64, "beta": 1.03,
    },
    {
        "ticker": "CNC", "target_weight_pct": 11.0, "target_amount": 220.0,
        "shares": 3.27527, "entry_price": 67.17, "beta": 0.19,
    },
    {
        "ticker": "HRTG", "target_weight_pct": 9.0, "target_amount": 180.0,
        "shares": 5.31915, "entry_price": 33.84, "beta": 0.34,
        "correlation_alert": {"ref": None, "threshold": 0.30, "persist_weeks": 2},
    },
    {
        "ticker": "LNVGY", "target_weight_pct": 7.0, "target_amount": 140.0,
        # Position ouverte 2026-08-21 (post-earnings) : 37.094844 ACTIONS
        # ORDINAIRES 0992.HK détenues directement (relevé eToro), pas des
        # unités ADR LNVGY — donc shares_per_adr=1 (défaut), pas de ×20.
        # (37.094844 × prix 0992.HK / USDHKD ≈ $140.9 ≈ target_amount : le
        # calcul confirme l'hypothèse "actions ordinaires directes".)
        # entry_price natif HKD (exécution 21/08/2026, relevé eToro) — reconverti
        # en USD par le routeur au taux live (même mécanisme que current_price).
        "shares": 37.094844, "entry_price": 29.58, "beta": 0.99,
        # LNVGY (ADR US non sponsorisée) ne remonte pas de prix fiable sur
        # yfinance → on interroge la cotation primaire HKEX (0992.HK), qui
        # est aussi l'instrument réellement détenu ici (voir ci-dessus).
        "price_ticker": "0992.HK", "currency": "HKD",
        "reason": "Conviction WS la plus forte (5.00/5)",
        "sell_signal": "2 trimestres manqués consécutifs",
    },
    {
        "ticker": "MU", "target_weight_pct": 4.5, "target_amount": 90.0,
        "shares": 0.09659, "entry_price": 931.79, "beta": 2.53,
        "reason": "Pari sur contrats NAND prix plancher",
        "sell_signal": "Retour cycle surcapacité classique",
    },
    {
        "ticker": "NUTX", "target_weight_pct": 4.0, "target_amount": 80.0,
        "shares": 0.41824, "entry_price": 191.28, "beta": 1.69,
        "reason": "Décorrélé (max 0.18) malgré beta élevé",
        "sell_signal": "Effondrement revenus arbitrage",
    },
    {
        "ticker": "ERO", "target_weight_pct": 3.0, "target_amount": 60.0,
        "shares": 1.76367, "entry_price": 34.02, "beta": 1.63,
        "reason": "Exposition matériaux/Brésil, réduit (corrélé MU)",
        "sell_signal": "Corrélation MU >0.50",
        "correlation_alert": {"ref": "MU", "threshold": 0.50, "persist_weeks": 1},
    },
]

CASH_RESERVE_PCT    = 10.0
CASH_RESERVE_AMOUNT = 200.0

# Enveloppe totale FIXE du book ($2000 = somme des target_amount + réserve
# cash). C'est le SEUL dénominateur valide pour le poids réel d'une ligne.
#
# Bug corrigé le 2026-08-20 : le poids réel divisait par la somme des
# valeurs *actuellement investies* (variable, ~$1646 quand des positions
# comme PSX/LNVGY ne sont pas encore pleinement déployées). Ça gonflait
# artificiellement le poids réel de TOUTES les autres lignes (MU affichait
# +25.7% de dérive au lieu de +3.5% réel). Calculé, pas codé en dur, pour
# rester cohérent si POSITIONS change.
TOTAL_ENVELOPE_AMOUNT = sum(p["target_amount"] for p in POSITIONS) + CASH_RESERVE_AMOUNT

# Watchlist pure — thèse cassée, aucune position, 0% cible.
WATCHLIST: list[dict] = [
    {"ticker": "SEZL", "target_weight_pct": 0.0, "note": "Thèse cassée, pas détenu"},
]

# Dérive relative (|réel - cible| / cible) au-delà de laquelle on affiche
# l'alerte de rééquilibrage visuel. Ne s'applique qu'aux positions pleinement
# déployées (voir DEPLOYMENT_THRESHOLD_PCT) — sous ce seuil, une dérive est
# juste le signe d'un DCA pas terminé, pas d'un besoin de rééquilibrage.
REBALANCE_DRIFT_THRESHOLD_PCT = 25.0

# En dessous de ce % de déploiement (valeur actuelle / montant cible), une
# position est considérée "en cours de déploiement" plutôt que "dérivée" —
# on affiche une barre de progression au lieu de l'alerte de rééquilibrage.
# LNVGY (0 part) est le cas extrême de cette logique (0% déployé).
DEPLOYMENT_THRESHOLD_PCT = 70.0
