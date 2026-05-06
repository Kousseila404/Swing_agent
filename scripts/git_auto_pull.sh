#!/usr/bin/env bash
# Auto-pull main from origin if remote moved ahead AND CI on that commit is green.
# Triggered by cron every minute. Fast-forward only — never overwrites local work.
#
# CI gate (2026-05-06) :
#   On interroge GitHub Actions /commits/<sha>/check-runs sur le commit remote.
#   - Tous les check-runs success     → on pull.
#   - Au moins 1 in_progress/queued   → WAIT (on retry au prochain tick).
#   - Au moins 1 failure/cancelled    → BLOCK (le commit ne sera pas tiré tant
#                                       qu'il n'est pas remplacé par un commit
#                                       vert ou que le check est rejoué).
#   - 0 check_runs (commit sans CI)   → WAIT (GHA peut prendre 30-60s à enqueuer
#                                       après un push). Après _MAX_NO_CHECK_AGE
#                                       minutes, on tire quand même (filet pour
#                                       les commits qui ne déclenchent pas la CI,
#                                       ex: tag, doc-only sur path filtré).
#
# Token GitHub : lu depuis ~/.git-credentials (format
#   `https://USER:TOKEN@github.com`). Le token doit avoir le scope `repo` pour
#   les repos privés. Si absent ou invalide, le script retombe sur l'ancien
#   comportement (pull sans gate) avec un WARNING.

set -u

REPO="/home/swing/swingquant"
BRANCH="main"
LOG="$REPO/logs/git_auto_pull.log"
LOCK="/tmp/swingquant_git_auto_pull.lock"
GH_OWNER="Kousseila404"
GH_REPO="Swing_agent"
# Si un commit n'a aucun check_run après ce délai, on tire quand même (commits
# doc-only / tag qui ne déclenchent pas la CI). 10 min = sweet spot : laisse à
# GHA le temps d'enqueuer, sans bloquer indéfiniment des commits sans CI.
MAX_NO_CHECK_MINUTES=10

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

# ─── CI gate ───────────────────────────────────────────────────────
TOKEN=""
if [ -f ~/.git-credentials ]; then
  TOKEN="$(awk -F'[:@]' '/github\.com/ {print $3; exit}' ~/.git-credentials 2>/dev/null || true)"
fi

if [ -z "$TOKEN" ]; then
  log "WARN no GitHub token in ~/.git-credentials → CI gate désactivé, pull sans vérif"
else
  api_url="https://api.github.com/repos/$GH_OWNER/$GH_REPO/commits/$remote_sha/check-runs"
  api_resp="$(curl -s -m 10 -H "Authorization: token $TOKEN" \
                          -H "Accept: application/vnd.github+json" \
                          "$api_url" 2>/dev/null || echo '{}')"

  ci_status="$(python3 - <<PY 2>/dev/null
import json, sys
try:
    d = json.loads("""$api_resp""")
except Exception:
    print("error"); sys.exit()
if d.get("message"):
    # Erreur API (404, rate limit, etc.)
    print("error")
    sys.exit()
runs = d.get("check_runs") or []
if not runs:
    print("no_check"); sys.exit()
# Statuts possibles : queued, in_progress, completed.
# Conclusions completed : success, failure, cancelled, neutral, skipped, timed_out, action_required, stale.
pending = [r for r in runs if r.get("status") != "completed"]
if pending:
    print("pending"); sys.exit()
bad = [r for r in runs if r.get("conclusion") in ("failure", "cancelled", "timed_out", "action_required")]
if bad:
    print("failure"); sys.exit()
print("success")
PY
)"

  case "$ci_status" in
    success)
      ;;  # OK, on continue le pull
    pending)
      log "WAIT CI in_progress sur $remote_sha — retry au prochain tick"
      exit 0
      ;;
    failure)
      log "BLOCK CI failed sur $remote_sha — pull suspendu jusqu'à push d'un fix"
      exit 0
      ;;
    no_check)
      # Commit sans CI déclenchée : on attend MAX_NO_CHECK_MINUTES avant de
      # tirer (laisse à GHA le temps d'enqueuer ; après c'est doc-only
      # probablement).
      commit_age_sec="$(curl -s -m 5 -H "Authorization: token $TOKEN" \
                       -H "Accept: application/vnd.github+json" \
                       "https://api.github.com/repos/$GH_OWNER/$GH_REPO/commits/$remote_sha" 2>/dev/null \
                       | python3 -c "
import json, sys, datetime
try:
    d = json.load(sys.stdin)
    iso = d['commit']['author']['date']
    dt = datetime.datetime.fromisoformat(iso.replace('Z','+00:00'))
    print(int((datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()))
except Exception:
    print(0)
" 2>/dev/null)"
      commit_age_sec="${commit_age_sec:-0}"
      if [ "$commit_age_sec" -lt "$((MAX_NO_CHECK_MINUTES * 60))" ]; then
        log "WAIT no_check sur $remote_sha (age=${commit_age_sec}s, seuil ${MAX_NO_CHECK_MINUTES}min)"
        exit 0
      fi
      log "OK no_check sur $remote_sha mais age > ${MAX_NO_CHECK_MINUTES}min — pull autorisé (commit doc-only ou CI désactivée)"
      ;;
    error|*)
      log "WARN CI status indéterminé ($ci_status) sur $remote_sha — pull autorisé par fail-open"
      ;;
  esac
fi

# ─── Pull ───────────────────────────────────────────────────────────
if out="$(git pull --ff-only --quiet origin "$BRANCH" 2>&1)"; then
  log "OK pulled $local_sha -> $remote_sha"
else
  log "SKIP pull --ff-only refused: $out"
fi
