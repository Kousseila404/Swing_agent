# TITAN — Documentation

Moteur de scoring multi-piliers pour la sélection de tickers long-terme.

## Index

| Document | Quand le lire |
|---|---|
| [audit_2026-05-07.md](./audit_2026-05-07.md) | Source de vérité des 24 défauts identifiés. Lu en priorité quand un commentaire `Bug #N fix (audit 2026-05-07)` apparaît dans le code. |
| [hardening_2026-05-07.md](./hardening_2026-05-07.md) | Détail des 18 patches appliqués. Diff before/after, top-25 ranking shift, distributions. |
| [monitoring_plan.md](./monitoring_plan.md) | Plan pour les 6 défauts non-codables (validation empirique, calibration WFO). Gates et seuils. |
| [scoring_payload_reference.md](./scoring_payload_reference.md) | Référence exhaustive des champs retournés par `_score_universe`. À consulter pour développer un consommateur. |

## Architecture (high-level)

```
universe.json  (raw fundamentals from FMP+YF fallback)
      ↓
modules/sector_metrics/_score_universe()
      ├─ DQ gate (data_quality ≥ 0.70 AND market_cap > 0)
      ├─ Winsorization p1/p99 intra-secteur
      ├─ Percentile ranks (intra-secteur pour Q/V/R/G, global pour S/M)
      ├─ Piliers (Q V R S M P G Rv I) — moyenne des composantes non-None
      ├─ Composite = Σ w_i × pilier_i (renorm dynamique si piliers vides)
      ├─ Tilts ad hoc (cheap_junk, falling_knife, qarp, garp, consistency)
      │   capés à ±_TILT_TOTAL_CAP=8
      ├─ Composite × dq_coef (0.92 + 0.08 × DQ)
      └─ Z-scores universe et sector
      ↓
get_scored_universe()  →  consommé par auto_proposer, portfolio_engine, API
```

## Conventions de pondération

Poids des 9 piliers (Σ = 1.00) — choix manuels itératifs sur 17 Lots, **non
calibrés OOS**. Toute modification doit attendre la convergence WFO (cf.
[monitoring_plan.md §1, §2](./monitoring_plan.md)).

```python
_W_TITAN_QUALITY    = 0.18
_W_TITAN_VALUE      = 0.13
_W_TITAN_RISK       = 0.10
_W_TITAN_SENTIMENT  = 0.03
_W_TITAN_MOMENTUM   = 0.15
_W_TITAN_PIOTROSKI  = 0.09
_W_TITAN_GROWTH     = 0.13
_W_TITAN_REVISIONS  = 0.11
_W_TITAN_INSIDER    = 0.08
```

## Statut empirique

⚠️ **TITAN est en phase prototype-spécification, pas en validation empirique.**

| Indicateur | État | Cible |
|---|---|---|
| Snapshots historiques | 16 j | ≥ 60 j |
| Trades clos | 0 | ≥ 20 |
| WFO convergent | non | ≥ 25 folds avec IC_test > 0.02 |
| Backtest avec lag réaliste | non (`lag=0`) | `lag ≥ 30j` |

Tant que ces 4 jalons ne sont pas atteints, **TITAN doit être utilisé comme
outil de filtrage human-in-the-loop**, pas pour automatiser des allocations.
Détails dans [monitoring_plan.md](./monitoring_plan.md).

## Audit & patches — chronologie

- **2026-05-07** — Audit complet (24 bugs) + 18 patches (Groups A/B/C/D) +
  monitoring plan. Cf. [audit_2026-05-07.md](./audit_2026-05-07.md) et
  [hardening_2026-05-07.md](./hardening_2026-05-07.md).
