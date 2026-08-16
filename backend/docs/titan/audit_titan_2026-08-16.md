# Audit TITAN — edge du score composite (2026-08-16)

**Déclencheur** : discussion utilisateur sur la construction d'un outil pour
automatiser (remplacer) la décision d'achat actuellement humaine, au-delà de
la règle `auto_approve.py` existante (TITAN≥80 nominal, ou [70,80) + support
fort + Piotroski≥7, cap 2/run et 5/semaine). Objectif de cet audit : vérifier
empiriquement si le score TITAN a un edge assez robuste pour justifier plus
d'automatisation, **sans fitter quoi que ce soit** (seulement ~10 trades
clôturés en historique live réel — tout ML dessus serait de l'overfitting).

**Méthode générale** : backtest cross-sectionnel sur l'univers entier
(≠ sur les 10 trades réels), en utilisant `backend/data/.universe_history/`
matérialisé à 361 snapshots hebdomadaires (116 live 2026-04→08 + 245
bootstrappés via `universe_history_bootstrap.py`, fenêtre 2021-08→2026-08).
Ça répond directement au gate posé par [audit_2026-05-07.md#bug-4](./audit_2026-05-07.md#bug-4)
(*"coverage_days ≥ 60 requis pour un backtest production-grade"* — on est
maintenant à ~1780 jours de couverture, mais voir limite lookahead ci-dessous).

**Limite méthodologique majeure, assumée dès le départ** : les snapshots
bootstrappés ont les fondamentaux (Quality/Value/Piotroski/PEG) **figés à
aujourd'hui** — seuls Momentum et `current_price` sont point-in-time propres
sur toute la fenêtre 5 ans (flag `_bootstrap_lookahead=True`). Le score
TITAN composite utilisé dans ce document pour les dates < 2026-04-22 n'est
donc **pas** celui qui aurait été calculé à l'époque. Les résultats sur le
composite doivent être lus comme "qu'est-ce qui distingue les tickers dont
les fondamentaux *actuels* sont bons, rétroactivement" — pas comme un vrai
backtest as-reported. Le pilier Momentum seul, lui, est fiable sur toute la
fenêtre.

---

## Rappel — ce qui était déjà su avant cet audit

Le diagnostic [scoring_ic_diagnostic_2026-06-05.md](./scoring_ic_diagnostic_2026-06-05.md)
(44 jours, avant que le bootstrap existe) avait déjà trouvé, par
Information Coefficient (Spearman score↔rendement forward) :

| Pilier | IC 20j | Lecture |
|---|---|---|
| momentum | +0.119 | seul vrai moteur |
| revisions | +0.075 | le plus stable |
| titan_composite | +0.037 | positif mais **dilué** |
| sentiment | −0.108 | **activement nuisible** |

Et top-20 momentum seul (+6.84%) battait déjà top-20 composite (+4.42%).
Cet audit confirme et **approfondit** cette conclusion avec 8× plus de
couverture temporelle et deux angles nouveaux : la distribution des
rendements (pas seulement leur rang), et la concentration par ticker.

---

## Partie 1 — Le bucket TITAN≥80 a un alpha moyen positif mais très inégal

Backtest cross-sectionnel : retour forward démeané par la moyenne
cross-univers du jour (isole l'alpha du drift de marché ambiant, à la
Fama-MacBeth), buckets par score, horizons 20/60/120j.

| Bucket | N (60j) | alpha moy. | **alpha médian** | win% |
|---|---|---|---|---|
| **TITAN ≥80** | 476 | +15.2% | **+1.3%** | 51.1% |
| [70,80) | 2633 | +2.4% | -1.0% | 47.4% |
| [60,70) | 20890 | +0.6% | -0.4% | 48.3% |
| **<60** | 120270 | -0.2% | -0.9% | 46.6% |

L'écart moyenne/médiane explose avec l'horizon (à 120j : moyenne +65%,
médiane +25%, N=65 seulement — peu fiable mais même pattern). Le win-rate du
bucket ≥80 ne dépasse jamais ~51-61%, jamais franchement différent d'un
pile-ou-face pondéré. Conclusion provisoire (avant Partie 2) : l'edge
apparent vient d'une poignée de gagnants extrêmes, pas d'un hit-rate
supérieur généralisé.

---

## Partie 2 — Concentration : le bucket ≥80 tient sur 2 tickers

**C'est le résultat le plus important de cet audit.** Sur les 5 ans, **9
tickers uniques seulement** ont jamais atteint TITAN≥80, tous horizons
confondus (N=476 observations à 60j, mais réparties sur 9 noms seulement —
chaque nom réapparaît des dizaines de semaines consécutives).

| Ticker | n occurrences | alpha cumulé | % de la somme totale | alpha moy/occurrence |
|---|---|---|---|---|
| **SNDK** | 52 | +4851.8 pts | **67.2%** | +93.3% |
| **MU** | 58 | +3181.2 pts | **44.1%** | +54.9% |
| INCY | 5 | +58.8 pts | 0.8% | +11.8% |
| EXPE | 21 | +33.9 pts | 0.5% | +1.6% |
| CF | 181 | -26.1 pts | -0.4% | -0.1% |
| APP | 4 | -35.9 pts | -0.5% | -9.0% |
| EOG | 28 | -148.1 pts | -2.1% | -5.3% |
| EQT | 14 | -218.2 pts | -3.0% | -15.6% |
| NEM | 113 | -476.8 pts | -6.6% | -4.2% |

**SNDK + MU expliquent à eux seuls 111% de la somme totale des alphas du
bucket** (les autres se compensent globalement en négatif). SNDK
(SanDisk/mémoire flash) et MU (Micron) ont traversé un supercycle mémoire/IA
structurel sur une bonne partie de la fenêtre — leur présence répétée dans
le bucket ≥80 reflète ce cycle sectoriel, pas une compétence de sélection
généralisable du score. Les 181 occurrences de CF (engrais) ou 113 de NEM
(or) — les tickers les plus *fréquents* du bucket — ont un alpha net
quasi-nul ou négatif.

**Implication directe pour la Partie 1** : l'alpha moyen positif du bucket
≥80 n'est quasiment pas un signal de "score qui marche" — c'est
essentiellement "avoir possédé SNDK/MU pendant leur rally". Automatiser
l'achat sur ce score reviendrait à parier que le *prochain* nom qui
atteindra 80+ sera aussi le prochain SNDK — hypothèse non testée et non
testable avec cet historique (2 occurrences structurelles en 5 ans).

---

## Partie 3 — Backtest SL/TP réaliste (pas juste un hold à horizon fixe)

Le système réel n'attend pas un horizon fixe : il sort sur stop-loss ou
take-profit. Simulation avec la formule exacte de
`backend/modules/fundamentals_levels.py::compute_fundamental_levels`
(SL base 30%, TP base 100%, clamps 15-45% / 25-120%, facteurs
Quality/Piotroski/Value/PEG), marche semaine par semaine sur les closes
suivants jusqu'à toucher SL, TP, ou sortie temps (limite testée : 26 et 52
semaines).

**Caveat de granularité** : marche hebdomadaire, pas intraday. Un gap qui
traverse le SL entre deux closes est capturé (on prend le close réel, pas
le seuil théorique) mais la *fréquence* de déclenchement SL/TP intra-semaine
est sous-estimée par rapport à un vrai monitoring quotidien du broker.

| Bucket | N (hold 52sem) | ret moy. | ret médian | win% | SL touché | TP touché | Sortie temps |
|---|---|---|---|---|---|---|---|
| **≥80** | 639 | +28.2% | +6.6% | 57.4% | 3.8% | 14.6% | 81.7% |
| [70,80) | 3387 | +18.0% | +5.2% | 60.9% | 4.6% | 8.7% | 86.7% |
| [60,70) | 26051 | +12.3% | +5.6% | 63.0% | 5.6% | 4.2% | 90.2% |
| <60 | 143978 | +8.2% | +4.4% | 60.4% | 11.2% | 4.3% | 84.5% |

**Deux lectures, pas une seule** :

1. **Lecture brute (dangereuse)** : tous les buckets sont positifs, même
   `<60` (+4.4% médian, 60.4% win-rate) — ce test n'est **pas démeané** par
   le marché. 84-96% des trades sortent par "temps" (ni SL ni TP touché),
   donc ce tableau mesure surtout *"la tendance haussière générale du
   marché sur la fenêtre testée"*, pas la valeur du score. Ne pas lire les
   colonnes en absolu.

2. **Lecture relative (utile)** : à niveau de marché égal (même fenêtre,
   même méthode, comparaison inter-bucket), le score **fait quelque chose
   de réel sur le risque** — le taux de SL touché est monotone et net :
   11.2% pour `<60` vs 3.8% pour `≥80` (÷3). Le score identifie
   correctement les noms qui cassent plus souvent leur stop. Le taux de TP
   touché suit la même logique inversée (4.3% → 14.6%). **Mais** — cf.
   Partie 2 — une bonne partie du delta de TP-hit du bucket ≥80 est
   mécaniquement portée par les mêmes 2 tickers (SNDK/MU) qui, en rally
   quasi continu, touchent leur TP à répétition sur des dizaines
   d'observations hebdomadaires non-indépendantes. Ce test **ne
   valide pas indépendamment** la Partie 1 — il est probablement corrélé
   à la même cause.

---

## Limites non couvertes (assumé faute de temps, pas d'omission silencieuse)

- **Pas de split out-of-sample** (ex: calibrer l'observation sur 2021-2024,
  vérifier sur 2025-2026 seul). Recommandé avant toute décision
  d'automatisation supplémentaire.
- **Pas de démeaning sector-neutral** — un effet rotation sectorielle
  (ex: matières premières/semiconducteurs en 2023-25) n'est pas isolé d'un
  effet stock-picking pur.
- **t-stats non ajustés** (pas de correction Newey-West) — les fenêtres se
  chevauchent (entrées hebdo, horizons 60-365j), donc le N affiché
  surestime largement le nombre d'observations réellement indépendantes.
  Sur le bucket ≥80 en particulier, l'indépendance réelle est proche de
  "2 épisodes structurels" (SNDK, MU), pas des centaines.
- Publication lag fondamentaux non modélisé ici (cf. déjà connu et non
  patché : [audit_2026-05-07.md#bug-5](./audit_2026-05-07.md#bug-5)).

---

## Conclusion

**Pas d'edge robuste et diversifié à automatiser en l'état.** Le signal
positif du bucket ≥80 est réel mais **structurellement concentré sur 2
noms** sur 5 ans de données — ce n'est pas un pattern répétable qu'on peut
industrialiser sans savoir si le "prochain SNDK" existe. Élargir l'auto-
approve ou augmenter `MAX_AUTO_APPROVE_PER_RUN` sur la base de ce signal
serait parier sur une répétition d'un phénomène qui ne s'est produit que 2
fois observées en 5 ans.

### Recommandations, par solidité décroissante

1. **Robuste — ne rien changer côté achat automatique.** Le score ≥80 ne
   justifie pas plus d'automatisation du déclenchement d'achat. Statu quo
   sur `auto_approve.py`.
2. **Robuste — le filtre `<60` reste défendable.** Alpha négatif, SL-hit
   3× plus fréquent que le top bucket, gros N réparti sur beaucoup plus de
   tickers (donc moins vulnérable à l'effet de concentration de la Partie
   2 — non vérifié formellement mais la combinatoire N=143978 sur un
   univers de ~490 tickers l'exclut mécaniquement). Automatiser
   uniquement le *rejet* (pas l'achat) sur ce filtre est la piste la moins
   risquée pour réduire la charge de review humaine.
3. **À surveiller, pas à exploiter** : ajouter un compteur
   `n_unique_tickers_qualifying_80` au monitoring (cf.
   [monitoring_plan.md](./monitoring_plan.md)) — si ce nombre reste
   structurellement bas (proche de 9), c'est un signal que
   l'"auto-approve" continuera de miser sur 1-2 noms à la fois, pas sur un
   portefeuille diversifié. Une bonne alerte anti-sur-confiance.
4. **Si automatisation du sizing est souhaitée malgré tout** : calibrer sur
   la distribution réelle trouvée ici (fortement asymétrique, quelques
   gagnants extrêmes) plutôt qu'une hypothèse gaussienne — mais faire
   d'abord le split out-of-sample listé ci-dessus, sans quoi c'est de
   l'overfitting déguisé en rigueur.
