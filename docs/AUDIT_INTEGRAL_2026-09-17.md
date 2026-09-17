# Audit intégral SwingQuant TITAN — 2026-09-17

**Périmètre** : backend (API, tracker, broker, proposer, scoring), crons, données
live, journal de trades, compte Alpaca paper, frontend (build/tests), docs.
**Méthode** : lecture du code + logs prod + interrogation directe de l'API
Alpaca + backtest sur l'historique de snapshots + suite de tests/lint.
**Verdict en une phrase** : le code est propre (1386 tests verts, ruff/mypy/
eslint OK) mais **la couche d'exécution détruit le signal** — le compte paper
est à −1,2 % depuis avril alors que le S&P fait +7,5 % et que le panier
TITAN top-20 rééquilibré chaque semaine fait +18 % net sur la même fenêtre.

---

## 0. Résumé exécutif

| Question | Réponse |
|---|---|
| Le scoring TITAN a-t-il un edge ? | Oui, modeste et concentré (IC composite +0,037, momentum +0,119). Sur la fenêtre live 28/04 → 17/09 : top-20 équipondéré, rebalance hebdo, 10 bps slippage = **+18,1 % net**, hit-rate 62 %, vs SPY **+7,5 %**. 21 périodes seulement : indicatif, pas significatif. |
| Le compte gagne-t-il de l'argent ? | **Non.** Equity Alpaca 98 978 $ (−1,0 %), 11 trades clos (6W/5L), gains moyens +3 %, pertes moyennes −16 %. |
| Pourquoi l'écart ? | 1) 80 % du capital dort en cash (6 positions, 20 k$ investis). 2) Les stops n'existent pas chez le broker (bug `DAY`). 3) Le journal est corrompu par 3 bugs de synchronisation. 4) Les sorties coupent les gagnants à +3 % puis rachètent le même titre 2 semaines plus tard. 5) L'auto-approve achète à l'ouverture, dans l'ordre de la liste, sans regarder le buy_signal. |
| Est-ce dangereux si on passe en réel ? | **Oui, en l'état** : 4 positions sur 6 n'ont aucun stop, ni chez Alpaca ni dans le journal (`nan%→SL` dans le tracker). |
| Qualité du code | Bonne : tests, lint, types, CI. Les problèmes sont fonctionnels et opérationnels, pas de la dette syntaxique. |

**État live au 17/09 19:00 UTC** (paper) :

| Ticker | Qté | PnL | SL broker | SL journal | Scores entrée | Thèse LT |
|---|---|---|---|---|---|---|
| CF | 50 | +12,2 % | aucun | aucun | absents | TRIM (revisions 25) |
| MU | 1 | −3,2 % | aucun | 652 (−35 %) | ok | HOLD |
| EIX | 80 | −23,6 % | aucun | 49,7 (−31 %) | ok | **ADD_ON** (rang #30, Risk 27) |
| EOG | 10 | −2,0 % | aucun | aucun | absents | NO_DATA |
| HAS | 39 | −4,8 % | aucun | aucun | absents | NO_DATA |
| NEM | 22 | +1,2 % | aucun | aucun | absents | NO_DATA |

---

## 1. Défauts P0 — sécurité du capital

### P0-1 · Les brackets Alpaca sont envoyés en `time_in_force=DAY` → SL/TP expirent chaque soir

- **Preuve** : `broker_gateway.py:471` `TimeInForce.DAY`. Interrogation Alpaca :
  100 % des jambes STOP depuis juillet ont `canceled_at = 20:00–20:02 UTC`
  (clôture NYSE) le jour même du fill, les jambes LIMIT `expired` au même
  instant. Aucun ordre ouvert sur le compte aujourd'hui.
- **Impact** : la seule protection est le tracker (cron 2 min, prix
  yfinance/Alpaca, clôture par market order). Gap overnight, panne cron, panne
  provider = aucune protection. Sur du long terme (60 j+) le bracket DAY est
  un non-sens.
- **Fix** : `TimeInForce.GTC` ; après fill, relire les `legs` du parent et
  vérifier qu'un STOP `accepted/new` existe ; tracker : à chaque cycle, si une
  position n'a aucun ordre stop ouvert chez le broker → ré-armer un
  `StopOrderRequest` GTC au SL journal et alerter Telegram.

### P0-2 · Race « lost update » sur `trade_journal.csv` entre le tracker et l'API

- **Preuve** : le 01/09 13:30:05 l'API a écrit les lignes HAS/NEM (order_id
  `a24cc5ac`, `34901db4`, avec SL/TP/scores). Elles n'existent ni dans le CSV
  ni dans DuckDB. Seules les lignes `ALPACA-IMPORT-*` de 13:45 (sans SL, sans
  scores) subsistent. `load_journal()` lit sous lock, relâche, évalue 5–30 s
  (timeouts Alpaca), puis `save_journal(df)` réécrit **tout** le fichier sous
  un nouveau lock → l'append de l'API est perdu.
- **Impact** : positions sans SL/TP ni scores d'entrée → `thesis_stop`
  aveugle (`NO_DATA` sur 3/6 positions), `lt_exit_policy` inopérant.
- **Fix** : tenir le lock sur tout le cycle lecture → évaluation → écriture
  est trop long. Meilleure option : le tracker n'écrit que des **mises à jour
  ciblées par Order_ID** (relecture sous lock, patch des seules colonnes
  modifiées, écriture) — et `_log_to_csv` fait aussi `shadow_insert` en
  DuckDB (aujourd'hui absent).

### P0-3 · `sync_fills_from_alpaca` clôture des trades neufs avec de vieux fills

- **Preuve** : CF ouvert le 17/08 13:30 → marqué `WIN @ 120.0475 BROKER_SYNC`
  à 13:32. 120,0475 est le fill de **clôture du 27/07**. EOG 25/08 → `LOSS @
  140.1` = fill du **13/07**. Cause : `GetOrdersRequest(status=CLOSED,
  symbols=[t], limit=10)` sans filtre `after`, combiné à la course entre le
  cycle tracker 13:30:0x et le fill à l'ouverture (position pas encore
  visible dans `get_all_positions`).
- **Impact** : PnL réalisé faux (+121 $ CF, −75 $ EOG fictifs), puis
  ré-import 15 min plus tard d'une ligne orpheline sans SL. Idem NEM 09/06 et
  EQT 26/06 (doubles pertes comptées : −238 $).
- **Fix** : filtrer `after = Date d'entrée`, exiger `side=SELL`, `filled_at >
  entry`, `qty == Size` ; ne jamais réconcilier une ligne de moins de 30 min ;
  utiliser `client_order_id` déterministe pour lier parent/enfants ;
  `import_positions_to_csv` ne doit plus créer de ligne muette : il doit
  retrouver les jambes bracket ou alerter.

### P0-4 · Le journal est faux et doit être reconstruit

18 lignes dont 3 doublons fantômes et 4 imports sans niveaux. `realized_pnl`
−591 $ inclut −238 $ de doublons. Reconstruire depuis l'historique d'ordres
Alpaca (source de vérité) + les propositions `executed` (SL/TP/scores) via un
script one-shot, avec backup préalable (`make backup`).

---

## 2. Défauts P1 — exécution, ordonnancement, gestion de portefeuille

### P1-1 · `CRON_TZ` est ignoré (cron Debian 3.0pl1 ne le supporte pas ; système en UTC)

| Ligne cron | Intention | Réalité (UTC) |
|---|---|---|
| auto-approve `*/30 9-16` | 9h–16h30 NY | 5h–12h30 NY → **premier passage utile = 9:30:00 = enchère d'ouverture** |
| alpaca-sync `*/15 9-16` | heures marché | s'arrête à 12h45 NY : après-midi non réconcilié |
| monitor `30 16` | 30 min après clôture | 12h30 NY, en séance |
| earnings `0 6` Paris | 6h Paris | 6h UTC |

Preuve : `auto_approve.log` refus « NYSE fermée » de 09:00 à 13:00 UTC tous
les jours, exécution à 13:30:05 UTC. Fix : écrire les horaires en UTC (ou
systemd timers avec `OnCalendar=... America/New_York`).

### P1-2 · Auto-approve : mauvais candidats, mauvais moment, mauvaise méthode

- `candidates[:n_take]` prend les 2 premiers **dans l'ordre du fichier**, pas
  les mieux notés.
- Ignore `context.buy_signal` (WAIT_PULLBACK / split 0-40-40-20 validé en
  mémoire) et `qualification`. EIX acheté à TITAN 72 / Risk 27 / Momentum 46
  → −23 %.
- Market order à 9:30:00 (spread et volatilité maximaux). Placer à 10:00 NY
  ou en limit order.
- 5 ordres/semaine et 2/run + règle TITAN ≥ 80 : seuls 4 tickers ≥ 80
  aujourd'hui, tous déjà détenus → **la file de 14 propositions ne sera
  jamais exécutée sans clic humain** et 75 % expirent (audit 08-14).

### P1-3 · Cash drag structurel = première cause de sous-performance

- 20 578 $ investis sur 100 k$ (20 %). Allocation max calculée = 43 830 $
  (HRP + vol-target 18 % + cap 20 noms + multiplicateur régime 0,9).
- Poids 0,7 %–6,8 % → MU = 1 action (1 % du book) ; WDC proposé à 412 $.
- Le backtest qui « gagne » est un **panier top-N pleinement investi et
  rééquilibré** ; le système live est un stock-picking gate à 80 avec 80 % de
  cash. Ce sont deux stratégies différentes ; seule la première a des preuves.

### P1-4 · Sorties : trailing stop trop serré + churn

- 9 trades clos par le système : WIN moyen **+3,1 %**, LOSS moyen **−16 %**.
  CF/INCY/NEM/EOG coupés à +2,4…+4,5 % par TS (activation 8 %, lock 40 %),
  puis **rachetés** 2–3 semaines plus tard (CF ×3, NEM ×3, EOG ×2).
  Asymétrie inverse de ce que la thèse LT demande.
- `WIN_COOLDOWN_DAYS=14` + TS serré = vendre bas, racheter plus haut.

### P1-5 · `lt_exit_policy` recommande ADD_ON sur EIX (−22,8 %, rang #30, Risk 27)

`thesis_status=INTACT` car le drift TITAN n'est que −5 pts ; l'effondrement
du pilier Risk et la sortie du top-30 ne sont pas des critères. Ajouter : rang
courant > N ou Risk < 40 → jamais ADD_ON, TRIM au minimum.

### P1-6 · Circuit breaker progressif = fonctionnalité morte

Chaque cycle logue `Initialisé — n_history=1`. Le tracker est un process
one-shot : `cb.update()` n'est jamais appelé, l'historique roulant ne se
remplit pas, le peak reste figé à 100 032 $.

### P1-7 · Killswitch « nuclear » −4 %/jour incohérent avec du long terme

Une journée S&P à −4 % avec un book investi à 100 % liquiderait tout au pire
moment. À remplacer par un frein sur les **nouvelles entrées** + alerte, pas
par une liquidation.

---

## 3. Défauts P2 — fiabilité, observabilité, hygiène

| # | Défaut | Preuve | Fix |
|---|---|---|---|
| P2-1 | Telegram HTTP 400 sur les notifications propositions/digest | `narrative` contient `TITAN 69 < 70` non échappé en HTML (`routers/proposals.py:_notify_new_proposals`), 6 échecs/7 j | `html.escape()` sur ticker/secteur/narrative ; tronquer avant les balises |
| P2-2 | pytest écrit dans les données **prod** | `data/trade_journal.duckdb` modifié par `test_api_readonly.py` et `test_auto_proposer_gates.py` (scan fichier par fichier, 17/09) ; `agent.log` pollué par 250+ lignes CRITICAL factices par run (le gate pytest n'existe que pour le logger `Tracker`) | fixture autouse qui redirige `DUCKDB_PATH`, `CSV_PATH`, `data/` vers `tmp_path` ; gate pytest dans `modules/log.py` ; garde-fou CI : hash des fichiers data avant/après |
| P2-3 | Logs : preuves perdues | `tracker.log` 5 Mo ≈ 4 jours (≈3 000 lignes/jour), impossible de rejouer le 01/09 | rotation `daily`, `rotate 30`, passer les lignes « En cours » en DEBUG |
| P2-4 | `uvicorn --reload` en prod (systemd) | chaque `git_auto_sync` recharge l'API en pleine séance ; process `multiprocessing` orphelin | `--reload` seulement en dev ; déploiement = `systemctl restart` déclenché par le sync |
| P2-5 | NDX100 : parse Wikipedia échoue chaque jour | 138 erreurs `table Components introuvable` | parser robuste ou source alternative (Nasdaq API / FMP) ; sinon l'univers dépend d'un cache |
| P2-6 | Alpaca data timeouts 24×/semaine par ticker | `backend request timeout` | timeout court + fallback déjà présent ; passer le prix tracker sur `latest_trade` batch |
| P2-7 | `import_positions_to_csv` crée des lignes sans SL/TP/scores silencieusement | 4 lignes `ALPACA-IMPORT-*` | voir P0-3 |
| P2-8 | `_log_to_csv` n'écrit pas en DuckDB | DuckDB ne contient que les imports | `shadow_insert` |
| P2-9 | `main.py --alpaca-sync` et tracker font la même réconciliation avec deux fenêtres différentes | double logique | une seule fonction, appelée par le tracker |
| P2-10 | JPY sans paire FX, `best_strategy.json` legacy, `benchmark_compare` 500 (avril) | logs | nettoyage |

---

## 4. Ce qui va bien (à ne pas casser)

- Suite de tests (1386) + Vitest (58) + ruff/mypy/eslint verts ; CI + Docker
  démarrable ; auth fail-closed ; backups quotidiens vérifiés (`backups/`).
- Scoring : sentiment déjà neutralisé (IC −0,108), momentum renforcé ; WFO
  purement informatif (bien : 30 j de train ferait de l'overfit).
- Gates du proposer (killswitch, régime, fraîcheur données, cash, corrélation)
  fonctionnent ; `data_health` propre (breaker yf fermé, 0 stale).
- Frontend : build à jour, pas de défaut bloquant relevé (audit UI non
  approfondi).

---

## 5. Plan de travail — ordonné, avec critère de « fini »

### Lot 0 — Mise en sécurité immédiate (≤ 1 h, aucune modif de code)
1. `make backup`.
2. Poser 6 ordres STOP **GTC** chez Alpaca au niveau catastrophe (−35 % de
   l'entrée, ou SL journal quand il existe : MU 652, EIX 49,7).
3. Décision humaine sur EIX (−23 %, rang #30, ADD_ON suggéré à tort).
4. **Fini quand** : `get_orders(status=OPEN)` montre 6 stops GTC.

### Lot 1 — P0 code (2–3 jours)
1. `submit_order` : GTC + vérification des legs post-fill + `client_order_id`.
2. Tracker : ré-armement automatique d'un stop si aucun ordre stop ouvert.
3. Journal : écritures ciblées par Order_ID (tracker) + `shadow_insert` dans
   `_log_to_csv` ; test de concurrence (append pendant un cycle).
4. `sync_fills_from_alpaca` : filtre temporel, side, qty, délai de grâce.
5. Script `scripts/rebuild_journal_from_alpaca.py` + restauration SL/TP/scores
   depuis `proposals.json` ; exécuter, vérifier `realized_pnl`.
6. **Fini quand** : tests ajoutés pour chacun des 4 bugs reproduits en rouge
   avant fix ; journal = historique Alpaca ligne à ligne.

### Lot 2 — Ordonnancement & exécution (1 jour)
1. Crontab en UTC explicite (ou systemd timers). Auto-approve 14:00–20:30
   UTC, sync `*/15 13-21`, monitor 20:30.
2. Auto-approve : tri par `titan_score`, respect de `buy_signal`
   (`WAIT_PULLBACK` → ordre limite / split), premier passage à 10:00 NY,
   ordres **limit** à ±0,3 % du dernier prix.
3. **Fini quand** : un dry-run montre les 2 meilleurs candidats, pas les 2
   premiers de la liste ; aucun ordre à 13:30 UTC dans les logs.

### Lot 3 — Portefeuille : déployer le capital comme le backtest (2–3 jours)
1. Cible d'investissement ≥ 80 % (vol-target seulement si VIX > 25),
   12–20 positions, poids min 3 % / max 10 %, montant min 2 500 $.
2. Mode « panier » : rebalance mensuel vers le top-N courant (entrées /
   sorties / ajustements), auto-approve étendu aux top-N avec `buy_signal ≥
   BUY`, plafond 5/semaine relevé à 10.
3. Garder les gates existants (régime, corrélation, secteur, earnings).
4. **Fini quand** : proposer génère un plan de rebalance complet, exécutable
   en 1 clic (ou auto), et le book est investi ≥ 80 % en régime BULL.

### Lot 4 — Sorties cohérentes avec le LT (1–2 jours)
1. TS : activation 15 %, lock 25 %, ou ATR 3× seulement — supprimer le cas
   qui coupe à +3 %.
2. `WIN_COOLDOWN_DAYS` = 0 en mode panier (le rebalance gère le churn).
3. `lt_exit_policy` : rang > 40 ou Risk < 40 → jamais ADD_ON ; drift Risk
   −30 → TRIM.
4. Circuit breaker : persister `equity_history` à chaque cycle (append) ;
   killswitch −4 % → gel des entrées + alerte, pas de liquidation.
5. **Fini quand** : rejouer les 9 trades clos avec les nouveaux paramètres
   donne un ratio WIN/LOSS moyen ≥ 1.

### Lot 5 — Fiabilité (1 jour)
1. `html.escape` Telegram + test.
2. Isolation pytest (fixture autouse `data/` → tmp) + garde-fou hash en CI.
3. logrotate daily/30 j ; tracker « En cours » en DEBUG.
4. systemd sans `--reload` ; sync = restart contrôlé hors séance.
5. NDX100 parser ; timeout Alpaca data.

### Lot 6 — Mesurer avant de toucher au scoring (continu)
1. Tableau de bord : equity paper vs SPY vs « top-20 théorique » (même
   fenêtre), mis à jour par `run_titan.sh`.
2. Ne modifier aucun poids de pilier avant 20 trades clos **propres**
   (journal reconstruit) et 6 mois de snapshots live (les 5 ans bootstrappés
   ont un look-ahead : backtest +2 416 % vs SPY +84 %, inexploitable).
3. Passage en réel uniquement après Lots 0–4 et 2 mois de paper avec journal
   fiable.

---

## 5bis. État d'avancement (mis à jour le 2026-09-17, soir)

| Lot | Statut | Détail |
|---|---|---|
| 0 — mise en sécurité | ✅ fait automatiquement | le tracker (nouveau code) a armé 6 stops GTC chez Alpaca (MU/EIX en OCO stop+TP, CF/EOG/HAS/NEM en stop simple). `GET /api/portfolio/protection` → 6/6 protégées |
| 1 — P0 code | ✅ | GTC + `client_order_id` + vérif jambes ; `ensure_protective_stops` à chaque cycle (réalignement si dérive > 1 %) ; `save_journal` fusionné par clé ; sync filtrée (grâce 30 min, parent par Order_ID, fills postérieurs) ; journal reconstruit depuis Alpaca (14 lignes, PnL réalisé −169 $) ; 12 tests de non-régression |
| 2 — ordonnancement | ⚠️ à installer | `deploy/crontab.txt` (UTC explicite) écrit ; **`crontab deploy/crontab.txt` à exécuter à la main** (permission refusée à l'agent). Auto-approve : tri par score, gates buy_signal / Risk ≥ 40 / killswitch / CB |
| 3 — portefeuille | ✅ | vol-target seulement si VIX ≥ 25 ; 15 positions max ; 2 500 $ min. Allocation test : 87 % du cash déployé (vs 44 %) |
| 4 — sorties | ✅ | TS 15 % / lock 30 % / ATR 4× ; killswitch `freeze` à −6 % ; circuit breaker journalier −8/−12/−16 % (fenêtre 60 j) ; le tracker surveille les positions même sous killswitch |
| 5 — fiabilité | ✅ (sauf systemd) | Telegram échappé ; pytest isolé (garde-fou `SWINGQUANT_TEST_GUARD=1`, suite verte sans toucher aux données prod) ; logrotate daily/30 j ; NDX100 fallback. `--reload` en prod conservé volontairement (topologie dev de l'utilisateur) |
| 6 — mesure | ✅ | `/api/performance/benchmark`, `/api/portfolio/protection`, `/api/system/health`, `/api/scoring/lab` + pages **Cockpit** (route par défaut) et **Scoring Lab** |

Restent ouverts : installation du crontab ; ADD_ON/TRIM de `lt_exit_policy`
(gate rang/Risk — non fait, EIX reste en ADD_ON) ; source Nasdaq-100 (Wikipedia
ne sert plus la table : fallback vide tant qu'aucun scrape ne réussit — l'univers
est S&P 500 seul depuis juillet).

**Réponse à « le scoring est-il efficace ? »** — Non, pas démontré. Sur la
même fenêtre live : top-20 +18,1 %, **univers équipondéré +13,9 %**, bottom-50
+17,6 %, momentum seul +57,8 %, quality seul +2,5 %, piotroski seul −3,0 %.
L'edge du composite vs l'univers est de ~+4 pts sur 21 semaines, porté par
quelques noms (mémoire/IA). Le seul pilier robuste est Momentum (IC +0,119 en
juin, +58 % ici). Le Scoring Lab mesure ça chaque dimanche ; aucune
re-pondération n'a été faite (ce serait de l'overfitting sur 21 points).

## 6. Chiffres de référence (fenêtre live 28/04 → 17/09/2026)

| Mesure | Valeur |
|---|---|
| Equity paper Alpaca | 98 978 $ (−1,0 %) |
| SPY | +7,5 % |
| Backtest TITAN top-20 équipondéré, hebdo, 10 bps | +18,1 % net, 21 périodes, hit 62 %, pire semaine −2,5 % |
| Trades clos système | 9 (+2 imports fantômes) : WIN moy +3,1 %, LOSS moy −16 % |
| Capital investi | 20,6 k$ / 100 k$ |
| Positions sans stop broker | 6/6 ; sans stop journal 4/6 |
| Propositions en attente | 14 (max TITAN 79,5) ; 83 expirées, 6 exécutées depuis juillet |
| Tests | pytest 1386 ✓, vitest 58 ✓, ruff/mypy/eslint ✓ |
