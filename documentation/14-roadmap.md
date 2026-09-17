# 14 · Roadmap

Par ordre de valeur attendue sur le PnL, avec le critère de « fini ».

## Court terme (validation de la stratégie)

1. **Laisser tourner sans toucher aux paramètres** jusqu'au 2026-12-17
   (3 mois de shadow + lab). Fini quand : le Cockpit montre 3 mois de
   compte vs panier, et le shadow classe les profils avec des drawdowns.
2. **Vérifier la première semaine de la chaîne basket** (18 → 25/09) :
   propositions en 1/N à 06:00, achats à 15:00 UTC, entrées limites fillées,
   sweep corrigeant `Entry`, rotation EIX au 5ᵉ jour, stops présents. Fini
   quand : `/api/portfolio/protection` = 100 % et `Slippage_Bps` renseigné sur
   chaque nouvelle ligne.
3. **Rebalance du 1er octobre** : premier passage réel de `basket_rebalance`
   (aperçu dans le Cockpit). Fini quand : `rebalance_log.jsonl` contient les
   ordres et les stops ont suivi les quantités.

## Moyen terme (exécution et robustesse)

4. **Entrées fractionnées** (J / J+3) : reporté (ADR-015) ; nécessite un
   journal multi-lots ou un agrégat par ticker. Valeur : lissage du prix
   d'entrée, faible en 1/N.
5. **Filtre de régime testé** : lire les variantes « regime » du lab
   (Momentum 5 ans avec/sans MA200). Si le filtre réduit le drawdown sans
   coûter plus de 2 pts/an, l'activer comme gate d'achat supplémentaire.
6. **Second compte paper réel** (profil challenger) si le shadow montre un
   écart > 5 pts sur 3 mois : réalisme d'exécution.
7. **Russell 1000** : élargir l'univers (source à trouver : iShares IWB
   refuse les robots, Nasdaq API ne couvre pas). Coût : budget providers ×2.

## Long terme (passage en réel)

8. **Réel à 10 %** (`LIVE_CAPITAL_FRACTION`) après 2 mois de paper avec
   journal fiable et 20 trades clos propres ; comparaison paper vs réel
   (fills, slippage) 2 semaines ; puis 25 %, 50 %, 100 %.
9. **Résilience infra** : systemd timers à la place de cron, alerte si
   `swing-api` redémarre en séance, seconde VM ou conteneur de secours.

## Ce qu'on ne fera pas sans preuve

- Re-pondérer le composite « à la main », ajouter des piliers, brancher un
  LLM dans le scoring, passer en top-5. Chaque idée passe par le lab puis le
  shadow, et une entrée dans 11-decisions.
