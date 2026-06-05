# Diagnostic scoring TITAN — Information Coefficient (2026-06-05)

**Déclencheur** : intuition utilisateur « le scoring est mauvais ». Objectif :
répondre **empiriquement**, sans recalibrer les poids à l'aveugle.

**Outil** : `scripts/backtest_scoring_ic.py` (réutilisable, relançable à mesure
que l'historique grossit).

**Données** : 44 snapshots quotidiens `data/.universe_history/snapshot_*.json.gz`
(2026-04-22 → 2026-06-05), ~489 titres/jour, scores par-pilier + `current_price`.

**Méthode** : Information Coefficient = corrélation de rang (Spearman) entre le
score à `t` et le rendement forward à `t+H`, moyennée sur toutes les dates de
départ. IC>0 = le pilier prédit ; IC≈0 = bruit ; IC<0 = contrarian.

## Résultats (cohérents sur H = 10 / 20 / 30 jours)

IC moyen (horizon 20 j, n=27 fenêtres) :

| Pilier | IC 20j | % fenêtres + | Lecture |
|---|---|---|---|
| momentum | **+0.119** | 78 % | seul vrai moteur |
| revisions | **+0.075** | 100 % | le plus stable |
| piotroski | +0.027 | 78 % | faible+ |
| growth | +0.016 | 81 % | faible+ |
| *titan_composite_score* | *+0.037* | *74 %* | positif mais **dilué** |
| value | −0.010 | 48 % | non-prédictif |
| insider | −0.015 | 18 % | nul/contrarian |
| quality | −0.025 | 41 % | contrarian (ce régime) |
| risk | −0.031 | 11 % | contrarian (ce régime) |
| sentiment | **−0.108** | 19 % | **activement nuisible** |

Rendement forward 20j des top-20 par schéma :

| Schéma | Rdt | vs univers |
|---|---|---|
| momentum seul | +6.84 % | +5.84 pts |
| **composite actuel** | **+4.42 %** | +3.41 pts |
| value seul | +2.44 % | +1.44 pts |
| univers (moyenne) | +1.00 % | — |
| value-gate → quality/growth/momo | +0.53 % | **−0.48 pts** |

## Conclusions

1. **Le composite n'est pas cassé, il est dilué.** IC positif (il classe les
   gagnants au-dessus des perdants) mais il **noie** 2 piliers prédictifs
   (momentum, revisions) sous 4 piliers neutres-à-nuisibles → il fait **moins
   bien que le momentum seul** à chaque horizon. Cohérent avec l'analyse de
   structure : value (13 % de poids) a une influence ~0 sur le classement (annulé
   par corrélation négative à growth/momentum), insider/sentiment quasi-constants.

2. **Un tilt « value » aurait EMPIRÉ les choses.** value IC ~0, value-gate
   dernier (sous le marché). Le fix value initialement envisagé est invalidé par
   la donnée. → « backtest d'abord » a évité une fausse correction.

3. **Sentiment = seul coupable robuste.** IC négatif aux 3 horizons, positif
   dans 0-38 % des fenêtres seulement. 3 % de poids, données SEC éparses.

## Garde-fou méthodologique (à NE PAS oublier)

Ces résultats viennent d'**UNE fenêtre de ~6 semaines en marché BULL**. Que le
momentum domine en jambe haussière est quasi tautologique ; il se fait massacrer
au retournement, où quality/value protègent. **Conclure « tout momentum, supprime
quality/value » serait de l'overfitting de régime qui explose au premier bear.**

## Recommandations (par solidité décroissante)

- **Robuste, faible risque** : neutraliser *sentiment* (3 % poids, IC<0 partout).
- **Le vrai fix = process** : faire tourner cet IC en **rolling** — c'est le job
  du module WFO. Besoin de 60+ jours et de plusieurs régimes avant toute
  recalibration durable. Human-in-the-loop, pas d'auto-allocation.
- **À NE PAS faire** : tilter value (prouvé contre-productif ici), ou
  sur-pondérer momentum en dur (régime-dépendant).
