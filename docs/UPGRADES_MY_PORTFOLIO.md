# Spec technique — 4 upgrades "Mon Portefeuille"

Document de spécification, pas d'implémentation. Objectif : poser le problème,
l'existant réutilisable, le découpage fichiers/données/calcul/affichage, et les
cas limites, pour permettre un découpage en tâches indépendant.

Contexte commun : `backend/modules/my_portfolio_data.py` (10 positions
statiques, hors univers TITAN) + `backend/routers/my_portfolio.py`
(`GET /api/my_portfolio`) + `frontend/src/components/MyPortfolioPage.jsx`.
Depuis le fix de prix du 2026-08-21, chaque position porte déjà
`price_ticker` / `currency` / `shares_per_adr` (résolution d'alias +
conversion devise via `modules/tracker/market.get_fx_rate` /
`routers/my_portfolio._usd_multiplier`) — ce mécanisme est un bloc réutilisable
transversal aux 4 upgrades ci-dessous, pas seulement au prix courant.

---

## Upgrade 1 — Moteur de risque en tâche de fond (PRIORITÉ HAUTE)

### Problème résolu

3 des 10 `sell_signal` du book référencent des métriques jamais calculées :

| Ticker | Seuil décoratif |
|---|---|
| BNP.PA | "Beta >0.8 durable **ou corrélation >0.40**" |
| HRTG | "Corrélation **moyenne** >0.30 durable" |
| ERO | "Corrélation **MU** >0.50" |

Ces seuils ne se déclenchent jamais : rien dans le backend ne calcule de
corrélation ni de beta recalculé. `beta` dans `my_portfolio_data.py` est une
valeur figée saisie à la main (jamais revalidée).

### Existant réutilisable

- **`backend/modules/correlation_check.py`** — calcule déjà une matrice de
  corrélation Pearson sur log-returns (`_to_returns`, `df.corr(min_periods=...)`),
  une corrélation moyenne par ticker (`per_ticker_avg`), et un flag
  `over_correlated` au-delà d'un seuil. Conçu pour le panier TITAN (fenêtre
  60j) mais la logique (returns → corr matrix → moyenne par ticker) se porte
  telle quelle sur 10 tickers avec une fenêtre 2 ans. **Ne PAS réécrire cette
  logique — l'importer/paramétrer.**
- **`backend/modules/portfolio/_hrp.py`** — `_correlation_distance`,
  clustering scipy (`scipy.cluster.hierarchy.linkage`), matrice de covariance
  pandas déjà construite (`df.cov(min_periods=...)`) : utile pour le ratio de
  diversification (a besoin de la matrice Σ complète, pas juste des moyennes).
- **`backend/modules/backtest.py::_capm_alpha_beta`** (ligne ~571) — régression
  OLS pure Python `β = cov(x,y)/var(x)` déjà écrite et testée pour le
  portefeuille TITAN vs benchmark. Le terme `risk_free_rate_annual` s'annule
  mathématiquement dans le calcul du β seul (un décalage constant ne change ni
  covariance ni variance) — on peut réutiliser une version simplifiée sans
  RF, ou l'appeler telle quelle et ignorer `alpha_*`.
- **`backend/main.py`** — pattern CLI déjà établi (`argparse` + flags type
  `--health-check`, `--alpaca-sync`, `--daily-digest`) pour les jobs cron
  one-shot. Un nouveau flag `--recompute-portfolio-risk` suit ce même moule.
- **`backend/scripts/git_auto_sync.sh` / crontab actuel** — le pattern de job
  hebdomadaire existe déjà ("Ligne 3 : Rafraîchissement du cache marché,
  chaque dimanche 22h" dans le crontab) : même créneau réutilisable.
- **Script standalone mentionné par l'utilisateur** (yfinance + scipy, calcul
  local de matrice de corrélation / régression beta / Risk Parity) :
  **introuvable sur ce dépôt/VPS** (recherché dans `backend/scripts/`, `~/`,
  git history — absent). À traiter comme référence de méthode côté
  utilisateur, pas comme code à importer : `correlation_check.py` +
  `_hrp.py` + `_capm_alpha_beta` couvrent déjà les 3 briques qu'il décrit
  (corrélation, beta, risk parity) côté serveur.
- **`modules/tracker/market.py`** — pas de `MarketDataProviderBase` branché
  sur `my_portfolio` (contrairement au moteur TITAN) ; le plus simple est de
  fetcher l'historique 2 ans directement via `yf.Ticker(price_ticker).history(period="2y")`,
  dans le même style que `get_current_price_detailed`, plutôt que de câbler
  l'abstraction `MarketDataProviderBase` pour 10 tickers + `^GSPC`.

### Fichiers à modifier ou créer

- **Créer** `backend/modules/portfolio_risk.py` — calcul batch : fetch
  historique 2 ans (10 tickers `price_ticker` + benchmark `^GSPC`), log-returns,
  matrice de corrélation, beta par ticker, agrégats portefeuille. Porte la
  logique de `correlation_check.py`/`_hrp.py`/`_capm_alpha_beta`, paramétrée
  pour ce book (fenêtre 2 ans au lieu de 60j).
- **Créer** `backend/data/my_portfolio_risk.json` — état persisté (pattern
  `equity_state.json`/`macro_state.json`) : dernier snapshot calculé + date.
- **Modifier** `backend/main.py` — ajouter le flag `--recompute-portfolio-risk`.
- **Modifier** `backend/routers/my_portfolio.py` — lire `my_portfolio_risk.json`,
  merger `beta_recalculated`/`avg_correlation`/flags dans chaque row, ajouter
  le bloc `risk_snapshot` au niveau racine de la réponse.
- **Modifier** `backend/modules/my_portfolio_data.py` — remplacer le texte
  libre `sell_signal` par des champs structurés machine-lisibles pour les 3
  lignes concernées (voir "Nouveaux champs").
- **Modifier** crontab — nouvelle ligne hebdomadaire (ex. dimanche 22h30,
  juste après le refresh cache marché existant).
- **Modifier** `frontend/src/components/MyPortfolioPage.jsx` + `index.css` —
  bandeau risque + colonne beta recalculé + highlight signal de vente.

### Nouveaux champs de données

Dans `my_portfolio_data.py`, par position concernée (au lieu de parser le
texte `sell_signal`) :

```python
"correlation_alert": {
    "ref": None,        # None = corrélation moyenne vs le book ; "MU" = vs un ticker précis
    "threshold": 0.40,
    "persist_weeks": 2, # nombre de recalculs hebdo consécutifs au-dessus du seuil avant déclenchement
},
```

- BNP.PA : `{"ref": None, "threshold": 0.40, "persist_weeks": 2}` (le volet
  beta ">0.8 durable" reste géré séparément via `beta_recalculated`).
- HRTG : `{"ref": None, "threshold": 0.30, "persist_weeks": 2}` ("durable").
- ERO : `{"ref": "MU", "threshold": 0.50, "persist_weeks": 1}` (pas de mot
  "durable" dans le texte original → déclenchement immédiat).

Dans `my_portfolio_risk.json` / réponse API, par ticker :

```json
{
  "beta_recalculated": 0.42,
  "beta_diff_pct": 16.7,
  "beta_flag": false,
  "avg_correlation": 0.09,
  "correlation_vs_ref": null,
  "correlation_alert_triggered": false,
  "correlation_streak_weeks": 0,
  "data_quality": "ok"
}
```

Bloc racine `risk_snapshot` :

```json
{
  "portfolio_beta": 0.653,
  "avg_weighted_correlation": 0.11,
  "diversification_ratio": 2.15,
  "most_correlated_pairs": [
    {"a": "MU", "b": "ERO", "corr": 0.43},
    {"a": "PSX", "b": "DRH", "corr": 0.36}
  ],
  "last_recalc_date": "2026-08-17",
  "next_recalc_date": "2026-08-24",
  "n_tickers_ok": 10,
  "n_tickers_missing": 0
}
```

### Logique de calcul

1. **Historique** : `yf.Ticker(price_ticker).history(period="2y")["Close"]`
   pour chaque position + `^GSPC`. Log-returns via `np.log(s/s.shift(1))`
   (identique à `correlation_check._to_returns`).
2. **Beta par ticker** : régression sur la série de returns du ticker vs
   `^GSPC` alignée (dates communes uniquement) : `β = cov(r_t, r_bench) / var(r_bench)`.
   `beta_diff_pct = (beta_recalculated - beta_statique) / beta_statique × 100`.
   `beta_flag = abs(beta_diff_pct) > 30`.
3. **Corrélation** : `df.corr(min_periods=20)` sur les 10 séries de returns
   alignées (réutilise `correlation_check.compute_correlation`, `window_days=504`
   ≈ 2 ans de jours de bourse). `avg_correlation[t]` = moyenne de la ligne `t`
   hors diagonale (déjà fait par `per_ticker_avg`).
4. **Devise des séries** : calculer les returns sur les prix **convertis en
   USD** (post-FX), pas sur les prix natifs. Le risque de change fait partie
   du risque réellement subi par l'investisseur — un log-return EUR de BNP.PA
   ignorerait la volatilité EUR/USD à laquelle le book est réellement exposé.
5. **Beta portefeuille pondéré** : `Σ wᵢ × βᵢ` avec `wᵢ = real_weight_pct/100`
   (cash traité comme β=0, poids `cash_weight`).
6. **Corrélation moyenne pondérée** : `Σ_{i≠j} wᵢwⱼρᵢⱼ / Σ_{i≠j} wᵢwⱼ`.
7. **Ratio de diversification** : `DR = (Σ wᵢσᵢ) / sqrt(wᵀΣw)` avec `Σ` la
   matrice de covariance (déjà construite façon `_hrp.py`) et `σᵢ` l'écart-type
   annualisé de chaque ticker.
8. **Persistance "durable"** : réutiliser le pattern *streak* déjà existant
   côté `modules/proposals.py` (`recurrence_streaks()` — mémoire projet
   2026-08-14) : à chaque run hebdo, si `avg_correlation` (ou
   `correlation_vs_ref`) dépasse le seuil, incrémenter
   `correlation_streak_weeks`, sinon le remettre à 0. Déclenchement quand
   `correlation_streak_weeks >= persist_weeks`.

### Affichage frontend attendu

- **Colonne Beta** : afficher le beta statique existant + `βʳᵉᶜᵃˡᶜ` en petit à
  côté, avec un badge ⚠ (réutiliser la classe `.mp-stale`/`.mp-drift-alert`)
  si `beta_flag=true`. Tooltip : "Beta recalculé sur 2 ans vs S&P500, écart
  de X% avec le beta déclaré."
- **Colonne "Signal de vente"** : si `correlation_alert_triggered=true`,
  surligner la ligne (nouvelle classe `.mp-row-risk-alert`, même famille que
  `.mp-row-alert` déjà utilisée pour `rebalance_alert`) et préfixer le texte
  du signal par 🔴.
- **Nouveau bandeau** au-dessus du tableau (à côté ou sous `.mp-tiles`) :
  4 tuiles — Beta portefeuille / Corrélation moyenne pondérée / Ratio de
  diversification / "Dernier recalcul : 17/08/2026" (utiliser le même
  `fmtTimeAgo` déjà construit pour le prix, appliqué à `last_recalc_date`).
- **Paires les plus corrélées** : petite liste (top 3-5) sous le bandeau ou
  dans un tooltip du bandeau, ex. "MU ↔ ERO : 0.43".

### Cas limites à gérer

- **Historique < 2 ans** (nouvelle ligne récente) → `data_quality: "insufficient_history"`,
  ne pas afficher de beta/corrélation halluciné ; réutiliser le pattern
  fail-open de `correlation_check.py` (`applied=False` + `reason`).
- **0992.HK vs `^GSPC`** : calendriers de bourse différents (HKEX vs NYSE) →
  dates non alignées, corrélation intrinsèquement bruitée sur un ticker
  asiatique vs un indice US. Documenter la limite dans le tooltip UI plutôt
  que de la masquer ("corrélation calculée sur jours de bourse communs
  US/HK, échantillon réduit").
- **Échec du job hebdo** (yfinance down, etc.) → ne PAS écraser
  `my_portfolio_risk.json` avec un état vide ; conserver le dernier snapshot
  valide et laisser `last_recalc_date` visiblement daté (même principe que le
  fix `price_as_of` de cette session — la staleness doit être visible, pas
  masquée).
- **"Durable" mal calibré** : si `persist_weeks` est trop bas, un pic de
  corrélation d'une semaine (ex. panique de marché généralisée) déclenche
  une fausse alerte — d'où le paramètre `persist_weeks` par ligne plutôt
  qu'une constante globale.
- **BNP.PA — signal composite** : le texte original est "Beta >0.8 **ou**
  corrélation >0.40" (OR, pas AND) → `correlation_alert_triggered` et
  `beta_flag` doivent chacun pouvoir déclencher indépendamment le badge rouge.

---

## Upgrade 2 — Calendrier d'earnings automatique

### Problème résolu

`"badge": "⏳ En attente earnings 21/08"` était une chaîne codée en dur dans
`my_portfolio_data.py`, et fausse : l'earnings a eu lieu le 12/08. Rien ne
revalide ni ne retire ce badge — il est resté affiché >1 semaine après
publication sans aucun signal d'incohérence. (Ce badge vient d'ailleurs
d'être retiré manuellement lors du fix du share count LNVGY du 2026-08-21 —
cet upgrade évite de recréer le même trou.)

### Existant réutilisable

- **`backend/data_providers/finnhub_provider.py`** (ligne ~297-319) —
  interroge déjà `/calendar/earnings` par ticker, avec le bug de tri déjà
  corrigé ("Finnhub renvoie les entrées triées date DESCENDANTE... bug
  confirmé en prod : 479/489 tickers avec next_earnings_date à 250-680j
  d'écart" — fix : `min(dated, key=lambda e: e["date"])`). Directement
  réutilisable, ne pas recoder la logique de sélection de date.
- **`FINNHUB_API_KEY`** déjà configurée dans `.env` (utilisée par
  `finnhub_enrich.py` sur l'univers TITAN — free tier, 60 appels/min).
- **`backend/modules/finnhub_enrich.py`** — pattern de job d'enrichissement
  cron-friendly, cache 24h, pas-op silencieux si clé absente. **Mais** il
  opère sur `data/universe.json` (491 tickers du scan TITAN) — les 10 tickers
  `my_portfolio` (BNP.PA, FMX, PSX, DRH, CNC, HRTG, LNVGY, MU, NUTX, ERO) +
  watchlist (SEZL) sont **hors** de cet univers (`my_portfolio_data.py` docstring :
  "Ces tickers ne font pas partie de l'univers S&P500 scanné par TITAN") →
  ce cron ne les touche jamais aujourd'hui. Besoin d'un enrichissement
  dédié, léger (11 tickers), pas d'extension du scan complet.
- **`backend/data_providers/_disk_cache.py`** (`read_json_cache`/
  `write_json_cache`) — cache générique JSON+TTL déjà utilisé par
  `finnhub_provider.py`, `sec_edgar.py`, `finnhub_news` : exactement l'outil
  pour cacher `next_earnings_date` par ticker sans réinventer une couche.
- **`backend/data_providers/yfinance_provider.py`** (lignes 274-396) —
  extrait aussi `next_earnings_date` (déjà utilisé comme fallback ailleurs
  dans le pipeline TITAN) : bon second recours si Finnhub ne couvre pas un
  ticker.
- **`backend/routers/calendar.py`** — `GET /api/calendar` agrège déjà les
  earnings des positions OPEN + watchlist **du book TITAN** (`universe.json`
  + `duckdb_journal`). Ne couvre pas `my_portfolio` — sert de référence de
  format (`{date, days_delta, type, ticker, label, source, scope}`), pas de
  code à appeler directement pour ce book (source de données différente).

### Fichiers à modifier ou créer

- **Créer** `backend/modules/my_portfolio_earnings.py` — fetch + cache
  `next_earnings_date` pour les 10 tickers `my_portfolio` + watchlist,
  fail-open Finnhub → yfinance, persistance via `_disk_cache` générique
  (`data/.my_portfolio_earnings_cache.json`, TTL 24h).
- **Modifier** `backend/main.py` — flag `--refresh-my-portfolio-earnings`.
- **Modifier** `backend/modules/my_portfolio_data.py` — **retirer** tout champ
  `badge` statique (déjà fait pour LNVGY ; s'assurer qu'aucune autre ligne
  n'en réintroduit un à la main).
- **Modifier** `backend/routers/my_portfolio.py` — lire le cache, calculer
  `next_earnings_date`/`earnings_days_until`/badge dynamique par row.
- **Modifier** crontab — job quotidien léger (ex. `0 7 * * * ... --refresh-my-portfolio-earnings`,
  avant le digest Telegram 07h30 existant).
- **Modifier** `frontend/src/components/MyPortfolioPage.jsx` — badge devient
  piloté par état calculé (upcoming / just-reported / rien), plus par texte
  figé.

### Nouveaux champs de données

Par position, dans la réponse API (pas dans `my_portfolio_data.py` statique) :

```json
{
  "next_earnings_date": "2026-11-05",
  "earnings_days_until": 76,
  "earnings_source": "finnhub",
  "earnings_data_stale": false
}
```

`earnings_days_until` négatif et proche de 0 (ex. -2) signale un earnings
très récent — état transitoire "résultats publiés" (voir logique ci-dessous).
`earnings_source` ∈ `{"finnhub", "yfinance", null}` ; `null` = aucune source
n'a de date pour ce ticker (silencieux côté UI, mais loggé).

### Logique de calcul

1. Pour chaque ticker `my_portfolio` (+ watchlist) : essayer
   `FinnhubProvider` (`/calendar/earnings`, fenêtre `[today, today+365j]`,
   `min(dated, key=date)` — logique déjà écrite). Si absent/erreur/HK non
   couvert → fallback `yfinance` (`Ticker.calendar` / `get_earnings_dates()`).
   Si les deux échouent → `next_earnings_date=null`, pas de badge, log
   warning (visibilité diagnostique, même philosophie que le fix
   `price_as_of` de cette session).
2. `earnings_days_until = (next_earnings_date - today).days`.
3. Badge calculé côté router :
   - `0 <= earnings_days_until <= 14` → `"📅 Earnings dans {N} j"`.
   - `-5 <= earnings_days_until < 0` → `"✅ Résultats publiés le {date}"`
     (état transitoire, disparaît après 5 jours — évite la disparition
     instantanée qui masquerait que l'événement a bien eu lieu, tout en
     évitant de le laisser traîner indéfiniment comme le bug initial).
   - sinon → pas de badge.
4. Si le cache Finnhub/yfinance échoue plusieurs jours d'affilée pour un
   ticker donné → `earnings_data_stale=true` (âge du cache > 48h alors que
   le TTL nominal est 24h), affiché discrètement pour que l'anomalie soit
   visible plutôt que silencieuse (c'est exactement le trou que ce upgrade
   corrige).

### Affichage frontend attendu

- Badge existant (`mp-badge-pending`, déjà dans `MyPortfolioPage.jsx`) reste
  visuellement identique, mais son contenu vient désormais de
  `p.next_earnings_date`/`p.earnings_days_until` calculés côté API, pas
  d'un champ statique.
- Nouvelle variante visuelle discrète pour `earnings_data_stale=true` (ex.
  petit "?" gris avec tooltip "calendrier earnings indisponible depuis Xj").
- Optionnel : réutiliser `/api/calendar` (Upgrade existant, TITAN uniquement
  aujourd'hui) en lui ajoutant une source `my_portfolio` pour que la vue
  Calendrier globale liste aussi ces earnings — hors scope minimal, à
  évaluer séparément.

### Cas limites à gérer

- **0992.HK (LNVGY)** : couverture Finnhub incertaine sur les tickers HK en
  plan gratuit — c'est le cas explicitement soulevé par l'utilisateur.
  Fallback yfinance obligatoire pour cette ligne ; si les deux échouent,
  absence de badge plutôt qu'une date fausse (jamais de valeur codée en dur
  en filet de sécurité — c'est le bug initial).
- **Rate limit Finnhub** (60/min) — 11 tickers/jour est négligeable, pas de
  souci de throttle, contrairement à `finnhub_enrich.py` sur 491 tickers.
- **Cache stale prolongé** → servir la dernière valeur connue plutôt que de
  faire disparaître le badge brutalement (pattern `STALE_FALLBACK_MAX_AGE_SECONDS`
  de `fundamentals_cache.py`), mais avec `earnings_data_stale=true` visible.
- **Double earnings rapprochés** (rare, ré-annonce/correction) → toujours
  prendre la date la plus proche future, cohérent avec `finnhub_provider.py`.
- **Position pas encore ouverte** (`shares=0`, ex. futur cas similaire à
  LNVGY avant le 21/08) — le badge earnings reste indépendant du statut
  `is_pending` : les deux peuvent coexister ou non selon le calendrier réel.

---

## Upgrade 3 — Générateur d'ordres de rééquilibrage

### Problème résolu

Quand `rebalance_alert=true` (dérive >±25%, position pleinement déployée),
le dashboard affiche seulement "⚠ +42%" — l'utilisateur doit calculer
lui-même combien acheter/vendre, dans quelle devise, en actions
fractionnaires ou non.

### Existant réutilisable

- **`routers/my_portfolio.py`** — `drift_pct`, `real_weight_pct`,
  `target_amount`, `current_value`, `current_price`, `shares` sont déjà
  calculés par row. Le fait que `target_amount` soit défini comme
  `target_weight_pct% × TOTAL_ENVELOPE_AMOUNT` (invariant déjà en place,
  voir commentaire `TOTAL_ENVELOPE_AMOUNT` dans `my_portfolio_data.py`)
  signifie que **la valeur-cible en $ pour ramener une ligne à son poids
  cible est déjà `target_amount` lui-même** — pas besoin de recalcul.
- **Mécanisme FX/alias construit cette session** (`_usd_multiplier`,
  `get_fx_rate`, `price_ticker`, `currency`, `shares_per_adr` dans
  `my_portfolio_data.py`/`my_portfolio.py`) — directement réutilisable pour
  exprimer l'ordre dans la devise native tradée (EUR pour BNP.PA, HKD pour
  0992.HK) plutôt qu'en USD uniquement, ce qui est ce que l'utilisateur
  tape réellement dans son ordre eToro.
- **Seuil `rebalance_alert`** (`REBALANCE_DRIFT_THRESHOLD_PCT`,
  `DEPLOYMENT_THRESHOLD_PCT`) déjà en place — cet upgrade ne change pas le
  déclenchement, juste ce qu'on affiche une fois déclenché.

### Fichiers à modifier ou créer

- **Modifier** `backend/routers/my_portfolio.py` — ajouter le calcul
  `rebalance_order` par row (pas de nouveau fichier, extension de la
  boucle existante).
- **Modifier** `backend/modules/tracker/market.py` ou `_safe_price` dans
  `my_portfolio.py` — exposer le prix natif brut (pré-FX, pré-ADR) et le
  multiplicateur FX utilisé, actuellement calculés puis jetés à l'intérieur
  de `_safe_price` (voir "cas limites").
- **Modifier** `frontend/src/components/MyPortfolioPage.jsx` + `index.css` —
  afficher l'ordre sous le badge `.mp-drift-alert` existant.

### Nouveaux champs de données

Par position :

```json
{
  "rebalance_order": {
    "direction": "SELL",
    "shares_native": 0.42,
    "amount_native": -34.10,
    "currency": "USD",
    "amount_usd": -34.10
  }
}
```

`null` si `rebalance_alert=false`, ou si le prix est `price_stale`, ou si le
taux FX nécessaire à la conversion est indisponible.

### Logique de calcul

1. `delta_usd = target_amount - current_value` (positif = acheter, négatif =
   vendre — réutilise directement l'invariant `target_amount` existant, pas
   de nouvelle formule de "valeur cible").
2. `direction = "BUY" if delta_usd > 0 else "SELL"`.
3. Prix natif : `raw_native_price = current_price_usd / shares_per_adr / fx`
   (inverse de la transformation déjà appliquée dans `_safe_price`) — plutôt
   que de refaire un fetch, exposer `fx` et `raw_native_price` comme
   retour supplémentaire de `_safe_price` pour éviter la double conversion
   silencieuse et les erreurs d'arrondi cumulées.
4. `amount_native = delta_usd / fx` (devise native), `shares_native =
   amount_native / raw_native_price`.
5. Précision d'affichage : même nombre de décimales que `shares` dans
   `my_portfolio_data.py` (jusqu'à 6, cohérent avec les valeurs
   déjà saisies type `37.094844`).

### Affichage frontend attendu

- Sous le badge `⚠ +42%` existant (dans la cellule "Poids réel", classe
  `.mp-drift-alert`), une ligne supplémentaire :
  `→ Vendre ~0.42 actions (−$34)` ou, pour une ligne non-USD :
  `→ Vendre ~1.10 actions (−31,20 €)`.
- Toujours afficher le montant USD entre parenthèses même pour une ligne
  non-USD native, pour rester comparable entre les lignes du book.
- Couleur cohérente avec la direction (rouge pour SELL, vert pour BUY —
  réutiliser `.mp-pnl-pos`/`.mp-pnl-neg` ou dédier une paire de classes).

### Cas limites à gérer

- **Jamais générer un ordre sur un prix `price_stale`** — même garde-fou que
  le P&L (`entry_price and shares > 0 and price is not None`) : un ordre
  calculé sur le prix de repli `target_amount` serait trompeur par
  construction (circularité).
- **FX indisponible** pour une ligne non-USD → `rebalance_order: null`
  plutôt qu'un montant natif potentiellement faux ; ne pas afficher
  "USD only" comme repli silencieux (le point de cet upgrade est la
  précision actionnable, un ordre approximatif est pire qu'aucun ordre).
- **Delta minuscule** (poussière d'arrondi) — à un seuil de dérive de 25%,
  le montant minimum plausible dépend de `target_amount` (ex. ERO $60 ×
  25% = $15, jamais vraiment "poussière"), mais documenter le cas si un
  jour `target_amount` devient très petit.
- **Ligne `cash_reserve`** et **`watchlist`** — pas de `rebalance_order`
  (pas d'instrument tradable cible pour le cash ; watchlist = 0% cible par
  définition, un ordre "vendre 0 action" n'a pas de sens).
- **Actions fractionnaires** : eToro les supporte déjà (positions actuelles
  type `2.338323` actions) — pas de rounding vers un nombre entier. Si un
  jour ce book s'étend à un broker qui n'accepte pas les fractions, ce
  serait un champ de config supplémentaire par position (hors scope ici).

---

## Upgrade 4 — Journal de qualité d'exécution

### Problème résolu

Deux mauvais fills constatés a posteriori, sans qu'aucun log n'ait capturé
le contexte au moment du trade :

- **CNC** acheté à 67.17 alors que le marché était ~64 (ordre 09h50 heure de
  Paris = hors séance US, spread élargi 24/5, ~4.7% de coût caché).
- **PSX** acheté à 06h52 (Paris), même symptôme.

`my_portfolio` n'a aujourd'hui **aucun journal de transactions** — c'est un
book 100% statique (`shares`/`entry_price` saisis à la main dans
`my_portfolio_data.py`, sans horodatage d'exécution). Contrairement au book
TITAN (`trade_journal.csv`), il n'existe aucun enregistrement du "quand" et
"dans quelles conditions" un fill a eu lieu.

### Existant réutilisable

- **`modules/tracker/market.py::is_market_hours()`** — heuristique NY
  (zoneinfo, DST-aware, autorité Alpaca `get_clock()` si dispo) déjà écrite
  et testée. Réutilisable telle quelle pour le volet marché US, mais
  **limitée à une seule place boursière** — à généraliser (voir ci-dessous).
- **`modules/utils.py` — colonne `Slippage_Bps`** ("Lot 13 — execution
  quality tracking (slippage réel vs reco)", déjà dans le schéma
  `trade_journal.csv`) — même **concept** (écart fill vs référence en bps,
  signé selon la direction), mais conçu pour comparer un fill Alpaca à la
  **reco système** du moteur TITAN. `my_portfolio` n'a pas de reco système
  (achats manuels eToro) → la "référence" doit être un prix de marché
  reconstruit a posteriori (historique intraday/EOD), pas une reco interne.
  La **formule** `(fill - reco) / reco × 10000` reste directement réutilisable
  en remplaçant "reco" par "prix de référence reconstruit".
- **Gate marché Alpaca** (mémoire projet 2026-04-24, `AlpacaBroker.submit_order`
  + `get_clock()` fail-closed avant soumission) — même logique d'évaluation
  "marché ouvert ?", mais appliquée ici **a posteriori en lecture seule**
  (diagnostic, pas blocage — ces fills sont manuels côté eToro, rien à
  bloquer côté ce système).
- **`get_current_price_detailed`** (construit cette session) — capable de
  fetcher un prix + timestamp source ; pour un prix de référence
  **historique** (pas "maintenant"), il faut une variante qui prenne une
  date/heure cible et interroge `yf.Ticker(...).history(start=, end=,
  interval="1m")` — extension du même module, même style.
- **Mécanisme FX/devise** (`_usd_multiplier`/`get_fx_rate`) — mais ce
  dernier ne connaît que le taux **live** ("maintenant"). Pour comparer un
  fill EUR/HKD historique à sa contre-valeur USD à la date du fill, il faut
  une variante `get_historical_fx_rate(pair, date)` (nouvelle capacité, même
  provider `yfinance`, `.history(start=, end=)` au lieu de `period="1d"`).

### Fichiers à modifier ou créer

- **Créer** `backend/data/my_portfolio_executions.csv` — nouveau journal de
  transactions (pattern `trade_journal.csv`, colonnes ci-dessous). **Saisie
  manuelle** : ce dépôt n'a pas d'intégration API eToro (`broker_gateway.py`
  ne supporte qu'Alpaca) — il n'existe aucun moyen de capturer un fill eToro
  automatiquement. Cet upgrade est un outil de **log volontaire assisté**
  (formulaire + calcul de la référence), pas une capture automatique de
  fills. À énoncer explicitement pour ne pas sur-promettre.
- **Créer** `backend/modules/my_portfolio_executions.py` — CRUD du CSV +
  calcul de la référence (prix historique + FX historique + statut marché
  au moment du fill).
- **Créer** `backend/modules/exchange_hours.py` — table multi-place
  (généralisation additive de `is_market_hours()`, **sans modifier** cette
  fonction existante pour ne pas toucher le chemin critique TITAN/tracker) :
  US, Euronext Paris, HKEX (avec pause déjeuner).
- **Modifier** `backend/modules/tracker/market.py` — ajouter
  `get_historical_price(ticker, at)` et `get_historical_fx_rate(pair, at)`
  (extensions additives, pas de changement aux fonctions existantes).
- **Créer** `backend/routers/my_portfolio_executions.py` — `POST
  /api/my_portfolio/executions` (log un fill), `GET
  /api/my_portfolio/executions` (liste + agrégats).
- **Créer** `frontend/src/components/ExecutionQualityPage.jsx` (ou onglet
  dans `MyPortfolioPage.jsx`) — formulaire de saisie + table historique +
  tuile coût cumulé.

### Nouveaux champs de données

`data/my_portfolio_executions.csv` :

| Colonne | Exemple | Note |
|---|---|---|
| `Date` | 2026-08-19 | |
| `Ticker` | CNC | |
| `Direction` | BUY | |
| `Shares` | 3.27527 | |
| `Fill_Price_Native` | 67.17 | devise = `Currency` |
| `Currency` | USD | |
| `Executed_At` | 2026-08-19T09:50:00+02:00 | **datetime avec offset explicite**, saisi par l'utilisateur |
| `Primary_Exchange` | US | US / EURONEXT_PARIS / HKEX |
| `Market_Open_At_Fill` | false | calculé, pas saisi |
| `Reference_Price_USD` | 64.02 | calculé (historique) |
| `Reference_Price_Resolution` | intraday | intraday (≤30j) ou daily_close (fallback) |
| `Slippage_Bps` | 493 | `(fill-ref)/ref × 10000`, signé |
| `Slippage_Usd` | 10.29 | coût $ absolu (`slippage × shares × ref` normalisé) |
| `Notes` | "hors séance US" | libre |

### Logique de calcul

1. **Saisie** : l'utilisateur entre ticker, direction, shares, prix payé
   (devise native), date/heure d'exécution **avec fuseau horaire explicite**
   (pas de datetime naïf — cause d'erreur silencieuse sinon).
2. **Place principale** : déduite de `price_ticker`/`currency` de la
   position (US pour la plupart, EURONEXT_PARIS pour BNP.PA, HKEX pour
   LNVGY/0992.HK).
3. **Marché ouvert ?** : `exchange_hours.is_open(exchange, executed_at)` —
   table horaires (voir ci-dessous), DST-aware (réutilise `zoneinfo`, même
   pattern que `is_market_hours()`).
4. **Prix de référence** : `get_historical_price(price_ticker, executed_at)`
   — 1-minute intraday si `executed_at` est dans les ~30 derniers jours
   (limite connue de yfinance), sinon repli sur le close journalier de la
   date (résolution dégradée, `Reference_Price_Resolution="daily_close"`).
5. **Conversion devise** : si `Currency != "USD"`, convertir `Fill_Price_Native`
   ET `Reference_Price` avec le taux FX **historique à `executed_at`**
   (pas le taux live) via `get_historical_fx_rate`.
6. **Slippage** : `(Fill_Price_USD - Reference_Price_USD) / Reference_Price_USD
   × 10000` (bps, signé selon la direction — même formule que
   `Slippage_Bps` du book TITAN).
7. **Agrégats** (`GET /api/my_portfolio/executions` → bloc `summary`) :
   coût total $ cumulé (`Σ Slippage_Usd`), % de fills hors séance, pire
   exécution (top 3 par `abs(Slippage_Bps)`).

### Affichage frontend attendu

- **Formulaire de saisie** : ticker (select depuis `POSITIONS`), direction,
  shares, prix payé, devise (pré-remplie selon le ticker), date + heure +
  fuseau (sélecteur explicite, pas de champ datetime ambigu).
- **Table historique** : Date/Heure | Ticker | Marché principal ouvert
  (✅/❌) | Prix payé | Prix référence | Écart (bps, coloré rouge si
  `abs > 200bps`) | Notes.
- **Tuile agrégée** : "Coût d'exécution cumulé : −$XX (Y fills hors
  séance sur Z)".
- **Rappel horaires par place** (statique, informatif, en bas de page ou en
  tooltip du formulaire) :
  - US (NYSE/NASDAQ) : 15h30–22h00 heure de Paris (09h30–16h00 ET, hors DST
    mismatch ponctuel mars/nov).
  - Euronext Paris : 09h00–17h30 heure de Paris.
  - HKEX : 03h30–06h00 et 07h00–10h00 heure de Paris (09h30–12h00 /
    13h00–16h00 heure de Hong Kong, **avec pause déjeuner** — à ne pas
    simplifier en un seul bloc "matin").

### Cas limites à gérer

- **Aucune capture automatique** : ce journal ne vaut que ce que
  l'utilisateur y saisit — pas d'intégration eToro. À documenter comme
  limite structurelle du produit, pas comme bug futur.
- **Fenêtre intraday yfinance (~30j)** : un fill de plus d'un mois ne peut
  être comparé qu'au close journalier → `Reference_Price_Resolution` doit
  être visible dans l'UI pour ne pas laisser croire à une précision
  intraday inexistante.
- **Pause déjeuner HKEX** : un fill à 12h15 HKT doit être classé "marché
  fermé" malgré l'heure diurne — piège si la table `exchange_hours` est
  simplifiée en un seul intervalle continu.
- **DST** : réutiliser le pattern `zoneinfo` déjà validé dans
  `is_market_hours()` pour les 3 places (les transitions DST US/EU ne sont
  pas synchronisées, ce qui décale ponctuellement le chevauchement
  Paris/US de ±1h deux fois par an).
- **FX historique manquant** (jour férié FX, données Yahoo incomplètes) →
  fallback sur le taux du jour de bourse valide le plus proche, avec
  `Reference_Price_Resolution` ou un flag dédié signalant l'approximation.
- **Erreur de fuseau à la saisie** (utilisateur qui tape l'heure de Paris
  mais que le formulaire interprète en UTC) → risque de faux positif "hors
  séance". Le formulaire doit afficher explicitement l'heure convertie dans
  les 3 fuseaux de référence avant validation, pour que l'erreur soit
  visible avant enregistrement plutôt que découverte plus tard.
- **Position déjà soldée / pas dans `POSITIONS`** — le journal doit rester
  indépendant de l'état courant des positions (un fill reste un fait
  historique même si la ligne a été depuis clôturée) — pas de contrainte de
  clé étrangère stricte vers `my_portfolio_data.py`.

---

## Ordre d'implémentation recommandé

| Ordre | Upgrade | Complexité relative | Pourquoi cet ordre |
|---|---|---|---|
| 1 | **Upgrade 2** — Calendrier earnings | 🟢 Faible | Corrige un bug actif et visible (badge stuck faux depuis >1 semaine). Réutilise `FinnhubProvider` quasi telle quelle, pas de nouvelle persistance transactionnelle, pas de nouvelle UI complexe. Gain de confiance rapide. |
| 2 | **Upgrade 3** — Ordres de rééquilibrage | 🟢 Faible-Moyenne | Pur calcul arithmétique sur des champs déjà exposés par l'API (`drift_pct`, `target_amount`, FX déjà en place). Aucune nouvelle source de données externe, aucune nouvelle persistance. Le plus rapide à livrer une fois Upgrade 1 ou pas — **indépendant** des 3 autres. |
| 3 | **Upgrade 1** — Moteur de risque | 🟠 Moyenne-Élevée | Priorité déclarée par l'utilisateur malgré une complexité plus élevée que 2/3 : nouveau job batch, nouvelle persistance (`my_portfolio_risk.json`), logique statistique (beta/corrélation/diversification) assemblée depuis 3 modules existants + nouveau bandeau UI. Le "durable" (streak hebdo) ajoute un état à gérer dans le temps. Aucune dépendance externe nouvelle (yfinance déjà utilisé partout). |
| 4 | **Upgrade 4** — Journal d'exécution | 🔴 Élevée | Le seul des 4 qui crée un **nouveau modèle de données transactionnel** de zéro (aucun journal `my_portfolio` n'existe aujourd'hui), une **nouvelle table horaires multi-place** (HKEX pause déjeuner, DST), et une **nouvelle capacité FX historique** (le `get_fx_rate` construit cette session est live-only). Dépend structurellement d'une saisie manuelle fiable (pas d'API eToro) — valeur diagnostique/rétrospective plutôt qu'urgence opérationnelle. À faire en dernier, une fois les 3 autres stabilisés. |

Upgrades 2 et 3 sont mutuellement indépendants et peuvent être menés en
parallèle ou dans n'importe quel ordre entre eux. Upgrade 1 n'a de
dépendance dure sur aucun des 3 autres mais bénéficie d'être fait après 2/3
(mêmes fichiers `my_portfolio.py`/`my_portfolio_data.py` touchés — moins de
conflits si le petit refactor de structure de données pour les
`sell_signal` structurés arrive après les upgrades plus simples). Upgrade 4
est le plus indépendant des quatre (nouveaux fichiers dédiés quasi partout)
mais aussi le moins urgent au vu du problème initial (deux fills isolés,
pas un bug systémique actif).
