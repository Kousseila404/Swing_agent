# Documentation SwingQuant TITAN

Dossier de référence du logiciel : ce qu'il fait, pourquoi il le fait ainsi,
comment il tourne, et l'historique des décisions. Chaque fichier est
autonome ; le numéro donne l'ordre de lecture recommandé.

| Fichier | Contenu |
|---|---|
| [01-architecture.md](01-architecture.md) | Vue d'ensemble : processus, modules, flux de données, topologie de déploiement |
| [02-strategie.md](02-strategie.md) | La stratégie « basket » : sélection, sizing, rotation, rebalance, stops — et les preuves |
| [03-scoring.md](03-scoring.md) | Le score TITAN : piliers, profils de poids, Scoring Lab, shadow portfolios, ce qui est prouvé ou non |
| [04-execution-broker.md](04-execution-broker.md) | Alpaca : brackets GTC, entrées limites, gate de spread, sweep, ré-armement des stops, réconciliation |
| [05-risque.md](05-risque.md) | Stops catastrophe, trailing, killswitch (freeze), circuit breaker, stop de portefeuille, régime macro |
| [06-operations.md](06-operations.md) | Crons (UTC), run_titan.sh, déploiement, sync git, runbook incidents, Telegram |
| [07-donnees.md](07-donnees.md) | Fichiers de données, schéma du journal, snapshots, caches, providers |
| [08-api.md](08-api.md) | Référence des endpoints (générée depuis OpenAPI) |
| [09-frontend.md](09-frontend.md) | Les 7 pages, conventions React/CSS, comment ajouter une page |
| [10-tests-qualite.md](10-tests-qualite.md) | Suite de tests, garde-fous, lint/typage, CI, comment tester sans toucher la prod |
| [11-decisions.md](11-decisions.md) | Journal des décisions (ADR) : chaque choix structurant, son contexte, ses alternatives |
| [12-historique.md](12-historique.md) | Chronologie du projet, d'avril à septembre 2026 |
| [13-glossaire.md](13-glossaire.md) | Vocabulaire (TITAN, bracket, OCO, HRP, IC, look-ahead…) |
| [14-roadmap.md](14-roadmap.md) | Ce qui reste à faire, par ordre de valeur, avec les critères de « fini » |
| [15-configuration.md](15-configuration.md) | Référence des paramètres (config.py + variables d'environnement) |
| [audits/](audits/) | Rapports d'audit datés (texte intégral) |

Conventions :
- Les dates sont absolues (ISO). « Fenêtre live » = snapshots de scoring
  matérialisés chaque jour depuis le 2026-04-22 (sans look-ahead).
- Un chiffre de performance cite toujours sa fenêtre, sa fréquence de
  rebalance et ses frais. Sans ces trois éléments, il n'est pas comparable.
- Les chemins sont relatifs à la racine du dépôt (`backend/`, `frontend/`).
- Le [CHANGELOG.md](../CHANGELOG.md) à la racine reste le détail commit par
  commit ; ce dossier explique le *pourquoi* et l'état courant.

Mise à jour : quand un comportement change, mettre à jour le fichier
thématique concerné **et** ajouter une entrée dans `11-decisions.md` si le
choix est structurant, dans `12-historique.md` sinon.
