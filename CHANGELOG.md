# SwingQuant — Changelog

Historique des changements significatifs côté moteur scoring TITAN, providers
de données et orchestrateur portfolio.

Format inspiré de [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased] — branche `claude/titan-hardening-2026-05-07`

### Audit & Hardening TITAN (2026-05-07)

Audit complet du moteur scoring → 24 défauts identifiés, 18 patchés, 6 reportés
au monitoring. Documentation: [`backend/docs/titan/`](./backend/docs/titan/).

#### Added — Diagnostics scoring

Nouveaux champs exposés dans le payload `_score_universe` (consommés par
`/api/universe`, `auto_proposer`, frontend) :

- `fundamentals_report_age_days` — âge depuis `fundamentals_period_end` (Q-latest).
- `fundamentals_report_age_days_y1` — âge depuis `fundamentals_period_end_y1`.
- `value_unprofitable_flags` — `["ev_ebitda_negative"]`, `["forward_pe_negative"]`, ou `[]`.
- `cross_provider_divergence` — `["trailing_pe=15.0/22.5", ...]` quand FMP/YF divergent > 5 %.
- `structural_gaps` — fields non-applicables au secteur mais effectivement absents.
- `sector_relative_fallback` — `True` si fallback global déclenché (secteur < 12 tickers).
- `titan_weight_mode="renormalized"` — nouveau mode quand un pilier est dynamiquement exclu.

Référence: [`scoring_payload_reference.md`](./backend/docs/titan/scoring_payload_reference.md).

#### Added — Gates & validations

- **`fundamentals_cache.py`** — bornes dures `PERIOD_END_MAX_AGE_DAYS=200`,
  `PERIOD_END_Y1_MAX_AGE_DAYS=450`. Tag `report_stale=` propagé via
  `source_provider="/stale_report"`.
- **`auto_proposer.py`** — nouveau gate `_gate_fundamentals_staleness`
  (`FUNDAMENTALS_STALE_MAX_RATIO=0.30`). Bloque les recommandations si > 30 %
  des tickers sont stale.
- **`_scoring.py`** — gate hard `market_cap > 0` ajouté en amont du DQ filter.

#### Changed — Comportement scoring

- **Clipping composite réordonné** ([#6](./backend/docs/titan/audit_2026-05-07.md#bug-6))
  — `clip((composite + tilt) × dq_coef)` au lieu de `clip(composite + tilt) × dq_coef`.
  Préserve les tilts pour DQ < 1.
- **Piotroski sector-aware** ([#10](./backend/docs/titan/audit_2026-05-07.md#bug-10))
  — `f_score_max=5` Financials (skip f5/f6/f7/f9), `=6` Real Estate (skip f6/f7/f9),
  `=9` ailleurs. Effet observable: PRU/AIG/TRV/WFC +3 à +7 pts.
- **Renormalisation dynamique des poids** ([#17](./backend/docs/titan/audit_2026-05-07.md#bug-17))
  — quand un pilier est entièrement vide (count=0), il est exclu et les
  poids des piliers restants sont renormalisés.
- **Tilt cap absolu `±8`** ([#13](./backend/docs/titan/audit_2026-05-07.md#bug-13))
  — empêche l'empilement (LEN/KHC/HPQ remontent +8).
- **Magnitudes tilts atténuées** —
  `cheap_junk: -10 → -7`, `falling_knife: -6 → -4` (atténuation double-counting
  avec piliers Q/V/M).
- **Consistency stricter** — `_CONSISTENCY_MIN_PILLARS_USED: 6 → 7` (bloque
  faux positif sur tickers FMP-only).
- **DQ-coef plus discriminant** — `_DQ_MIN_COEF: 0.95 → 0.92` (8 pts spread
  vs 1.5 pts).

#### Changed — Data providers

- **`FinancialRatios.cross_provider_divergence`** — nouveau champ optionnel.
  Propagé du `FallbackFundamentalProvider._merge` jusqu'au scoring (avant: log
  WARNING uniquement).
- **`UniverseTicker.cross_provider_divergence`** — propagation idem.

#### Tests

- 884/884 passent (baseline et post-patch identiques).
- Aucun test n'a été modifié.
- Voir [`hardening_2026-05-07.md` § Tests](./backend/docs/titan/hardening_2026-05-07.md#tests).

#### Reported — non patchés (6)

Reportés au [`monitoring_plan.md`](./backend/docs/titan/monitoring_plan.md) :

1. Pondérations piliers non calibrées OOS (#1) — exige WFO convergent.
2. WFO calibration cassée (#2) — exige 60+ jours d'historique.
3. Zéro trades clos pour attribution (#3) — exige temps qui passe.
4. Historique snapshots < 60 jours (#4) — exige cron quotidien à accumuler.
5. Look-ahead bias backtest (#5) — patch ponctuel `--lag-days ≥ 30` planifié.
6. Revisions + Insider opaques (#16) — recalibration dépendant de #1-3.

#### Files

```
backend/data_providers/_fallback.py        +9 -3
backend/data_providers/base.py             +6 -0
backend/modules/auto_proposer.py          +73 -0
backend/modules/fundamentals_cache.py     +40 -7
backend/modules/sector_metrics/_scoring.py +362 -64
backend/modules/universe_engine.py         +4 -0
backend/docs/titan/README.md                       (new)
backend/docs/titan/audit_2026-05-07.md             (new)
backend/docs/titan/hardening_2026-05-07.md         (new)
backend/docs/titan/monitoring_plan.md              (new)
backend/docs/titan/scoring_payload_reference.md    (new)
CHANGELOG.md                                       (new)
```
