# Recherche « meilleur scoring possible » — 2026-11-20

Six agents : 2 revues de littérature (value/qualité ; momentum/risque/construction),
3 études empiriques (prix 2005-2026 ; fondamentaux SEC point-in-time 2011-2026 ;
fenêtre live 2026), 1 validateur statistique. Résumés chiffrés et scripts versionnés dans [2026-11-20-recherche/](2026-11-20-recherche/README.md)
(données brutes non versionnées, régénérables).

## Résultat

**Aucun score de sélection n'a d'edge démontré une fois le biais de survivance retiré.**

| Source | Value (V15) | Momentum 12-1 sector-z | Rentabilité | Ancien composite |
|---|---|---|---|---|
| Littérature | prime big-cap ≈ 0,6 %/an 1991-2019 (Fama-French 2020) | robuste en large caps (Israel-Moskowitz 2013) | robuste (Novy-Marx 2013, Ball et al. 2016) | — |
| Prix 2005-2026 (top-30 cap 4) | — | +5,6 à +6,7 %/an, t 2,3-3,1 | — | — |
| PIT SEC 2011-2026 (top-30 cap 4) | **−1,9 à −2,9 %/an** | +8,4 %/an (t 2,9) ; 300 plus grosses +3,7 % | IC t 2,2-2,6 (300 plus grosses), pas d'excès panier | — |
| Live 30 sem. 2026 | +4,0 pts (t 0,54) | non mesurable (bug 8M-1M) | ≈ 0 | **−12,7 pts (0ᵉ centile)** |

Biais de survivance mesuré (univers actuel reconstruit vs RSP réel) : **+6,6 pts/an**
(prix) et **+5,5 pts/an** (PIT), +2,4 pts sur les 300 plus grosses. Le validateur
estime qu'il explique l'essentiel de l'edge momentum : pente ≈ 1,5 pt de momentum
par pt de survivance, hors 2024 sur les 300 plus grosses +1,8 %/an (t 0,9).
54 tests PIT → seuil de Bonferroni t ≈ 3,3, jamais atteint.

**Edge plausible restant : momentum, 0 à 2 %/an — non démontré.**

Ce qui ne marche pas (sur cet univers) : value seule (toutes définitions),
filtre qualité/Piotroski (dégrade la value), Piotroski et « ROE stable »
(négatifs), faible volatilité/beta/plus-haut 52 sem. (négatifs, mais biaisés
par la survivance), momentum sectoriel, reversal 1 mois.

## Décision (ADR-022)

Choix utilisateur, recommandation du validateur : **70 % RSP + 30 % satellite
momentum**, mesure honnête plutôt que pari sur un score.

Prochaine mesure utile pour toute recherche de sélection : un univers S&P 500
point-in-time incluant les titres sortis (`modules/delisted.py`) — sans lui,
aucun backtest de sélection du projet n'est interprétable.

## Bugs trouvés en route (corrigés)

- `momentum_return_pct` était un 8M-1M (yfinance « 9mo ») ; 52wH = plus haut 9 mois.
- `dividendYield` yfinance 1.2.0 en %, lu comme fraction (×100 sur petits payeurs).
- Pilier « Risk » = solidité du bilan (D/E, liquidité) ; seuil 40 bloquait 1/3 du panier.
- Univers tronqué 2026-07-14→17 (96 à 370 tickers) dans les snapshots.
