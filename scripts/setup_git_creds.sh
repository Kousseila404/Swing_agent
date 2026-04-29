#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# SwingQuant TITAN — Synchronise ~/.git-credentials depuis backend/.env
#
# Source unique = backend/.env (GITHUB_USER + GITHUB_TOKEN).
# Ce script régénère ~/.git-credentials et active le credential.helper
# `store` pour que `git push` fonctionne sans prompt.
#
# À ré-exécuter après chaque rotation du PAT (édite backend/.env, puis
# lance ce script).
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="${TITAN_ROOT:-/home/swing/swingquant}"
ENV_FILE="$ROOT/backend/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "FATAL: $ENV_FILE introuvable" >&2
    exit 1
fi

# Charge les vars sans les afficher
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${GITHUB_USER:?GITHUB_USER manquant dans $ENV_FILE}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN manquant dans $ENV_FILE}"

# Écrit ~/.git-credentials en mode 600
umask 077
printf 'https://%s:%s@github.com\n' "$GITHUB_USER" "$GITHUB_TOKEN" > "$HOME/.git-credentials"
chmod 600 "$HOME/.git-credentials"

# Active le helper si pas déjà fait
git config --global credential.helper store

echo "✓ ~/.git-credentials régénéré (mode 600) pour user=$GITHUB_USER"
echo "  credential.helper = $(git config --global --get credential.helper)"
