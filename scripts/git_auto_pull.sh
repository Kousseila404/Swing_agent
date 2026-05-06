#!/usr/bin/env bash
# Auto-pull main from origin if remote moved ahead.
# Triggered by cron every minute. Fast-forward only — never overwrites local work.
# Skips with a logged reason when the working tree conflicts with incoming commits.

set -u

REPO="/home/swing/swingquant"
BRANCH="main"
LOG="$REPO/logs/git_auto_pull.log"
LOCK="/tmp/swingquant_git_auto_pull.lock"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

exec 9>"$LOCK"
flock -n 9 || exit 0

cd "$REPO" || { log "ERR cd $REPO failed"; exit 1; }

current_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
if [ "$current_branch" != "$BRANCH" ]; then
  log "SKIP on branch=$current_branch (auto-pull only on $BRANCH)"
  exit 0
fi

if ! git fetch --quiet origin "$BRANCH" 2>>"$LOG"; then
  log "ERR fetch failed"
  exit 1
fi

local_sha="$(git rev-parse @)"
remote_sha="$(git rev-parse "@{u}")"
base_sha="$(git merge-base @ "@{u}")"

if [ "$local_sha" = "$remote_sha" ]; then
  exit 0
fi

if [ "$local_sha" != "$base_sha" ]; then
  log "SKIP local diverged from origin/$BRANCH (local=$local_sha remote=$remote_sha) — resolve manually"
  exit 0
fi

if out="$(git pull --ff-only --quiet origin "$BRANCH" 2>&1)"; then
  log "OK pulled $local_sha -> $remote_sha"
else
  log "SKIP pull --ff-only refused: $out"
fi
