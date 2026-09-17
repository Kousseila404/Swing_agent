# 05 · Gestion du risque

Cinq couches, de la ligne au portefeuille. Toutes sont paramétrées dans
`backend/config.py` (voir 15-configuration).

## 1. Stop catastrophe par ligne (broker)

- Niveau : σ-scaled, borné entre −5 % et **−35 %** (`portfolio/_trade_levels.py`),
  posé chez Alpaca en jambe STOP **GTC** dès l'entrée, ré-armé à chaque cycle
  s'il manque. TP à +100 % (plafond nominal, la sortie fondamentale prime).
- Ce stop n'est pas un signal : c'est le filet anti-accident (fraude, gap,
  panne du tracker). Il tient sans le tracker et sans l'API.

## 2. Trailing stop (tracker, `tracker/evaluation.py`)

- Activation à **+15 %** (ou 50 % de la distance au TP si inférieure),
  verrouille **30 %** du gain ; mode ATR si OHLCV disponible : activation
  4 × ATR, trail 4 × ATR. Une mise à jour par jour maximum, poussée au broker
  (`update_stop_loss`).
- Avant le 2026-09-17 : 8 % / 40 % → gagnants coupés à +3 % puis rachetés
  (CF ×3, NEM ×3). Justification dans 11-decisions.

## 3. Time-exit et couche fondamentale

- 60 jours de détention → clôture, **sauf** si la dernière décision LT
  (≤ 3 j) est HOLD/ADD_ON avec thèse intacte.
- `lt_exit_policy.decide` agrège thèse (`thesis_stop`), valorisation
  (`fundamentals_levels`), catégorie Buffett, confiance des données, insiders :
  HOLD / ADD_ON / TRIM / EXIT_THESIS / EXIT_VALUATION / EXIT_CATASTROPHE.
  Informative (alertes Telegram), sauf exemption de rotation pour ADD_ON.
  Depuis le 2026-09-17 : **jamais d'ADD_ON si rang > 40 ou pilier Risk < 40**.

## 4. Rotation et rebalance (stratégie)

- Rang > 40 pendant 5 jours → vente (max 3/jour). Rebalance 1/N mensuel,
  bande ±2 pts. Voir 02-strategie.

## 5. Freins de portefeuille

| Frein | Déclencheur | Effet | Levée |
|---|---|---|---|
| Killswitch journalier | equity −6 % vs départ du jour (`MAX_DAILY_DRAWDOWN_PCT`) | `freeze` : gel des **entrées** ; positions et stops conservés (mode `liquidate` disponible, non recommandé) | nouveau jour **et** equity ≥ 96 % du peak |
| Circuit breaker progressif | drawdown vs plus haut 60 séances : −8 % → taille 75 %, −12 % → 50 %, −16 % → pause 5 séances | réduit/bloque les nouvelles entrées | automatique |
| Stop de portefeuille | equity ≤ −15 % sous le plus haut 60 séances (`portfolio_stop`, quotidien 06:00) | gel des entrées + alerte | hysteresis du killswitch |
| Régime macro | BEAR/CRASH confirmé 3 jours (S&P vs MA200, VIX ≥ 35 = panique) | aucune proposition ; hedge SH 10 % en BEAR | retour BULL confirmé |
| Vol-target | VIX ≥ 25 | scale le book vers 18 % de vol annualisée (leverage 0,5–1,0) | VIX < 25 |
| Caps | secteur 30 %, nom 10 %, ADTV 5 %, corrélation moyenne 0,55 | sizing | — |

Le tracker **continue de surveiller** les positions pendant un gel (avant le
2026-09-17, un killswitch actif rendait le tracker aveugle).

## Ce qui n'est pas couvert

- Risque de gap overnight au-delà du stop (le stop est un ordre marché
  déclenché : exécution au prix suivant). Le clamp comptable du journal
  (1,5 %) ne change pas le fill réel.
- Risque de contrepartie / broker : un seul compte, une seule VM. Backups
  quotidiens du journal (`backups/`), reconstruction possible depuis Alpaca.
- Risque de modèle : 21 semaines de preuve. D'où shadow + lab avant tout
  changement.
