"""Sentinel — surveillance des RÉSULTATS du système (audit 2026-11-18).

Pourquoi : `/api/system/health` ne regarde que l'âge des logs. Le proposer
est resté mort du 2026-09-18 au 2026-11-18 (KeyError) alors que tous les
crons "tournaient" : aucune alerte pendant 2 mois. Le sentinel vérifie des
invariants de résultat :

  proposals_flow     une proposition a-t-elle été créée récemment ? le
                     dernier refresh est-il ok ?
  pipeline_daily     run_titan.sh s'est-il terminé (DONE) il y a < 30 h, sans KO ?
  api_alive          l'API répond-elle ?
  oom_kills          l'OOM killer a-t-il tué l'API dans les dernières 24 h ?
  tracker_heartbeat  heartbeat < 10 min pendant la séance
  deployment         taux d'investissement du panier (positions / BASKET_TOP_N)
  equity_sanity      equity == ACCOUNT_SIZE pile = fallback silencieux broker
  universe_fresh     universe.json < 36 h
  log_errors         Traceback dans les logs des dernières 24 h
  host_resources     disque / RAM disponible

Sorties : data/sentinel/latest.json + history.jsonl. Alerte Telegram quand
l'ensemble des problèmes change (empreinte) ou au plus 1×/24 h s'il persiste.
Le triage IA (agent Claude headless) est déclenché par scripts/sentinel_triage.sh.

CLI : python -m modules.sentinel [--no-telegram] [--json]
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
DATA = BACKEND / "data"
LOGS = BACKEND / "logs"
OUT_DIR = DATA / "sentinel"
LATEST = OUT_DIR / "latest.json"
HISTORY = OUT_DIR / "history.jsonl"
ALERT_STATE = OUT_DIR / "alert_state.json"

API_URL = os.getenv("TITAN_API_URL", "http://localhost:8000")
REALERT_SECONDS = 24 * 3600

OK, WARN, CRIT = "ok", "warn", "crit"
_SEVERITY_RANK = {OK: 0, WARN: 1, CRIT: 2}


@dataclass
class Check:
    name: str
    status: str
    detail: str
    value: Any = None
    hint: str = ""


@dataclass
class Report:
    ts: str
    status: str
    checks: list[Check] = field(default_factory=list)

    @property
    def problems(self) -> list[Check]:
        return [c for c in self.checks if c.status != OK]

    def fingerprint(self) -> str:
        key = "|".join(sorted(f"{c.name}:{c.status}" for c in self.problems))
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "status": self.status, "fingerprint": self.fingerprint(),
                "checks": [asdict(c) for c in self.checks]}


# ─── helpers ──────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(UTC)


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _business_days_between(start: datetime, end: datetime) -> int:
    days, d = 0, start.date()
    while d < end.date():
        d += timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def _tail(path: Path, max_bytes: int = 400_000) -> str:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _market_open() -> bool | None:
    try:
        from modules.tracker.market import is_market_hours
        return bool(is_market_hours())
    except Exception:
        return None


# ─── checks ───────────────────────────────────────────────────────────

def check_proposals_flow(now: datetime) -> Check:
    props = _read_json(DATA / "proposals.json", []) or []
    created: list[datetime] = [
        c for c in (_parse_ts(p.get("created_at")) for p in props if isinstance(p, dict)) if c
    ]
    last = max(created) if created else None
    refresh = _read_json(DATA / "proposals_last_refresh.json", {}) or {}
    refresh_ok = refresh.get("ok")
    failing_gates = [g.get("name") for g in refresh.get("gates", []) if not g.get("ok")]
    err = (refresh.get("diagnostics") or {}).get("error") if isinstance(refresh.get("diagnostics"), dict) else None

    bdays = _business_days_between(last, now) if last else None
    value = {"last_created": last.isoformat() if last else None, "business_days_since": bdays,
             "last_refresh_ok": refresh_ok, "failing_gates": failing_gates, "error": err}
    hint = ("Lancer `python -c 'from modules import auto_proposer as ap; "
            "print(ap.plan_proposals())'` pour voir l'exception.")
    if bdays is None or bdays > 5:
        return Check("proposals_flow", CRIT,
                     f"aucune proposition créée depuis {bdays if bdays is not None else '∞'} jours ouvrés", value, hint)
    if refresh_ok is False and not failing_gates:
        return Check("proposals_flow", CRIT, f"dernier refresh en échec sans gate fermé ({err or 'exception'})", value, hint)
    if bdays > 2:
        return Check("proposals_flow", WARN, f"dernière proposition il y a {bdays} jours ouvrés", value, hint)
    return Check("proposals_flow", OK, f"dernière proposition il y a {bdays} j ouvrés", value)


def check_pipeline_daily(now: datetime) -> Check:
    path = REPO / "logs" / "titan_daily.log"
    lines = [ln for ln in _tail(path, 200_000).splitlines() if "DONE —" in ln and ln.startswith("[")]
    if not lines:
        return Check("pipeline_daily", CRIT, "aucune ligne DONE dans titan_daily.log")
    last = lines[-1]
    ts = _parse_ts(last[1:20].replace(" ", "T"))
    age_h = round((now - ts).total_seconds() / 3600, 1) if ts else None
    ko = [ln[1:11] for ln in lines[-7:] if " KO" in ln]
    value = {"last_done": last, "age_h": age_h, "ko_days_last7": ko}
    if age_h is None or age_h > 30:
        return Check("pipeline_daily", CRIT, f"pipeline non terminé depuis {age_h} h", value)
    if " KO" in last:
        return Check("pipeline_daily", WARN, f"dernier run partiel : {last[22:]}", value,
                     "Voir logs/titan_daily.log (step 4) et journalctl -u swing-api (OOM ?).")
    if len(ko) >= 3:
        return Check("pipeline_daily", WARN, f"{len(ko)} runs KO sur les 7 derniers", value)
    return Check("pipeline_daily", OK, f"dernier run OK il y a {age_h} h", value)


def check_api_alive() -> Check:
    import urllib.request
    t0 = time.time()
    try:
        with urllib.request.urlopen(f"{API_URL}/api/market_status", timeout=10) as resp:
            code = resp.status
    except Exception as exc:
        return Check("api_alive", CRIT, f"API injoignable : {exc}", None,
                     "systemctl status swing-api ; journalctl -u swing-api -n 50")
    ms = round((time.time() - t0) * 1000)
    status = OK if code == 200 and ms < 5000 else WARN
    return Check("api_alive", status, f"HTTP {code} en {ms} ms", {"http": code, "ms": ms})


def check_oom_kills() -> Check:
    cmd = ["journalctl", "-u", "swing-api", "--since", "-24h", "--no-pager", "-q"]
    out = ""
    for prefix in (["sudo", "-n"], []):
        try:
            res = subprocess.run(prefix + cmd, capture_output=True, text=True, timeout=20)
            if res.returncode == 0 and res.stdout:
                out = res.stdout
                break
        except Exception:
            continue
    n = out.count("oom-kill")
    if n:
        return Check("oom_kills", WARN, f"API tuée {n}× par l'OOM killer (24 h)", n,
                     "VM 4 Go partagée (openclaw/n8n/prospection) : réduire la mémoire ou ajouter MemoryMax/swap.")
    return Check("oom_kills", OK, "aucun OOM kill (24 h)", 0)


def check_tracker_heartbeat(now: datetime, market_open: bool | None) -> Check:
    hb = _read_json(DATA / "tracker_heartbeat.json", {}) or {}
    epoch = hb.get("epoch")
    age_min = round((now.timestamp() - float(epoch)) / 60, 1) if epoch else None
    value = {"age_min": age_min, "status": hb.get("status"), "market_open": market_open}
    if market_open and (age_min is None or age_min > 10):
        return Check("tracker_heartbeat", CRIT, f"tracker silencieux depuis {age_min} min en séance", value,
                     "tail -50 backend/logs/tracker.log")
    if hb.get("status") == "nuclear_stop":
        return Check("tracker_heartbeat", CRIT, "nuclear stop actif", value)
    return Check("tracker_heartbeat", OK, f"heartbeat {age_min} min ({hb.get('status')})", value)


def check_deployment() -> Check:
    try:
        import pandas as pd

        import config
        df = pd.read_csv(DATA / "trade_journal.csv")
        n_open = int((df["Status"] == "OPEN").sum())
        target = int(getattr(config, "BASKET_TOP_N", 20))
    except Exception as exc:
        return Check("deployment", WARN, f"lecture journal impossible : {exc}")
    eq = _read_json(DATA / "equity_state.json", {}) or {}
    invested = sum(abs(float(p.get("size") or 0) * float(p.get("current_price") or p.get("entry") or 0))
                   for p in eq.get("open_positions", []) if isinstance(p, dict))
    equity = float(eq.get("current_equity") or 0)
    pct = round(100 * invested / equity, 1) if equity > 0 and invested > 0 else None
    value = {"open_positions": n_open, "target": target, "invested_pct": pct}
    ratio = n_open / target if target else 1
    if ratio < 0.5:
        return Check("deployment", WARN,
                     f"{n_open}/{target} positions (investi ≈ {pct if pct is not None else '?'} %)", value,
                     "Le panier n'est pas déployé : vérifier proposals_flow et auto_approve.log.")
    return Check("deployment", OK, f"{n_open}/{target} positions", value)


def check_equity_sanity() -> Check:
    import config
    eq = _read_json(DATA / "equity_state.json", {}) or {}
    cur = eq.get("current_equity")
    if cur is not None and float(cur) == float(config.ACCOUNT_SIZE):
        return Check("equity_sanity", WARN, f"equity == ACCOUNT_SIZE ({cur}) : probable fallback broker", cur,
                     "broker_gateway.get_account_equity renvoie ACCOUNT_SIZE sur erreur → killswitch aveugle.")
    return Check("equity_sanity", OK, f"equity {cur}", cur)


def check_universe_fresh(now: datetime) -> Check:
    path = DATA / "universe.json"
    try:
        age_h = round((now.timestamp() - path.stat().st_mtime) / 3600, 1)
    except OSError:
        return Check("universe_fresh", CRIT, "universe.json absent")
    status = OK if age_h < 36 else CRIT
    return Check("universe_fresh", status, f"universe.json âgé de {age_h} h", age_h)


def check_log_errors(now: datetime) -> Check:
    cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
    counts: dict[str, int] = {}
    for path in sorted(LOGS.glob("*.log")) + [REPO / "logs" / "titan_daily.log"]:
        text = _tail(path, 300_000)
        n = 0
        last_ts = ""
        for ln in text.splitlines():
            head = ln.lstrip("[")[:16]
            if len(head) == 16 and head[4] == "-" and head[10] == " ":
                last_ts = head
            if "Traceback (most recent call last)" in ln and last_ts >= cutoff:
                n += 1
        if n:
            counts[path.name] = n
    if not counts:
        return Check("log_errors", OK, "aucun traceback (24 h)", {})
    total = sum(counts.values())
    return Check("log_errors", WARN, f"{total} traceback(s) sur 24 h", counts,
                 "grep -n Traceback -A15 <log> pour le détail.")


def check_host_resources() -> Check:
    disk = shutil.disk_usage("/")
    disk_pct = round(100 * disk.used / disk.total, 1)
    avail_mb = None
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                avail_mb = int(ln.split()[1]) // 1024
    except OSError:
        pass
    value = {"disk_pct": disk_pct, "mem_available_mb": avail_mb}
    if disk_pct > 90:
        return Check("host_resources", CRIT, f"disque {disk_pct} %", value, "voir project_disk_cleanup")
    if disk_pct > 85 or (avail_mb is not None and avail_mb < 300):
        return Check("host_resources", WARN, f"disque {disk_pct} %, RAM dispo {avail_mb} Mo", value)
    return Check("host_resources", OK, f"disque {disk_pct} %, RAM dispo {avail_mb} Mo", value)


# ─── orchestration ────────────────────────────────────────────────────

def run_checks() -> Report:
    now = _now()
    market_open = _market_open()
    checks: list[Check] = []
    g = globals()
    for name, args in (
        ("proposals_flow", (now,)), ("pipeline_daily", (now,)), ("api_alive", ()),
        ("oom_kills", ()), ("tracker_heartbeat", (now, market_open)), ("deployment", ()),
        ("equity_sanity", ()), ("universe_fresh", (now,)), ("log_errors", (now,)),
        ("host_resources", ()),
    ):
        try:
            checks.append(g[f"check_{name}"](*args))
        except Exception as exc:  # un check cassé ne doit jamais masquer les autres
            checks.append(Check(name, WARN, f"check en erreur : {exc}"))
    worst = max((c.status for c in checks), key=_SEVERITY_RANK.__getitem__, default=OK)
    return Report(ts=now.isoformat(timespec="seconds"), status=worst, checks=checks)


def persist(report: Report) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LATEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str))
    tmp.replace(LATEST)
    with HISTORY.open("a") as fh:
        fh.write(json.dumps({"ts": report.ts, "status": report.status, "fingerprint": report.fingerprint(),
                             "problems": [f"{c.name}:{c.status}" for c in report.problems]}) + "\n")


def should_alert(report: Report, state: dict[str, Any], now_epoch: float) -> bool:
    """Alerte si l'ensemble des problèmes a changé, ou s'il persiste depuis 24 h."""
    fp = report.fingerprint()
    if fp != state.get("fingerprint"):
        return bool(report.problems) or state.get("status") not in (None, OK)  # nouveau problème ou retour au vert
    return bool(report.problems) and now_epoch - float(state.get("alerted_at") or 0) > REALERT_SECONDS


def format_telegram(report: Report) -> str:
    icon = {OK: "✅", WARN: "⚠️", CRIT: "🚨"}
    if not report.problems:
        return "✅ <b>Sentinel</b> — tous les invariants sont revenus au vert."
    lines = [f"{icon[report.status]} <b>Sentinel SwingQuant</b> — {len(report.problems)} problème(s)"]
    for c in sorted(report.problems, key=lambda c: -_SEVERITY_RANK[c.status]):
        lines.append(f"{icon[c.status]} <b>{c.name}</b> : {c.detail}")
    return "\n".join(lines)


def notify(report: Report, *, send: bool = True) -> bool:
    state = _read_json(ALERT_STATE, {}) or {}
    now_epoch = time.time()
    if not should_alert(report, state, now_epoch):
        return False
    if not send:  # dry-run : ne consomme pas l'alerte
        return True
    try:
        from modules.alerter import _send_telegram_message
        _send_telegram_message(format_telegram(report))
    except Exception as exc:
        print(f"[sentinel] Telegram KO : {exc}", file=sys.stderr)
        return False
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ALERT_STATE.write_text(json.dumps({"fingerprint": report.fingerprint(), "status": report.status,
                                       "alerted_at": now_epoch}))
    return True


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    report = run_checks()
    persist(report)
    alerted = notify(report, send="--no-telegram" not in argv)
    if "--json" in argv:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str))
    else:
        for c in report.checks:
            print(f"[{c.status.upper():4}] {c.name:18} {c.detail}")
        print(f"→ {report.status.upper()} fingerprint={report.fingerprint()} alerted={alerted}")
    return {OK: 0, WARN: 1, CRIT: 2}[report.status]


if __name__ == "__main__":
    sys.path.insert(0, str(BACKEND))
    raise SystemExit(main())
