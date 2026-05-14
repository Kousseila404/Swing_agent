# Conventions — SwingQuant TITAN

Document court à respecter pour tout nouveau code. L'objectif est d'éviter
les bugs de conversion d'unités et les surprises de fuseau horaire qui
coûtent plus cher en debug qu'à imposer en revue.

## 1. Pourcentages

| Contexte | Convention | Exemples |
|----------|------------|----------|
| API HTTP (réponses JSON) | **0-100** explicite | `"titan_composite_score": 82.5`, `"upside_pct": 12.3` |
| Helpers de formatting front | `fmtPctRaw(12.3)` = "12.3%" déjà en % | `fmtSignedPct(-2.1)` = "-2.1%" |
| Helpers fraction front | `fmtPct(0.123)` = "12.3%" attend une fraction | utilisé pour ratios bruts (`win_rate=0.62`) |
| Sizing / pondérations algos backend | **0-1** fractionnel | `MAX_POSITION_PCT = 0.18` (= 18 % du capital) |
| Tickers persistés (CSV, DuckDB) | **0-1** fractionnel | `Last_Alert_Pct: 0.05` |

**Règle d'or :** si une variable s'appelle `*_pct` ou `*_pct_*`, elle est en
0-100. Si elle s'appelle `*_fraction`, `*_ratio` ou `*_weight`, elle est en 0-1.
Tout le reste doit être explicité par un commentaire.

Conversion : ne jamais multiplier/diviser sans commentaire :

```python
# OK
weight_fraction = weight_pct / 100.0  # 18 → 0.18 pour sizing
# Pas OK
w = wp / 100
```

## 2. Dates & horodatages

| Contexte | Convention |
|----------|------------|
| Persistance (CSV/JSON/DuckDB) | **ISO 8601 UTC**, suffixe `Z` ou `+00:00` |
| Timestamps logs | UTC seconds-resolved, `2026-05-14T09:50:48Z` |
| Affichage front (humain) | Locale du navigateur, jamais persisté |
| Dates trading (Earnings_Date, Exit_Date) | `YYYY-MM-DD` UTC sans heure |

**Implémentation Python (3.12+) :**

```python
# ✓ Bon
from datetime import UTC, datetime
ts = datetime.now(UTC).isoformat(timespec="seconds")

# ✗ Mauvais — naïve, deprecated en 3.12
ts = datetime.utcnow().isoformat()

# ✗ Mauvais — local timezone, non sérialisable
ts = datetime.now().isoformat()
```

**Frontend (JS) :**

```js
// Parsing API (UTC string) → Date locale pour affichage
const localDate = new Date(updatedAt);  // "2026-05-14T..." → Date
localDate.toLocaleString('fr-FR', { ... });
```

## 3. Montants monétaires

- Stockage : `float` USD, 4 décimales max (`Entry`, `Stop_Loss`).
- Affichage : `fmtMoney`, `fmtPrice`, `fmtSignedMoney` (jamais de `toFixed` inline).
- Pas de devise multi : tout en USD. Si besoin multi-currency un jour, ajouter
  un champ `currency: "USD"` explicite, pas d'inférence.

## 4. Tickers

- Toujours **uppercase** côté API et persistance (`AAPL`, pas `aapl`).
- Validation : `[A-Z0-9.]{1,8}` (les `.` couvrent BRK.B, BF.B).
- Marché US uniquement (NYSE/Nasdaq/AMEX). Pas de cross-listed (TSX, LSE).

## 5. Logs

- Niveau par défaut prod : `INFO`. `DEBUG` uniquement local.
- Format : utiliser le logger `modules.log.logger` (cf. `modules/log.py`).
- Inclure ticker / context dans le message, pas en stacktrace seul :
  ```python
  logger.warning("[support_score] %s data error (%s): %s", ticker, type(e).__name__, e)
  ```
- Pas de prix live en clair dans les logs prod (PII-ish — peut révéler des
  stratégies si les logs sont shippés vers un tiers).

## 6. Audit trail

Toute mutation passant par un endpoint HTTP doit logger un événement dans
`data/proposals_audit.jsonl` via `proposals.log_mutation_audit()`. Ce trail
capture IP/UA/payload et permet de reconstituer une chronologie utilisateur
lors d'un incident.

## 7. Tests

- Backend : un test par bug fixé (régression). Cible CI 60 %+ coverage.
- Frontend : helpers utils 100 %, hooks > 80 %, pages au cas par cas.
- Pas de network réel dans les tests (mock yfinance via `yf_safe_call` ou
  `unittest.mock.patch`).
