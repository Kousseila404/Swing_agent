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

**Statut : NOT_STARTED**

Checklist :

- [ ] `backend/modules/portfolio_risk.py` créé (historique 2 ans via
      yfinance, beta par ticker vs `^GSPC`, matrice de corrélation, ratio de
      diversification, beta/corrélation pondérés portefeuille)
- [ ] `data/my_portfolio_risk.json` — format de persistance défini et
      versionné dans le code (pas juste écrit à la volée sans schéma)
- [ ] Flag `--recompute-portfolio-risk` ajouté à `backend/main.py`
- [ ] Champs `correlation_alert` structurés ajoutés à `my_portfolio_data.py`
      pour BNP.PA / HRTG / ERO (remplace le seuil texte libre)
- [ ] Mécanisme de streak "durable" (`correlation_streak_weeks`,
      `persist_weeks`) — réutilise le pattern `recurrence_streaks()` de
      `modules/proposals.py` en s'en inspirant, pas forcément en l'import direct
- [ ] `routers/my_portfolio.py` merge `beta_recalculated` / `avg_correlation`
      / flags par row + bloc racine `risk_snapshot`
- [ ] Tests unitaires (jeux de données synthétiques/mockés — ne PAS dépendre
      d'un vrai appel yfinance 2 ans dans les tests, trop lent et non
      déterministe pour CI)
- [ ] Vérification live via `TestClient` : `risk_snapshot` présent, cohérent
      avec des données mockées de test
- [ ] Frontend : colonne beta recalculé + highlight signal de vente +
      bandeau risque (4 tuiles) + top paires corrélées
- [ ] Cas limite historique <2 ans géré (`data_quality`) sans halluciner de
      valeur
- [ ] Cron hebdo documenté (même remarque que Upgrade 2 : la routine
      documente la ligne crontab, ne l'active pas elle-même sur le VPS de
      prod)
- [ ] `npm run build` + `npx vitest run` + suite pytest complète verts

**Note de run** : (vide)

---

### Upgrade 4 — Journal de qualité d'exécution

**Statut : NOT_STARTED**

Checklist :

- [ ] `backend/modules/exchange_hours.py` créé (table US / Euronext Paris /
      HKEX avec pause déjeuner, DST-aware via `zoneinfo`, additif — ne
      modifie PAS `is_market_hours()` existant)
- [ ] `get_historical_price(ticker, at)` et `get_historical_fx_rate(pair, at)`
      ajoutés à `modules/tracker/market.py` (extensions additives)
- [ ] `data/my_portfolio_executions.csv` — schéma de colonnes défini (voir
      spec) + `backend/modules/my_portfolio_executions.py` (CRUD + calcul
      slippage/référence)
- [ ] `backend/routers/my_portfolio_executions.py` — endpoints POST/GET
- [ ] Frontend : formulaire de saisie + table historique + tuile coût cumulé
      (nouveau composant ou onglet)
- [ ] Tests unitaires (résolution intraday vs daily_close, pause déjeuner
      HKEX, DST, FX historique manquant — tous les cas limites de la spec)
- [ ] Vérification live via `TestClient` : POST puis GET round-trip sur le
      nouvel endpoint
- [ ] `npm run build` + `npx vitest run` + suite pytest complète verts

**Note de run** : (vide)

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
