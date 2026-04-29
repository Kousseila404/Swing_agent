#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# SwingQuant TITAN — Backup atomique du trade journal
#
# Copie horodatée de trade_journal.duckdb (+ .csv mirror) vers
# $ROOT/backups/, puis purge des sauvegardes > RETENTION_DAYS.
#
# Usage manuel :
#     ./scripts/backup_journal.sh
#
# Cron (post-cycle quotidien, après run_titan.sh) :
#     30 6 * * * /home/swing/swingquant/scripts/backup_journal.sh
#
# Exit codes :
#     0  succès
#     10 source absente (journal jamais créé — non fatal en init)
#     20 cp/sync échec
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="${TITAN_ROOT:-/home/swing/swingquant}"
SRC_DIR="$ROOT/backend/data"
DST_DIR="$ROOT/backups"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DST_DIR"

backup_one() {
    local name="$1"
    local src="$SRC_DIR/$name"
    if [[ ! -f "$src" ]]; then
        echo "[backup] skip — source absente : $src"
        return 0
    fi
    # DuckDB tolère mal une copie pendant qu'un writer tient le fichier ;
    # cp -p est suffisant ici car le tracker fait des writes courtes (pas
    # de transaction ouverte plusieurs secondes), et on tourne ce script
    # post run_titan.sh quand l'API n'écrit pas. Si jamais on observe de
    # la corruption, basculer sur `duckdb -c "EXPORT DATABASE ..."`.
    local dst="$DST_DIR/${name}.${STAMP}"
    cp -p "$src" "$dst"
    # Hash pour détection de bit-rot ultérieure
    sha256sum "$dst" > "$dst.sha256"
    echo "[backup] ok   $dst"
}

backup_one "trade_journal.duckdb"
backup_one "trade_journal.csv"
backup_one "equity_state.json"

# ── Rétention : supprime les .duckdb/.csv/.json + .sha256 plus vieux que N jours
find "$DST_DIR" -maxdepth 1 -type f \
    \( -name 'trade_journal.duckdb.*' \
       -o -name 'trade_journal.csv.*' \
       -o -name 'equity_state.json.*' \) \
    -mtime "+${RETENTION_DAYS}" -print -delete

echo "[backup] done — retention ${RETENTION_DAYS}d"
