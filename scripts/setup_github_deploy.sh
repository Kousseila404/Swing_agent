#!/usr/bin/env bash
# Lance ce script avec un token GitHub valide pour finaliser le setup SSH
# Usage: bash scripts/setup_github_deploy.sh ghp_XXXX...

set -e
TOKEN="${1:-$(grep GITHUB_TOKEN backend/.env | cut -d= -f2)}"
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

echo "→ Mise à jour du token dans .env..."
sed -i "s|GITHUB_TOKEN=.*|GITHUB_TOKEN=$TOKEN|" backend/.env

echo "✓ Setup complet. Le cron auto-pull va reprendre dans 1 minute."
