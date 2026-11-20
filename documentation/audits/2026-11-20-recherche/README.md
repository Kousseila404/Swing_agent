# Données de la recherche scoring du 2026-11-20

Synthèse et conclusions : [../2026-11-20-recherche-scoring.md](../2026-11-20-recherche-scoring.md).
Décision : ADR-022 (`documentation/11-decisions.md`).

| Dossier | Contenu | Points clés |
|---|---|---|
| `prix_2005-2026/` | facteurs de prix, univers S&P 500 actuel, rebalance mensuel, top-30 cap 4, 10 bps | `factor_summary.csv`, `factor_yearly.csv`, `survivorship_yearly.csv` (univers reconstruit vs RSP) |
| `pit_sec_2011-2026/` | fondamentaux SEC point-in-time (date de dépôt + 1 j ouvré), value / qualité / combinés | `factor_summary.csv`, `factor_summary_large300.csv` (300 plus grosses), règles PIT dans `scripts/build_pit.py` |
| `live_2026/` | snapshots live 2026-04-22 → 11-20, IC et paniers walk-forward, référence aléatoire | `ic_summary.csv`, `wf_summary.csv` |
| `etude_initiale/` | première étude IC / walk-forward (V15) — contient le snapshot tronqué du 07-15, chiffres corrigés dans `live_2026/` | — |

Reproductibilité : les scripts lisaient/écrivaient dans le scratchpad de session
(`/tmp/claude-1000/.../scratchpad/agent_*`), non conservé ; les données brutes
(prix journaliers 2003-2026, extraits SEC, panels) ne sont pas versionnées
(volume) et se régénèrent avec `download.py` / `scripts/fetch_sec.py`
(User-Agent SEC générique du projet, ≤ 8 req/s). Adapter les chemins en tête de script.

Limite majeure de TOUS ces backtests : univers = composition ACTUELLE du S&P 500
(biais de survivance mesuré +5,5 à +6,6 pts/an). Prochaine brique : univers
point-in-time incluant les titres sortis.
