# 11 · Journal des décisions (ADR)

Format : contexte → décision → alternatives écartées → conséquences. Une
décision n'est modifiée que par une nouvelle entrée qui la remplace.

## ADR-001 · 2026-04-17 — Pivot « quantamental long terme », abandon du swing
Contexte : 33 trades swing archivés, stats non concluantes. Décision :
horizon semaines/mois, scoring fondamental + momentum, brackets larges.
Conséquence : toute la chaîne RSI/optimizer supprimée.

## ADR-002 · 2026-04-22 — Journal CSV source de vérité, DuckDB miroir
Contexte : besoin d'analytique sans casser les scripts. Décision : CSV reste
la vérité, DuckDB reconstruit (tmp + rename). Alternative écartée : DuckDB
seul (migration risquée en prod). Conséquence : dual-write à discipliner
(voir ADR-011).

## ADR-003 · 2026-04-24 — Alpaca comme broker, brackets natifs
Décision : bracket (entrée + SL + TP) côté broker. Conséquence : le tracker
n'est plus la seule protection… à condition que les jambes survivent
(ADR-010).

## ADR-004 · 2026-05-12 — Sentiment neutralisé, momentum renforcé (V14.1)
Contexte : diagnostic IC (27 fenêtres) : sentiment −0,108, momentum +0,119.
Décision : sentiment 0 %, insider 4 %, momentum 17 %, revisions 12 %.

## ADR-005 · 2026-08-16 — Ne pas automatiser davantage l'achat sur TITAN ≥ 80
Contexte : l'alpha du bucket ≥ 80 tenait sur 2 tickers (SNDK, MU) en 5 ans.
Décision : statu quo auto-approve (2/run, 5/sem). Remplacée par ADR-013.

## ADR-006 · 2026-09-17 — Brackets GTC + ré-armement automatique des stops
Contexte : jambes `DAY` annulées à la clôture depuis juillet, 6 positions
sans stop. Décision : GTC, `client_order_id`, `ensure_protective_stops` à
chaque cycle (OCO si TP connu), réalignement niveau/qty. Alternative
écartée : ne compter que sur le tracker (dépend d'un cron et d'un provider).

## ADR-007 · 2026-09-17 — Journal fusionné par clé, jamais réécrit en bloc
Contexte : lost-update tracker ↔ API. Décision : `save_journal` fusionne sur
`Order_ID` ; DuckDB rejoué après chaque écriture, co-localisé avec le CSV.

## ADR-008 · 2026-09-17 — Réconciliation broker bornée dans le temps
Décision : grâce 30 min, parent relu par id, fills de sortie postérieurs à
l'entrée. Alternative écartée : conserver la recherche « 10 derniers ordres ».

## ADR-009 · 2026-09-17 — Horaires cron en UTC explicite
Contexte : `CRON_TZ` ignoré par cron Debian. Décision : `deploy/crontab.txt`
versionné, horaires valides été/hiver, auto-approve hors enchère d'ouverture.
Alternative : systemd timers (plus lourd à migrer).

## ADR-010 · 2026-09-17 — Trailing stop LT (15 % / 30 %) et killswitch « freeze »
Contexte : WIN moyen +3 % vs LOSS −16 % ; killswitch −4 % liquidait tout.
Décision : activation 15 %, lock 30 %, ATR 4× ; killswitch −6 % gèle les
entrées, ne vend pas ; circuit breaker −8/−12/−16 % sur 60 séances,
échantillonné 1×/jour.

## ADR-011 · 2026-09-17 — Tests isolés de la prod par construction
Décision : fixture autouse + chemins résolus à l'appel + garde-fou hash.
Conséquence : un test qui écrit en prod est un bug de test, pas un risque.

## ADR-012 · 2026-09-17 — Profil de poids `equal_7`
Contexte : Scoring Lab à taille réelle (21 sem.) : equal_7 +34 %/hit 76 %/
DD −5 % vs V14.1 +18 %. Décision : profil équipondéré sur les 7 piliers à
IC ≥ 0, choisi parce qu'il n'a aucun paramètre ajusté à la fenêtre.
Alternatives écartées : `momentum_tilt` (+43 % top-15 mais DD −8,5 %),
top-5 (concentration). Condition de révision : shadow 3 mois + lab.

## ADR-013 · 2026-09-17 — Stratégie « basket » automatisée (remplace ADR-005)
Contexte : le compte dormait à 80 % en cash ; le backtest gagnant est un
panier plein rebalancé. Décision : `STRATEGY_MODE=basket` — achats par rang
≤ 20, pondération 1/N, rotation rang > 40 / 5 j, rebalance mensuel ±2 pts,
budgets 4/20, vol-target seulement si VIX ≥ 25. Garde-fous conservés
(régime, earnings, caps, killswitch, CB, stops).

## ADR-014 · 2026-09-17 — Interface réduite à 7 pages
Contexte : 20 pages, styles inline, pas de vue « ça gagne ou pas ».
Décision : Cockpit par défaut, suppression de 13 pages, endpoints conservés.

## ADR-015 · 2026-09-17 — Exécution : limite marketable, gate de spread, sweep
Décision : entrée `ask × 1,003` GTC, refus si spread > 0,5 %, remplacement
par market après 60 min, `Entry` corrigé au fill réel. Alternative écartée :
entrées fractionnées J/J+3 (complexifie le journal mono-ligne ; reporté).

## ADR-016 · 2026-09-17 — Forward-test A/B en shadow plutôt qu'un second compte
Décision : portefeuilles virtuels internes (mêmes règles, prix du jour) pour
5 profils ; un vrai second compte reste possible (03-scoring). Raison :
zéro clé supplémentaire, comparaison de scoring pure, résultats dès J+1.

## ADR-017 · 2026-09-17 — Stop de portefeuille −15 % / 60 séances
Décision : gel des entrées via le mécanisme killswitch, alerte. Pas de
liquidation (cohérent ADR-010).

## ADR-018 · 2026-11-18 — Agents : sentinel de résultats + sous-agents Claude en lecture seule
Contexte : le proposer est resté mort du 2026-09-18 au 2026-11-18
(`KeyError` en mode `equal`) sans aucune alerte, car `/api/system/health`
ne mesure que l'âge des logs. 4 runs quotidiens sur 7 étaient KO (API tuée
par l'OOM killer). Décision : (1) `modules/sentinel.py`, déterministe, qui
vérifie des **résultats** (propositions créées, panier déployé, OOM…),
cron 30 min + Telegram dédupliqué ; (2) triage IA headless
(`scripts/sentinel_triage.sh` → agent `sq-ops-investigator`) seulement sur
nouveau CRIT, ≤ 2/24 h, outils allow-listés en lecture ; (3) cinq
sous-agents `.claude/agents/sq-*` + skill `/sq-ops` pour l'usage
interactif. Principe : les agents diagnostiquent et recommandent, ils
n'écrivent ni code, ni données, ni ordres. Alternatives écartées : agent
autonome qui corrige et commit (le dépôt est auto-poussé chaque minute :
aucune revue humaine) ; agent qui trade sur eToro (argent réel, edge non
démontré OOS). Condition de révision : 3 mois de triages utiles.

## ADR-019 · 2026-11-20 — Compte eToro réel visible dans l'app (lecture seule)
Contexte : audit 2026-11-18 P0 #12/#13 — l'accueil ne montrait que le paper
Alpaca ; le copy-trading (~58 % des ~9,6 k$ réels) était invisible et le book
« Mon Portefeuille » était saisi à la main. Décision : `modules/etoro_sync.py`
produit un snapshot normalisé (totaux, lignes directes agrégées par
instrument, copy-traders) dans `backend/data/etoro/` (gitignoré) + une ligne
d'historique par jour. Deux sources au même format : l'API publique eToro
(`GET /api/v1/trading/info/real/pnl`, clés `ETORO_API_KEY` + `ETORO_USER_KEY`
dans `backend/.env`, cron 22:30 UTC) ou l'ingestion d'un « portfolio summary »
du connecteur Claude (`POST /api/etoro/snapshot`, auth complète).
`GET /api/etoro/overview` ajoute les actions du jour (signaux de thèse
`declenche` sur les lignes réellement détenues, stop de copie ≥ 40 %, copie en
pause) et la réconciliation book ↔ eToro (unités, lignes manquantes).
Cockpit : carte « Argent réel · eToro » en tête, au-dessus du paper.
Jamais d'ordre eToro : aucune route d'écriture n'est appelée.
Conséquence : snapshot périmé > 36 h signalé en orange ; sans clés API, la
fraîcheur dépend d'une ingestion manuelle.
