# TITAN Hardening — 2026-05-07

**Branche:** `claude/titan-hardening-2026-05-07`
**Périmètre:** 18 patches sur 24 défauts identifiés dans [audit_2026-05-07.md](./audit_2026-05-07.md). Les 5 défauts de validation et le défaut #16 (Revisions/Insider opaques) sont reportés au [monitoring_plan.md](./monitoring_plan.md).
**Tests:** 884/884 passent (baseline et post-patch identiques).
**Diff:** 6 fichiers, +423/-71 LOC.

---

## Vue d'ensemble par groupe

| Group | Bugs traités | Fichiers touchés | Risque |
|---|---|---|---|
| A — Bugs scoring purs | #6, #7, #8, #9, #11, #19, #20, #22 | `_scoring.py`, `fundamentals_cache.py`, `_fallback.py`, `base.py`, `universe_engine.py`, `auto_proposer.py` | Bas (additif, pas de changement formule) |
| B — Sector adjustments | #10, #12, #21 | `_scoring.py` | Moyen (Piotroski denom change pour Financials/REITs) |
| C — Tilts dedup | #13, #14, #15 | `_scoring.py` | Bas (magnitude réduite + cap) |
| D — NaN/edge cases | #17, #18, #23, #24 | `_scoring.py` | Moyen (renorm dynamique, DQ-coef) |

---

## Détail des patches

### A.6 — Clipping prématuré du composite (cf. [#6](./audit_2026-05-07.md#bug-6))
- **Fichier:** `modules/sector_metrics/_scoring.py:1119-1128`
- **Avant:** `composite = max(0, min(100, composite + tilt_adjust))` puis `× dq_coef`
- **Après:** `composite_weighted = max(0, min(100, (composite + tilt) × dq_coef))`
- **Effet:** Un ticker à 98 + QARP(+5) + DQ=1.0 n'est plus écrêté à 95 mais à 100. Pour DQ<1, la mise à l'échelle préserve les tilts (LEN/KHC/HPQ qui étaient à -16 cumulé sont désormais cappés à -8 par #C.13 ET correctement multipliés par dq_coef avant clip).

### A.7 — Freshness sur period_end (cf. [#7](./audit_2026-05-07.md#bug-7))
- **Fichier:** `modules/sector_metrics/_scoring.py` (nouveau `_parse_period_end_age_days`)
- **Avant:** seul `fundamentals_age_days` (basé sur `fetched_at`) exposé.
- **Après:** ajout `fundamentals_report_age_days` et `fundamentals_report_age_days_y1` calculés vs `fundamentals_period_end[_y1]`. Aval (UI, lt_exit_policy, alerter) peut filtrer sur l'âge réel du rapport fiscal.
- **Champs ajoutés au payload `_score_universe`:** `fundamentals_report_age_days`, `fundamentals_report_age_days_y1`.

### A.8 — Stale fallback bound on period_end (cf. [#8](./audit_2026-05-07.md#bug-8))
- **Fichier:** `modules/fundamentals_cache.py:55,282`
- **Constantes ajoutées:** `PERIOD_END_MAX_AGE_DAYS=200`, `PERIOD_END_Y1_MAX_AGE_DAYS=450`
- **Mécanique:** `_validate_period_end_not_future` étendu — si `period_end > 200j` (ou `period_end_y1 > 450j`), tag `report_stale=...` ajouté à `error` et suffixe `/stale_report` appendu à `source_provider`. La pénalité freshness `data_confidence._freshness_factor × 0.5` se déclenche automatiquement (consomme déjà le motif `/stale`).

### A.9 — EV/EBITDA flag d'unprofitability (cf. [#9](./audit_2026-05-07.md#bug-9))
- **Fichier:** `modules/sector_metrics/_scoring.py:789` (extraction `unprofitable_flags`)
- **Comportement scoring inchangé:** EV/EBITDA et Forward P/E ≤ 0 toujours convertis en `None`.
- **Nouveau champ payload:** `value_unprofitable_flags: list[str]` (`ev_ebitda_negative`, `forward_pe_negative`). Permet à l'UI/auto_proposer de détecter une turnaround story sans en faire un signal Value pourri.

### A.11 — YF circuit breaker (cf. [#11](./audit_2026-05-07.md#bug-11))
- **Couvert par:** A.8 + endpoint existant `/api/data_health` + handler `data_confidence._freshness_factor` qui consomme déjà le motif `/stale`.
- **Aucun nouveau code:** la chaîne est cohérente après #8.

### A.19 — Hard market_cap gate (cf. [#19](./audit_2026-05-07.md#bug-19))
- **Fichier:** `modules/sector_metrics/_scoring.py:760` (filter `_has_valid_mcap`)
- **Avant:** `market_cap` n'était pas dans `_TITAN_SCORING_FIELDS` → un OTC defunct pouvait passer le DQ gate.
- **Après:** filter `dq[k] >= _MIN_DATA_QUALITY AND _has_valid_mcap(row)` — exclusion explicite avant tout calcul de scoring.

### A.20 — Cross-provider divergence propagée (cf. [#20](./audit_2026-05-07.md#bug-20))
- **Fichiers:**
  - `data_providers/base.py:179` — nouveau champ `cross_provider_divergence: list[str] | None` dans `FinancialRatios`
  - `data_providers/_fallback.py:99-136` — `_merge` capture le retour de `_check_divergence` et le pose sur l'objet retourné
  - `modules/universe_engine.py:177` — propagation au modèle interne `UniverseTicker`
  - `modules/sector_metrics/_scoring.py:1198` — exposé dans le payload scored
- **Format:** `["trailing_pe=15.0/22.5", "ev_to_ebitda=12.3/18.1", ...]`. UI peut afficher les divergences > 5 %.

### A.22 — Gate fundamentals freshness intra-ticker (cf. [#22](./audit_2026-05-07.md#bug-22))
- **Fichier:** `modules/auto_proposer.py:231` (nouveau `_gate_fundamentals_staleness`)
- **Constante:** `FUNDAMENTALS_STALE_MAX_RATIO=0.30`
- **Mécanique:** Compte les tickers dont `source_provider` contient `/stale` ou `stale_report`. Si > 30 %, `_gate_fundamentals_staleness` retourne `ok=False` → bloque les recommandations. Branché dans `plan_proposals` après `_gate_universe_freshness`.

### B.10 — Piotroski sector-aware (cf. [#10](./audit_2026-05-07.md#bug-10))
- **Fichier:** `modules/sector_metrics/_scoring.py:452`
- **Mécanique:** Nouveau `_PIOTROSKI_SKIP_CRITERIA_BY_SECTOR`:
  - Financials: skip f5 (D/E baisse), f6 (CR amélio), f7 (CR>1), f9 (gross_margin) → `f_score_max=5`
  - Real Estate: skip f6, f7, f9 → `f_score_max=6`
  - Autres secteurs: `f_score_max=9` (inchangé)
- **Signature de `_piotroski_f_score_absolute` étendue:** retourne maintenant `(n_passed, n_evaluated, n_max_applicable, breakdown)`.
- **Effet ranking:** Financials et REITs gagnent 3-7 pts (PRU, AIG, TRV, WFC, NTRS, AMT, VICI observés). Pas d'impact sur les autres secteurs.

### B.12 — Sector-relative fallback flag (cf. [#12](./audit_2026-05-07.md#bug-12))
- **Fichier:** `modules/sector_metrics/_scoring.py:323` (paramètre `fallback_record`)
- **Mécanique:** `_percentile_rank_by_sector` accepte un set partagé pour collecter les tickers ayant subi un fallback global (intra-secteur < 12 tickers). `_score_universe` agrège sur toutes les métriques sector-relative.
- **Nouveau champ payload:** `sector_relative_fallback: bool`.

### B.21 — Structural gaps diagnostic (cf. [#21](./audit_2026-05-07.md#bug-21))
- **Fichier:** `modules/sector_metrics/_scoring.py:680` (nouveau `_compute_structural_gaps`)
- **Mécanique:** Pour chaque ticker, retourne la liste des fields *non-applicables* au secteur (exclus du DQ) qui sont également *absents* (`None`). Ex: une banque sans `debt_to_equity` (qui est exclu de DQ Financials) verra `["debt_to_equity"]`.
- **Effet:** DQ continue de masquer ces fields (par design — sector-aware S3.x), mais l'UI peut afficher les gaps réels.
- **Nouveau champ payload:** `structural_gaps: list[str]`.

### C.13 — Tilt cap absolu (cf. [#13](./audit_2026-05-07.md#bug-13))
- **Fichier:** `modules/sector_metrics/_scoring.py:181` (constante `_TILT_TOTAL_CAP=8`)
- **Mécanique:** `tilt_adjust = max(-8, min(8, tilt_adjust))` avant ajout au composite.
- **Effet observé:** 8 tickers cappés au tilt limit (avant: -16 cumulé possible).

### C.14 — Falling_knife magnitude réduite (cf. [#14](./audit_2026-05-07.md#bug-14))
- **Fichier:** `modules/sector_metrics/_scoring.py:155`
- **Avant:** `_TILT_FALLING_KNIFE_PENALTY = -6.0`
- **Après:** `_TILT_FALLING_KNIFE_PENALTY = -4.0` (atténuation double-counting avec pilier Momentum 15 %)

### C.15 — Cheap_junk magnitude réduite (cf. [#15](./audit_2026-05-07.md#bug-15))
- **Fichier:** `modules/sector_metrics/_scoring.py:152`
- **Avant:** `_TILT_CHEAP_JUNK_PENALTY = -10.0`
- **Après:** `_TILT_CHEAP_JUNK_PENALTY = -7.0` (atténuation redondance avec piliers Q+V)

### D.17 — Renormalisation dynamique des poids (cf. [#17](./audit_2026-05-07.md#bug-17))
- **Fichier:** `modules/sector_metrics/_scoring.py:1062-1095`
- **Avant:** Si Sentiment absent → renorm statique `_W_TITAN_*_NO_SENTIMENT`. Aucune renorm pour les autres piliers vides.
- **Après:**
  ```python
  pillar_pieces = [(w_i, score_i) for pilier in piliers if count_i > 0]
  weight_total = sum(w for w, _ in pillar_pieces)
  composite = sum(w × s for w, s in pillar_pieces) / weight_total
  ```
- **Effet:** Si Momentum API down → tous les tickers ont `M_count=0` → Momentum exclu du composite, weights des piliers restants renormalisés. Pas de "pull artificiel vers 50".
- **Nouveau weight_mode:** `"renormalized"` (un ou plusieurs piliers exclus dynamiquement) en plus de `"full"`, `"no_sentiment"`, `"all_empty"`.

### D.18 — Consistency stricter (cf. [#18](./audit_2026-05-07.md#bug-18))
- **Fichier:** `modules/sector_metrics/_scoring.py:177`
- **Avant:** `_CONSISTENCY_MIN_PILLARS_USED = 6`
- **Après:** `_CONSISTENCY_MIN_PILLARS_USED = 7`
- **Effet:** Bloque le faux positif "consistency" sur tickers FMP-only (Sentiment + Revisions souvent vides).

### D.23 — IPO Piotroski (cf. [#23](./audit_2026-05-07.md#bug-23))
- **Couvert par B.10:** dénominateur `n_max_applicable` (et non plus 9 fixe) reflète honnêtement le maximum applicable au secteur.
- **Pour IPO non-Financial/REIT:** comportement inchangé. Un ticker avec 4/4 absolu et pas de Y/Y → `score = 4/9 = 44 %`. Représente fidèlement "données partielles passées" sans surinterprétation.

### D.24 — DQ-coef plus discriminant (cf. [#24](./audit_2026-05-07.md#bug-24))
- **Fichier:** `modules/sector_metrics/_scoring.py:143`
- **Avant:** `_DQ_MIN_COEF = 0.95` (1.5 pts spread entre DQ=0.70 et DQ=1.0)
- **Après:** `_DQ_MIN_COEF = 0.92` (8 pts spread)
- **Justification:** assez discriminant pour départager des ex-aequo, sans réintroduire le biais mega-caps de l'ancien `0.50` (15 pts spread).

---

## Top-25 ranking — diff pré/post

```
  rank ticker sector              comp_pre comp_post       Δ   rank_pre  rank_post
    1 NEM    Basic Materials        94.05     92.05   -2.00          1          1
    2 CF     Basic Materials        82.31     82.31   +0.00          2          2
    3 MU     Technology             80.49     80.49   +0.00          4          3
    4 EQT    Energy                 82.02     80.02   -2.00          3          4
    5 INCY   Healthcare             79.37     79.37   +0.00          5          5
    6 LVS    Consumer Cyclical      73.85     73.85   +0.00          6          6
    7 APP    Communication Serv     71.62     71.62   +0.00          7          7
    8 GOOGL  Communication Serv     71.53     71.53   +0.00          8          8
    9 DECK   Consumer Cyclical      70.58     70.58   +0.00          9          9
   10 LRCX   Technology             70.12     70.12   +0.00         10         10
   ...
   18 HST    Real Estate            66.98     67.48   +0.50         21         18
   ...
   25 SNA    Industrials            66.32     66.32   +0.00         26         25
```

**Observations:**
- **Top-10 quasi-stable** : NEM reste #1 (-2 pts via cap tilt), MU passe #4→#3.
- **HST (Real Estate)** remonte #21→#18 grâce au fix Piotroski sector (#10).
- **Aucun nouveau dans le top-10**, aucun ticker éjecté du top-25 sauf rotation marginale.

## Top-15 plus gros shifts composite

| Ticker | Sector | Pre | Post | Δ | Cause |
|---|---|---|---|---|---|
| LEN | Consumer Cyclical | 21.4 | 29.4 | +8.00 | tilt cap (#13) |
| KHC | Consumer Defensive | 30.8 | 38.8 | +8.00 | tilt cap (#13) |
| HPQ | Technology | 17.7 | 25.5 | +7.81 | tilt cap (#13) |
| PRU | Financial Services | 42.9 | 49.9 | +7.00 | Piotroski sectors (#10) |
| CFG | Financial Services | 54.8 | 59.7 | +4.97 | Piotroski sectors (#10) |
| NTRS | Financial Services | 55.2 | 59.3 | +4.13 | Piotroski sectors (#10) |
| AIG | Financial Services | 44.5 | 48.5 | +4.00 | Piotroski sectors (#10) |
| TRV | Financial Services | 56.6 | 60.6 | +4.00 | Piotroski sectors (#10) |
| CINF | Financial Services | 59.0 | 62.5 | +3.50 | Piotroski sectors (#10) |
| WFC | Financial Services | 46.8 | 50.0 | +3.20 | Piotroski sectors (#10) |
| AMT | Real Estate | 56.4 | 59.4 | +3.00 | Piotroski sectors (#10) |
| VICI | Real Estate | 57.9 | 60.9 | +3.00 | Piotroski sectors (#10) |

## Distributions post-patch

```
PIOTROSKI f_score_max distribution:
  f_score_max=5:  69 tickers (Financials)
  f_score_max=6:  29 tickers (Real Estate)
  f_score_max=9: 390 tickers (autres)

sector_relative_fallback=True: 0/488 tickers
value_unprofitable_flags:      0/488 tickers (tous nettoyés en amont par data_validation.sanitize_ratios)

Tilt flags distribution (488 tickers):
  qarp:           15
  garp:           19
  consistent:     30 (avec seuil 7 piliers vs 6 avant)
  falling_knife:  17 (magnitude -4)
  cheap_junk:     11 (magnitude -7)
  Tickers cappés ±8: 8
```

---

## Tests

- **Baseline (avant tout patch):** 884 passed
- **Après Group A:** 884 passed
- **Après Group B (initial):** 5 fails (test_lot8_*) — tests asseraient `f_score_max == 9` codifiant l'ancien comportement
- **Après correction du dénominateur Piotroski:** 884 passed (tests préservés, fix subtil — `n_max_applicable = 9 - len(skip_sector)`, donc pour Tech sans skip → 9, identique à avant)
- **Après Group C, D:** 884 passed

Aucun test n'a dû être modifié.

---

## Champs nouvellement exposés au payload `_score_universe`

| Champ | Type | Source bug | Description |
|---|---|---|---|
| `fundamentals_report_age_days` | float \| None | #7 | Âge du report fiscal (vs `fundamentals_period_end`) |
| `fundamentals_report_age_days_y1` | float \| None | #7 | Âge du report Y-1 (vs `fundamentals_period_end_y1`) |
| `value_unprofitable_flags` | list[str] | #9 | `["ev_ebitda_negative"]`, `["forward_pe_negative"]`, ou `[]` |
| `cross_provider_divergence` | list[str] | #20 | `["trailing_pe=15.0/22.5", ...]` ou `[]` |
| `structural_gaps` | list[str] | #21 | Fields non-applicables au secteur mais effectivement absents |
| `sector_relative_fallback` | bool | #12 | `True` si au moins une métrique a fallbacké en rank global |

Référence complète : [scoring_payload_reference.md](./scoring_payload_reference.md).
