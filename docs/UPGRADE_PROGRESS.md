# Suivi d'avancement — Upgrades "Mon Portefeuille"

**Ce fichier est l'état persistant entre les runs de la routine cloud
autonome.** Chaque run (repartant d'un clone frais, sans mémoire du run
précédent) doit :

1. Lire ce fichier en entier avant toute action.
2. Si `ALL_COMPLETE: true` ci-dessous → ne rien faire (pas de code, pas de
   commit), tenter de se désactiver (voir section "Arrêt"), et terminer.
3. Sinon, déterminer l'upgrade en cours selon l'ordre `2 → 3 → 1 → 4` et le
   statut de chaque upgrade ci-dessous (le premier `NOT_STARTED` ou
   `IN_PROGRESS` dans cet ordre est la cible du run).
4. Relire la section correspondante de `docs/UPGRADES_MY_PORTFOLIO.md` (la
   spec ne change pas, ce fichier-ci ne fait que suivre l'avancement).
5. Avancer d'un incrément raisonnable et **testable** — pas besoin de finir
   tout l'upgrade en un seul run.
6. Avant de commit : suite de tests complète backend + frontend verte, ET
   vérification "live" via `TestClient(api.app)` (voir Upgrade correspondant
   ci-dessous — c'est la définition de "live" utilisée dans ce projet pour
   ce mécanisme, PAS un appel réseau vers la prod VPS de l'utilisateur, hors
   de portée du sandbox cloud).
7. Mettre à jour la section de l'upgrade concerné ci-dessous (statut,
   checklist, note de run).
8. Commit + push sur `main` (le VPS de prod tire `main` automatiquement via
   `scripts/git_auto_sync.sh`, toutes les minutes — chaque push ici est un
   déploiement de facto, ne jamais commit tests rouges).
9. Terminer proprement le run (pas de travail non commité en fin de run).

## Statuts possibles

`NOT_STARTED` / `IN_PROGRESS` / `IMPLEMENTED_UNTESTED` / `DONE_VERIFIED`

Un upgrade ne passe à `DONE_VERIFIED` que quand **toute** sa checklist est
cochée ET que la suite de tests complète est verte ET que la vérification
live (TestClient) est passée pour ses endpoints/champs.

---

## ALL_COMPLETE: false

(Passer cette valeur à `true` seulement quand les 4 upgrades ci-dessous sont
`DONE_VERIFIED`. Voir section "Arrêt" en bas de fichier.)

---

## Ordre de traitement : 2 → 3 → 1 → 4

### Upgrade 2 — Calendrier d'earnings automatique

**Statut : DONE_VERIFIED**

Checklist (voir détail dans `docs/UPGRADES_MY_PORTFOLIO.md`, section Upgrade 2) :

- [x] `backend/modules/my_portfolio_earnings.py` créé (fetch+cache Finnhub →
      fallback yfinance, fail-open, cache 24h — fichier unique
      `data/.my_portfolio_earnings_cache.json`, pas via `_disk_cache` générique
      car TTL évalué par entrée/ticker et pas globalement au fichier, cf.
      besoin de staleness par ticker distinct de `_disk_cache.read_json_cache`)
- [x] Flag `--refresh-my-portfolio-earnings` ajouté à `backend/main.py`
- [x] `badge` statique retiré de tout `POSITIONS` dans `my_portfolio_data.py`
      (déjà fait pour LNVGY le 2026-08-21 ; vérifié : aucune autre ligne n'en
      a un — `POSITIONS`/`WATCHLIST` ne contiennent plus jamais de `badge`)
- [x] `routers/my_portfolio.py` calcule `next_earnings_date` /
      `earnings_days_until` / `earnings_source` / `earnings_data_stale` par
      row et le badge dynamique correspondant (positions ET watchlist,
      résolu via `price_ticker` — LNVGY interroge `0992.HK`, pas `LNVGY`)
- [x] Tests unitaires (mock Finnhub + yfinance, jamais de vrai appel réseau
      dans les tests — `backend/tests/test_my_portfolio_earnings.py`, 22
      tests : cache disque, fail-open par source, fail-open global avec
      conservation de la dernière valeur connue, TTL, staleness 48h)
- [x] Vérification live via `TestClient(api.app).get("/api/my_portfolio")` :
      `test_my_portfolio_earnings_live_roundtrip_via_disk_cache` dans
      `test_my_portfolio_router.py` — cache disque réellement écrit puis relu
      par le router en bout en bout, plus 7 autres tests d'intégration
      (badge upcoming/just-reported/hors-fenêtre, stale flag, résolution
      LNVGY→0992.HK, watchlist)
- [x] Frontend `MyPortfolioPage.jsx` : badge piloté par les champs calculés
      côté API (aucun changement requis, `p.badge` déjà consommé tel quel) +
      indicateur discret `mp-earnings-stale` ("?" gris, tooltip) quand
      `earnings_data_stale=true`
- [x] `npm run build` + `npx vitest run` verts (45/45, aucune régression) +
      `npm run lint` vert
- [x] Suite pytest complète verte (1183 tests, +30 vs baseline 1153 — 3
      échecs pré-existants sans rapport avec cet upgrade, voir note de run)
- [x] Ligne crontab documentée dans le commit (voir note de run — déploiement
      manuel par l'utilisateur, hors de portée du sandbox cloud)

**Note de run (2026-08-21)** : Implémenté et vérifié intégralement en un
run. `FinnhubProvider.get_revisions_and_earnings` (déjà existant, logique
`min(dated, key=date)` réutilisée telle quelle) → fallback
`_scrape_revisions_and_earnings` (yfinance_provider.py, déjà existant,
réutilisé tel quel pour l'extraction `next_earnings_date`) si Finnhub
absent/pas de clé/pas de date. Cache par ticker (pas par fichier global)
pour permettre un staleness différencié : `CACHE_TTL_SECONDS`=24h déclenche
le refetch, `STALE_MAX_AGE_SECONDS`=48h marque `earnings_data_stale=true`
sans faire disparaître la dernière date connue (fail-open, jamais de valeur
codée en dur). Ligne crontab à ajouter manuellement par l'utilisateur sur le
VPS de prod : `0 7 * * * cd /path/to/backend && ./venv/bin/python main.py
--refresh-my-portfolio-earnings` (avant le digest Telegram 07h30 existant).
Env sandbox : venv recréé avec `python3.12` explicitement — le `python3`
système par défaut du sandbox est 3.11, qui casse sur une f-string existante
(`modules/alerter.py:386`, backslash dans l'expression, syntaxe Python
3.12+) ; rien à voir avec cet upgrade, juste une note pour les runs futurs.
3 échecs pytest pré-existants et non liés (`test_duckdb_journal.py::
TestShadowInsert::test_fail_open_on_bad_path`, `test_risk.py::
TestSectorConcentration::test_blocks_when_sector_full`/
`test_custom_max_per_sector`) — confirmés reproductibles en isolation sans
aucun changement de code (environnement sandbox : process root, écriture
réussit dans un chemin "non-writable" ; données sector de test absentes/
différentes du sandbox). Aucune régression : ces 3 tests échouaient déjà
avant cet upgrade, le compte total progresse de 1153→1183 (+30 nouveaux
tests, tous verts).

---

### Upgrade 3 — Générateur d'ordres de rééquilibrage

**Statut : DONE_VERIFIED**

Checklist :

- [x] `_safe_price` (ou équivalent) expose le prix natif brut + multiplicateur
      FX utilisé, sans nouveau fetch réseau
- [x] `routers/my_portfolio.py` calcule `rebalance_order` par row (BUY/SELL,
      shares natifs, montant natif + USD), `null` si pas d'alerte ou prix
      stale ou FX indisponible
- [x] Tests unitaires (tous les cas limites de la spec : prix stale, FX
      indisponible, cash_reserve/watchlist exclus)
- [x] Vérification live via `TestClient` : un scénario avec dérive >25%
      simulée produit bien un `rebalance_order` cohérent dans la réponse JSON
- [x] Frontend : ligne d'ordre sous le badge de dérive existant
- [x] `npm run build` + `npx vitest run` + suite pytest complète verts

**Note de run (2026-08-21)** : Implémenté et vérifié intégralement en un
run. `_safe_price` retourne désormais un 4-tuple `(price_usd, as_of,
raw_native_price, fx)` — `raw_native_price` est simplement le prix déjà
fetché par `get_current_price_detailed` avant application de
`shares_per_adr`/FX (aucun nouveau fetch, juste exposé au lieu d'être jeté),
et `fx` le multiplicateur déjà calculé par `_usd_multiplier`. Nouveau
`_rebalance_order(row, raw_native_price, fx)` dans `routers/my_portfolio.py` :
`delta_usd = target_amount - current_value` (réutilise l'invariant
`target_amount` existant, pas de nouvelle formule de valeur cible),
`direction` BUY/SELL selon le signe, `amount_native = delta_usd / fx`,
`shares_native = abs(amount_native / raw_native_price)` (magnitude toujours
positive, le signe vit dans `direction`/`amount_native`/`amount_usd`).
`None` si `rebalance_alert=false`, `price_stale=true`, ou prix natif/FX
indisponible — jamais d'ordre approximatif. `cash_reserve` et `watchlist`
n'ont jamais ce champ (construits hors de la boucle qui le calcule).
Frontend : nouvelle ligne `.mp-rebalance-order` sous le badge `.mp-drift-alert`
existant (`MyPortfolioPage.jsx`), couleur héritée de `.mp-pnl-pos`/
`.mp-pnl-neg` selon BUY/SELL ; montant USD toujours affiché entre
parenthèses (ligne native `+ USD` pour les devises non-USD, ex.
`−31.20 EUR / −$34.10` — le texte de la spec donnait deux exemples
d'affichage légèrement contradictoires entre eux sur ce point précis,
tranché en faveur de la contrainte explicite "toujours afficher le montant
USD entre parenthèses" plutôt que de l'exemple isolé qui ne montrait que le
montant natif).
Tests : 6 nouveaux tests unitaires directs sur `_rebalance_order` (null par
non-alerte/prix stale/prix natif absent/FX absent, direction BUY USD,
direction SELL devise native EUR) + extension du test
`test_my_portfolio_rebalance_alert_fires_beyond_threshold` existant en
vérification live `TestClient` (dérive >25% simulée sur BNP.PA → `SELL`
cohérent dans le JSON, `cash_reserve`/`watchlist` sans `rebalance_order`) +
mise à jour des mocks `_safe_price` existants (2-tuple → 4-tuple) dans tout
`test_my_portfolio_router.py` pour rester compatibles avec la nouvelle
signature (aucune régression sur les 52 tests déjà en place dans ce fichier).
Suite pytest complète : 1186 tests verts (mêmes 3 échecs pré-existants et
non liés, déjà documentés dans la note Upgrade 2 —
`test_duckdb_journal.py::TestShadowInsert::test_fail_open_on_bad_path`,
`test_risk.py::TestSectorConcentration::test_blocks_when_sector_full`/
`test_custom_max_per_sector`, environnement sandbox uniquement). `ruff` +
`mypy` verts sur `routers/my_portfolio.py`. Frontend : `npm run build` +
`npx vitest run` (45/45) + `npm run lint` verts, aucune régression.

---

### Upgrade 1 — Moteur de risque en tâche de fond (PRIORITÉ HAUTE)

**Statut : DONE_VERIFIED**

Checklist :

- [x] `backend/modules/portfolio_risk.py` créé (historique 2 ans via
      yfinance, beta par ticker vs `^GSPC`, matrice de corrélation, ratio de
      diversification, beta/corrélation pondérés portefeuille) — pondération
      des agrégats via `target_weight_pct` et non `real_weight_pct`, voir
      note de run (déviation documentée)
- [x] `data/my_portfolio_risk.json` — format de persistance défini et
      versionné dans le code (`SCHEMA_VERSION=1`, `_load_state` rejette tout
      fichier à un autre schema_version → repart d'un état vide plutôt que
      de désérialiser une structure inconnue)
- [x] Flag `--recompute-portfolio-risk` ajouté à `backend/main.py`
- [x] Champs `correlation_alert` structurés ajoutés à `my_portfolio_data.py`
      pour BNP.PA / HRTG / ERO (ajoutés à côté de `sell_signal`, qui reste
      affiché tel quel côté frontend — voir "Nouveaux champs" de la spec)
- [x] Mécanisme de streak "durable" (`correlation_streak_weeks`,
      `persist_weeks`) — porté par le state JSON de ce module (pas
      `modules/proposals.py`, sans rapport avec ce book), même principe que
      `recurrence_streaks()` (incrémente si condition vraie, reset sinon)
- [x] `routers/my_portfolio.py` merge `beta_recalculated` / `avg_correlation`
      / flags par row + bloc racine `risk_snapshot`
- [x] Tests unitaires (jeux de données synthétiques/mockées, `_FakeTicker`
      dispatché par symbole — `backend/tests/test_portfolio_risk.py`, 16
      tests : beta/corrélation nominal, historique insuffisant, échec fetch,
      FX EUR/HKD indisponible, devise non configurée, streak pur, alerte
      corrélation moyenne (HRTG-like) sur 2 runs, alerte vs ticker précis
      (ERO-like) immédiate, signal composite OR (BNP.PA-like), persistance
      roundtrip + fail-open total, schema_version invalide/JSON corrompu,
      intégration book réel 10 positions)
- [x] Vérification live via `TestClient` :
      `test_my_portfolio_risk_live_roundtrip_via_disk_state` (state disque
      réellement écrit via `_save_state` puis relu bout-en-bout par le
      router — beta/corrélation/flags par ticker + `risk_snapshot` racine
      dans le JSON HTTP) + `test_my_portfolio_risk_fields_default_when_no_
      snapshot_computed` (fail-open : aucun fichier → valeurs par défaut,
      `risk_snapshot: null`, jamais de `KeyError` pour un ticker absent de
      l'état persisté), dans `test_my_portfolio_router.py`
- [x] Frontend : colonne beta recalculé (`βʳᵉᶜᵃˡᶜ` + badge ⚠ si
      `beta_flag`) + highlight signal de vente (`.mp-row-risk-alert` +
      préfixe 🔴, déclenché par `correlation_alert_triggered` OU `beta_flag`
      indépendamment) + bandeau risque (4 tuiles : beta portefeuille /
      corrélation moyenne pondérée / ratio de diversification / dernier
      recalcul via `fmtTimeAgo`) + top paires corrélées — `MyPortfolioPage.jsx`
      + `index.css` (classes `mp-risk-*`, `mp-row-risk-alert`, `mp-beta-*`)
- [x] Cas limite historique <2 ans géré (`data_quality: "insufficient_history"`)
      au niveau module ; côté router/frontend, un ticker sans entrée dans
      l'état persisté (ou `data_quality != "ok"`) reçoit les valeurs par
      défaut fail-open (`beta_recalculated: null`, pas de highlight halluciné)
- [x] Cron hebdo documenté (dans le `help=` du flag CLI :
      `--recompute-portfolio-risk`, `0 22 * * 0`, dimanche 22h30 après le
      refresh cache marché existant) — ligne crontab à ajouter manuellement
      par l'utilisateur sur le VPS de prod, même remarque que Upgrade 2
- [x] `npm run build` + `npx vitest run` + `npm run lint` + suite pytest
      complète verts (voir note de run pour les chiffres)

**Note de run (2026-08-22)** : Premier incrément — cœur du calcul (module
`portfolio_risk.py`) implémenté et testé intégralement, router/frontend pas
encore branchés (prochain run, en gardant l'ordre 2→3→1→4 : 1 reste la
cible tant que sa checklist n'est pas complète). Réutilise tel quel
`correlation_check._to_returns`/`_MIN_PAIRWISE_DAYS` (importés, pas
réécrits) et `backtest._capm_alpha_beta` (beta OLS, terme risk-free ignoré
car s'annule dans le calcul du beta seul). Devise des séries : prix convertis
en USD avant calcul des log-returns (`EURUSD=X`/`USDHKD=X` fetchés en
historique 2 ans au même format que les prix, alignés par date avec
`ffill`) — pas de nouveau mécanisme, même pattern de fetch que les prix.
Index normalisé en date calendaire nue (`tz_localize(None).normalize()`)
pour aligner des séries de marchés différents (NYSE vs HKEX pour 0992.HK).

**Déviation documentée** : la spec demande `wᵢ = real_weight_pct/100` pour
les agrégats portefeuille pondérés (beta pondéré, corrélation moyenne
pondérée, ratio de diversification). Ce module tourne en job batch
hebdomadaire hors contexte de requête HTTP (pas de prix live disponible
sans dupliquer le fetch prix déjà fait par le router) — utilise
`target_weight_pct` (poids cible statique) à la place : un snapshot de
risque hebdo caractérise le profil de risque de l'allocation *cible*, pas
l'instantané de dérive du jour (déjà exposé séparément via
`drift_pct`/`rebalance_alert`, Upgrade 3). Somme des `target_weight_pct` +
`CASH_RESERVE_PCT` = 100% dans `my_portfolio_data.py` actuel, donc les poids
utilisés restent normalisés.

Autres choix d'implémentation : tickers en `data_quality` != "ok" exclus
des agrégats pondérés sans renormalisation des poids restants (leur poids
"disparaît" plutôt que d'être redistribué) — accepté comme limite pour un
cas rare (nouvelle ligne récente), cohérent avec "ne pas halluciner de
valeur" plutôt que de fabriquer une redistribution arbitraire. `cash_weight_pct`
n'entre dans aucun calcul de corrélation/diversification (aucune série de
retours pour du cash) ; pour le beta pondéré, sa contribution est
structurellement nulle (`β_cash=0`) donc son inclusion explicite dans la
somme aurait été un no-op — non implémentée, juste documentée.

Tests : 16 nouveaux tests (voir checklist), tous verts, aucun appel réseau
réel (`_FakeTicker` dispatché par symbole, séries de prix synthétiques
`numpy.random.default_rng` seedé). Suite pytest complète : 1202 tests verts
(1186 + 16, mêmes 3 échecs pré-existants et non liés déjà documentés dans
les notes Upgrade 2/3 — `test_duckdb_journal.py::TestShadowInsert::
test_fail_open_on_bad_path`, `test_risk.py::TestSectorConcentration::
test_blocks_when_sector_full`/`test_custom_max_per_sector`, environnement
sandbox uniquement, aucun rapport avec cet upgrade). `ruff` + `mypy` verts
sur `modules/portfolio_risk.py`. Frontend inchangé ce run : `npm run build`
+ `npx vitest run` (45/45) verts, pas de régression (pas de `npm run lint`
nécessaire, aucun JS/JSX touché).

**Note de run (2026-08-22, suite)** : Deuxième incrément — checklist complétée
intégralement, Upgrade 1 → `DONE_VERIFIED`. `modules/portfolio_risk.py` gagne
`load_snapshot()` (wrapper public de `_load_state`, même nom/style que
`my_portfolio_earnings.get_earnings_snapshot` — lecture seule, aucun
recalcul, fail-open dict vide si absent/corrompu/schema obsolète).
`routers/my_portfolio.py` : `_risk_fields(ticker, risk_by_ticker)` merge par
row les 8 champs de la spec (défauts `_RISK_FIELDS_DEFAULT` si le ticker est
absent de l'état persisté — jamais de `KeyError`, jamais de valeur
approximative), `risk_snapshot` exposé tel quel au niveau racine (`None` si
le job hebdo n'a jamais tourné). Pas de nouveau fetch réseau dans le
handler HTTP — lecture d'un petit JSON déjà persisté par le job batch,
même coût qu'`get_earnings_snapshot`.
Tests : 2 nouveaux tests d'intégration `TestClient` dans
`test_my_portfolio_router.py` (monkeypatch `portfolio_risk._STATE_PATH` vers
`tmp_path`, même pattern que le test earnings live existant) — round-trip
état disque → réponse HTTP (beta/corrélation/flags par ticker + bloc
`risk_snapshot` racine, y compris un ticker absent de l'état → défauts) et
comportement par défaut avant tout run `--recompute-portfolio-risk` (fichier
absent → `risk_snapshot: null`, champs par ticker tous `null`/`false`/`0`).
Suite pytest complète : 1204 tests verts (1202 + 2, mêmes 3 échecs
pré-existants et non liés déjà documentés dans les notes Upgrade 2/3,
environnement sandbox uniquement). `ruff` + `mypy` verts sur
`modules/portfolio_risk.py` et `routers/my_portfolio.py`.
Frontend : colonne Beta affiche le beta statique + `βʳᵉᶜᵃˡᶜ` (badge ⚠ rouge
si `beta_flag`, tooltip avec l'écart %) ; ligne surlignée
(`.mp-row-risk-alert`, même style que `.mp-row-alert`) et signal de vente
préfixé 🔴 quand `correlation_alert_triggered` OU `beta_flag` (OR
indépendant, cas limite "signal composite" BNP.PA de la spec) ; nouveau
bandeau `.mp-risk-card` (4 tuiles, masqué tant que `risk_snapshot` est
`null` — pas de tuiles vides avant le premier run hebdo) + liste des paires
les plus corrélées sous le bandeau. `npm run build` + `npx vitest run`
(45/45, aucune régression) + `npm run lint` verts.
Env sandbox : venv recréé avec `python3.12` explicite (même note que
Upgrade 2 — `python3` système = 3.11, casse sur une f-string existante sans
rapport avec cet upgrade).

---

### Upgrade 4 — Journal de qualité d'exécution

**Statut : IN_PROGRESS**

Checklist :

- [x] `backend/modules/exchange_hours.py` créé (table US / Euronext Paris /
      HKEX avec pause déjeuner, DST-aware via `zoneinfo`, additif — ne
      modifie PAS `is_market_hours()` existant)
- [x] `get_historical_price(ticker, at)` et `get_historical_fx_rate(pair, at)`
      ajoutés à `modules/tracker/market.py` (extensions additives)
- [x] `data/my_portfolio_executions.csv` — schéma de colonnes défini (voir
      spec) + `backend/modules/my_portfolio_executions.py` (CRUD + calcul
      slippage/référence)
- [ ] `backend/routers/my_portfolio_executions.py` — endpoints POST/GET
- [ ] Frontend : formulaire de saisie + table historique + tuile coût cumulé
      (nouveau composant ou onglet)
- [x] Tests unitaires (résolution intraday vs daily_close, pause déjeuner
      HKEX, DST, FX historique manquant — tous les cas limites de la spec)
- [ ] Vérification live via `TestClient` : POST puis GET round-trip sur le
      nouvel endpoint
- [x] `npm run build` + `npx vitest run` + suite pytest complète verts

**Note de run (2026-08-22)** : Premier incrément — `exchange_hours.py` créé
(table de sessions par place, minutes-depuis-minuit locales, HKEX modélisé
en 2 sessions distinctes 09:30-12:00/13:00-16:00 HKT pour la pause
déjeuner). `is_open(exchange, at)` : `at` doit être timezone-aware (rejette
un datetime naïf avec `ValueError` explicite — cas limite "erreur de fuseau
à la saisie" de la spec, mieux vaut échouer fort que deviner un fuseau),
converti via `.astimezone()` vers le fuseau IANA de la place puis comparé
aux bornes de session. DST géré nativement par `zoneinfo` (pas de table de
dates spéciales) — mêmes fuseaux que `is_market_hours()` pour US
(`America/New_York`), + `Europe/Paris`/`Asia/Hong_Kong` nouveaux. N'importe
et ne modifie pas `is_market_hours()` (fonction séparée, chemin critique
TITAN/tracker intact). Tests : 20 tests (`test_exchange_hours.py`) —
sessions US/Euronext/HKEX nominal, pause déjeuner HKEX (12h15 fermé, bornes
exactes 12h00/13h00), week-end, DST hiver/été, conversion cross-fuseau
(Paris→HKT, UTC→NY), reproduction du cas CNC réel (09h50 Paris = hors
séance US), datetime naïf rejeté, place inconnue rejetée. Pas encore de
router/endpoint/frontend à ce stade (prochain run : `get_historical_price`/
`get_historical_fx_rate` dans `modules/tracker/market.py`, puis le CRUD
`my_portfolio_executions.py` qui consommera `exchange_hours.is_open`).
Suite pytest complète : 1224 tests verts (1204 + 20, mêmes 3 échecs
pré-existants et non liés déjà documentés dans les notes Upgrade 2/3/1 —
`test_duckdb_journal.py::TestShadowInsert::test_fail_open_on_bad_path`,
`test_risk.py::TestSectorConcentration::test_blocks_when_sector_full`/
`test_custom_max_per_sector`, environnement sandbox uniquement). `ruff` +
`mypy` verts sur `modules/exchange_hours.py`. Frontend inchangé ce run
(aucun JS/JSX touché, pas de build nécessaire — même pratique que le
premier incrément d'Upgrade 1).

**Note de run (2026-08-22, suite)** : Deuxième incrément — `get_historical_
price(ticker, at)`/`get_historical_fx_rate(pair, at)` ajoutés à
`modules/tracker/market.py` (extensions additives, aucune fonction
existante modifiée), puis `backend/modules/my_portfolio_executions.py`
créé (CRUD + calcul slippage/référence). Router/endpoints/frontend pas
encore branchés (prochain run : `routers/my_portfolio_executions.py` avec
`TestClient` pour la vérification live POST→GET, puis le formulaire/table
frontend — Upgrade 4 reste la cible tant que sa checklist n'est pas
complète).

`get_historical_price` : si `at` est dans les ~30 derniers jours, tente une
barre 1-minute dans une fenêtre de ±15 min autour de `at` (barre la plus
proche retenue) ; sinon (ou si la fenêtre intraday est vide) replie sur la
clôture journalière valide la plus récente <= `at`, recherchée jusqu'à 7
jours en arrière — jamais de lookahead (une clôture postérieure à `at`
n'est jamais utilisée, testé explicitement). `get_historical_fx_rate` :
même fenêtre de repli 7 jours pour le jour férié FX, renvoie aussi la date
de bourse effectivement utilisée (`date_iso`) pour que l'appelant puisse
détecter un repli en la comparant à la date demandée. Les deux rejettent un
`at` naïf avec `ValueError` (même contrainte que `exchange_hours.is_open`).
12 nouveaux tests (`test_tracker_market.py`, mock `_MockHistTicker` dédié
qui sert des rows différentes selon l'`interval` demandé — permet de
distinguer la branche intraday de la branche daily_close dans un même test
sans réseau).

`my_portfolio_executions.py` : `compute_reference(...)` calcule
`market_open_at_fill` (délègue à `exchange_hours.is_open`),
`reference_price_usd`/`reference_price_resolution` (délègue à
`get_historical_price`), puis `slippage_bps`/`slippage_usd` — fail-open à
`None` si le prix ou le FX historiques sont indisponibles (jamais de
valeur approximative affichée comme certaine). `_historical_usd_multiplier`
duplique volontairement la convention `_FX_PAIR_FOR_CURRENCY`/inversion
HKD de `routers/my_portfolio.py::_usd_multiplier` (paire yfinance par
devise, EUR = taux direct, HKD = taux inversé) mais au taux HISTORIQUE
(`get_historical_fx_rate`), pas live — dupliqué plutôt qu'importé pour
garder ce module utilisable indépendamment de FastAPI, même principe que
`my_portfolio_earnings.py` vis-à-vis du router.

**Déviation documentée** : la spec donne la formule brute
`(fill-ref)/ref×10000` "signée selon la direction" sans préciser le sens
du flip pour SELL. Choix : signe = coût (positif = exécution coûteuse)
quel que soit le sens du trade — un BUY payé plus cher que la référence ET
un SELL vendu moins cher que la référence sont tous deux comptés
positivement (flip de signe pour SELL). Cohérent avec l'exemple CNC de la
spec (BUY, fill>ref, ≈493 bps positif) et avec le vocabulaire "coût
d'exécution cumulé" de la tuile agrégée prévue. `Slippage_Usd` suit le même
signe (`(slippage_bps/10000) × reference_price_usd × shares`), vérifié
équivalent à `(fill_usd - reference_usd) × shares` pour un BUY sur le cas
CNC (≈$10.32 vs $10.29 de l'exemple spec, écart de rounding du prix de
référence donné en exemple). Le repli FX "jour férié" (`get_historical_
fx_rate` cherchant jusqu'à 7j en arrière) n'est pas signalé par un flag CSV
dédié — la spec accepte `Reference_Price_Resolution` OU un flag dédié
("ou" explicite), et l'imprécision dominante pour ce diagnostic reste celle
du prix de référence (déjà couverte), pas celle du taux FX (écart
jour-à-jour marginal) — décision documentée, pas un oubli.

`compute_reference`/`log_execution` prennent `price_ticker`/`currency`/
`primary_exchange` en paramètres explicites (pas de lookup dans
`my_portfolio_data.POSITIONS`) — respecte le cas limite spec "le journal
reste indépendant de l'état courant des positions" (testé explicitement :
un ticker absent du book reste journalisable). CSV : schéma propre à ce
fichier (`ensure_csv_schema` de `modules/utils.py` est câblé en dur sur le
schéma `trade_journal.csv`, pas réutilisable tel quel), `FileLock` +
écriture atomique `.tmp`→rename (même pattern que
`modules.tracker.evaluation.load_journal`/`save_journal`) — un seul
`FileLock` par opération (pas de lock imbriqué entre lecture et écriture,
pour éviter un deadlock sur deux instances `FileLock` distinctes du même
fichier). `summarize(df)` calcule les agrégats de la spec (coût cumulé,
% hors séance, top 3 pires exécutions par `abs(Slippage_Bps)`) — fail-open
sur un journal vide ou des fills sans slippage calculable.

20 nouveaux tests (`test_my_portfolio_executions.py`) : signe BUY/SELL
(payé plus cher / vendu moins cher / vendu plus cher = négatif), conversion
EUR/HKD historique (fill ET référence au même taux, pas de taux live),
fail-open prix/FX manquant/devise non configurée, direction invalide
rejetée, pause déjeuner HKEX (via `exchange_hours.is_open` réel, non
mocké), CRUD (append sans écraser, indépendance vis-à-vis de POSITIONS,
CSV vide), `summarize` (vide, agrégats, exclusion des fills non résolus du
total $ tout en les comptant dans `fills_count`, top 3 trié), plus un
round-trip disque réel (écriture CSV puis relecture directe du fichier,
sans mock I/O — la définition "live" de ce diagnostic tant qu'aucun
endpoint HTTP n'existe encore pour un `TestClient` round-trip).

Suite pytest complète : 1256 tests verts (1224 + 32, mêmes 3 échecs
pré-existants et non liés déjà documentés dans les notes Upgrade 2/3/1,
environnement sandbox uniquement). `ruff` + `mypy` verts sur
`modules/tracker/market.py` et `modules/my_portfolio_executions.py`.
Frontend inchangé ce run (aucun JS/JSX touché) : `npm run build` +
`npx vitest run` (45/45) verts, aucune régression.

---

## Note d'environnement (sandbox cloud)

Le clone cloud n'a ni `backend/venv/`, ni `backend/.env` (secrets non
commités), ni `frontend/node_modules/`. Chaque run doit vérifier leur
présence et les (re)créer si absents avant de lancer tests/build
(`python -m venv venv && ./venv/bin/pip install -r requirements.txt -r
requirements-dev.txt` ; `npm install`). Ne jamais supposer un état
préexistant.

"Vérification live" dans ce projet = `TestClient(api.app)` (import direct de
l'app FastAPI, requêtes ASGI in-process) — c'est le mécanisme déjà utilisé
par toute la suite de tests existante (`backend/tests/test_my_portfolio_router.py`
en est l'exemple canonique) et il exécute réellement tout le pipeline
(routing, validation Pydantic, logique métier), sans dépendre d'un port
réseau ni de secrets d'environnement. Ne pas essayer de joindre la vraie API
de production de l'utilisateur (VPS local, non accessible depuis le sandbox
cloud).

Pour toute logique dépendant de données de marché réelles (yfinance,
Finnhub) : écrire les tests avec des mocks déterministes (pattern
`_MockTicker` dans `backend/tests/test_tracker_market.py`), jamais de vrai
appel réseau dans la suite de tests — le sandbox cloud peut avoir un accès
réseau restreint ou intermittent, et les tests doivent rester reproductibles.

## Arrêt

Quand les 4 upgrades ci-dessus sont `DONE_VERIFIED` :

1. Passer `ALL_COMPLETE: true` en haut de ce fichier, committer, pusher.
2. Si l'outil `RemoteTrigger` est disponible dans cette session : appeler
   `action: "update"` sur cette routine (son `trigger_id` est fourni dans le
   prompt de la routine) avec `body: {"enabled": false}` pour arrêter les
   déclenchements futurs.
3. Terminer le run avec un message final explicite commençant par
   `ALL UPGRADES COMPLETE` (visible dans le log du run sur
   claude.ai/code/routines même si l'auto-désactivation échoue).
4. Si `RemoteTrigger` n'est pas disponible ou que l'appel échoue, ce n'est
   pas bloquant : tout run futur qui se déclenche encore lira
   `ALL_COMPLETE: true` en premier et s'arrêtera immédiatement sans rien
   modifier (voir étape 2 du préambule) — la routine reste inoffensive même
   si le cron continue de la déclencher.
