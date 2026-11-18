#!/usr/bin/env bash
# Sentinel + triage IA (audit 2026-11-18).
#
#   1. modules.sentinel : invariants de résultat, alerte Telegram dédupliquée.
#   2. Si statut CRIT avec une empreinte jamais triée ET < MAX_TRIAGES_24H
#      triages sur 24 h → agent Claude headless `sq-ops-investigator`, en
#      LECTURE SEULE STRICTE (outils allow-listés, écriture interdite), rapport
#      dans backend/data/sentinel/triage/ + résumé Telegram.
#
# Désactiver le triage IA : SENTINEL_AI_TRIAGE=0 dans backend/.env.
set -u

REPO="/home/swing/swingquant"
BACKEND="$REPO/backend"
PY="$BACKEND/venv/bin/python"
CLAUDE_BIN="${CLAUDE_BIN:-/home/swing/.npm-global/bin/claude}"
OUT="$BACKEND/data/sentinel"
TRIAGE_DIR="$OUT/triage"
STATE="$OUT/triage_state.json"
MAX_TRIAGES_24H=2

ts() { date -u '+%Y-%m-%d %H:%M:%S'; }
export PATH="/home/swing/.npm-global/bin:/usr/local/bin:/usr/bin:/bin"
export HOME="${HOME:-/home/swing}"

cd "$BACKEND" || exit 1
"$PY" -m modules.sentinel
rc=$?
echo "[$(ts)] sentinel rc=$rc"

AI_TRIAGE=$(grep -E '^SENTINEL_AI_TRIAGE=' .env 2>/dev/null | tail -n1 | cut -d= -f2 | tr -d '"'"'"' ')
[[ "${AI_TRIAGE:-1}" == "0" ]] && { echo "[$(ts)] triage IA désactivé"; exit 0; }
[[ $rc -ne 2 ]] && exit 0
[[ -x "$CLAUDE_BIN" ]] || { echo "[$(ts)] claude CLI introuvable"; exit 0; }

decision=$("$PY" - "$OUT/latest.json" "$STATE" "$MAX_TRIAGES_24H" <<'EOF'
import json, sys, time
latest, state_path, max_n = json.load(open(sys.argv[1])), sys.argv[2], int(sys.argv[3])
try:
    state = json.load(open(state_path))
except Exception:
    state = {}
now = time.time()
recent = [t for t in state.get("runs", []) if now - t < 86400]
fp = latest["fingerprint"]
if fp in state.get("fingerprints", []) or len(recent) >= max_n:
    print("skip")
else:
    state["fingerprints"] = (state.get("fingerprints", []) + [fp])[-50:]
    state["runs"] = recent + [now]
    json.dump(state, open(state_path, "w"))
    print(fp)
EOF
)
[[ "$decision" == "skip" || -z "$decision" ]] && { echo "[$(ts)] triage déjà fait / quota atteint"; exit 0; }

mkdir -p "$TRIAGE_DIR"
report="$TRIAGE_DIR/$(date -u +%Y%m%dT%H%M%SZ)-$decision.md"
echo "[$(ts)] triage IA → $report"

cd "$REPO" || exit 1
timeout 900 "$CLAUDE_BIN" -p \
  "Incident sentinel (empreinte $decision). Lis backend/data/sentinel/latest.json, diagnostique chaque check CRIT puis WARN, et rends ton rapport au format prévu. Tu ne peux rien modifier." \
  --agent sq-ops-investigator \
  --model sonnet \
  --max-turns 40 \
  --allowedTools "Read" "Grep" "Glob" \
      "Bash(tail:*)" "Bash(head:*)" "Bash(grep:*)" "Bash(sed -n:*)" "Bash(ls:*)" "Bash(cat:*)" \
      "Bash(wc:*)" "Bash(git log:*)" "Bash(git show:*)" "Bash(git diff:*)" \
      "Bash(journalctl:*)" "Bash(sudo -n journalctl:*)" "Bash(free:*)" "Bash(df:*)" \
  --disallowedTools "Write" "Edit" "NotebookEdit" \
  > "$report" 2>&1
echo "[$(ts)] triage rc=$?"

cd "$BACKEND" && "$PY" - "$report" <<'EOF'
import html, sys
from modules.alerter import _send_telegram_message
text = open(sys.argv[1]).read().strip()
body = "\n".join(text.splitlines()[:30])[:3500]
_send_telegram_message("🤖 <b>Triage IA (sq-ops-investigator)</b>\n<pre>" + html.escape(body) + "</pre>")
EOF
