# 03 · Scoring TITAN

## Piliers

Chaque ticker de l'univers reçoit un score 0–100 par pilier, calculé dans
`backend/modules/sector_metrics/_scoring.py` (rangs cross-section, winsorisés
p1/p99, relatifs au secteur pour Value) :

| Pilier | Champ | Ingrédients principaux | IC 20 j (juin 2026) |
|---|---|---|---|
| Quality | `quality_score` | ROE, marges, ROA, cash-flow | −0,025 |
| Value | `value_score` | Forward P/E, EV/EBITDA, PEG, E/P (sector-relative) | −0,010 |
| Risk | `risk_score` | Beta, dette, liquidité, volatilité | −0,031 |
| Momentum | `momentum_score` | Rendement 6 M ajusté du risque, ratio 52 semaines | **+0,119** |
| Piotroski | `piotroski_score` | F-score 9 critères | +0,027 |
| Growth | `growth_score` | Croissance CA / résultats | +0,016 |
| Revisions | `revisions_score` | Révisions d'analystes 30/90 j | **+0,075** |
| Insider | `insider_score` | Achats/ventes SEC Form 4 | −0,015 |
| Sentiment | `sentiment_score` | Reco analystes + upside | **−0,108** |

Le composite `titan_composite_score` est la moyenne pondérée des piliers
disponibles (renormalisation dynamique si un pilier manque), multipliée par un
coefficient de qualité de données, puis ajustée par des signaux croisés
(QARP/GARP, cheap-junk, falling-knife).

## Profils de poids

Depuis le 2026-09-17, les poids sont un **profil** choisi par la variable
d'environnement `TITAN_WEIGHT_PROFILE` (`WEIGHT_PROFILES` dans `_scoring.py`) :

| Profil | Q | V | R | M | P | G | Rev | Ins | Sent |
|---|---|---|---|---|---|---|---|---|---|
| `v14_1` (historique) | 0,18 | 0,13 | 0,10 | 0,17 | 0,13 | 0,13 | 0,12 | 0,04 | 0 |
| **`equal_7` (actif)** | 1/7 | 1/7 | 1/7 | 1/7 | 1/7 | 1/7 | 1/7 | 0 | 0 |
| `equal_8` | 1/8 ×8 | | | | | | | 1/8 | 0 |
| `momentum_tilt` | 0,14 | 0,10 | 0,08 | 0,30 | 0,13 | 0,10 | 0,15 | 0 | 0 |

Changer de profil = modifier `.env` puis redémarrer l'API (`make redeploy`).
Les crons chargent le profil à chaque exécution. Les snapshots historiques
gardent le composite calculé à leur époque ; le benchmark et le lab
recalculent chaque profil **depuis les piliers stockés** pour rester
comparables.

## Ce qui est prouvé, ce qui ne l'est pas

- **Prouvé sur deux analyses indépendantes** (IC juin 2026 sur 27 fenêtres,
  Scoring Lab septembre sur 21 semaines) : Momentum et Revisions portent
  l'essentiel du signal ; Sentiment et Insider sont nuls ou nuisibles.
- **Observé sur la fenêtre live seulement** : `equal_7` bat V14.1 de 16 points
  et l'univers équipondéré de 20 points. Quality seul et Piotroski seul
  perdent de l'argent sur cette période. Ces chiffres ne sont pas
  statistiquement significatifs (21 points).
- **Non prouvé** : la persistance de l'edge en marché baissier (la fenêtre
  est un bull market) ; la valeur ajoutée des ajustements croisés ; le
  bénéfice des filtres de régime (variantes « regime » du lab, calculées sur
  5 ans avec le pilier Momentum seul, le seul point-in-time propre sur les
  snapshots bootstrappés).

## Scoring Lab

`python -m modules.scoring_lab` (dimanche via `run_titan.sh`, ~2 min grâce au
cache de snapshots) écrit `data/.scoring_lab.json`, lu par `GET
/api/scoring/lab` et la page **Scoring Lab**. Groupes : tailles de panier,
références (univers EW, bottom-20), piliers seuls, profils, régime. Chaque
ligne : rendement, hit, max DD, Sharpe hebdo annualisé, pire semaine, nombre
de périodes, écart vs univers et vs top-20. Le verdict qualifie l'edge du
composite vs univers : `strong` ≥ 8 pts, `weak` ≥ 2, sinon `none`.

## Shadow portfolios (forward-test A/B)

`modules/shadow_portfolios.py` (quotidien dans `run_titan.sh`) gère cinq
portefeuilles virtuels — `v14_1`, `equal_7`, `momentum_tilt`,
`momentum_only`, `universe_ew` — avec les règles live (top-20, 1/N, rotation
hebdo, 10 bps), valorisés chaque jour aux prix de l'univers. NAV et
statistiques dans `GET /api/shadow` et le Cockpit. **Règle de décision** :
on ne change de profil actif que si un autre profil bat `equal_7` sur au
moins 3 mois de shadow, avec un drawdown comparable, et si le Scoring Lab
confirme sur la fenêtre live.

Pour un vrai second compte paper Alpaca : dupliquer `backend/.env` avec les
clés B, `STRATEGY_MODE=basket`, `TITAN_WEIGHT_PROFILE=<profil>`, et lancer une
seconde instance (autre port, autre `data/`). Le shadow interne suffit pour
la comparaison de scoring ; le second compte n'apporte que le réalisme
d'exécution.

## Diagnostics et garde-fous

- `backend/docs/titan/` : audits scoring (2026-05-07, 2026-06-05, 2026-08-16),
  plan de monitoring, référence du payload.
- WFO (`wfo_monitor`, mensuel) : purement informatif (train 30 j = overfit
  garanti) ; ne pilote rien.
- `data_confidence` : coverage × fraîcheur × sanité par ticker ; STRONG_BUY
  exige ≥ 60.
