# TITAN — Plan de monitoring (5 défauts validation + 1 défaut différé)

**Date:** 2026-05-07
**Référence audit:** [audit_2026-05-07.md](./audit_2026-05-07.md)
**Référence patches:** [hardening_2026-05-07.md](./hardening_2026-05-07.md)

L'audit du 2026-05-07 a identifié 24 défauts dans le moteur scoring TITAN. **18
ont été patchés** (Groups A/B/C/D, ~423 lignes modifiées, 884/884 tests verts).
Les 6 restants ne se résolvent pas par du code — ils exigent du temps qui passe
(historique, trades clos, calibration WFO convergente) ou une recalibration
dépendante de ces données. Ce document définit les **gates et dashboards** qui
les transforment en signaux observables jusqu'à ce qu'on puisse les valider
empiriquement.

---

## 1. Pondérations piliers non calibrées (CRITIQUE) — [#1](./audit_2026-05-07.md#bug-1)

**État actuel:** Q22/V18/R13/S18/M18/P11 + tilts cappés ±8. Choix manuels itérés
sur 17 Lots, jamais calibrés OOS.

**Gate à mettre en place:**
- Avant tout changement de poids `_W_TITAN_*`, exiger un IC OOS ≥ 0.03 sur ≥ 60
  jours rolling vs benchmark (random portfolio + composite actuel).
- Bloquer le merge si la nouvelle config a un IC test inférieur à la précédente.

**Dashboard:**
- `/api/wfo` (à étendre) — exposer `weights_history[]` avec `(version, ic_train,
  ic_test, sharpe_oos, n_obs)` pour chaque rebalance.
- Alerte Telegram si `ic_test_30d < 0.0` deux runs consécutifs.

**Sortie de monitoring:** quand 60+ snapshots disponibles + WFO converge (point 2),
calibrer les poids via grid search contraint (Σ=1, w∈[0.05,0.30]) sur Sharpe OOS.

---

## 2. WFO calibration cassée (CRITIQUE) — [#2](./audit_2026-05-07.md#bug-2)

**État actuel:** 1 fold, 5 tickers train, IC_test=0, surfit Risk+Momentum à eux
deux. Run prod-grade (lag=90j) plante "moins de 3 paires".

**Gate à mettre en place:**
- WFO doit produire ≥ 3 folds consécutifs avec `n_train ≥ 100`, `n_test ≥ 50`,
  `ic_test > 0` pour être considéré "converged".
- Si non-converged → l'API retourne les poids statiques avec un flag
  `wfo_calibrated=false` dans `/api/data_health`.

**Dashboard:**
- `/api/wfo/health`: statut converged, dernier run, n_folds_valid, IC distribution.
- Cron: `python -m modules.wfo_monitor` quotidien, log `wfo_history.jsonl`.

**Sortie de monitoring:** quand `wfo_history.jsonl` contient ≥ 30 entrées dont
≥ 25 avec `ic_test > 0.02`, considérer WFO opérationnel. À ce moment, activer
le gate « bloquer rebalance si non-converged ».

---

## 3. Zéro trades clos pour attribution (CRITIQUE) — [#3](./audit_2026-05-07.md#bug-3)

**État actuel:** 5 trades total, 0 clos. `performance_attribution` exige ≥ 20.

**Gate à mettre en place:**
- Compteur `n_closed_trades` exposé dans `/api/portfolio/health`.
- Seuils:
  - `< 20` → mode "preview" (pas d'attribution publiée, banner UI "données
    insuffisantes").
  - `[20, 50]` → attribution avec intervalle de confiance large affiché.
  - `≥ 50` → attribution standard.

**Dashboard:**
- Page `/PerformanceAttributionPage` (nouvelle) — tableau
  Quality/Value/.../Insider avec contribution moyenne, t-stat, p-value.
- Cron: recalcul hebdomadaire.

**Sortie de monitoring:** quand n_closed_trades ≥ 20 ET au moins 6 mois écoulés
depuis le premier trade, lancer `python -m modules.performance_attribution`
pour obtenir la première attribution réelle. Comparer à la spec théorique des
poids piliers.

---

## 4. Historique snapshots < 60 jours (CRITIQUE) — [#4](./audit_2026-05-07.md#bug-4)

**État actuel:** 16 jours d'archives JSON.gz dans `data/universe_history/`.

**Gate à mettre en place:**
- `/api/data_health/universe_history` retourne `n_snapshots`, `oldest_date`,
  `coverage_days`.
- Seuils backtest:
  - `coverage_days < 30` → backtest désactivé (404).
  - `[30, 60]` → backtest actif mais flag `low_history=true`, banner UI.
  - `≥ 60` → backtest "production-grade".

**Dashboard:**
- Section "Universe History" dans DataHealthPage avec barre de progression
  (jours / 365 cible).

**Sortie de monitoring:** rien d'autre à faire que laisser le cron
`scripts/run_titan.sh` accumuler. Cible mid-term: 90 jours pour WFO trimestriel,
365 jours pour backtest "5 ans rolling" théorique.

---

## 5. Look-ahead bias backtest (CRITIQUE) — [#5](./audit_2026-05-07.md#bug-5)

**État actuel:** `publication_lag_days=0` dans backtest CLI. FMP/yfinance ont
T+2 à T+5 réel pour fundamentals, 10-K à T+90 pour annuels.

**Gate à mettre en place:**
- `modules/backtest.py`: passer `publication_lag_days=90` par défaut (déjà
  utilisé pour `_lookup_yoy_snapshot`).
- Refuser tout backtest avec `lag < 30` sans flag explicite `--unsafe-lag`.

**Patch ponctuel à faire (suit ce monitoring):**
- Modifier `modules/backtest.py` argparse pour exiger `--lag-days ≥ 30`.
- Dans la suite de tests, ajouter un test qui vérifie qu'un backtest avec
  `lag=0` plante explicitement avec un message clair.

**Dashboard:**
- Toute sortie de backtest expose `publication_lag_days_used` dans son JSON.
- UI BacktestPage affiche "Look-ahead protection: lag=Xj" en banner.

**Sortie de monitoring:** quand on aura > 90 jours d'historique, refaire un
backtest avec `lag=90` et comparer à l'ancien `lag=0` — l'écart d'alpha donnera
la magnitude du biais (estimé 10–50 bps par l'audit).

---

## 6. Revisions + Insider opaques (IMPORTANT, différé) — [#16](./audit_2026-05-07.md#bug-16)

**État actuel:** 19 % du composite (Revisions 11 % + Insider 8 %) repose sur des
modules à validation faible : `compute_revisions_pillar` et
`enrich_universe_with_insider`. Pas de backtest IC documenté.

**Pourquoi non-patché immédiatement:** réduire ces poids à l'aveugle ferait
perdre du signal sans base empirique. La calibration doit attendre que WFO
converge (point 2) et qu'on ait des trades clos (point 3) pour mesurer la
contribution réelle de ces piliers.

**Plan de re-calibration:**
- Quand `n_closed_trades ≥ 30`, calculer la contribution Revisions et Insider
  à l'alpha empirique (régression cross-section).
- Si contribution non-significative (t-stat < 1.5 sur 30 obs) → réduire poids
  à 5 % chacun.
- Si contribution significative → garder ou augmenter selon IC.

---

## Synthèse — ordre d'observation

| Étape | Délai | Métrique | Gate |
|---|---|---|---|
| 1. Accumuler snapshots | 30j | `n_snapshots ≥ 30` | Backtest activable |
| 2. Backtest avec lag=90 | dès J+30 | `alpha_oos > 0` | Audit corrigé |
| 3. Trades clos | dès J+90 | `n_closed ≥ 20` | Attribution activable |
| 4. WFO convergent | J+60 | `ic_test > 0.02 sur 25 folds` | Calibration weights |
| 5. Re-pondération piliers | J+90 | grid search Sharpe OOS | TITAN v2 calibré |
| 6. Re-calibration Revisions/Insider | J+120 | t-stat contribution sur 30 trades clos | Poids corrigés ou conservés |

**À cet horizon, TITAN passe de "prototype spécification" à "moteur validé"
au sens académique standard.** Avant: tool de filtrage human-in-the-loop
uniquement.
