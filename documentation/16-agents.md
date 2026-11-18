# 16 · Agents

Architecture en trois couches (ADR-018). Les agents **observent, diagnostiquent
et recommandent** ; aucun n'écrit de code, de données ou d'ordre.

```
 cron */30 06-22 UTC
 scripts/sentinel_triage.sh
   │
   ├─ 1. modules/sentinel.py  (déterministe, ~2 s)
   │      10 invariants de résultat → data/sentinel/latest.json + history.jsonl
   │      Telegram si l'ensemble des problèmes change (ou 1×/24 h s'il persiste)
   │
   └─ 2. si nouveau CRIT (≤ 2/24 h) : claude -p --agent sq-ops-investigator
          outils : Read/Grep/Glob + Bash allow-listé en lecture (tail, grep, git log…)
          → data/sentinel/triage/<ts>-<empreinte>.md + résumé Telegram

 3. Interactif (Claude Code dans le dépôt) : /sq-ops → dispatch des sous-agents
```

## Sentinel — invariants

| Check | CRIT | WARN |
|---|---|---|
| `proposals_flow` | aucune proposition depuis > 5 j ouvrés, ou refresh en échec sans gate fermé | > 2 j ouvrés |
| `pipeline_daily` | pas de `DONE` depuis 30 h | dernier run « proposals KO », ou ≥ 3 KO / 7 j |
| `api_alive` | API injoignable | réponse > 5 s |
| `oom_kills` | — | API tuée par l'OOM killer (24 h) |
| `tracker_heartbeat` | > 10 min en séance, ou nuclear stop | — |
| `deployment` | — | positions OPEN < 50 % de `BASKET_TOP_N` |
| `equity_sanity` | — | equity == `ACCOUNT_SIZE` (fallback broker silencieux) |
| `universe_fresh` | `universe.json` > 36 h | — |
| `log_errors` | — | Traceback dans les logs (24 h) |
| `host_resources` | disque > 90 % | disque > 85 % ou RAM dispo < 300 Mo |

CLI : `cd backend && ./venv/bin/python -m modules.sentinel [--no-telegram] [--json]`
(code retour 0/1/2 = ok/warn/crit). Désactiver le triage IA : `SENTINEL_AI_TRIAGE=0` dans `backend/.env`.

## Sous-agents (`.claude/agents/`, versionnés)

| Agent | Rôle | Modèle | Droits |
|---|---|---|---|
| `sq-ops-investigator` | cause racine d'un incident sentinel | sonnet | lecture |
| `sq-code-guardian` | revue des commits récents (auto-sync = pas de revue humaine) | sonnet | lecture + pytest |
| `sq-risk-gate` | GO / NO-GO motivé sur les propositions ou un ordre envisagé | sonnet | lecture |
| `sq-quant-validator` | juge statistique de tout « edge » (OOS, t-stat, coûts, biais) | opus | lecture |
| `sq-etoro-analyst` | portefeuille réel eToro ↔ SwingQuant | sonnet | lecture + outils eToro de **lecture** uniquement |

Skill `/sq-ops` : lance le sentinel, dispatche les agents selon les signaux, synthèse ≤ 15 lignes.
