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

**Statut : NOT_STARTED**

Checklist (voir détail dans `docs/UPGRADES_MY_PORTFOLIO.md`, section Upgrade 2) :

- [ ] `backend/modules/my_portfolio_earnings.py` créé (fetch+cache Finnhub →
      fallback yfinance, fail-open, cache 24h via `_disk_cache`)
- [ ] Flag `--refresh-my-portfolio-earnings` ajouté à `backend/main.py`
- [ ] `badge` statique retiré de tout `POSITIONS` dans `my_portfolio_data.py`
      (déjà fait pour LNVGY le 2026-08-21 — vérifier qu'aucune autre ligne
      n'en a un)
- [ ] `routers/my_portfolio.py` calcule `next_earnings_date` /
      `earnings_days_until` / `earnings_source` / `earnings_data_stale` par
      row et le badge dynamique correspondant
- [ ] Tests unitaires (mock Finnhub + yfinance, jamais de vrai appel réseau
      dans les tests — pattern `_MockTicker` déjà utilisé dans
      `test_tracker_market.py`)
- [ ] Vérification live via `TestClient(api.app).get("/api/my_portfolio")` :
      les champs earnings apparaissent dans la réponse JSON
- [ ] Frontend `MyPortfolioPage.jsx` : badge piloté par les champs calculés
- [ ] `npm run build` + `npx vitest run` verts
- [ ] Suite pytest complète verte (`cd backend && ./venv/bin/python -m
      pytest -q` — ou équivalent si pas de venv pré-existant dans le
      sandbox, voir note d'environnement en bas de fichier)
- [ ] Ligne crontab documentée dans le commit/PR (le déploiement effectif de
      la ligne crontab reste manuel côté utilisateur — la routine ne peut
      pas éditer le crontab du VPS de prod depuis le sandbox cloud, elle
      documente juste la commande à ajouter)

**Note de run** : (vide — à remplir par le premier run qui touche cet upgrade)

---

### Upgrade 3 — Générateur d'ordres de rééquilibrage

**Statut : NOT_STARTED**

Checklist :

- [ ] `_safe_price` (ou équivalent) expose le prix natif brut + multiplicateur
      FX utilisé, sans nouveau fetch réseau
- [ ] `routers/my_portfolio.py` calcule `rebalance_order` par row (BUY/SELL,
      shares natifs, montant natif + USD), `null` si pas d'alerte ou prix
      stale ou FX indisponible
- [ ] Tests unitaires (tous les cas limites de la spec : prix stale, FX
      indisponible, cash_reserve/watchlist exclus)
- [ ] Vérification live via `TestClient` : un scénario avec dérive >25%
      simulée produit bien un `rebalance_order` cohérent dans la réponse JSON
- [ ] Frontend : ligne d'ordre sous le badge de dérive existant
- [ ] `npm run build` + `npx vitest run` + suite pytest complète verts

**Note de run** : (vide)

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
