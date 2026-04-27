#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# SwingQuant TITAN — Cron quotidien
#
#   1. Refresh tournant de universe.json (budget 100 tickers/jour,
#      min-age 7j → rotation complète ~1 semaine).
#   2. Pré-chauffage du cache de recommandations (scored universe)
#
# Toutes les sorties (stdout + stderr) sont horodatées et appendées
# dans logs/titan_daily.log.
#
# Crontab (exécution à 06:00 locale, avant l'ouverture US) :
#   0 6 * * * /home/swing/swingquant/run_titan.sh
# ─────────────────────────────────────────────────────────────────
set -o pipefail

ROOT="/home/swing/swingquant"
BACKEND="$ROOT/backend"
VENV="$BACKEND/venv"
LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/titan_daily.log"
API_URL="${TITAN_API_URL:-http://localhost:8000}"

mkdir -p "$LOG_DIR"

# Redirection globale : stdout + stderr → log, avec horodatage ligne-à-ligne.
exec > >(awk '{ print strftime("[%Y-%m-%d %H:%M:%S]"), $0; fflush(); }' >> "$LOG_FILE") 2>&1

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

# ── 0. Macro regime + VIX refresh ────────────────────────────────
#     `get_market_regime(use_cache=False)` télécharge ^GSPC + ^VIX (yfinance),
#     détecte BULL/BEAR/CRASH_PANIC, applique la confirmation N-jours, et
#     écrit macro_state.json avec le régime + VIX + SP500 + EMA200.
#
#     CRITIQUE pour le sizing : sans ce step, macro_state.json reste figé,
#     le champ `vix` est absent → `regime_adjusted_risk` tombe sur fallback
#     VIX=20 → aucun downsize en panique (CRASH_PANIC jamais détecté).
echo "── Step 0/4 : macro_engine (VIX + régime)"
"$PYTHON" -c "from modules.macro_engine import get_market_regime; print('macro →', get_market_regime(use_cache=False))"
rc_macro=$?
if [[ $rc_macro -ne 0 ]]; then
    echo "WARN: macro_engine exit=$rc_macro (on continue — VIX restera stale)"
fi

# ── 1. Universe scheduler (budget 100 tickers/j, rotation 7j) ────
#     ~500 tickers / 100 par jour / min-age 7j → chaque ticker voit ses
#     fundamentals refresh au plus 1 semaine après ses earnings. Le pilier
#     Momentum bouge déjà quotidiennement via step 2 (warm /recommendations),
#     donc aucun besoin de refresh tout chaque jour (waste quota yfinance).
echo "── Step 1/2 : universe_scheduler --budget 100 --min-age-days 7"
"$PYTHON" -m modules.universe_scheduler --budget 100 --min-age-days 7
rc_sched=$?
if [[ $rc_sched -ne 0 ]]; then
    echo "WARN: universe_scheduler exit=$rc_sched (on continue le warm)"
fi

# ── 2. Warm du cache recommendations ─────────────────────────────
#     L'API est un process long-running ; son cache est mtime-keyed sur
#     universe.json → le refresh ci-dessus l'a invalidé. Un hit sur
#     /api/portfolio/recommendations reconstruit le scored universe
#     côté API pour que la prochaine requête frontale soit instantanée.
echo "── Step 2/3 : warm /api/portfolio/recommendations ($API_URL)"
rc_warm=0
if curl --silent --show-error --fail --max-time 180 \
        "$API_URL/api/portfolio/recommendations?total_capital=100000&max_holdings=20" \
        -o /dev/null -w "http=%{http_code} time=%{time_total}s\n"; then
    echo "OK — cache API chaud."
else
    rc_warm=$?
    echo "WARN: warm HTTP failed (rc=$rc_warm) — l'API tourne-t-elle sur $API_URL ?"
fi

# ── 3. Snapshot historique fundamentaux + scores TITAN ───────────
#     Persiste l'état du jour (raw fundamentaux + scores Q/V/R/S/M + composite
#     + macro context) dans data/.universe_history/snapshot_YYYYMMDD.json.gz.
#     Idempotent (1 snapshot/jour, dedup par overwrite).
#     Prérequis pour backtests historiques + WFO calibration des poids.
echo "── Step 3/4 : universe_history snapshot"
"$PYTHON" -m modules.universe_history
rc_hist=$?
if [[ $rc_hist -ne 0 ]]; then
    echo "WARN: universe_history snapshot exit=$rc_hist"
fi

# ── 4. Refresh propositions (auto_proposer + Telegram) ───────────
#     Évalue les gates (killswitch, CB, régime macro, slots libres, cash)
#     et enqueue jusqu'à N propositions à valider manuellement dans l'UI.
#     Skipped silencieusement si API_TOKEN absent (auth fail-closed côté API).
echo "── Step 4/4 : POST /api/proposals/refresh"
rc_props=0
if [[ -z "${API_TOKEN:-}" ]]; then
    # On tente de relire backend/.env pour récupérer le token (config systemd
    # standard SwingQuant : EnvironmentFile=backend/.env). Permet d'éviter de
    # définir le token deux fois dans crontab + service.
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
        # Compte les propositions (best-effort jq, fallback grep).
        n=$(jq '.proposals | length' /tmp/titan_proposals.json 2>/dev/null \
            || grep -o '"id"' /tmp/titan_proposals.json 2>/dev/null | wc -l)
        echo "OK — ${n:-0} proposition(s) enqueuée(s)."
    else
        rc_props=$?
        echo "WARN: refresh proposals HTTP failed (rc=$rc_props)"
    fi
fi

# ── Exit code synthétique ────────────────────────────────────────
if [[ $rc_sched -ne 0 ]]; then
    echo "DONE with errors (scheduler rc=$rc_sched)"
    exit $rc_sched
fi
if [[ $rc_warm -ne 0 ]]; then
    echo "DONE — scheduler OK, warm KO"
    exit 1
fi
if [[ $rc_hist -ne 0 ]]; then
    echo "DONE — scheduler+warm+proposals OK, history KO"
    exit 3
fi
if [[ $rc_props -ne 0 ]]; then
    echo "DONE — scheduler+warm+history OK, proposals KO"
    exit 2
fi
echo "DONE — scheduler + warm + history + proposals OK"
exit 0
