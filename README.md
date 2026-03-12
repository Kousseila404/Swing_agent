# 🎯 Swing Trading Agent V1 — Radar Intelligent

Agent IA de détection d'anomalies sur les actions françaises (Euronext Paris).
**La V1 ne passe aucun ordre.** Elle scanne, analyse, et vous alerte.

## Architecture

```
main.py                  ← Point d'entrée (orchestre le pipeline)
config.py                ← Toutes les clés API + paramètres
modules/
  ├── scanner.py         ← Scan yfinance : volume + RSI
  ├── news_fetcher.py    ← Récupération des actualités
  ├── ia_brain.py        ← Analyse de sentiment via API Anthropic
  ├── alerter.py         ← Envoi des alertes Telegram
  └── log.py             ← Logger centralisé
logs/
  └── agent.log          ← Historique des exécutions
```

## Pipeline V1

```
[Scanner]  →  [News]  →  [Cerveau IA]  →  [Alerte Telegram]
  yfinance     yfinance     Anthropic       Bot Telegram
  RSI + Vol    Actualités   Sentiment       Message formaté
```

## Installation rapide

```bash
# 1. Cloner / copier le projet
cd swing_agent

# 2. Installer les dépendances
pip install -r requirements.txt

# 3. Configurer les clés API dans config.py
#    → TELEGRAM_BOT_TOKEN  (via @BotFather)
#    → TELEGRAM_CHAT_ID    (via @userinfobot)
#    → LLM_API_KEY         (clé API Anthropic)

# 4. Premier test (sans Telegram)
python main.py --dry-run

# 5. Lancement réel
python main.py
```

## Commandes

| Commande | Description |
|----------|-------------|
| `python main.py` | Scan complet + alertes Telegram |
| `python main.py --dry-run` | Scan complet, affichage console uniquement |
| `python main.py --ticker VLA.PA` | Analyse un seul ticker |
| `python main.py --dry-run --ticker STM.PA` | Test sur un ticker sans Telegram |

## Configuration Telegram

1. Ouvrir Telegram → chercher **@BotFather**
2. Envoyer `/newbot`, suivre les instructions → récupérer le **token**
3. Chercher **@userinfobot** → il vous donne votre **chat_id**
4. Renseigner les deux dans `config.py`

## Personnaliser les seuils

Tout est dans `config.py` :
- `VOLUME_SPIKE_RATIO = 1.5` → Augmenter pour moins de signaux (plus strict)
- `RSI_OVERSOLD = 40` → Baisser à 30 pour des signaux de survente plus extrêmes
- `RSI_OVERBOUGHT = 60` → Monter à 70 pour du momentum plus confirmé
- `TICKERS` → Ajouter/retirer des actions de votre univers

## Automatiser (cron)

Pour lancer le scan tous les jours à 17h30 (après clôture Euronext) :

```bash
crontab -e
# Ajouter cette ligne :
30 17 * * 1-5 cd /chemin/vers/swing_agent && python main.py >> logs/cron.log 2>&1
```

## Roadmap V2

- [ ] Ordres automatiques via broker API (IBKR / Degiro)
- [ ] Stop-loss et take-profit dynamiques
- [ ] Backtesting sur données historiques
- [ ] Dashboard web temps réel
- [ ] Détection de patterns (double bottom, breakout)
