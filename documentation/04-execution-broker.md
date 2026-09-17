# 04 · Exécution et broker (Alpaca)

Module : `backend/modules/broker_gateway.py` (`AlpacaBroker`). `PaperBroker`
(CSV seul) reste disponible via `BROKER_MODE=paper`.

## Cycle de vie d'un ordre

1. **Soumission** (`submit_order`, appelée par `/api/proposals/approve_batch`) :
   - gate horaire : refus si NYSE fermée (jamais d'ordre en queue avec des
     niveaux figés) ;
   - gate de spread : `get_quote` (NBBO Alpaca) → refus si spread > 0,5 %
     (`EXEC_MAX_SPREAD_PCT`), la proposition repasse `pending` ;
   - **entrée limite marketable** : ask × 1,003 à l'achat
     (`EXEC_ENTRY_TYPE=limit`, `EXEC_LIMIT_OFFSET_PCT`), en **bracket GTC**
     avec jambes stop (SL) et limite (TP) ; `client_order_id = SQ-<TICKER>-<UTC>-<hex>` ;
   - ligne journal `OPEN` avec `Entry = prix proposé` (le fill n'est pas connu
     à cet instant), scores TITAN d'entrée, miroir DuckDB.
2. **Sweep** (`sweep_unfilled_entries`, chaque cycle tracker) :
   - parent fillé → `Entry` remplacé par le fill réel, `Slippage_Bps`
     calculé vs `Reco_Entry` ;
   - parent limite non fillé après 60 min (`EXEC_LIMIT_MAX_AGE_MIN`) en
     séance → annulé et remplacé par un **market bracket** (mêmes SL/TP) ;
   - partial fill → `Size` corrigé.
3. **Protection** (`ensure_protective_stops`, chaque cycle) : pour toute
   position Alpaca présente dans le journal, s'il n'y a **aucune jambe STOP
   ouverte** (legs nested inclus), pose un stop GTC — OCO stop + TP si le TP
   est connu, sinon stop simple — au SL journal, ou au plancher −35 % si le
   journal n'en a pas (alerte). Ré-arme si la quantité diverge (rebalance) ou
   si le niveau diverge de plus de 1 % du journal.
4. **Trailing / sorties tracker** : `update_stop_loss` remplace la jambe
   stop (ou la crée) ; `close_position` annule les jambes puis vend au
   marché, attend la confirmation du fill (5 × 1,5 s) avant d'écrire le
   journal.
5. **Réconciliation** (`sync_fills_from_alpaca`, chaque cycle + cron 15 min) :
   fenêtre de grâce 30 min, parent relu par `Order_ID` (jamais fillé et
   annulé → `CANCELED`), sinon fill de **sortie** postérieur à l'entrée →
   `WIN/LOSS` avec `Close_Reason` (`SL_HIT`, `TP_HIT`, `BROKER_SYNC`).
   Aucun fill trouvé → la ligne reste `OPEN` avec un WARNING.
6. **Rebalance** (`adjust_position`) : achat/vente partiel au marché, `Size`
   et `Entry` moyen mis à jour, trace dans `data/rebalance_log.jsonl`.

## Ce qui a été corrigé le 2026-09-17 (et pourquoi ça compte)

| Défaut | Effet observé | Correction |
|---|---|---|
| Bracket `time_in_force=DAY` | 100 % des jambes SL/TP annulées à 20:00 UTC le jour du fill depuis juillet ; 6 positions sans stop | GTC + ré-armement automatique |
| Réconciliation sans borne temporelle | trades neufs clôturés avec le fill d'un trade précédent (CF 17/08 → fill du 27/07), puis ré-importés sans stop | filtre `after`, side, `filled_at`, grâce 30 min |
| Journal réécrit en bloc par le tracker | lignes écrites par l'API pendant un cycle perdues (HAS/NEM 01/09) | fusion par clé `Order_ID` |
| `Entry` = prix proposé | PnL journal faux dès l'entrée (MU 1003,58 vs 1007,74) | sweep → fill réel |
| Legs OCO invisibles | OCO annulé/recréé à chaque cycle (spam) | lecture `nested=True` + aplatissement |
| NaN CSV « truthy » | `Direction` lu comme `"nan"` → side de sortie inversé | `_cell()` normalise |

## Journal : lien avec Alpaca

`Order_ID` = id du parent Alpaca (UUID) ; `PAPER-…` / `MANUAL_…` pour les
ordres hors broker ; `ALPACA-IMPORT-<T>-<date>` pour une position trouvée
chez le broker sans ligne (filet, bruyant). `scripts/rebuild_journal_from_alpaca.py`
reconstruit le journal depuis l'historique d'ordres (dry-run par défaut,
`--apply` = backup + écriture + DuckDB) ; il refuse si les positions
reconstruites ne correspondent pas aux positions Alpaca.

## Coûts et frictions mesurés

- Slippage : `Slippage_Bps` par ligne (fill vs prix proposé) ; agrégé dans
  l'attribution de l'écart (`/api/performance/gap`).
- Commissions Alpaca : 0. Le backtest et le shadow supposent 10 bps
  aller-simple par rotation, volontairement pessimiste.
- Données de prix : plan Alpaca gratuit = IEX (pas NBBO consolidé) ; les
  quotes peuvent dévier de quelques dixièmes sur les titres peu liquides.
  L'offset limite de 0,3 % couvre ce bruit ; le gate de spread 0,5 % refuse
  les cas dégradés.

## Passage paper → réel

1. `.env` : `ALPACA_BASE_URL=https://api.alpaca.markets`, clés live.
2. `LIVE_CAPITAL_FRACTION` (0,10) est appliqué automatiquement au capital
   déployé par le proposer.
3. `python main.py --alpaca-test` puis un cycle tracker manuel
   (`python tracker.py`) et vérification de `/api/portfolio/protection`.
4. Comparer 2 semaines paper vs réel (slippage, fills) avant d'augmenter la
   fraction.
