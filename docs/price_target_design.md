# Prix Cible 12 Mois — Design Doc

Statut : **conception validée, implémentation non démarrée**
Auteur : Claude (session 2026-08-09), validé par Kousseila
Objectif : intégrer un prix cible algorithmique (pas un chiffre random) dans les propositions, basé sur les moteurs TITAN existants, calibré par une boucle d'agents autonome jusqu'à un seuil mesurable.

---

## 1. Décisions déjà prises (à ne pas rouvrir sans raison)

| Question | Décision |
|---|---|
| Méthodologie | Fair value **fondamentale** (pas de blend technique/analystes en input direct du modèle — voir §5 pour la nuance sur le consensus analystes) |
| Horizon | **12 mois** |
| Métrique de succès | **Hit-rate backtesté**, mais transitoirement un **proxy IC de rang** (voir §4 — contrainte data) |
| Exécution | **Boucle de dev qui converge puis s'arrête**, une fois calibrée le calcul devient une étape normale du cron quotidien (`run_titan.sh`), pas un cron Claude permanent |
| Accès mobile | Routine **RemoteTrigger** (cloud, visible app Claude sur tel), auto-désactivation + notif Telegram à la fin |
| Métrique proxy (contrainte data) | **Oui**, IC de rang sur fenêtre courte maintenant, bascule auto vers vrai hit-rate 12 mois quand assez d'historique (~avril 2027) |

---

## 2. Contrainte data — pourquoi pas de vrai backtest 12 mois aujourd'hui

`universe_history` (Lot 6) ne stocke des snapshots quotidiens que depuis le **2026-04-22**. Au 2026-08-09 :

- **109 snapshots**, 108 jours calendaires consécutifs utilisables (un seul gap : 2026-04-23 manquant)
- Aucune contamination bootstrap (les fichiers `_bootstrap_lookahead` n'existent pas sur disque actuellement)
- Donc impossible de mesurer "le prix cible calculé à T a-t-il été atteint à T+12mois" avant **avril 2027**

**Conséquence pratique pour la calibration immédiate** : avec ~108 jours d'historique, une fenêtre de test à 90 jours ne laisse qu'environ 1 à 2 points de départ non-chevauchants — les fenêtres qu'on utilisera seront donc majoritairement chevauchantes (autocorrélées). Le signal IC mesuré aujourd'hui est **bruité et doit être traité comme indicatif, pas définitif**. Le seuil de convergence (§6) est fixé en conséquence — modeste, pas ambitieux.

Chaque calcul de `price_target` doit être **horodaté et journalisé** (nouveau fichier ou table, ex. `data/.price_target_history/`) pour que le vrai hit-rate 12 mois puisse être mesuré rétroactivement dès que l'échéance est atteinte, sans dépendre de `universe_history` (qui pourrait évoluer). Voir §7.

### 2.1 Pont data pour la routine cloud (résolu 2026-08-09)

`data/.universe_history/` est **gitignored** (donnée de production régénérable, pas du code) — une routine cloud (sandbox = clone git frais, aucun accès au disque du VPS) n'a donc **aucun accès** aux snapshots bruts. Résolu par un nouveau module `backend/modules/price_target_calibration_export.py` (`export_full()`) qui réécrit intégralement un export trié (champs de la §3.1/§6 uniquement — pas les 117 champs bruts) vers `backend/data/calibration/universe_history_export.jsonl.gz`, un chemin **non couvert par les patterns du `.gitignore`** donc suivi par git et propagé par `git_auto_sync.sh` comme le reste du repo.

Câblé dans `run_titan.sh` (nouveau Step 3a, juste après le snapshot `universe_history` Step 3) — tourne quotidiennement, indépendamment de l'état de la calibration. Premier export généré le 2026-08-09 : 52 620 lignes (109 jours × ~490 tickers), 3.5 MB compressé.

**La routine de calibration cloud (§6) doit lire ce fichier, jamais `universe_history` directement** — ce dernier n'existera simplement pas dans son sandbox.

---

## 3. Formule — Fair Value Fondamentale 12 mois

Nouveau module : `backend/modules/price_target.py`

Aucune nouvelle collecte de donnée. Tout est déjà disponible sur le record ticker (`universe.json` / `sector_metrics.get_scored_universe()`).

### 3.1 Composantes (blend pondéré, poids à calibrer — voir §6)

**A. Multiple reversion sector-relative**
```
fair_multiple = médiane sectorielle de forward_pe (ou ev_to_ebitda si dispo, fallback fwd_pe)
                 parmi les tickers du même secteur (GICS "sector", cf. sector_metrics)
fair_price_multiple = current_price * (fair_multiple / current_forward_pe)
```
Champs bruts : `forward_pe`, `ev_to_ebitda`, `trailing_pe`, `sector`, `current_price`. Le calcul de médiane sectorielle est à écrire (pas de fonction existante qui persiste ces rangs individuels — `_percentile_rank_by_sector` dans `sector_metrics/_scoring.py` fait un rang mais ne retourne pas la médiane brute ; réutiliser le même seuil `_MIN_SECTOR_SIZE_FOR_RELATIVE = 12` avec fallback global si secteur trop petit).

**B. PEG-reversion**
```
fair_price_peg = current_price * (peg_sector_median / current_peg_ratio)   si peg_ratio > 0
```
Champ : `peg_ratio`.

**C. Ancrage Buffett-TP existant**
Réutiliser directement `modules/fundamentals_levels.compute_fundamental_levels()` — son `tp` (Buffett-TP) est déjà une fair-value ceiling fondamentale validée en prod (utilisée par `lt_exit_policy` pour EXIT_VALUATION). Ne pas dupliquer la logique quality/piotroski/value/peg-factor, **appeler la fonction existante**.
```
fair_price_buffett = compute_fundamental_levels(price=current_price, quality_score=..., piotroski_score=f_score, value_score=..., peg_ratio=...)["tp"]
```

**D. Ajustement tilt (signal déjà calculé, pas recalculé)**
Réutiliser `titan_tilt_flags` / `titan_tilt_adjust` déjà présents sur le record scoré (`cheap_junk`, `falling_knife`, `qarp`, `garp`, `consistent` — cf. `sector_metrics/_scoring.py` lignes ~1257-1282). Compresse la fourchette basse si `cheap_junk`/`falling_knife`, élargit la fourchette haute si `qarp`/`garp`/`let_it_ride`.

### 3.2 Blend final
```
price_target = weighted_mean([fair_price_multiple, fair_price_peg, fair_price_buffett], weights=W)
price_target_low  = price_target * (1 - band_pct)   # band_pct réduit par confidence, élargi par tilt négatif
price_target_high = price_target * (1 + band_pct)   # élargi si let_it_ride / qarp / garp
upside_pct = (price_target / current_price - 1) * 100
confidence = data_confidence.compute_confidence(...)  # réutilisé tel quel, pas de nouvelle formule
```
`W` (poids A/B/C) et `band_pct` de base = **paramètres appris par la boucle de calibration** (§6), pas des constantes choisies à la main.

### 3.3 Fallback
Si toutes les composantes manquent (comme `fundamentals_levels` le fait déjà) → `method="unavailable"`, tous les champs `None`. Ne jamais inventer un chiffre sans donnée.

---

## 4. Référence de calibration — consensus analystes (déjà en prod, inutilisé)

`price_target_mean` / `price_target_high` / `price_target_low` (yfinance `targetMeanPrice`) sont **déjà peuplés sur 491/494 tickers**, déjà stockés quotidiennement dans `universe_history`. Décision méthodologique : ne **pas** l'utiliser comme input direct du modèle (cf. choix "fair value fondamentale pure" en §1), mais l'utiliser comme **baseline de calibration** :

> Le modèle fondamental n'a de valeur ajoutée que s'il égale ou bat l'IC du consensus analystes brut sur la même fenêtre. Si le modèle fait pire que `(price_target_mean/current_price - 1)` tout seul, c'est un signal que la formule est mal pondérée ou redondante — pas la peine de la déployer.

Cette baseline est gratuite à calculer (donnée déjà là) et doit être rapportée à chaque round de calibration à côté de l'IC du modèle.

---

## 5. Intégration — points d'ancrage exacts trouvés dans le code

### Backend
- **Nouveau module** : `backend/modules/price_target.py` (formule §3) + `backend/modules/price_target_calibration.py` (script de calibration, §6)
- **Pipeline** : nouvelle étape dans `run_titan.sh`, insérée **après** l'étape "Step 2" (warm cache `/api/portfolio/recommendations`, où `sector_metrics.get_scored_universe()` devient disponible) et **avant** "Step 4" (`POST /api/proposals/refresh`) — c-à-d juste après `modules.metrics_agent` (Step 3c), avant le `curl` de refresh proposals. Doit tourner avant `auto_proposer` pour que `alloc.get("price_target")` soit peuplé.
- **Proposals** : `backend/modules/auto_proposer.py`, fonction `_build_proposal_from_alloc` (lignes ~510-551) — ajouter `price_target`, `price_target_low`, `price_target_high`, `upside_pct`, `price_target_confidence` au dict `context`, même pattern que `titan_score`/`support` existants. Pas de changement de schéma nécessaire (`context` est un dict libre, `modules/proposals.py::make_proposal` ne valide rien).
- **Ticker Analysis** : `backend/routers/ticker_analysis.py`, nouveau bloc top-level `"fair_value": {...}` juste avant le `return` (~ligne 554), à côté du bloc `"analysts"` existant (qui contient déjà `price_target_mean/high/low` — les deux vont coexister, clairement labellisés "consensus analystes" vs "modèle fondamental TITAN").

### Frontend
- Helpers de formatage déjà présents : `fmtPrice()` (dollars), `fmtSignedPct()` (upside signé) dans `frontend/src/utils/format.js` — réutiliser tel quel.
- Badge : suivre le pattern `SupportBadge` (`ProposalsPage.jsx` lignes 376-438 — valeur principale + label + tooltip, palette par niveau de confidence) plutôt que le badge TITAN simple. Candidat à extraire en composant partagé `components/common/PriceTargetBadge.jsx` (précédent : `ConvictionBadge.jsx` déjà extrait de la même façon), réutilisable dans `ProposalsPage.jsx` et `TickerAnalysisModal.jsx`.

### Historisation dédiée
- Nouveau : `data/.price_target_history/` — un enregistrement par `(ticker, date_calcul, price_target, horizon=12mois, méthode/poids utilisés)`, indépendant de `universe_history` pour ne pas dépendre de sa rétention/format s'il change. Permet la bascule vers le vrai hit-rate en avril 2027 (§2).

---

## 6. Boucle de calibration — mécanique et critère d'arrêt

**Outil** : agent(s) en boucle (pattern *loop-until-plateau*), lancé comme routine `RemoteTrigger` cloud (visible app mobile Claude), auto-désactivation en fin de run.

**Round de calibration :**
1. Choisir/ajuster les poids `W = (w_multiple, w_peg, w_buffett)` et `band_pct` de base (grid search ou ajustement guidé par le round précédent)
2. Calculer `price_target` pour tout l'univers à chaque date disponible dans `data/calibration/universe_history_export.jsonl.gz` (voir §2.1 — **pas** `universe_history` directement, inaccessible en sandbox cloud)
3. Pour chaque paire de dates `(t, t+N)` avec `N` = plus grande fenêtre exploitable sans trop chevaucher (proposer `N≈60j`, à ajuster selon densité de points disponibles) : calculer le rendement réalisé, et le **rank IC (Spearman)** entre le signal `(price_target_t / current_price_t - 1)` et le rendement réalisé — réutiliser `wfo_calibration._spearman` (fonction privée mais copiable, formule de corrélation de rang avec mid-ranks) plutôt que réinventer.
4. Calculer la même IC pour la baseline consensus analystes (§4) sur le même échantillon.
5. Logger : IC modèle, IC baseline, poids utilisés, n_paires valides, round #.
6. Garder la config si IC modèle améliore le round précédent ; sinon revenir à la meilleure config connue et essayer une autre direction.

**Critère d'arrêt (remplace "parfait") — les deux premiers qui arrivent déclenchent l'arrêt :**
- **Convergence** : IC modèle ≥ **0.10** ET IC modèle ≥ IC baseline (consensus analystes) sur le même échantillon, stable (± 0.02) sur 3 rounds consécutifs
- **Plateau** : 5 rounds consécutifs sans amélioration de l'IC modèle
- **Garde-fou dur** : 20 rounds maximum, quoi qu'il arrive (évite une boucle infinie si le seuil est irréaliste compte tenu du bruit data — cf. §2)

Dans tous les cas le round final rapporte : meilleure config trouvée, IC atteint vs baseline, et une phrase honnête sur la fiabilité statistique compte tenu de l'échantillon (108 jours, fenêtres chevauchantes). **Pas de "objectif atteint à 100%"** — le rapport final doit dire explicitement "seuil atteint sur proxy court-terme, validation définitive 12 mois prévue avril 2027" si applicable.

**Une fois le seuil (ou le garde-fou) atteint** : la routine intègre le module dans le pipeline (§5), écrit les tests, commit, désactive son propre `RemoteTrigger`, notifie par Telegram (canal déjà utilisé pour les autres alertes).

---

## 7. Tests attendus (avant intégration au pipeline)

- `test_price_target.py` : cas nominal (toutes composantes dispo), fallback (aucune donnée), tickers avec `peg_ratio` négatif/nul, secteur trop petit (`< 12` tickers → fallback global comme `_percentile_rank_by_sector`), cohérence avec `let_it_ride`/tilt flags existants
- `test_price_target_calibration.py` : IC calculée correctement sur un jeu de données synthétique connu (vérifier signe et magnitude attendus), critère d'arrêt (convergence/plateau/garde-fou) déclenché correctement sur des séries de rounds simulées

---

## 8. Ce qui reste à décider pendant l'exécution (pas bloquant pour démarrer)

- Poids initiaux du grid search pour `W` (peut démarrer équi-pondéré 1/3-1/3-1/3)
- Fenêtre `N` exacte pour l'IC proxy (60j proposé, ajustable selon densité de points valides)
- Faut-il exposer aussi le consensus analystes (`price_target_mean`) tel quel dans l'UI à côté du modèle TITAN, en complément (probable — les deux apportent une info différente), ou seulement le modèle fondamental (décision UI, pas bloquante pour le backend)
