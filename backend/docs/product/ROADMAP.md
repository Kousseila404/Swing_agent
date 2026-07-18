# SwingQuant TITAN — Ligne directrice produit

Document vivant. On l'update à chaque étape franchie (pas un audit figé).
Objectif : transformer TITAN en vraie appli perso de qualification de signaux
d'investissement LT (inspiration Seeking Alpha), en itérant étape par étape,
avec validation utilisateur avant chaque implémentation.

## Vision

Pas juste un moteur de scoring consultable — une appli qui **qualifie** les
signaux (pourquoi celui-là, pourquoi maintenant) et qui **pousse** l'info
plutôt que d'attendre que l'utilisateur vienne la chercher.

## État des lieux (2026-07-18)

Le logiciel est déjà substantiel, pas un MVP :
- Backend : 63 endpoints, 58 modules (scoring 9 piliers, support score,
  thesis_stop, buy_signal, price_alerts, insider, SEC filings, news,
  sector benchmark, peer comparison, correlation check, HRP weighting…)
- Frontend : 16 pages React 19 + TanStack Query, `TickerAnalysisModal`
  factsheet 11 sections (breakdown TITAN, fundamentals, valuation, quality,
  health, growth, analystes, price action, support, drift, flags)
- Automations existantes : cron quotidien (universe refresh + macro), digest
  Telegram matinal, `titan_alerts`, alertes `thesis_stop` et `price_alerts`
- Historisation des scores en place (`universe_history.ticker_history()`)
  → réutilisable pour tout ce qui touche à la fraîcheur/l'évolution d'un
  signal, pas besoin de nouvelle infra data.

Donc le vrai gap n'est pas "il manque des données ou des features" — c'est
**la qualification et la mise en avant du signal**.

## Diagnostic — les deux frictions remontées

### 1. "L'onglet Proposals n'est pas qualitatif, c'est le même signal tout le temps"

Root cause identifiée dans `auto_proposer.py` (`plan_proposals`, ~L793-798) :

- Le ranking est un **simple tri par `titan_score` décroissant**. Aucune
  notion de fraîcheur, de changement d'état, ou de segmentation par
  conviction.
- L'univers est rescoré par rotation budgétée (~100 tickers/jour, cf. mémoire
  `cron_budget_tuning`) → cycle complet ~5 jours ouvrés. Les scores du top
  bougent donc à peine d'un refresh à l'autre : **les mêmes tickers
  ressortent en tête quasi tous les jours**, ce qui donne l'impression d'un
  signal figé — parce que c'en est un.
- Les signaux qualitatifs existent déjà (verdict `buy_signal`, badge
  `support`, tilt flags QARP/GARP/consistent/cheap-junk/falling-knife,
  `revisions_score`, earnings blackout) mais ils sont :
  - uniquement **affichés**, jamais utilisés pour réordonner ou segmenter ;
  - noyés dans une table dense de 14 colonnes — pas de lecture "pourquoi
    celui-là plutôt qu'un autre" en un coup d'œil.
- Aucun signal de **nouveauté** : un ticker qui vient de passer
  `WATCH → STRONG_BUY` n'est pas distingué d'un ticker en `STRONG_BUY`
  depuis 3 semaines. Le deuxième cas est du bruit répété, le premier est
  l'info qui a de la valeur.

### 2. "Trop long, je veux être plus proactif"

- Flow actuel = 100% pull : ouvrir la page → paramétrer capital/max
  holdings/mode → cliquer "Générer un plan" → lire un tableau dense →
  cocher → approuver.
- Le digest Telegram matinal et `titan_alerts` existent mais sont
  **déconnectés** de ce flow — c'est de l'info passive, pas un call-to-action
  qui ramène directement à la décision.
- Pas de "résumé 3 lignes" avant de plonger dans le détail — l'utilisateur
  doit parcourir toute la table pour juger s'il y a quelque chose de neuf.

## Roadmap — étape par étape

On implémente une étape, on valide en usage réel, puis on passe à la
suivante. Pas de gros refactor big-bang.

### Étape 1 — Qualifier le signal (fraîcheur + segmentation) — PAS COMMENCÉE
Exploite `universe_history` (déjà en place, zéro nouvelle infra) :
- Delta de score / flip de verdict vs N jours en arrière.
- Segmentation en 3 catégories de conviction plutôt qu'un tri plat :
  🔥 Nouveau signal (verdict vient de changer) / ⭐ Confirmé (stable, fort)
  / 👁 Surveillance (proche du seuil, pas encore actionnable).

### Étape 2 — Condenser l'UI Proposals — PAS COMMENCÉE
- Vue "résumé" (3-5 top picks en cartes) au-dessus de la table détaillée,
  table repliable pour les power-users.
- Réduire les colonnes par défaut ; le détail existe déjà via
  `TickerAnalysisModal`, pas besoin de le dupliquer en colonnes.

### Étape 3 — Proactivité réelle — PAS COMMENCÉE
- Le digest Telegram devient un vrai call-to-action (lien direct vers la
  proposition), pas une info à côté.
- Notifications ciblées sur signal **nouveau/changé** uniquement — pas de
  répétition du même score statique jour après jour.

### Étape 4 — Exploration (si besoin après 1-3) — PAS COMMENCÉE
- Screener multi-critères sauvegardable.
- Vue comparaison multi-tickers dédiée (au-delà de `PeerComparison`
  existant, à auditer avant de dupliquer).

## Notes de méthode

- Aucune étape ne touche à la logique trading/risk (gates, killswitch,
  sizing) — c'est strictement de la couche qualification/présentation du
  signal, pas de nouvelle prise de décision automatisée.
- Chaque étape se ferme par un test manuel de l'utilisateur en conditions
  réelles avant de passer à la suivante.
