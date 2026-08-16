#!/usr/bin/env bash
# Auto-sync bidirectionnel de main avec origin. Triggered par cron chaque minute.
#
#   - Changements locaux non commit  → auto-commit ("auto: sync ...")
#   - Local en avance (rien de neuf en amont)     → push direct
#   - Remote en avance, local propre              → pull gated CI (cf. plus bas)
#   - Divergé (les deux ont bougé)                → rebase local sur remote puis push
#                                                    (abort + log si conflit — jamais
#                                                    de résolution automatique de conflit)
#
# CI gate sur pull (2026-05-06) :
#   On interroge GitHub Actions /commits/<sha>/check-runs sur le commit remote.
#   - Tous les check-runs success     → on pull.
#   - Au moins 1 in_progress/queued   → WAIT (on retry au prochain tick).
#   - Au moins 1 failure/cancelled    → BLOCK (le commit ne sera pas tiré tant
#                                       qu'il n'est pas remplacé par un commit
#                                       vert ou que le check est rejoué).
#   - 0 check_runs (commit sans CI)   → WAIT (GHA peut prendre 30-60s à enqueuer
#                                       après un push). Après MAX_NO_CHECK_MINUTES
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
MAX_NO_CHECK_MINUTES=10

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

exec 9>"$LOCK"
flock -n 9 || exit 0

cd "$REPO" || { log "ERR cd $REPO failed"; exit 1; }

current_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
if [ "$current_branch" != "$BRANCH" ]; then
  log "SKIP on branch=$current_branch (auto-sync only on $BRANCH)"
  exit 0
fi

# ─── Garde-fou 2026-07-16 : jamais committer un état cassé ──────────
# Incident : un git stash pop malheureux a laissé des marqueurs de conflit
# dans 20 fichiers, et l'ancienne version de ce script les a committés +
# pushés tels quels → API cassée en prod (SyntaxError au reload). On
# vérifie maintenant, AVANT tout commit, qu'aucun fichier modifié ne
# contient de marqueur de conflit ni de .py invalide.
#
# Garde-fou secrets (2026-08-16) : ce script commit et push tout seul,
# chaque minute, sans revue humaine du contenu. Le check syntaxe/conflit
# ci-dessus ne dit rien sur un token ou une clé privée qui traînerait dans
# un fichier oublié hors .gitignore — sans ce filtre, un secret ajouté par
# erreur atteint GitHub en moins d'une minute. On bloque le commit si un
# fichier modifié est un .env réel ou contient un pattern de secret connu.
SECRET_PATTERN='ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}|ghu_[A-Za-z0-9]{36}|ghs_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[baprs]-[A-Za-z0-9-]{10,}'

if [ -n "$(git status --porcelain)" ]; then
  bad=0
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    case "$f" in
      .env|*/.env|.env.*|*/.env.*)
        case "$f" in
          *.env.example|*/.env.example) ;;
          *)
            log "ERR fichier .env détecté dans les changements ($f) — commit annulé, ne doit jamais être tracké"
            bad=1
            ;;
        esac
        ;;
    esac
    if grep -qEI "$SECRET_PATTERN" "$f" 2>/dev/null; then
      log "ERR pattern de secret détecté dans $f — commit annulé, résolution manuelle requise"
      bad=1
    fi
    if grep -qE '^(<{7}|={7}|>{7})( |$)' "$f" 2>/dev/null; then
      log "ERR marqueur de conflit détecté dans $f — commit annulé, résolution manuelle requise"
      bad=1
    fi
    case "$f" in
      *.py)
        if ! python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$f" 2>>"$LOG"; then
          log "ERR $f invalide (SyntaxError) — commit annulé"
          bad=1
        fi
        ;;
    esac
  done <<< "$(git status --porcelain | awk '{print $2}')"

  if [ "$bad" -eq 1 ]; then
    exit 1
  fi

  git add -A
  stat_line="$(git diff --cached --shortstat | sed 's/^ *//')"
  git commit --quiet -m "auto: sync $(ts) — ${stat_line:-no stat}"
  log "OK auto-commit $(git rev-parse --short HEAD) ($stat_line)"
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

# ─── Local en avance, rien de neuf en amont → push direct ──────────
if [ "$remote_sha" = "$base_sha" ]; then
  if out="$(git push --quiet origin "$BRANCH" 2>&1)"; then
    log "OK pushed $base_sha -> $local_sha"
  else
    log "ERR push failed: $out"
  fi
  exit 0
fi

# ─── Divergé : local ET remote ont bougé → rebase puis push ────────
if [ "$local_sha" != "$base_sha" ]; then
  if out="$(git pull --rebase --quiet origin "$BRANCH" 2>&1)"; then
    if out2="$(git push --quiet origin "$BRANCH" 2>&1)"; then
      log "OK rebased+pushed -> $(git rev-parse --short HEAD)"
    else
      log "ERR push after rebase failed: $out2"
    fi
  else
    git rebase --abort 2>/dev/null
    log "ERR rebase conflict — résolution manuelle requise (local=$local_sha remote=$remote_sha)"
  fi
  exit 0
fi

# ─── Remote en avance, local propre → pull gated CI ─────────────────
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

# ─── Pull ─────────────────────────────────────────────────────────
if out="$(git pull --ff-only --quiet origin "$BRANCH" 2>&1)"; then
  log "OK pulled $local_sha -> $remote_sha"
else
  log "SKIP pull --ff-only refused: $out"
fi
