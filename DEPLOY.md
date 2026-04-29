# SwingQuant TITAN — Runbook ingénierie

Ce fichier est le **mode d'emploi opérationnel** : redéploiement, backups,
cron, CI, tests. Pour le contexte produit/quant, voir la mémoire conversation
et les commits.

---

## 1. Redéploiement de l'API (prod)

L'API tourne en `systemd`. Tout changement Python doit déclencher un restart.

```bash
make redeploy                    # restart swing-api.service + status
# équivalent :
sudo systemctl restart swing-api.service
sudo systemctl status swing-api.service --no-pager | head -10
```

Vérifications post-deploy :

```bash
curl -s http://localhost:8000/api/status | jq
curl -s http://localhost:8000/api/market_status | jq
tail -f logs/titan_daily.log
```

Si `swing-api` ne redémarre pas : `journalctl -u swing-api.service -n 100 --no-pager`.

---

## 2. Tests + lint en local (avant commit)

```bash
make ci-local        # ruff + pytest + frontend build (reproduit la CI GitHub)
make test            # pytest seul
make test-fast       # pytest -x --ff (premier échec, flaky priorité)
make lint            # ruff backend
make lint-fix        # ruff --fix
make types           # frontend openapi → ts (fail si drift)
```

Suite de référence : **708 tests passants** au 2026-04-29 (HEAD `chore/infra-baseline`).

Si `make lint` retourne >0 erreurs sur le HEAD, c'est attendu tant que le WIP
métier n'est pas commité — la CI va piquer ces erreurs au push.

---

## 3. Backups

### Quotidien (cron, automatique)

```cron
30 6 * * * /home/swing/swingquant/scripts/backup_journal.sh >> /home/swing/swingquant/logs/backup_journal.log 2>&1
```

→ copie horodatée de `trade_journal.duckdb`, `trade_journal.csv`, `equity_state.json`
vers `backups/`, avec sha256 + rétention 30j.

### Manuel

```bash
make backup
ls -lh backups/ | tail
```

### Restauration

```bash
# 1. Stopper l'API (sinon DuckDB lock)
sudo systemctl stop swing-api.service

# 2. Choisir le snapshot cible
ls backups/trade_journal.duckdb.* | tail
SNAP=backups/trade_journal.duckdb.20260429T063000Z

# 3. Vérifier intégrité
sha256sum -c "$SNAP.sha256"

# 4. Remplacer
cp -p "$SNAP" backend/data/trade_journal.duckdb

# 5. Redémarrer
sudo systemctl start swing-api.service
```

---

## 4. Cycle quotidien TITAN

```cron
0 6 * * * /home/swing/swingquant/run_titan.sh
```

Étapes (cf. `run_titan.sh`) :

0. `macro_engine` → refresh VIX + régime, écrit `macro_state.json`
1. `universe_scheduler --budget 100 --min-age-days 7` → rotation tournante
2. Pré-chauffage du cache `/api/portfolio/recommendations`

Logs : `logs/titan_daily.log` (horodatés ligne-à-ligne).

Cron complets actuels :

| Schedule | Commande | Rôle |
|---|---|---|
| `*/2 * * * 1-5` | `tracker.py` | Évaluation positions toutes les 2 min en heures de marché |
| `0 9 * * 1-5` | `main.py --health-check` | Health |
| `0 6 * * *` | `run_titan.sh` | Cycle quotidien (universe + recos) |
| `30 6 * * *` | `scripts/backup_journal.sh` | Backup post-cycle |
| `*/15 9-16 * * 1-5` | `main.py --alpaca-sync` | Sync broker |
| `30 16 * * 1-5` | `scripts/run_monitor_alerts` | Alerts post-clôture |
| `0 3 * * 1` | logrotate | Rotation logs |

---

## 4bis. Authentification git (push / API GitHub)

`backend/.env` contient `GITHUB_USER` + `GITHUB_TOKEN` (PAT scope `repo`+`workflow`).
C'est la **source unique** pour git et tout script qui appelle l'API GitHub.

### Première installation / rotation du PAT

```bash
# 1. Générer un PAT : https://github.com/settings/tokens (scopes : repo, workflow)
# 2. Mettre à jour backend/.env (GITHUB_USER, GITHUB_TOKEN)
# 3. Synchroniser ~/.git-credentials
make git-creds
# ou directement :
scripts/setup_git_creds.sh
```

Le script régénère `~/.git-credentials` (mode 600) et active `credential.helper=store`.
Plus aucun prompt sur `git push`.

---

## 5. Branches & CI

- `main` = dernier état stable.
- `chore/*` = ingénierie (CI, infra, build).
- `feat/*` = nouvelle feature métier.
- CI GitHub Actions (`.github/workflows/ci.yml`) tourne sur push/PR : ruff + pytest+cov + frontend build + docker smoke.
- Pas de push direct sur `main` une fois la CI active — passer par PR.

---

## 6. Pre-commit (optionnel mais recommandé)

```bash
make install-dev                 # installe pre-commit dans le venv
backend/venv/bin/pre-commit install
backend/venv/bin/pre-commit run -a    # premier passage sur tout
```

Hooks : ruff fix, fichiers >500 KB, fins de ligne, secrets-leak basique.

---

## 7. Reproduire l'environnement (Docker)

```bash
docker compose up backend                   # API seule
docker compose --profile dev up             # API + frontend Vite
docker compose run --rm backend pytest tests/   # tests dans l'image
```

Sert à valider une bump de Python/deps avant de toucher au venv prod.
La prod reste en systemd, **pas** en compose.

---

## 8. Checklist avant un changement risqué

- [ ] `make ci-local` vert
- [ ] `make backup` exécuté
- [ ] Branche feature poussée + PR ouverte
- [ ] Diff métier review (pas de print debug, pas de secret, tests à jour)
- [ ] Si touche scoring/risk : noter dans MEMORY le delta attendu
- [ ] `make redeploy` puis vérifier `tail -f logs/titan_daily.log`
- [ ] Si régression : `git revert` + redeploy + restaurer backup si journal corrompu
