# Spec technique — Onglet Swing (paper trading) dans "Mon Portefeuille"

Document de spécification, pas d'implémentation. Objectif : auditer
précisément l'état livré des 4 upgrades "Mon Portefeuille" (moteur de risque,
calendrier earnings, générateur d'ordres de rééquilibrage, journal de qualité
d'exécution), puis spécifier un nouvel onglet Swing (paper trading) sans
dupliquer ni casser ces briques. Suit le même format que
`docs/UPGRADES_MY_PORTFOLIO.md`.

---

## 0. Résumé

Restructurer "Mon Portefeuille" en une section avec deux sous-onglets :

- **Long** — l'existant, simplement renommé. Logique et fichiers backend
  inchangés.
- **Swing** — nouveau. Journal de trading swing en mode paper (positions
  fictives, aucun argent réel), avec CRUD utilisateur, surveillance
  automatique stop/objectif, critères de stratégie configurables, et vue
  d'ensemble de performance paper.

TITAN (scoring quantamental, univers S&P 500, proposals) n'est pas concerné —
aucun fichier de ce moteur n'est touché.

---

## 1. Audit de l'existant — état réel du code après les 4 upgrades

C'est la section la plus importante du document : chaque brique ci-dessous a
un comportement précis qu'il faut connaître avant de toucher quoi que ce soit,
sous peine de dupliquer du code ou de casser un calcul livré il y a moins
d'une semaine.

### 1.1 `backend/modules/my_portfolio_data.py` — le cœur du couplage

`POSITIONS: list[dict]` contient **10 dicts en dur**, un par position Long
(ticker, `target_weight_pct`, `target_amount`, `shares`, `entry_price`,
`beta`, `currency`, `price_ticker`, `sell_signal`, `correlation_alert`...).
Deux constantes calculées à l'import sont **dérivées de `POSITIONS`** :

```python
TOTAL_ENVELOPE_AMOUNT = sum(p["target_amount"] for p in POSITIONS) + CASH_RESERVE_AMOUNT
```

`TOTAL_ENVELOPE_AMOUNT` est le **dénominateur unique** du poids réel de
*chaque* ligne Long (`real_weight_pct = current_value / TOTAL_ENVELOPE_AMOUNT`).
**Toute ligne ajoutée à `POSITIONS` avec un `target_amount` non nul modifie ce
dénominateur et donc silencieusement le `real_weight_pct`/`drift_pct` de
toutes les 10 lignes existantes.** C'est le piège n°1 à documenter : les
positions Swing ne doivent **jamais** apparaître dans `POSITIONS`, même avec
`target_weight_pct: 0`.

`WATCHLIST` est une liste séparée, 0% cible, pas de `shares`/`entry_price` —
sert uniquement au badge earnings (§1.3).

### 1.2 `backend/routers/my_portfolio.py` — `GET /api/my_portfolio`

Point d'entrée unique. À chaque requête (pas de cache) :

1. Fetch prix live pour chaque `POSITIONS[i]["price_ticker"]` en parallèle
   (`ThreadPoolExecutor`), via `_safe_price()`.
2. Fetch `earnings_snapshot` pour `POSITIONS + WATCHLIST` (lecture cache pure,
   §1.3).
3. Charge `my_portfolio_risk.json` (lecture pure, §1.4).
4. Boucle sur `POSITIONS` : calcule `current_value`, `pnl_usd`/`pnl_pct`,
   `real_weight_pct` (÷ `TOTAL_ENVELOPE_AMOUNT`), `drift_pct`,
   `rebalance_alert`, **`rebalance_order`** (§1.5), merge earnings + risk
   fields.
5. Retourne `{positions, cash_reserve, watchlist, total_value, total_pnl_usd,
   drift_threshold_pct, deployment_threshold_pct, risk_snapshot}`.

Aucune persistance dans ce router : tout est recalculé à chaque GET à partir
de `POSITIONS` (statique) + prix live + les deux caches JSON en lecture
(§1.3, §1.4). **Il n'existe aujourd'hui aucun stockage inscriptible pour ce
book** — c'est exactement le trou que l'onglet Swing doit combler, sans
réutiliser cette route qui suppose des positions statiques.

### 1.3 Upgrade 2 livrée — `backend/modules/my_portfolio_earnings.py`

Module **générique par conception** : `refresh_earnings(symbols: list[str])`
et `get_earnings_snapshot(symbols: list[str])` prennent une liste de symboles
en paramètre — rien de figé sur les 10 tickers Long. Cache disque unique
`data/.my_portfolio_earnings_cache.json` (`{symbol: {next_earnings_date,
source, fetched_at}}`), TTL 24h, fail-open à deux niveaux (par ticker, par
run), Finnhub → fallback yfinance. Le router appelle `get_earnings_snapshot()`
avec `{p.get("price_ticker", p["ticker"]) for p in POSITIONS + WATCHLIST}` —
c'est cette *union de symboles* qui est le point d'extension naturel (§4.G).

Le rafraîchissement (`--refresh-my-portfolio-earnings`, cron) appelle
`refresh_earnings()` séparément — **il faut vérifier séparément que le cron
voit aussi les tickers Swing**, pas seulement le point de lecture du router
(§7, risque n°4).

### 1.4 Upgrade 1 livrée — `backend/modules/portfolio_risk.py`

Job batch hebdomadaire (`--recompute-portfolio-risk`) qui **itère
explicitement sur `POSITIONS`** (10 tickers fixes) : historique 2 ans par
ticker + `^GSPC`, log-returns en USD post-FX, corrélation (réutilise
`correlation_check._to_returns`), beta (réutilise
`backtest._capm_alpha_beta`), agrégats pondérés par **`target_weight_pct`**
(poids cible statique, pas le poids réel — déviation documentée dans le
module lui-même : un snapshot hebdo caractérise le profil de risque de
l'allocation *cible*). Persiste dans `data/my_portfolio_risk.json`, lu en
lecture pure par le router (§1.2 étape 3).

**Aucun mécanisme de filtre** ne protège ce job d'un ticker ajouté à
`POSITIONS` — il tournerait dessus sans distinction. Beta/corrélation/streak
"durable" n'ont aucun sens sur une position fictive sans capital réel engagé :
raison supplémentaire, indépendante du problème `TOTAL_ENVELOPE_AMOUNT`
(§1.1), de ne jamais mettre les tickers Swing dans `POSITIONS`.

### 1.5 Upgrade 3 livrée — générateur d'ordres de rééquilibrage

**Pas de module séparé** : `_rebalance_order()` est une fonction privée dans
`routers/my_portfolio.py` elle-même, appelée dans la boucle principale.
Entrée : `target_amount` (déjà en $, invariant `target_weight_pct% ×
TOTAL_ENVELOPE_AMOUNT`), `current_value`, `raw_native_price`/`fx` exposés par
`_safe_price()`. Sortie : `{direction, shares_native, amount_native, currency,
amount_usd}` ou `None` si pas d'alerte de dérive / prix stale / FX
indisponible. **Génère une liste, n'appelle aucun broker** (ni Alpaca ni
autre) — purement informatif, l'utilisateur exécute lui-même sur eToro.

Comme pour §1.4, cette logique dépend de `target_weight_pct`/`target_amount`
que les positions Swing n'ont pas (pas d'allocation cible, achat/vente
trade-par-trade) — aucune adaptation possible, l'exclusion doit être
structurelle (Swing hors du tableau `positions` de `/api/my_portfolio`).

### 1.6 Upgrade 4 livrée — `backend/modules/my_portfolio_executions.py` + routeur

Journal de qualité d'exécution **réel**, pas générique en pratique malgré une
architecture propre :

- Stockage : `data/my_portfolio_executions.csv`, schéma fixe (`Direction`
  BUY/SELL, `Fill_Price_Native`, `Executed_At` tz-aware, `Primary_Exchange`,
  `Market_Open_At_Fill`, `Reference_Price_USD`, `Slippage_Bps`,
  `Slippage_Usd`). Pattern IO : `FileLock` + écriture atomique `.tmp` +
  `rename` (identique à `modules/tracker/evaluation.py`).
- `compute_execution()` calcule le slippage en comparant le prix payé à un
  **prix de référence marché reconstruit a posteriori**
  (`get_historical_price`/`get_historical_fx_rate`, nouvellement créés à
  cette upgrade) — c'est un diagnostic de coût d'exécution réel (mauvais fill
  eToro hors séance), pas un journal d'événements générique.
- `_lookup_position()` résout `price_ticker`/`currency` en cherchant le
  ticker dans `POSITIONS`/`WATCHLIST` uniquement ; un ticker absent retombe
  silencieusement sur `USD`/`US` par défaut.
- `get_summary()` calcule des agrégats globaux sur **tout le CSV** :
  `total_slippage_usd`, `pct_fills_outside_market`, `worst_executions` (top 3
  par `abs(Slippage_Bps)`) — pas de champ de segmentation par "type" de
  position dans le schéma actuel.
- Endpoints : `POST /api/my_portfolio/executions` (log un fill),
  `GET /api/my_portfolio/executions` (liste + `summary`).
- Frontend : `MyPortfolioExecutionJournal.jsx`, monté en bas de
  `MyPortfolioPage.jsx` (formulaire + table + tuile agrégée).

Voir §4.F pour la décision argumentée de ne **pas** réutiliser ce module pour
le log Swing.

### 1.7 Frontend actuel

- `frontend/src/config/nav.js` — une seule entrée `{id: 'my_portfolio', label:
  'Mon Portefeuille'}` dans la section "Mon Portefeuille". Un seul routage
  dans `App.jsx` (`my_portfolio: MyPortfolioPage`, lazy-loaded). **Aucun
  pattern de sous-onglets existant ailleurs dans le frontend** (recherché,
  aucun composant `*Page.jsx` n'a de switch d'onglets internes aujourd'hui) —
  ce sera un nouveau pattern UI à introduire, pas une réutilisation.
- `MyPortfolioPage.jsx` — composant unique : bandeau, 5 tuiles, bandeau risque
  conditionnel (`risk_snapshot`), table des 10 positions, watchlist, puis
  `<MyPortfolioExecutionJournal>` monté en dur en bas de page.
- `frontend/src/hooks/useApi.js` — `useMyPortfolio()` (query `'my_portfolio'`),
  `useMyPortfolioExecutions()` + `useLogExecution` (mutation avec
  invalidation de query). Pattern à reproduire pour Swing.
- CSS : classes `mp-*` dans `index.css`, identité visuelle volontairement
  distincte du reste du dashboard (accent cuivre/or) — "pour ne jamais
  laisser croire que ces positions sont notées ou décidées par TITAN"
  (commentaire du fichier lui-même). Le même principe doit s'appliquer à
  Swing : distinct visuellement de Long ET de TITAN.

### 1.8 Précédent de persistance inscriptible dans le repo

**Aucun usage de SQLite nulle part dans le repo** (recherché,
`import sqlite3` : 0 résultat hors venv). Le pattern établi pour tout état
inscriptible léger est **JSON ou CSV + `FileLock` + écriture atomique
`.tmp`/`rename`** :

| Fichier | Format | Écrit par |
|---|---|---|
| `data/my_portfolio_executions.csv` | CSV | Upgrade 4, cette semaine |
| `data/my_portfolio_risk.json` | JSON | Upgrade 1, cette semaine |
| `data/.my_portfolio_earnings_cache.json` | JSON | Upgrade 2, cette semaine |
| `data/equity_state.json`, `data/macro_state.json` | JSON | tracker (plus ancien) |
| `trade_journal.csv`/`.duckdb` | CSV+DuckDB | moteur TITAN (système plus lourd, journal réel) |

DuckDB n'est utilisé que pour `trade_journal` (le vrai journal de trading
TITAN, volumes et requêtes plus conséquents) — pas pour ce genre d'état CRUD
léger. **Les 3 briques livrées cette semaine (Upgrades 1, 2, 4) ont toutes
choisi JSON/CSV + FileLock, jamais SQLite**, y compris pour un besoin de log
transactionnel (Upgrade 4) directement comparable au besoin Swing.

### 1.9 Persistance et redéploiement — vérifié

`.gitignore` exclut tout `backend/data/*.json` et `backend/data/*.csv` —
**ces fichiers ne sont jamais versionnés**, ils vivent uniquement sur le
disque du VPS. Le déploiement (`git_auto_sync.sh`, service `swing-api.service`
via systemd) fonctionne par `git pull`/rebase **en place** dans
`/home/swing/swingquant` — ce n'est pas un rebuild de conteneur ni un clone
frais. `git pull` ne touche jamais les fichiers non trackés : un fichier
`data/my_portfolio_swing.json` survit donc à tous les redéploiements et syncs
**par construction**, tant que le disque VPS n'est pas réinitialisé et
qu'aucune commande destructive (`git clean -fdx`) n'est lancée.

Point faible identifié en auditant `scripts/backup_journal.sh` : il ne
sauvegarde que `trade_journal.duckdb`/`.csv` et `equity_state.json` — **aucun
des 3 fichiers `my_portfolio_*` livrés cette semaine n'est sauvegardé
hors-VPS**, y compris le journal d'exécution réel de l'Upgrade 4. C'est un
angle mort préexistant, pas introduit par Swing, mais à combler en même temps
(§7, risque n°6).

### 1.10 `use_alpaca=False` — raison documentée dans le code

`get_current_price_detailed(ticker, use_alpaca=True)` dans
`modules/tracker/market.py` : `use_alpaca=False` force yfinance même si
`BROKER_MODE=alpaca`. Raison, citée depuis le docstring (audit 2026-08-21) :
le plan Alpaca gratuit ne sert que le carnet **IEX** (une seule place), qui
dérive de plusieurs % vs le NBBO consolidé sur les tickers peu liquides
(FMX/HRTG constatés à ±7%, PSX à ±3%). Sans intérêt pour des positions non
exécutées via Alpaca — yfinance (consolidé, retard ~15 min) colle mieux au
prix affiché par un broker tiers. `routers/my_portfolio.py::_safe_price()`
appelle déjà `get_current_price_detailed(price_ticker, use_alpaca=False)`
pour ce motif exact. **Le même raisonnement s'applique mot pour mot aux
positions Swing** : paper trading, jamais exécutées via Alpaca.

### 1.11 Mapping ticker/devise/alias

Porté par 3 champs optionnels sur chaque dict `POSITIONS[i]` :
`price_ticker` (défaut = `ticker`), `currency` (défaut `"USD"`),
`shares_per_adr` (défaut `1`). Consommés par `_safe_price()`. Le cas LNVGY
documente explicitement la faute évitée : LNVGY (ADR US) ne remonte pas de
prix fiable sur yfinance → `price_ticker="0992.HK"` (cotation primaire HKEX,
l'instrument réellement détenu) avec `shares_per_adr` laissé à son **défaut
1** — le commentaire du fichier précise que les 37.094844 actions sont des
actions ordinaires HK détenues directement, **pas** des unités ADR, donc
aucun ratio ×20 ne doit être appliqué. `_FX_PAIR_FOR_CURRENCY = {"EUR":
"EURUSD=X", "HKD": "USDHKD=X"}` (dans `routers/my_portfolio.py`) est
**dupliqué à l'identique** dans `modules/my_portfolio_executions.py`
(`_CURRENCY_TO_FX_PAIR`), avec un commentaire explicite justifiant la
duplication : *"`modules/` ne doit pas dépendre de `routers/`"*. Un 3ᵉ
copier-coller de cette même constante dans le module Swing consoliderait
un anti-pattern déjà à sa 2ᵉ occurrence (§7, risque n°5).

---

## 2. Décision d'architecture — persistance des positions Swing

**Choix : fichier JSON `backend/data/my_portfolio_swing.json`, liste
d'enregistrements, protégé par `FileLock` + écriture atomique `.tmp`/rename —
même pattern exact que `my_portfolio_executions.py` (§1.6/§1.8).**

Justification :

- **Cohérence à 100% avec le précédent établi.** Les 3 briques livrées cette
  semaine (risk snapshot, cache earnings, journal d'exécution) ont toutes
  choisi JSON/CSV + FileLock pour un besoin d'état inscriptible léger — y
  compris l'Upgrade 4, qui est le cas le plus proche de Swing (CRUD manuel,
  faible volume, un seul utilisateur, calculs dérivés à la lecture).
  Introduire SQLite serait la première occurrence de cette techno dans tout
  le repo, pour un gain non justifié par le volume (quelques dizaines de
  trades, ajout/clôture au rythme humain, aucune écriture concurrente hors
  la seule instance FastAPI).
- **Pas de besoin relationnel.** Aucune jointure, aucune requête agrégée
  complexe qu'un simple `for` Python sur une liste de dicts ne couvre pas
  (le calcul d'overview, §4.E, est un `filter`/`sum` trivial).
- **FileLock règle déjà la seule contrainte de concurrence réelle** (un POST
  d'ajout/clôture simultané à une lecture GET) — prouvé en prod par l'Upgrade
  4 cette semaine.
- **JSON plutôt que CSV** (contrairement à l'Upgrade 4) parce que le modèle
  Swing a une structure plus riche par enregistrement (champs de clôture
  optionnels selon le statut, checklist de critères imbriquée) et un besoin
  de mise à jour ciblée par `id` (clôturer *une* position) — un JSON `list[
  dict]` avec ré-écriture complète du fichier à chaque mutation reste trivial
  à ce volume, alors qu'un CSV imposerait des colonnes vides pour tous les
  champs de clôture des positions encore ouvertes.
- **Survie aux redéploiements** : automatique par construction (§1.9),
  puisque `backend/data/*.json` est déjà dans la règle `.gitignore`
  existante — aucune modification de `.gitignore` nécessaire.

Fichier gitignoré automatiquement (règle `backend/data/*.json` déjà présente)
→ jamais commité, jamais poussé en clair sur GitHub. À ajouter explicitement
à `scripts/backup_journal.sh` (§7, risque n°6) pour ne pas reproduire l'angle
mort déjà présent sur `my_portfolio_executions.csv`/`my_portfolio_risk.json`.

---

## 3. Restructuration frontend — sous-onglets Long / Swing

Pas de changement de `nav.js`/`App.jsx` : une seule entrée de navigation
`my_portfolio` subsiste, pointant toujours vers `MyPortfolioPage.jsx`
(lazy-loaded, id de routage inchangé). Le découpage Long/Swing est **interne
au composant**, pas structurel côté navigation :

- Extraire le contenu actuel de `MyPortfolioPage.jsx` (bandeau, tuiles,
  bandeau risque, table, watchlist, `<MyPortfolioExecutionJournal>`) tel
  quel, sans changement de logique, dans un nouveau
  `MyPortfolioLongTab.jsx`.
- `MyPortfolioPage.jsx` devient une coquille fine : `useState` local pour
  l'onglet actif (`'long' | 'swing'`), deux boutons/segments "Long"/"Swing",
  rend `<MyPortfolioLongTab>` ou `<MyPortfolioSwingTab>` selon l'état.
- Nouveau `MyPortfolioSwingTab.jsx` — bandeau + tuiles overview + formulaire
  d'ajout + table positions + flags stop/objectif (§4).

---

## 4. Blocs fonctionnels

### Bloc A — Modèle de données & CRUD positions Swing

**Problème résolu** : aucun stockage inscriptible n'existe pour des positions
gérées par l'utilisateur via l'interface (§1.2, §1.8).

**Existant réutilisable** : pattern IO complet de
`modules/my_portfolio_executions.py` (`FileLock`, écriture atomique
`.tmp`/rename, fail-open lecture si fichier absent) ; pattern de router
Pydantic `BaseModel` + validation 400 explicite de
`routers/my_portfolio_executions.py` ; `Security(api_core.require_auth)` déjà
sur toutes les routes `my_portfolio`.

**Fichiers à créer** :
- `backend/modules/my_portfolio_swing.py` — CRUD JSON (`load_positions()`,
  `add_position()`, `close_position()`), calcul des champs dérivés (P&L,
  flags stop/objectif — délégués aux Blocs B/C plutôt que dupliqués ici).
- `backend/data/my_portfolio_swing.json` — runtime, déjà couvert par
  `.gitignore` (`backend/data/*.json`).
- `backend/routers/my_portfolio_swing.py` — endpoints.

**Endpoints** :
- `POST /api/my_portfolio/swing` — créer une position (statut `OPEN`
  implicite).
- `GET /api/my_portfolio/swing` — liste toutes les positions + prix live +
  flags + bloc `overview` (Bloc E).
- `POST /api/my_portfolio/swing/{id}/close` — clôturer (prix, date, raison).
  **Pas de `PUT`/`PATCH` général** sur stop/objectif/thèse — immuables après
  création (voir cas limites).

**Nouveaux champs** (par enregistrement) :

```json
{
  "id": "a1b2c3d4-...",
  "ticker": "SEZL",
  "name": "Sezzle Inc.",
  "entry_date": "2026-08-23",
  "entry_price": 42.10,
  "amount_invested": 100.0,
  "rsi14_entry": 38.2,
  "stoch_k_entry": 22.5,
  "technical_rating_entry": "Strong Buy",
  "thesis": "Rebond sur support MA50 + volume",
  "stop_loss": 38.50,
  "target": 50.00,
  "status": "OPEN",
  "created_at": "2026-08-23T16:12:00+02:00",
  "entered_during_market_hours": true,
  "close_price": null,
  "close_date": null,
  "close_reason": null
}
```

`technical_rating_entry` ∈ `{"Strong Buy", "Buy", "Hold", "Sell", "Strong
Sell"}`. `close_reason` ∈ `{"stop_hit", "target_hit", "thesis_invalidated",
"other"}` quand `status="CLOSED"`.

**Logique de calcul** : `shares_paper = amount_invested / entry_price`
(dérivé, jamais stocké) ; `pnl_usd = amount_invested × (price/entry_price -
1)` pour une position `OPEN` (`price` = prix live, Bloc B), ou
`amount_invested × (close_price/entry_price - 1)` pour une position `CLOSED`.

**Affichage frontend** : formulaire d'ajout (tous les champs ci-dessus,
checklist Bloc D en lecture seule informative), table positions
ouvertes/clôturées séparées ou filtrables par statut.

**Cas limites** :
- `entry_price`/`amount_invested` doivent être `> 0` (validation 400, même
  pattern que `my_portfolio_executions`).
- **Stop/objectif immuables** : pour "changer" un stop, l'utilisateur doit
  clôturer la position existante (`close_reason="other"` ou pertinent) puis
  créer un **nouvel enregistrement** — pas de réouverture d'un `id` clôturé.
  Ceci empêche de réécrire l'historique a posteriori (demande explicite).
- Une position `CLOSED` reste en lecture seule à 100% (aucun endpoint ne la
  modifie).
- v1 suppose des positions **longues uniquement** (stop < entrée < objectif)
  — cohérent avec les critères d'entrée orientés achat (RSI/Stoch bas,
  Strong Buy). Le support du short n'est pas demandé et n'est pas construit ;
  ajouter un champ `direction` serait l'extension naturelle si besoin futur
  (hors scope ici).

### Bloc B — Valorisation temps réel & devises (réutilisation)

**Problème résolu** : calculer un P&L live nécessite un prix courant fiable
et cohérent avec ce que l'utilisateur voit réellement sur son broker paper.

**Existant réutilisable** : `get_current_price_detailed(ticker,
use_alpaca=False)` (§1.10, même raison exacte : positions non exécutées via
Alpaca) ; le *shape* du mapping `price_ticker`/`currency`/`shares_per_adr`
(§1.11) pour les tickers Swing non-US éventuels, **sans jamais introduire de
défaut `shares_per_adr` ≠ 1** (piste déjà corrigée sur LNVGY, à ne pas
réintroduire).

**Fichiers à modifier** : aucun fichier existant à modifier — `_safe_price()`
et `_usd_multiplier()` restent privés à `routers/my_portfolio.py` et ne sont
pas appelés depuis le module Swing (imports `routers → modules` évités,
même convention que §1.6/§1.11).

**Point d'attention (voir §7, risque n°5)** : `_FX_PAIR_FOR_CURRENCY` est
déjà dupliqué 2 fois (`routers/my_portfolio.py`,
`modules/my_portfolio_executions.py`). Un 3ᵉ copier-coller dans
`my_portfolio_swing.py` est le pattern actuel attendu (cohérence avec
l'existant), mais c'est le moment recommandé pour l'extraire une bonne fois
dans un petit module partagé (ex. `modules/fx_pairs.py`, 5 lignes) plutôt que
d'ajouter une 3ᵉ copie — décision à trancher au moment de l'implémentation,
pas un blocant de cette spec.

**Cas limites** : mêmes garde-fous que `_safe_price()` — prix indisponible →
`current_price: null`, P&L non calculable ce jour-là plutôt qu'un fallback
trompeur (pas de `target_amount` de repli ici, cette notion n'existe pas côté
Swing).

### Bloc C — Surveillance automatique des niveaux (stop/objectif)

**Problème résolu** : signaler qu'un stop ou un objectif est franchi sans
jamais clôturer automatiquement (décision humaine).

**Existant réutilisable** : `get_current_price_detailed` (Bloc B) pour le
prix live comparé.

**Fichiers à modifier** : `backend/modules/my_portfolio_swing.py` — calcul
du flag à chaque `GET`, pas de job batch séparé (contrairement au risk
engine hebdo de l'Upgrade 1 — ici la volatilité intraday justifie un calcul
à chaque chargement, comme le fait déjà `_safe_price` pour le book Long).

**Nouveaux champs** (par position `OPEN`) : `level_flag` ∈ `{"stop_hit",
"target_hit", "in_range"}`.

**Logique de calcul** : `price <= stop_loss → "stop_hit"` ;
`price >= target → "target_hit"` ; sinon `"in_range"`. Évalué uniquement pour
`status="OPEN"` ; jamais de mutation de `status` déclenchée par ce calcul.

**Affichage frontend** : badge visuel distinct par flag (ex. 🔴 "Stop
touché" / 🟢 "Objectif atteint"), sans action automatique — l'utilisateur
clôture manuellement s'il le souhaite (Bloc A).

**Cas limites** : prix indisponible → pas de flag halluciné, afficher l'état
"prix indisponible" plutôt qu'un `in_range` par défaut trompeur (même
philosophie fail-open que §1.3/§1.4).

### Bloc D — Critères de stratégie configurables (pas en dur)

**Problème résolu** : éviter de reproduire le pattern déjà corrigé deux fois
dans ce book (badge earnings codé en dur §1.3, ratio ADR codé en dur §1.11) —
les seuils de stratégie ne doivent pas être une constante Python figée sans
mécanisme d'édition.

**Existant réutilisable** : pattern JSON + `FileLock` (Bloc A/§1.8).

**Fichiers à créer** : `backend/data/my_portfolio_swing_criteria.json`
(`{stoch_k_max: 70, technical_rating_min: "Strong Buy"}`) ; endpoints
`GET`/`PUT /api/my_portfolio/swing/criteria` dans le même routeur que le
Bloc A.

**Affichage frontend** : checklist affichée dans le formulaire d'ajout,
comparant les valeurs saisies (`rsi14_entry`, `stoch_k_entry`,
`technical_rating_entry`) aux critères configurés — ✅/⚠ par critère.

**Cas limite / décision à confirmer avec l'utilisateur au moment de
l'implémentation** : la spec initiale ne précise pas si la checklist est
**bloquante** (empêche la soumission si un critère n'est pas respecté) ou
**purement informative**. Recommandation de cette spec : **informative,
non bloquante** — le but déclaré de l'onglet Swing est de *tester* une
stratégie, y compris volontairement hors critères pour comparer ; bloquer la
saisie irait à l'encontre de cet usage. Le critère `technical_rating_min`
implique une relation d'ordre (Strong Buy > Buy > Hold > Sell > Strong Sell)
à définir explicitement dans le code de comparaison.

### Bloc E — Vue d'ensemble Swing

**Problème résolu** : donner une lecture agrégée de la performance paper
sans avoir à la recalculer à l'œil sur chaque ligne.

**Existant réutilisable** : pattern `get_summary()` de
`my_portfolio_executions.py` (agrégats calculés à la lecture, jamais
persistés — recalculés à chaque `GET`).

**Fichiers à modifier** : `backend/modules/my_portfolio_swing.py` — fonction
`get_overview(positions)`.

**Nouveaux champs** (bloc racine `overview` de la réponse `GET
/api/my_portfolio/swing`) :

```json
{
  "n_open": 3,
  "n_closed": 12,
  "win_rate_pct": 58.3,
  "avg_win_pct": 6.1,
  "avg_loss_pct": -3.4,
  "avg_win_loss_ratio": 1.79,
  "cumulative_pnl_usd": 214.30
}
```

`win_rate_pct`/`avg_win_pct`/`avg_loss_pct` calculés uniquement sur les
positions `CLOSED` (un P&L latent n'est pas un résultat). `cumulative_pnl_usd`
= somme des P&L réalisés (`CLOSED`) + latents (`OPEN`), **jamais mélangé
avec le P&L Long** (§4.H).

**Cas limites** : `n_closed = 0` → `win_rate_pct`/`avg_win_pct`/`avg_loss_pct`
= `null` plutôt que `0` ou une division par zéro masquée.

### Bloc F — Log de l'heure de saisie : ne pas réutiliser le journal d'exécution (Upgrade 4)

**Position argumentée : ne pas réutiliser `my_portfolio_executions.py`/
`.csv`.**

Arguments contre la réutilisation, fondés sur l'audit §1.6 :

1. **Incompatibilité sémantique du schéma.** Ce journal calcule
   `Slippage_Bps`/`Slippage_Usd` en comparant le prix payé à un **prix de
   marché reconstruit a posteriori** — conçu pour détecter un coût
   d'exécution réel (mauvais fill eToro hors séance). Une entrée Swing n'a
   pas de "fill" : le prix d'entrée saisi *est* généralement proche du prix
   de marché au même instant, donc la formule produirait un slippage proche
   de zéro et sans signification — ce n'est pas la question que l'onglet
   Swing pose.
2. **Corruption des agrégats réels.** `get_summary()` calcule
   `total_slippage_usd`/`pct_fills_outside_market` sur **tout le CSV** sans
   colonne de segmentation. Mélanger des entrées Swing y fausserait
   silencieusement la métrique de coût d'exécution réelle que l'Upgrade 4
   vient tout juste de corriger — un risque de régression direct sur du code
   livré cette semaine.
3. **`_lookup_position()` résout contre `POSITIONS`/`WATCHLIST` uniquement**
   (§1.6) — un ticker Swing absent de ces deux listes retomberait
   silencieusement sur `USD`/`US` par défaut, pouvant mal étiqueter la devise
   d'un ticker Swing non-US.

**Ce qui est réellement réutilisable** : la **technique**, pas les données —
`FileLock`+écriture atomique (déjà repris au Bloc A), l'exigence de
datetime tz-aware, et surtout `exchange_hours.is_open()` (module créé par
l'Upgrade 4, §1.6) pour dériver `entered_during_market_hours` **directement
comme champ du record Swing** (Bloc A), sans passer par le CSV
`my_portfolio_executions`. Zéro duplication de logique de calcul horaire,
zéro pollution de la donnée réelle.

### Bloc G — Calendrier earnings (Upgrade 2) : pertinence pour Swing — recommandation OUI

**Position argumentée : réutiliser `my_portfolio_earnings.py` tel quel.**

Arguments pour, fondés sur l'audit §1.3 :

1. `refresh_earnings(symbols)`/`get_earnings_snapshot(symbols)` prennent
   déjà une **liste de symboles arbitraire** en paramètre — le module n'est
   pas câblé sur les 10 tickers Long, c'est le router qui construit
   aujourd'hui l'union `POSITIONS + WATCHLIST`. Étendre cette union aux
   tickers Swing `OPEN` est une modification d'une ligne côté appelant, zéro
   changement du module.
2. Coût négligeable : cache clé par symbole, TTL 24h, Finnhub gratuit
   (60 appels/min) déjà utilisé pour 11 tickers — ajouter quelques tickers
   Swing reste très en dessous du quota.
3. **Valeur produit réelle, pas du scope creep** : savoir qu'une publication
   de résultats approche est arguablement *plus* critique en swing (horizon
   court, un gap earnings peut franchir stop et objectif dans la même
   séance) qu'en positionnement long terme.

**Fichiers à modifier** : `backend/routers/my_portfolio_swing.py` — appeler
`get_earnings_snapshot(swing_open_tickers)` (lecture pure, même fonction),
réutiliser `_earnings_badge()` (déjà une fonction pure dans
`routers/my_portfolio.py` — à extraire en utilitaire partagé ou dupliquer les
5 lignes, à trancher à l'implémentation).

**Point d'attention pour le cron** : `refresh_earnings()` (le *writer*,
`--refresh-my-portfolio-earnings`) doit aussi voir les tickers Swing au
moment de son exécution — il doit charger `my_portfolio_swing.json`
dynamiquement (positions `OPEN` au moment du run), pas seulement
`POSITIONS` statique. Sinon un ticker Swing ajouté après le passage du cron
n'aura simplement pas de badge avant le lendemain (dégradation gracieuse déjà
prévue par le fail-open du module — pas un bug bloquant, juste à documenter
dans le code du cron).

### Bloc H — Isolation stricte : moteur de risque (Upgrade 1) & rebalance (Upgrade 3)

**Règle structurelle, non négociable, fondée sur §1.1/§1.4/§1.5** : les
positions Swing ne doivent **jamais** apparaître dans `my_portfolio_data.py::
POSITIONS`, et la réponse de `GET /api/my_portfolio/swing` ne doit **jamais**
être mergée dans le tableau `positions` de `GET /api/my_portfolio`.

Raisons concrètes déjà identifiées dans le code actuel :

- `TOTAL_ENVELOPE_AMOUNT` (§1.1) est recalculé à l'import de
  `my_portfolio_data.py` à partir de `POSITIONS` — toute ligne ajoutée avec
  un `target_amount` non nul fausse silencieusement le `real_weight_pct` et
  le `drift_pct` des 10 positions Long existantes.
- `portfolio_risk.py` (§1.4) itère `POSITIONS` sans filtre pour son job hebdo
  beta/corrélation — une position fictive sans capital réel n'a pas de sens
  dans ce calcul, et polluerait `my_portfolio_risk.json`/le streak "durable".
- Le générateur d'ordres (§1.5) dépend de `target_weight_pct`/`target_amount`
  que les positions Swing n'ont structurellement pas (pas d'allocation
  cible).
- `total_value`/`total_pnl_usd` de `GET /api/my_portfolio` (§1.2) sont des
  agrégats du book Long réel — ne doivent jamais inclure de P&L paper
  (demande explicite de l'utilisateur, confirmée architecturalement par le
  fait que ces deux sommes sont déjà calculées exclusivement à partir de
  `rows` dérivé de `POSITIONS`).

Conséquence : `GET /api/my_portfolio/swing` est un endpoint **entièrement
séparé**, un hook React Query séparé (`useMyPortfolioSwing()`, query key
`'my_portfolio_swing'` distincte de `'my_portfolio'`), rendu par un
composant frontend séparé (`MyPortfolioSwingTab.jsx`) qui n'étend jamais la
table Long.

### Bloc I — Avertissement horaires à l'ajout

**Problème résolu** : signaler (sans bloquer) qu'une saisie a lieu hors
séance US normale (15h30–22h heure de Paris).

**Existant réutilisable** : `exchange_hours.is_open("US", at)` (module créé
à l'Upgrade 4, DST-aware) — plus robuste que l'heuristique historique
`is_market_hours()` de `modules/tracker/market.py` (§1.10) qui ne gère pas
les jours fériés/half-days sans Alpaca configuré.

**Logique** : le flag **autoritatif** est déjà calculé côté serveur à la
création (`entered_during_market_hours`, Bloc A/F, via
`exchange_hours.is_open`). L'avertissement dans le formulaire d'ajout, lui,
peut être calculé **côté client** en pur affichage (heure locale du
navigateur convertie), pour éviter un aller-retour réseau sur un simple
indice non bloquant — la valeur qui compte réellement (persistée) reste le
calcul serveur.

**Affichage frontend** : bandeau non bloquant dans le formulaire si l'heure
actuelle est hors 15h30–22h Paris, cohérent avec le tableau de rappel
horaires déjà affiché par `MyPortfolioExecutionJournal.jsx` (Upgrade 4) —
réutiliser ce même petit tableau de référence plutôt que d'en écrire un
second.

### Bloc J — Structure frontend détaillée

**Fichiers à créer** :
- `frontend/src/components/MyPortfolioLongTab.jsx` — contenu actuel de
  `MyPortfolioPage.jsx` déplacé tel quel (aucun changement de logique).
- `frontend/src/components/MyPortfolioSwingTab.jsx` — nouveau : bandeau,
  tuiles overview (Bloc E), formulaire d'ajout (Blocs A/D/I), table
  positions avec flags (Bloc C), badges earnings (Bloc G).

**Fichier à modifier** :
- `frontend/src/components/MyPortfolioPage.jsx` — devient une coquille de
  10-20 lignes : état local `activeTab`, deux boutons de bascule, rend le
  sous-composant sélectionné. **Le nom du fichier et l'export par défaut ne
  changent pas** — `App.jsx` continue de faire `my_portfolio:
  MyPortfolioPage` sans modification (§7, risque n°7).
- `frontend/src/hooks/useApi.js` — ajouter `useMyPortfolioSwing()`,
  `useAddSwingPosition()`, `useCloseSwingPosition()`, en suivant le pattern
  exact de `useMyPortfolioExecutions`/`useLogExecution` déjà en place.
- `frontend/src/index.css` — nouvelle famille de classes `mp-swing-*` pour
  les éléments spécifiques (flags stop/objectif, checklist critères) ;
  réutiliser les primitives `mp-*` génériques existantes (`mp-tile`,
  `mp-value-cell`, `mp-pnl-pos`/`mp-pnl-neg`) là où la forme correspond,
  plutôt que reconstruire un système parallèle complet.

---

## 5. Ordre d'implémentation recommandé

| Ordre | Bloc | Complexité relative | Pourquoi cet ordre |
|---|---|---|---|
| 1 | **A — Persistance & CRUD backend** | 🟠 Moyenne | Fondation : tout le reste (B à J) dépend de l'existence du stockage et des endpoints. |
| 2 | **B — Valorisation prix/devise** | 🟢 Faible | Branchement direct de `get_current_price_detailed(use_alpaca=False)`, zéro nouvelle fonction de fetch. |
| 3 | **F — Log horaire (champ sur le record)** | 🟢 Faible | Deux champs calculés à la création via `exchange_hours.is_open()` déjà existant — pas de nouveau module. |
| 4 | **D — Critères configurables** | 🟢 Faible | Petit JSON config + 2 endpoints, indépendant du reste. |
| 5 | **C — Surveillance stop/objectif** | 🟢 Faible-Moyenne | Comparaison simple à chaque `GET`, réutilise le prix déjà fetché au Bloc B. |
| 6 | **E — Vue d'ensemble** | 🟢 Faible | Agrégats Python simples sur les positions déjà chargées par le Bloc A. |
| 7 | **I — Avertissement horaires** | 🟢 Faible | Calcul client pur + le flag serveur déjà produit par le Bloc F. |
| 8 | **G — Calendrier earnings (réutilisation)** | 🟢 Faible | Extension d'un set de symboles déjà construit côté router Long, aucune nouvelle logique de fetch. |
| 9 | **J — Frontend Long/Swing** | 🟠 Moyenne | Refactor du fichier existant (extraction sans régression) + nouveau composant complet (formulaire, table, hooks). À faire une fois l'API stabilisée (Blocs A-E) pour éviter de recoder le frontend à chaque changement de schéma. |
| — | **H — Isolation risk/rebalance** | *(garde-fou transversal)* | Pas une étape d'implémentation en soi : une contrainte à vérifier à chaque Bloc A/B/C (ne jamais toucher `my_portfolio_data.py::POSITIONS`, ne jamais merger dans `GET /api/my_portfolio`). Vérification finale recommandée avant mise en prod : diff de `POSITIONS` avant/après pour confirmer 0 changement. |

Blocs D, F, I sont mutuellement indépendants entre eux une fois A livré, et
peuvent être menés dans n'importe quel ordre entre eux. G ne peut être fait
qu'après A (a besoin de la liste des tickers Swing `OPEN`).

---

## 6. Risques identifiés — collisions possibles avec les 4 upgrades livrées

1. **Ajout accidentel d'un ticker Swing dans `POSITIONS`** (copier-coller
   d'un enregistrement Long comme modèle) → fausse silencieusement
   `TOTAL_ENVELOPE_AMOUNT` et donc `real_weight_pct`/`drift_pct` des 10
   positions Long. Garde-fou recommandé : un test qui fige `len(POSITIONS)`
   et/ou la liste exacte des tickers Long, à faire échouer explicitement si
   un ticker Swing y apparaît par erreur.
2. **Réutilisation du CSV/module `my_portfolio_executions`** pour le log
   Swing (au lieu du champ dédié du Bloc A/F) → pollue `total_slippage_usd`/
   `pct_fills_outside_market`, la métrique que l'Upgrade 4 vient de
   corriger cette semaine. Garde-fou : le module Swing n'importe jamais
   `modules/my_portfolio_executions.py` ni son routeur.
3. **`portfolio_risk.py` (job hebdo) ne doit jamais recevoir de ticker
   Swing.** Si un besoin de beta/corrélation informatif sur une position
   Swing apparaît un jour, ce serait un calcul strictement séparé, jamais
   mergé dans `my_portfolio_risk.json`/`risk_snapshot` du book Long.
4. **Cron earnings (`--refresh-my-portfolio-earnings`) doit lire les
   tickers Swing dynamiquement** depuis `my_portfolio_swing.json` au moment
   du run, pas seulement `POSITIONS` statique — sinon un ticker Swing ajouté
   après le passage du cron reste sans badge jusqu'au lendemain (dégradation
   gracieuse déjà prévue par le fail-open existant, à documenter plutôt qu'à
   corriger).
5. **`_FX_PAIR_FOR_CURRENCY`/`_CURRENCY_TO_FX_PAIR` déjà dupliqué 2 fois**
   (`routers/my_portfolio.py`, `modules/my_portfolio_executions.py`). Un 3ᵉ
   copier-coller dans le module Swing consoliderait un anti-pattern à sa
   2ᵉ occurrence déjà connue — décision à prendre à l'implémentation
   (extraire un petit module partagé vs. dupliquer une 3ᵉ fois par
   cohérence avec l'existant).
6. **Aucune sauvegarde hors-VPS pour `my_portfolio_swing.json`** — angle
   mort déjà présent pour `my_portfolio_executions.csv` et
   `my_portfolio_risk.json` (§1.9), tous les trois absents de
   `scripts/backup_journal.sh`. Une perte disque VPS efface l'historique
   Swing sans recours. Recommandé : ajouter les 3 fichiers à
   `backup_journal.sh` au moment de ce chantier, referme un trou déjà
   identifié par cet audit plutôt que d'en créer un nouveau.
7. **`MyPortfolioPage.jsx` est le point d'entrée lazy-loadé par `App.jsx`**
   (`my_portfolio: MyPortfolioPage`) — le refactor Long/Swing doit garder ce
   nom de fichier et cet export par défaut inchangés, sous peine de casser
   le routage pour un gain purement cosmétique.
8. **Ambiguïté direction (long-only)** — la spec suppose des positions
   Swing longues uniquement (stop < entrée < objectif), cohérent avec des
   critères d'entrée orientés achat. Un besoin futur de paper *short*
   inverserait la sémantique stop/objectif et nécessiterait un champ
   `direction` — non traité ici, extension future si demandée.
9. **Checklist de critères (Bloc D) bloquante ou informative** — ambiguïté
   non tranchée par la demande initiale ; cette spec recommande
   informative/non-bloquante (§4.D) mais c'est un point à confirmer avant
   ou pendant l'implémentation, pas une certitude déduite du code existant.
