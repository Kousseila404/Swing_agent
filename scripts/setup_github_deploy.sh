#!/usr/bin/env bash
# Finalise le setup SSH avec le token GitHub présent dans backend/.env.
# Usage: bash scripts/setup_github_deploy.sh
#
# Le token ne doit JAMAIS être passé en argument de ligne de commande :
# argv est visible via `ps aux` par tout autre utilisateur de la machine et
# finit souvent en clair dans l'historique du shell. Mets GITHUB_TOKEN=...
# dans backend/.env (chmod 600) avant de lancer ce script.

set -e
TOKEN="$(grep GITHUB_TOKEN backend/.env | cut -d= -f2)"
if [ -z "$TOKEN" ]; then
  echo "✗ GITHUB_TOKEN absent ou vide dans backend/.env" >&2
  exit 1
fi
PUB_KEY=$(cat ~/.ssh/github_deploy.pub)

echo "→ Ajout de la deploy key sur GitHub..."
RESP=$(curl -s -o /tmp/gh_resp.json -w "%{http_code}" \
  -X POST \
  -H "Authorization: token $TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/Kousseila404/Swing_agent/keys \
  -d "{\"title\": \"VPS Auto-Pull\", \"key\": \"$PUB_KEY\", \"read_only\": true}")

if [ "$RESP" = "201" ]; then
  echo "✓ Deploy key ajoutée avec succès"
else
  echo "✗ Erreur HTTP $RESP"
  cat /tmp/gh_resp.json
  exit 1
fi

echo "→ Test connexion SSH..."
ssh -o StrictHostKeyChecking=no -T git@github.com 2>&1 || true

echo "→ Test git pull..."
git pull --ff-only

echo "✓ Setup complet. Le cron auto-pull va reprendre dans 1 minute."
