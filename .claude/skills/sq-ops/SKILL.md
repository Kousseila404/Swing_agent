---
name: sq-ops
description: Point de contrôle SwingQuant — lance le sentinel, puis dispatche les agents sq-* selon ce qui est trouvé (incident → sq-ops-investigator, régression → sq-code-guardian, propositions en attente → sq-risk-gate, portefeuille réel → sq-etoro-analyst, claim d'edge → sq-quant-validator). À utiliser pour « est-ce que tout va bien ? », « point du matin », « check SwingQuant », ou après un incident.
---

# /sq-ops — orchestration des agents SwingQuant

## 1. État factuel (toujours)
```bash
cd /home/swing/swingquant/backend && timeout 120 ./venv/bin/python -m modules.sentinel --no-telegram
```
Lis aussi le dernier triage automatique s'il existe : `ls -t backend/data/sentinel/triage/ | head -1`.

## 2. Dispatch (en parallèle quand c'est indépendant)
| Signal | Agent |
|---|---|
| un check CRIT, ou `pipeline_daily`/`oom_kills` WARN | `sq-ops-investigator` (passe-lui la liste des checks non OK) |
| `log_errors` WARN ou commits non-auto des dernières 48 h | `sq-code-guardian` |
| propositions `pending` dans `backend/data/proposals.json` | `sq-risk-gate` |
| l'utilisateur parle de son vrai portefeuille / eToro | `sq-etoro-analyst` |
| l'utilisateur ou un agent affirme un edge, ou dimanche après `scoring_lab` | `sq-quant-validator` |

Tout vert et rien en attente → ne lance aucun agent, réponds en 3 lignes.

## 3. Synthèse pour l'utilisateur (français, ≤ 15 lignes)
- Une ligne de verdict.
- Les problèmes avec cause racine et correctif proposé, du plus grave au moins grave.
- Ce qui attend une décision humaine.

## Garde-fous
- Les agents sont en lecture seule. Appliquer un correctif = étape séparée, après accord de l'utilisateur,
  avec un test de régression (le dépôt est auto-commité et poussé chaque minute).
- Jamais d'ordre eToro (argent réel). Alpaca est en paper, mais ne passe pas d'ordre non plus.
