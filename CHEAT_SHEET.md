# 📖 SWING QUANT — MANUEL D'UTILISATION OFFICIEL
> **Version :** V11 · **Mise à jour :** 2026-03-13
> _Garder ce fichier ouvert sur le deuxième écran._

---

## 🧠 Comment fonctionne le logiciel

**Swing Quant** est un agent IA de trading autonome. Il ne passe aucun ordre : il **scanne, analyse et alerte**.

```
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   SCANNER   │ →  │    NEWS      │ →  │  CERVEAU IA     │ →  │ ALERTE TELEGRAM │
│  yfinance   │    │  yfinance    │    │  Claude Sonnet  │    │  Bot Telegram   │
│  RSI + ADX  │    │  Actualités  │    │  Sentiment      │    │  Message formaté│
└─────────────┘    └──────────────┘    └─────────────────┘    └─────────────────┘
```

**Le pipeline en 4 étapes :**

| # | Étape | Rôle | Technologie |
|---|-------|------|-------------|
| 1 | **Filtre Macro** | Vérifie si le marché est en mode Bull/Bear/Crash (VIX + EMA200) | `^GSPC`, `^VIX` |
| 2 | **Scanner** | Détecte les anomalies de volume et de RSI sur 9 actifs | `yfinance` |
| 3 | **Cerveau IA** | Analyse le sentiment des news pour valider ou invalider le signal | `Claude Sonnet` |
| 4 | **Alerter** | Envoie un rapport structuré avec direction, confiance et risque | `Telegram API` |

**Les 3 régimes macro :**

| Régime | Condition | Effet |
|--------|-----------|-------|
| 🟢 `BULL_MARKET` | S&P500 > EMA200 et VIX < 25 | Signaux LONG activés |
| 🔴 `BEAR_MARKET` | S&P500 < EMA200 | Signaux SHORT activés |
| 🚨 `CRASH_PANIC` | VIX ≥ 35 | Pipeline **bloqué** (mode portfolio) |

**Univers suivi :**

| Catégorie | Tickers |
|-----------|---------|
| US Tech | `NVDA` `TSLA` `PLTR` `MSTR` `META` `COIN` |
| Crypto | `BTC-USD` `ETH-USD` `SOL-USD` |

---

## 🚀 Le Rituel de Connexion & Mise à Jour

### 1. Connexion SSH au serveur

```bash
# Connexion standard
ssh user@ADRESSE_IP_SERVEUR

# Avec un port personnalisé
ssh -p 2222 user@ADRESSE_IP_SERVEUR

# Avec une clé SSH (recommandé)
ssh -i ~/.ssh/ma_cle.pem user@ADRESSE_IP_SERVEUR
```

### 2. Naviguer vers le projet

```bash
cd ~/swing_agent
```

### 3. Activer l'environnement virtuel Python

```bash
# Activation (obligatoire avant tout script)
source venv/bin/activate

# Vérification : le prompt doit afficher (venv)
# (venv) user@serveur:~/swing_agent$

# Pour désactiver plus tard
deactivate
```

### 4. Mettre à jour le code depuis GitHub

```bash
# Récupérer les dernières modifications
git pull origin main

# Si conflit, forcer la mise à jour (⚠️ perd vos modifications locales)
git fetch origin && git reset --hard origin/main

# Vérifier l'état du dépôt
git status
git log --oneline -5
```

### 5. Vérifier / installer les dépendances après un pull

```bash
pip install -r requirements.txt
```

### 6. Vérifier la configuration `.env`

```bash
# Afficher les clés chargées (sans les valeurs)
python -c "import config"
# Si tout est OK : [ENV] ANTHROPIC_API_KEY → OK
#                  [ENV] TELEGRAM_BOT_TOKEN → OK
#                  [ENV] TELEGRAM_CHAT_ID → OK
```

---

## 🎛️ Tableau de Bord des Scripts (Comment tout lancer)

> ⚠️ **Prérequis :** être dans `~/swing_agent` avec le venv activé.

### MODE LIVE (production)

| Commande | Description | Telegram ? |
|----------|-------------|-----------|
| `python main.py` | Scan complet → alertes Telegram | ✅ Oui |
| `python main.py --dry-run` | Scan complet → console seulement | ❌ Non |
| `python main.py --ticker NVDA` | Analyse un seul ticker (live) | ✅ Oui |
| `python main.py --dry-run --ticker TSLA` | Test sur un ticker sans Telegram | ❌ Non |

### MODE PORTEFEUILLE (End-of-Day · V8)

| Commande | Description |
|----------|-------------|
| `python main.py --portfolio` | Top 3 Relative Strength → scan Daily |
| `python main.py --portfolio NVDA,TSLA,AAPL,MSFT` | Liste custom (calcule le Top 3 parmi eux) |
| `python main.py --portfolio --dry-run` | Mode portefeuille sans Telegram |

### MASSIVE SCANNER S&P500 (V15)

| Commande | Description |
|----------|-------------|
| `python main.py --scan-sp500` | S&P500 complet → Top 10 RS → ADX → IA → Telegram |
| `python main.py --scan-sp500 --dry-run` | Idem sans Telegram |

> ⚡ **À faire en premier (mise à jour du cache marché) :**
```bash
python main.py --update-cache             # Télécharge ~503 tickers (anti-ban, lots/20)
python main.py --update-cache --force-refresh  # Force le re-téléchargement intégral
```

### ANALYSE MACRO

| Commande | Description |
|----------|-------------|
| `python main.py --macro` | Affiche le régime actuel (S&P500 / EMA200 / VIX) |

### BACKTEST (V8 Classique)

| Commande | Description |
|----------|-------------|
| `python main.py --backtest` | Backtest tous les tickers (5 ans par défaut) |
| `python main.py --backtest --ticker NVDA` | Backtest un seul ticker |
| `python main.py --backtest --years 3` | Backtest sur 3 ans |

### BACKTEST VECTORISÉ + OPTIMISATION OPTUNA (V11)

| Commande | Description |
|----------|-------------|
| `python main.py --vector-backtest` | Optimisation ADX/RSI (20 trials Optuna) |
| `python main.py --vector-backtest --trials 50` | Plus précis (50 trials, plus long) |

> Génère un rapport HTML dans `data/reports/v11_backtest_YYYYMMDD.html`

### OPTIMISATION WALK-FORWARD

| Commande | Description |
|----------|-------------|
| `python main.py --optimize` | Optimise + conseil IA pour tous les tickers |
| `python main.py --optimize --ticker TSLA` | Optimise un seul ticker |
| `python main.py --optimize --no-ai` | Optimise sans appel IA (plus rapide) |

### RAPPORT

| Commande | Description |
|----------|-------------|
| `python main.py --report` | Rapport console + HTML (`data/backtest_report_*.html`) |
| `python main.py --report --no-html` | Rapport console seulement |
| `python main.py --tiers` | Classement des tickers par Tier (A/B/C/UNTRADABLE) |

---

## 🎹 Les Raccourcis de Survie (Clavier & Nano)

### Terminal — Touches indispensables

| Touche | Action |
|--------|--------|
| `Ctrl + C` | **Tuer** le script en cours (arrêt immédiat) |
| `Ctrl + Z` | Suspendre le processus (le met en pause en arrière-plan) |
| `Ctrl + D` | Fermer le terminal / déconnexion SSH |
| `Ctrl + L` | Effacer l'écran (équivalent `clear`) |
| `Ctrl + A` | Aller au **début** de la ligne |
| `Ctrl + E` | Aller à la **fin** de la ligne |
| `Ctrl + U` | Effacer tout ce qui est avant le curseur |
| `Tab` | **Auto-complétion** des commandes et chemins |
| `↑ / ↓` | Naviguer dans l'**historique** des commandes |
| `Ctrl + R` | **Rechercher** dans l'historique (taper un mot-clé) |

### Éditer le fichier `.env` avec Nano

```bash
# Ouvrir le fichier
nano .env
```

**Contenu type du `.env` :**
```
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxx
TELEGRAM_BOT_TOKEN=123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789
```

**Raccourcis Nano :**

| Raccourci | Action |
|-----------|--------|
| `Ctrl + O` puis `Entrée` | **Sauvegarder** le fichier |
| `Ctrl + X` | **Quitter** Nano |
| `Ctrl + W` | **Rechercher** du texte |
| `Ctrl + K` | **Couper** une ligne entière |
| `Ctrl + U` | **Coller** une ligne |
| `Ctrl + G` | Afficher l'**aide** |
| `↑ ↓ ← →` | Se déplacer dans le fichier |

> **Procédure complète pour modifier une clé API :**
> 1. `nano .env`
> 2. Naviguer jusqu'à la ligne à modifier avec les flèches
> 3. Modifier la valeur
> 4. `Ctrl + O` → `Entrée` (sauvegarder)
> 5. `Ctrl + X` (quitter)
> 6. `python -c "import config"` pour vérifier

---

## 🚨 Procédures d'Urgence & PM2

### Vérification rapide du statut

```bash
pm2 status                    # Tableau de tous les processus
pm2 list                      # Alias de pm2 status
```

**Lecture du tableau PM2 :**

| Colonne | Signification |
|---------|---------------|
| `name` | Nom du processus (ex: `swing_bot`) |
| `status` | `online` ✅ · `stopped` ⛔ · `errored` 🔴 |
| `↺ restart` | Nombre de redémarrages automatiques (élevé = problème) |
| `cpu` | Consommation CPU |
| `mem` | Consommation mémoire |

### Lire les logs en direct

```bash
pm2 logs                      # Tous les logs (live)
pm2 logs swing_bot            # Logs d'un processus spécifique
pm2 logs --lines 100          # Afficher les 100 dernières lignes
pm2 logs --err                # Uniquement les erreurs
```

> Pour quitter le flux de logs : `Ctrl + C`

**Lire les logs fichier directement :**
```bash
tail -f logs/agent.log        # Suivi en direct du log de l'agent
tail -n 50 logs/agent.log     # Dernières 50 lignes
```

### Contrôle d'urgence — Tuer / Redémarrer

```bash
# ── REDÉMARRER (garde les paramètres) ──────────────────────────
pm2 restart swing_bot         # Redémarrage propre
pm2 restart all               # Redémarrer TOUS les processus

# ── STOPPER (arrêt sans supprimer) ─────────────────────────────
pm2 stop swing_bot            # Stoppe le processus (conservé dans la liste)
pm2 stop all                  # Stoppe TOUT

# ── SUPPRIMER de PM2 ────────────────────────────────────────────
pm2 delete swing_bot          # Supprime de la liste PM2
pm2 delete all                # Supprime TOUT

# ── KILL (urgence absolue) ──────────────────────────────────────
pm2 kill                      # Tue le daemon PM2 ET tous ses processus
```

### Lancer le bot avec PM2

```bash
# Lancement initial
pm2 start main.py --name swing_bot --interpreter python

# Avec des arguments (ex: mode dry-run)
pm2 start main.py --name swing_bot_test --interpreter python -- --dry-run

# Sauvegarder la config PM2 (pour redémarrage après reboot serveur)
pm2 save

# Activer le démarrage automatique au reboot
pm2 startup
```

### Tableau de bord interactif

```bash
pm2 monit                     # Dashboard en temps réel (CPU/RAM/Logs)
```
> Pour quitter : `Ctrl + C`

### Fiche de débogage rapide

| Symptôme | Commande de diagnostic | Action |
|----------|------------------------|--------|
| Bot planté sans réponse | `pm2 status` | `pm2 restart swing_bot` |
| Erreur API Telegram | `pm2 logs --err` | Vérifier `.env` → `nano .env` |
| Erreur Anthropic (clé) | `python -c "import config"` | Vérifier `ANTHROPIC_API_KEY` |
| Mémoire qui explose | `pm2 monit` | `pm2 restart swing_bot` |
| Bot redémarre en boucle | `pm2 logs --lines 50` | Lire l'erreur, corriger, `pm2 restart` |
| Cache S&P500 vide | `ls data/market_cache/ \| wc -l` | `python main.py --update-cache` |

---

_Manuel généré automatiquement · Swing Quant V11 · © 2026_
