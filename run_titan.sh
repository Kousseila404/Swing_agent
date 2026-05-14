#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# SwingQuant TITAN — Cron quotidien (run quotidien 06:00 locale)
#
# Pipeline en 9 étapes :
#   1/9  macro_engine (VIX + régime BULL/BEAR/CRASH_PANIC)
#   2/9  universe_scheduler (rotation 100 tickers/j, min-age 7j)
#   3/9  insider_enrich (SEC EDGAR Form 4)
#   4/9  finnhub_enrich (revisions + earnings)
#   5/9  warm /api/portfolio/recommendations
#   6/9  universe_history snapshot (raw fundamentals + scores)
#   7/9  wfo_monitor (1er du mois uniquement)
#   8/9  POST /api/proposals/refresh
#   9/9  bear_hedge (régime macro défensif)
#
# Durcissement 2026-05-14 (V4 cron) :
#   • flock pour éviter le double-run (cron + override manuel)
#   • Exit code agrégé : tous les rc_* contribuent au code de sortie
#   • Alerte Telegram immédiate sur échec d'un step CRITIQUE
#   • Log rotation : titan_daily.log → .1, .2 si > 20 MB, garde 30j
#   • Durée totale + par-step trackée pour détecter les régressions
#
# Crontab :
#   0 6 * * * /home/swing/swingquant/run_titan.sh
# ─────────────────────────────────────────────────────────────────
set -o pipefail

ROOT="/home/swing/swingquant"
BACKEND="$ROOT/backend"
VENV="$BACKEND/venv"
LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/titan_daily.log"
LOCK_FILE="$LOG_DIR/titan_daily.lock"
API_URL="${TITAN_API_URL:-http://localhost:8000}"

mkdir -p "$LOG_DIR"

# ── Log rotation simple : rotate si > 20 MB, garde 30j max ───────
# Évite la croissance illimitée du log sur des années de run quotidien.
# logrotate système serait propre mais ajoute une dépendance ; ce script
# est self-contained.
_rotate_logs() {
    if [[ -f "$LOG_FILE" ]]; then
        local size_mb
        size_mb=$(du -m "$LOG_FILE" 2>/dev/null | cut -f1)
        if [[ "${size_mb:-0}" -ge 20 ]]; then
            mv "$LOG_FILE" "${LOG_FILE}.$(date +%Y%m%d-%H%M%S)"
        fi
    fi
    # Cleanup des archives > 30 jours.
    find "$LOG_DIR" -maxdepth 1 -name 'titan_daily.log.*' -mtime +30 -delete 2>/dev/null || true
}
_rotate_logs

# Redirection globale : stdout + stderr → log, avec horodatage ligne-à-ligne.
exec > >(awk '{ print strftime("[%Y-%m-%d %H:%M:%S]"), $0; fflush(); }' >> "$LOG_FILE") 2>&1

# ── Lock anti-concurrent run ─────────────────────────────────────
# flock -n : exit immédiat si déjà locké (un 2e cron + override manuel
# en parallèle = corruption potentielle universe.json + double snapshot).
# fd 200 réservé au lock, FUITE-safe (le shell ferme au exit).
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "════════════════════════════════════════════════════════════"
    echo "ABORT: un autre run TITAN tient déjà $LOCK_FILE (PID concurrent)."
    echo "════════════════════════════════════════════════════════════"
    exit 23
fi

# ── Tracking durée globale ───────────────────────────────────────
START_EPOCH=$SECONDS
declare -A STEP_DURATIONS=()

echo "════════════════════════════════════════════════════════════"
echo "TITAN daily run — PID $$ — host $(hostname)"
echo "════════════════════════════════════════════════════════════"

# ── Sanity checks ────────────────────────────────────────────────
PYTHON="$VENV/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo "FATAL: interpréteur introuvable à $PYTHON"
    exit 10
fi

cd "$BACKEND" || { echo "FATAL: cd $BACKEND impossible"; exit 11; }

# ── Helpers ──────────────────────────────────────────────────────
# _run_step <name> <command...> : exécute, log la durée, stocke dans
# STEP_DURATIONS, retourne le rc d'origine. À utiliser pour les steps
# qui n'ont pas besoin d'env spéciale ou de timeout extérieur.
_run_step() {
    local name="$1"; shift
    local t0=$SECONDS
    "$@"
    local rc=$?
    local dt=$((SECONDS - t0))
    STEP_DURATIONS["$name"]=$dt
    echo "[duration] $name=${dt}s rc=$rc"
    return $rc
}

# _alert_critical <step> <rc> <hint> : envoie une alerte Telegram
# pour les fails critiques (macro stale → sizing risqué, etc.).
# Best-effort : un échec d'alerte ne fait pas planter le cron.
_alert_critical() {
    local step="$1"
    local rc="$2"
    local hint="$3"
    local host
    host=$(hostname)
    "$PYTHON" -c "
import sys
sys.path.insert(0, '.')
try:
    from modules.alerter import send_crash_alert
    msg = f'TITAN cron $host : step {sys.argv[1]} exit={sys.argv[2]}\\n{sys.argv[3]}'
    send_crash_alert(msg)
except Exception as e:
    print(f'[alert] failed to send: {e}', file=sys.stderr)
" "$step" "$rc" "$hint" 2>/dev/null || echo "WARN: alerte Telegram impossible (alerter module muet)"
}

# ── 1/9 — Macro regime + VIX refresh ─────────────────────────────
#     CRITIQUE : sans ce step le VIX reste stale, le sizing tombe sur
#     fallback VIX=20 → aucun downsize en panique. Alerte si fail.
echo "── Step 1/9 : macro_engine (VIX + régime)"
t0=$SECONDS
"$PYTHON" -c "from modules.macro_engine import get_market_regime; print('macro →', get_market_regime(use_cache=False))"
rc_macro=$?
STEP_DURATIONS[macro]=$((SECONDS - t0))
echo "[duration] macro=${STEP_DURATIONS[macro]}s rc=$rc_macro"
if [[ $rc_macro -ne 0 ]]; then
    echo "WARN: macro_engine exit=$rc_macro (VIX stale → sizing fallback VIX=20)"
    _alert_critical "macro_engine" "$rc_macro" "VIX stale → sizing surconfiant possible. Vérifier yfinance + macro_state.json"
fi

# ── 2/9 — Universe scheduler (budget 100 tickers/j) ──────────────
echo "── Step 2/9 : universe_scheduler --budget 100 --min-age-days 7"
t0=$SECONDS
"$PYTHON" -m modules.universe_scheduler --budget 100 --min-age-days 7
rc_sched=$?
STEP_DURATIONS[universe_scheduler]=$((SECONDS - t0))
echo "[duration] universe_scheduler=${STEP_DURATIONS[universe_scheduler]}s rc=$rc_sched"
if [[ $rc_sched -ne 0 ]]; then
    echo "WARN: universe_scheduler exit=$rc_sched"
    _alert_critical "universe_scheduler" "$rc_sched" "Universe rotation a échoué. Stale severe gate (>48h) bloque les propositions."
fi

# ── 3/9 — Insider enrichment (SEC EDGAR Form 4, gratuit) ─────────
echo "── Step 3/9 : insider_enrich (SEC EDGAR, timeout 15min)"
t0=$SECONDS
timeout 900 "$PYTHON" -m modules.insider_enrich --workers 5
rc_insider=$?
STEP_DURATIONS[insider_enrich]=$((SECONDS - t0))
echo "[duration] insider_enrich=${STEP_DURATIONS[insider_enrich]}s rc=$rc_insider"
if [[ $rc_insider -eq 124 ]]; then
    echo "WARN: insider_enrich TIMEOUT (>15min) — pilier Insider restera sur état précédent"
elif [[ $rc_insider -ne 0 ]]; then
    echo "WARN: insider_enrich exit=$rc_insider (continue — pilier Insider sera neutre)"
fi

# ── 4/9 — Finnhub enrichment (Revisions + Earnings) ──────────────
echo "── Step 4/9 : finnhub_enrich (Revisions + Earnings, timeout 45min)"
t0=$SECONDS
timeout 2700 "$PYTHON" -m modules.finnhub_enrich
rc_finnhub=$?
STEP_DURATIONS[finnhub_enrich]=$((SECONDS - t0))
echo "[duration] finnhub_enrich=${STEP_DURATIONS[finnhub_enrich]}s rc=$rc_finnhub"
if [[ $rc_finnhub -eq 124 ]]; then
    echo "WARN: finnhub_enrich TIMEOUT (>45min) — cache partiel persisté"
elif [[ $rc_finnhub -ne 0 ]]; then
    echo "WARN: finnhub_enrich exit=$rc_finnhub (yfinance reste fallback)"
fi

# ── 5/9 — Warm du cache /api/portfolio/recommendations ───────────
echo "── Step 5/9 : warm /api/portfolio/recommendations ($API_URL)"
t0=$SECONDS
rc_warm=0
if curl --silent --show-error --fail --max-time 180 \
        "$API_URL/api/portfolio/recommendations?total_capital=100000&max_holdings=20" \
        -o /dev/null -w "http=%{http_code} time=%{time_total}s\n"; then
    echo "OK — cache API chaud."
else
    rc_warm=$?
    echo "WARN: warm HTTP failed (rc=$rc_warm) — API down sur $API_URL ?"
    _alert_critical "warm_api" "$rc_warm" "API HTTP inaccessible à $API_URL. swing-api.service down ?"
fi
STEP_DURATIONS[warm]=$((SECONDS - t0))
echo "[duration] warm=${STEP_DURATIONS[warm]}s rc=$rc_warm"

# ── 6/9 — Snapshot historique ────────────────────────────────────
echo "── Step 6/9 : universe_history snapshot"
t0=$SECONDS
"$PYTHON" -m modules.universe_history
rc_hist=$?
STEP_DURATIONS[universe_history]=$((SECONDS - t0))
echo "[duration] universe_history=${STEP_DURATIONS[universe_history]}s rc=$rc_hist"
if [[ $rc_hist -ne 0 ]]; then
    echo "WARN: universe_history snapshot exit=$rc_hist"
fi

# ── 7/9 — WFO monitor (1er du mois uniquement) ───────────────────
rc_wfo=0
if [[ "$(date +%d)" == "01" ]]; then
    echo "── Step 7/9 : wfo_monitor (mensuel, lag=30)"
    t0=$SECONDS
    "$PYTHON" -m modules.wfo_monitor --train-days 30 --test-days 10 --publication-lag-days 30
    rc_wfo=$?
    STEP_DURATIONS[wfo_monitor]=$((SECONDS - t0))
    echo "[duration] wfo_monitor=${STEP_DURATIONS[wfo_monitor]}s rc=$rc_wfo"
    if [[ $rc_wfo -ne 0 ]]; then
        echo "WARN: wfo_monitor exit=$rc_wfo (historique probablement insuffisant)"
    fi
else
    echo "── Step 7/9 : wfo_monitor SKIP (jour $(date +%d), exécuté seulement le 01)"
fi

# ── 8/9 — Refresh propositions (auto_proposer + Telegram) ────────
echo "── Step 8/9 : POST /api/proposals/refresh"
t0=$SECONDS
rc_props=0
if [[ -z "${API_TOKEN:-}" ]]; then
    if [[ -f "$BACKEND/.env" ]]; then
        API_TOKEN=$(grep -E '^API_TOKEN=' "$BACKEND/.env" | head -n1 | cut -d= -f2- | tr -d '"' | tr -d "'")
    fi
fi
if [[ -z "${API_TOKEN:-}" ]]; then
    echo "SKIP: API_TOKEN absent (set dans crontab ou backend/.env)."
else
    if curl --silent --show-error --fail --max-time 120 \
            -X POST \
            -H "Authorization: Bearer ${API_TOKEN}" \
            -H "Content-Type: application/json" \
            -d '{"notify_telegram": true}' \
            "$API_URL/api/proposals/refresh" \
            -o /tmp/titan_proposals.json \
            -w "http=%{http_code} time=%{time_total}s\n"; then
        n=$(jq '.proposals | length' /tmp/titan_proposals.json 2>/dev/null \
            || grep -o '"id"' /tmp/titan_proposals.json 2>/dev/null | wc -l)
        echo "OK — ${n:-0} proposition(s) enqueuée(s)."
    else
        rc_props=$?
        echo "WARN: refresh proposals HTTP failed (rc=$rc_props)"
    fi
fi
STEP_DURATIONS[proposals]=$((SECONDS - t0))
echo "[duration] proposals=${STEP_DURATIONS[proposals]}s rc=$rc_props"

# ── 9/9 — Bear hedge (régime macro défensif) ─────────────────────
echo "── Step 9/9 : bear_hedge (régime macro défensif)"
t0=$SECONDS
"$PYTHON" -m modules.bear_hedge
rc_hedge=$?
STEP_DURATIONS[bear_hedge]=$((SECONDS - t0))
echo "[duration] bear_hedge=${STEP_DURATIONS[bear_hedge]}s rc=$rc_hedge"
if [[ $rc_hedge -ne 0 ]]; then
    echo "WARN: bear_hedge exit=$rc_hedge (vérifier macro_state.json + broker)"
fi

# ── Récap durée totale + exit code agrégé ────────────────────────
TOTAL_DURATION=$((SECONDS - START_EPOCH))
echo "════════════════════════════════════════════════════════════"
echo "RUN SUMMARY — total=${TOTAL_DURATION}s"
for k in "${!STEP_DURATIONS[@]}"; do
    printf "  %-22s %ds\n" "$k" "${STEP_DURATIONS[$k]}"
done | sort
echo "  rc_macro=$rc_macro  rc_sched=$rc_sched  rc_insider=$rc_insider  rc_finnhub=$rc_finnhub"
echo "  rc_warm=$rc_warm  rc_hist=$rc_hist  rc_wfo=$rc_wfo  rc_props=$rc_props  rc_hedge=$rc_hedge"
echo "════════════════════════════════════════════════════════════"

# ── Exit code agrégé ─────────────────────────────────────────────
# Avant : seul rc_sched/warm/hist/props comptait → un macro_engine KO
# ne ressortait jamais. Maintenant : tout step CRITIQUE → exit != 0
# pour qu'un monitoring exit-code-only (systemd OnFailure=, etc.) voie.
#
# Codes :
#   0  = full OK
#   1  = échec non-critique (insider/finnhub/wfo/hedge)
#   2  = warm HTTP KO
#   3  = history snapshot KO
#   4  = proposals refresh KO
#   5  = macro_engine KO (CRITIQUE — VIX stale)
#   6  = universe_scheduler KO (CRITIQUE — univers stale)
#   10 = sanity check (venv manquant)
#   11 = sanity check (cd backend)
#   23 = lock conflict (run concurrent)
final_rc=0
if [[ $rc_sched   -ne 0 ]]; then final_rc=6; fi
if [[ $rc_macro   -ne 0 ]]; then final_rc=5; fi
if [[ $rc_props   -ne 0 ]]; then final_rc=4; fi
if [[ $rc_hist    -ne 0 ]]; then final_rc=3; fi
if [[ $rc_warm    -ne 0 ]]; then final_rc=2; fi
if [[ $rc_insider -ne 0 && $rc_insider -ne 124 ]] \
   || [[ $rc_finnhub -ne 0 && $rc_finnhub -ne 124 ]] \
   || [[ $rc_wfo  -ne 0 ]] \
   || [[ $rc_hedge -ne 0 ]]; then
    [[ $final_rc -eq 0 ]] && final_rc=1
fi

if [[ $final_rc -eq 0 ]]; then
    echo "DONE — all steps OK (${TOTAL_DURATION}s)"
else
    echo "DONE with errors — final_rc=$final_rc (${TOTAL_DURATION}s)"
fi
exit $final_rc
