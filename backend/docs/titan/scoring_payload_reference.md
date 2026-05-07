# TITAN Scoring — Référence du payload `_score_universe`

Ce document liste l'intégralité des champs retournés par
`modules.sector_metrics._score_universe(tickers_map) → dict[ticker, dict]`.
Source de vérité: `modules/sector_metrics/_scoring.py`.

Champs hérités de l'input (`tickers_map[ticker]`) sont **étoilés** ⭐.

---

## Composite & ranks

| Champ | Type | Description |
|---|---|---|
| `titan_composite_score` | float | Score final 0-100 utilisé pour le ranking. Formule: `clip((Σ w_i × pilier_i + tilt_adjust) × dq_coef, 0, 100)`. |
| `titan_composite_raw` | float | Composite avant DQ-coef (display). Clipé 0-100. |
| `titan_composite_z` | float | Z-score universe-relative (`(score - μ) / σ`). 3 décimales. |
| `titan_composite_sector_pct` | float \| None | Percentile rank intra-secteur (0-100). |
| `titan_composite_sector_z` | float | Z-score intra-secteur. |
| `titan_composite_sector_n` | int | Taille du secteur du ticker. |
| `titan_weight_mode` | str | `"full"` (9 piliers, sum w=1), `"no_sentiment"` (Sentiment exclu, renorm static), `"renormalized"` (≥1 pilier exclu dynamiquement par #17), `"all_empty"` (composite=50). |
| `titan_tilt_adjust` | float | Cumul des tilts ∈ [-8, +8] après cap (#13). |
| `titan_tilt_flags` | list[str] | Sous-ensemble de `["cheap_junk", "falling_knife", "qarp", "garp", "consistent"]`. |

## Piliers (sous-scores 0-100)

| Champ | Pilier | Poids | Composantes brutes |
|---|---|---|---|
| `quality_score` | Quality | 0.18 | ROE, ROA, op_margin, gross_margin (rank intra-secteur) |
| `value_score` | Value | 0.13 | EV/EBITDA (fallback Fwd P/E), FCF yield, PEG, Earnings yield (rank intra-secteur) |
| `risk_score` | Risk | 0.10 | D/E, current_ratio, quick_ratio (rank intra-secteur) |
| `sentiment_score` | Sentiment | 0.03 | recommendation_mean, upside_pct (rank GLOBAL) |
| `momentum_score` | Momentum | 0.15 | 12M-1M return, risk-adjusted return, 52w-high ratio (rank GLOBAL) |
| `piotroski_score` | Piotroski | 0.09 | F-Score normalisé sur `n_max_applicable` (cf. infra) |
| `growth_score` | Growth | 0.13 | revenue_growth, earnings_growth (rank intra-secteur) |
| `revisions_score` | Revisions | 0.11 | upgrades_90d, downgrades_90d, beat_rate, surprise_avg (cf. `modules/revisions_score.py`) |
| `insider_score` | Insider | 0.08 | C-level/director Form 4 transactions 30/90j (cf. `modules/insider_enrich.py`) |

## Piotroski détaillé

| Champ | Type | Description |
|---|---|---|
| `f_score` | int \| None | Nombre de critères passés. None si `n_evaluated < 4` (data sparse → neutral 50). |
| `f_score_max` | int | Maximum applicable: **9** par défaut, **5** Financials (skip f5/f6/f7/f9), **6** Real Estate (skip f6/f7/f9). Cf. [#10](./audit_2026-05-07.md#bug-10). |
| `f_score_breakdown` | dict[str, bool \| None] | 9 keys: `f1_roa_positive`, `f2_ocf_positive`, `f4_ocf_gt_ni`, `f7_current_ratio_gt_1` (absolus), `f3_roa_improved_yoy`, `f5_debt_decreased_yoy`, `f6_current_ratio_improved_yoy`, `f8_no_new_shares_yoy`, `f9_gross_margin_improved_yoy`. `None` = non évaluable ou skipped pour le secteur. |
| `f_score_neutral` | bool (optionnel) | `True` si `n_evaluated < 4` → score=50. |

## Data quality

| Champ | Type | Description |
|---|---|---|
| `data_quality` | float | Fraction des fields `_TITAN_SCORING_FIELDS` non-None sur le jeu applicable au secteur. ∈ [0, 1]. |
| `data_quality_coef` | float | Multiplicateur appliqué au composite final. Formule: `0.92 + 0.08 × data_quality` (cf. [#24](./audit_2026-05-07.md#bug-24)). ∈ [0.92, 1.0]. |
| `pillars_data_count` | dict[str, int] | Densité par pilier: `{"quality": 4, "value": 3, ...}` — nombre de composantes brutes effectivement disponibles (count > 0 = pilier réel, count = 0 = neutral 50 imputé). |
| `n_pillars_neutral` | int | Nombre de piliers avec `count == 0`. |
| `low_signal` | bool | `True` si `n_pillars_neutral ≥ 4` (≥ 4/9 piliers imputés → ranking peu fiable). |

## Diagnostics fraîcheur (cf. [#7](./audit_2026-05-07.md#bug-7), [#8](./audit_2026-05-07.md#bug-8))

| Champ | Type | Description |
|---|---|---|
| `fundamentals_age_days` | float \| None | Âge depuis `fetched_at` (download). Borné à 0 si `fetched_at` futur. 1 décimale. |
| `fundamentals_report_age_days` | float \| None | Âge depuis `fundamentals_period_end` (clôture fiscale Q-latest). Le bon proxy pour la fraîcheur du *signal*, pas du *fetch*. |
| `fundamentals_report_age_days_y1` | float \| None | Âge depuis `fundamentals_period_end_y1`. Critique pour Piotroski Y/Y. |

**Seuils recommandés pour l'aval:**
- `fundamentals_report_age_days > 200` ou `..._y1 > 450` → ticker tagué `report_stale` côté cache, freshness × 0.5 dans `data_confidence`.

## Diagnostics qualité (cf. [#9](./audit_2026-05-07.md#bug-9), [#12](./audit_2026-05-07.md#bug-12), [#20](./audit_2026-05-07.md#bug-20), [#21](./audit_2026-05-07.md#bug-21))

| Champ | Type | Description |
|---|---|---|
| `value_unprofitable_flags` | list[str] | Sous-ensemble de `["ev_ebitda_negative", "forward_pe_negative"]`. Indique des EBITDA ou EPS forward négatifs (turnaround story / pertes structurelles). N'affecte PAS le pilier Value (déjà imputé None). |
| `cross_provider_divergence` | list[str] | Format `["trailing_pe=15.0/22.5", ...]` quand FMP et YF divergent > 5 % sur fields critiques. Vide si pas de fallback déclenché. |
| `structural_gaps` | list[str] | Champs *non-applicables* au secteur (exclus du DQ par design) mais effectivement absents. Ex: une banque sans `debt_to_equity`. Aide à diagnostiquer "sector-aware DQ masque-t-il un trou réel ?". |
| `sector_relative_fallback` | bool | `True` si au moins une métrique sector-relative a basculé en rank global pour ce ticker (secteur < 12 tickers valides). |

## Champs hérités de l'input ⭐

Tous les champs de `tickers_map[ticker]` sont conservés (spread `**t_base`).
Principaux:

| Champ | Source | Usage typique |
|---|---|---|
| `ticker`, `name`, `sector`, `industry`, `country`, `currency`, `exchange` | Identité | Affichage |
| `market_cap`, `current_price`, `avg_volume_3m` | Taille | Sizing, ADTV cap |
| `forward_pe`, `trailing_pe`, `peg_ratio`, `price_to_book`, `dividend_yield`, `beta` | Valuation | Composantes Value |
| `return_on_equity`, `return_on_assets`, `operating_margin`, `profit_margin`, `gross_margin` | Quality | Composantes Q |
| `free_cash_flow`, `operating_cash_flow`, `net_income`, `shares_outstanding` | Cash & equity | Composantes Q + Piotroski |
| `revenue_growth`, `earnings_growth`, `earnings_quarterly_growth` | Growth | Pilier G |
| `upgrades_30d`, `downgrades_30d`, `upgrades_90d`, `downgrades_90d`, `revisions_net_score` | Analystes | Pilier Revisions |
| `earnings_surprise_*`, `earnings_beat_rate_8q`, `next_earnings_date`, `next_earnings_eps_estimate` | Earnings | Pilier Revisions, gate calendrier |
| `payout_ratio`, `dividends_paid`, `five_year_avg_dividend_yield` | Dividende | Dividend Safety scorecard |
| `momentum_return_pct`, `momentum_risk_adjusted`, `momentum_volatility_pct`, `momentum_high_52w_ratio` | Momentum | Pilier M |
| `recommendation_mean`, `recommendation_key`, `price_target_*`, `num_analysts` | Sentiment | Pilier S |
| `insider_*`, `insider_score`, `insider_components` | Insider | Pilier I |
| `debt_to_equity`, `current_ratio`, `quick_ratio`, `ev_to_ebitda`, `ev_to_revenue` | Risk + Value | Composantes R, V, Piotroski |
| `*_prev_year` (ROA, D/E, current_ratio, shares, gross_margin) | Y-1 scrappé yfinance | Piotroski F3/F5/F6/F8/F9 |
| `fundamentals_period_end`, `fundamentals_period_end_y1` | Métadonnées fiscales | Anti-lookahead, freshness |
| `source_provider`, `fetched_at`, `error`, `backfill_fields` | Traçabilité | Audit, data_health |

---

## Cas d'usage typiques

### "Tier 1 — recos high-conviction"
```python
candidates = [
    t for t in scored.values()
    if t["titan_composite_score"] >= 75
    and t["data_quality"] >= 0.85
    and not t.get("low_signal")
    and (t.get("fundamentals_report_age_days") or 0) <= 200
    and not t.get("sector_relative_fallback")
    and not t.get("value_unprofitable_flags")
]
```

### "Détection turnaround stories"
```python
turnarounds = [
    t for t in scored.values()
    if "ev_ebitda_negative" in t.get("value_unprofitable_flags", [])
    and t["titan_composite_score"] >= 60   # malgré l'EBITDA négatif
    and t["momentum_score"] >= 70           # revival confirmé
]
```

### "Diagnostic data integrity"
```python
flagged = [
    t for t in scored.values()
    if t.get("cross_provider_divergence")    # FMP/YF disagree
    or t.get("structural_gaps")              # sector-aware DQ masque
    or (t.get("fundamentals_report_age_days") or 0) > 180
]
```
