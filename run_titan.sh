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

# ── Dead-man-switch healthchecks.io (watchdog externe) ───────────
#    Détecte le cas que ni le health-check Telegram (9h) ni le digest
#    metrics_agent (step 3c) ne peuvent couvrir : le cron n'a pas tourné
#    du tout (VPS down, cron daemon KO, crontab cassé). healthchecks.io
#    alerte si aucun ping "succès" ou "start" n'arrive dans la fenêtre
#    prévue + marge — indépendant de l'infra locale, gratuit.
#    Optionnel : no-op silencieux tant que HEALTHCHECKS_PING_URL n'est
#    pas défini (crontab ou backend/.env, même pattern que API_TOKEN).
HC_PING_URL="${HEALTHCHECKS_PING_URL:-}"
if [[ -z "$HC_PING_URL" && -f "$BACKEND/.env" ]]; then
    HC_PING_URL=$(grep -E '^HEALTHCHECKS_PING_URL=' "$BACKEND/.env" | head -n1 | cut -d= -f2- | tr -d '"' | tr -d "'")
fi
if [[ -n "$HC_PING_URL" ]]; then
    curl --silent --max-time 10 "$HC_PING_URL/start" -o /dev/null || true
fi

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

# ── 1b. Insider enrichment (SEC EDGAR Form 4 — gratuit illimité) ─
#     Lot 17 — pilier Insider (8% poids). Scrape SEC EDGAR pour compter
#     les filings Form 4 (transactions C-level/director) sur 30/90j et
#     détecter les clusters (3+ insiders distincts en 7j = signal smart-money).
#     Très rapide (~40s pour 491 tickers, 5 workers, throttle 0.2s/req).
#     Idempotent : cache disque 24h dans data/sec_cache/.
#     Timeout 15 min : énorme marge vs run normal 40s, absorbe un
#     ralentissement SEC EDGAR exceptionnel. Si interrompu malgré tout,
#     le pilier Insider restera sur le dernier état persisté hier.
echo "── Step 1b/4 : insider_enrich (SEC EDGAR, timeout 15min)"
timeout 900 "$PYTHON" -m modules.insider_enrich --workers 5
rc_insider=$?
if [[ $rc_insider -eq 124 ]]; then
    echo "WARN: insider_enrich TIMEOUT (>15min) — pilier Insider restera sur état précédent"
elif [[ $rc_insider -ne 0 ]]; then
    echo "WARN: insider_enrich exit=$rc_insider (continue — pilier Insider sera neutre)"
fi

# ── 1c. Finnhub enrichment (Revisions + Earnings — free 60/min) ──
#     Lot 17 — remplace les recommendations yfinance pauvres par les
#     vraies données Finnhub (recommendation trends mensuels, earnings
#     calendar avec EPS estimate, surprise history). Skipped silencieux
#     si FINNHUB_API_KEY absent. Cache 24h, ~30 min pour 491 tickers en
#     1er run cold, ~30s ensuite (cache hit).
#     Timeout 45 min : couvre largement le run cold (30 min observé) avec
#     15 min de buffer si Finnhub ralentit. Si interrompu, le cache 24h
#     des tickers déjà fait est persisté → demain reprend là où ça s'est
#     arrêté. Idempotent.
echo "── Step 1c/4 : finnhub_enrich (Revisions + Earnings, timeout 45min)"
timeout 2700 "$PYTHON" -m modules.finnhub_enrich
rc_finnhub=$?
if [[ $rc_finnhub -eq 124 ]]; then
    echo "WARN: finnhub_enrich TIMEOUT (>45min) — cache partiel persisté, demain reprendra"
elif [[ $rc_finnhub -ne 0 ]]; then
    echo "WARN: finnhub_enrich exit=$rc_finnhub (pas critique — yfinance reste fallback)"
fi

# ── 2. Warm du cache recommendations ─────────────────────────────
#     L'API est un process long-running ; son cache est mtime-keyed sur
#     universe.json → le refresh ci-dessus l'a invalidé. Un hit sur
#     /api/portfolio/recommendations reconstruit le scored universe
#     côté API pour que la prochaine requête frontale soit instantanée.
echo "── Step 2/3 : warm /api/portfolio/recommendations ($API_URL)"
rc_warm=0
# L'endpoint est protégé (Security(api_core.require_auth)) — sans le header
# Authorization ce curl échouait systématiquement en 401 (warm KO quotidien
# silencieux, découvert lors de l'audit data du 2026-08-15). On relit le
# token depuis backend/.env comme au step 4 ci-dessous.
WARM_TOKEN="${API_TOKEN:-}"
if [[ -z "$WARM_TOKEN" && -f "$BACKEND/.env" ]]; then
    WARM_TOKEN=$(grep -E '^API_TOKEN=' "$BACKEND/.env" | head -n1 | cut -d= -f2- | tr -d '"' | tr -d "'")
fi
if [[ -z "$WARM_TOKEN" ]]; then
    echo "SKIP: API_TOKEN absent (set dans crontab ou backend/.env) — warm non authentifié impossible."
else
    if curl --silent --show-error --fail --max-time 180 \
            -H "Authorization: Bearer ${WARM_TOKEN}" \
            "$API_URL/api/portfolio/recommendations?total_capital=100000&max_holdings=20" \
            -o /dev/null -w "http=%{http_code} time=%{time_total}s\n"; then
        echo "OK — cache API chaud."
    else
        rc_warm=$?
        echo "WARN: warm HTTP failed (rc=$rc_warm) — l'API tourne-t-elle sur $API_URL ?"
    fi
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

# ── 3a. Export calibration prix cible (git-tracked, hors VPS) ────
#     data/.universe_history/ est gitignored (donnée régénérable, pas de
#     code) — la routine cloud de calibration du prix cible (voir
#     docs/price_target_design.md) tourne dans un sandbox avec un clone
#     git frais, sans accès au disque du VPS. Ce step exporte un
#     sous-ensemble trié des champs nécessaires vers
#     data/calibration/universe_history_export.jsonl.gz, suivi par git,
#     synchronisé par git_auto_sync.sh comme le reste du repo.
#     Réécriture complète à chaque run (peu coûteux, ~500 tickers × Njours).
echo "── Step 3a/4 : price_target_calibration_export"
"$PYTHON" -m modules.price_target_calibration_export
rc_calib_export=$?
if [[ $rc_calib_export -ne 0 ]]; then
    echo "WARN: price_target_calibration_export exit=$rc_calib_export (non bloquant)"
fi

# ── 3b. WFO monitor mensuel (1er du mois uniquement) ─────────────
#     Audit S1.3 — calibration walk-forward des poids piliers TITAN sur
#     l'historique universe_history. Compare l'IC test (out-of-sample)
#     entre runs et alerte Telegram si dégradation 2x consécutive sous
#     seuil 0.02. Append `data/wfo_history.jsonl` + persiste
#     `data/wfo_weights.json` (consommé par /api/wfo).
#
#     Gate `date +%d == 01` : exécution mensuelle (bruyant en daily,
#     trop lâche en yearly). 60j train + 20j test = signal stable.
rc_wfo=0
if [[ "$(date +%d)" == "01" ]]; then
    # Audit 2026-05-12 — lag 90→30 explicite. Historique snapshots actuel
    # = ~21j, lag 90 forçait l'auto-reduce (warning log). 30 est cohérent
    # avec un cycle trimestriel earnings + reporting yfinance T+5 à T+30.
    # À remonter vers 60-90 une fois 180+ jours d'historique disponibles.
    echo "── Step 3b/4 : wfo_monitor (mensuel, lag=30)"
    "$PYTHON" -m modules.wfo_monitor --train-days 30 --test-days 10 --publication-lag-days 30
    rc_wfo=$?
    if [[ $rc_wfo -ne 0 ]]; then
        echo "WARN: wfo_monitor exit=$rc_wfo (historique probablement insuffisant — non bloquant)"
    fi
else
    echo "── Step 3b/4 : wfo_monitor SKIP (jour $(date +%d), exécuté seulement le 01)"
fi

# ── 3c. Agent d'analyse des chiffres (local, pas cloud) ──────────
#     Étape 0bis roadmap — purement lecture/analyse : lit /api/data_health
#     + wfo_history.jsonl + universe_history + journal de trades, calcule
#     une tendance dans le temps et pousse un digest Telegram (canal
#     alerter existant). Persiste data/metrics_history.jsonl (mémoire
#     durable — Telegram est éphémère). Ne modifie jamais le code ni
#     l'état trading ; si un jour WFO produit un IC significatif, écrit au
#     plus une proposition markdown pour relecture humaine (jamais de
#     poids de scoring appliqués automatiquement).
echo "── Step 3c/4 : metrics_agent (digest tendance data quality + WFO)"
"$PYTHON" -m modules.metrics_agent
rc_metrics=$?
if [[ $rc_metrics -ne 0 ]]; then
    echo "WARN: metrics_agent exit=$rc_metrics (non bloquant)"
fi

# ── 3d. Prix cible fondamental 12 mois (docs/price_target_design.md §5) ──
#     Calcule price_target/price_target_low/price_target_high/upside_pct
#     pour tout l'univers scoré (poids calibrés dans
#     data/.price_target_calibration/state.json) et persiste
#     data/.price_target_history/{price_targets_YYYYMMDD.json.gz,latest.json}.
#     DOIT tourner avant Step 4 : auto_proposer lit latest.json pour peupler
#     alloc.get("price_target") sur chaque proposition.
echo "── Step 3d/4 : price_target_snapshot (fair value fondamentale 12 mois)"
"$PYTHON" -m modules.price_target_snapshot
rc_price_target=$?
if [[ $rc_price_target -ne 0 ]]; then
    echo "WARN: price_target_snapshot exit=$rc_price_target (non bloquant)"
fi

# ── 4. Refresh propositions (auto_proposer + Telegram) ───────────
#     Évalue les gates (killswitch, CB, régime macro, slots libres, cash)
#     et enqueue jusqu'à N propositions à valider manuellement dans l'UI.
#     Skipped silencieusement si API_TOKEN absent (auth fail-closed côté API).
echo "── Step 3e/4 : warm /api/performance/benchmark (panier TITAN théorique, cache 12h)"
WARM_TOKEN="${API_TOKEN:-}"
if [[ -z "$WARM_TOKEN" && -f "$BACKEND/.env" ]]; then
    WARM_TOKEN=$(grep -E '^API_TOKEN=' "$BACKEND/.env" | head -n1 | cut -d= -f2- | tr -d '"' | tr -d "'")
fi
if [[ -n "$WARM_TOKEN" ]]; then
    curl --silent --show-error --fail --max-time 180 \
        -H "Authorization: Bearer ${WARM_TOKEN}" \
        "$API_URL/api/performance/benchmark?months=6&top_n=20" \
        -o /dev/null -w "benchmark http=%{http_code} time=%{time_total}s\n" || echo "WARN: warm benchmark failed (non bloquant)"
fi

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

# ── 5. Bear hedge (audit 2026-05-12) ─────────────────────────────
#     Si confirmed_regime ∈ {BEAR_MARKET, CRASH_PANIC} pendant ≥
#     BEAR_HEDGE_MIN_DAYS jours, ouvre un LONG SH (10% du book). Si
#     BULL_MARKET revient ≥ 3j, ferme le hedge.
#
#     No-op silencieux en BULL_MARKET stable : la décision retourne
#     HOLD_NO_HEDGE et execute_decision ne fait rien. Gating via
#     config.BEAR_HEDGE_ENABLED (True depuis 2026-05-12).
echo "── Step 5/5 : bear_hedge (régime macro défensif)"
"$PYTHON" -m modules.bear_hedge
rc_hedge=$?
if [[ $rc_hedge -ne 0 ]]; then
    # Le module retourne exit 1 si OPEN/CLOSE a échoué (broker down, etc.).
    # HOLD_* / SKIP retournent exit 0.
    echo "WARN: bear_hedge exit=$rc_hedge (vérifier macro_state.json + broker)"
fi

# ── Exit code synthétique ────────────────────────────────────────
final_rc=0
if [[ $rc_sched -ne 0 ]]; then
    echo "DONE with errors (scheduler rc=$rc_sched)"
    final_rc=$rc_sched
elif [[ $rc_warm -ne 0 ]]; then
    echo "DONE — scheduler OK, warm KO"
    final_rc=1
elif [[ $rc_hist -ne 0 ]]; then
    echo "DONE — scheduler+warm+proposals OK, history KO"
    final_rc=3
elif [[ $rc_props -ne 0 ]]; then
    echo "DONE — scheduler+warm+history OK, proposals KO"
    final_rc=2
else
    echo "DONE — scheduler + warm + history + proposals OK"
fi

if [[ -n "$HC_PING_URL" ]]; then
    if [[ $final_rc -eq 0 ]]; then
        curl --silent --max-time 10 "$HC_PING_URL" -o /dev/null || true
    else
        curl --silent --max-time 10 --data-raw "run_titan.sh exit=$final_rc (voir logs/titan_daily.log)" "$HC_PING_URL/fail" -o /dev/null || true
    fi
fi

exit $final_rc
