"""Analyse empirique du score TITAN à l'entrée vs au moment de la sortie.

Objectif : décider si un "stop fondamental" (sortie sur dégradation TITAN) est
data-justifiable ou serait juste un seuil arbitraire de plus.

Trois analyses :
  Q1 — Trades clos LOSS : quel TITAN au stop loss ?
       → Médiane élevée = SL prix coupe trop tôt (thèse encore intacte).
       → Médiane basse  = SL prix sort au bon moment, stop thèse redondant.

  Q2 — Trades clos WIN  : trajectoire TITAN pendant le hold.
       → Score volatil franchissant 60 = stop thèse aurait coupé prématurément.
       → Score stable haut = seuil thèse à 60 sans risque.

  Q3 — Positions OPEN   : drift TITAN entrée → aujourd'hui (early signal).
       → Utile dès maintenant sans attendre des sorties.

Usage:
    python -m scripts.analyze_thesis_drift
"""
from __future__ import annotations

import statistics
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from modules.universe_history import iter_snapshots, ticker_history

JOURNAL_PATH = Path(__file__).parent.parent / "data" / "trade_journal.csv"
TITAN_FIELD = "titan_composite_score"
F_SCORE_FIELD = "f_score"


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (ValueError, TypeError):
        return None


def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _titan_at_date(ticker: str, target_date: date) -> tuple[float | None, float | None]:
    """Retourne (titan, f_score) pour ce ticker à cette date (ou plus proche disponible)."""
    hist = ticker_history(ticker, fields=[TITAN_FIELD, F_SCORE_FIELD])
    if not hist:
        return None, None
    # Trouve le snapshot le plus proche (≤ target_date), sinon le plus ancien dispo.
    best = None
    for row in hist:
        d = _parse_date(row.get("date", ""))
        if d is None:
            continue
        if d <= target_date:
            if best is None or d > _parse_date(best["date"]):
                best = row
    if best is None and hist:
        best = hist[0]  # fallback : prendre le plus ancien (au moins quelque chose)
    if best is None:
        return None, None
    return _safe_float(best.get(TITAN_FIELD)), _safe_float(best.get(F_SCORE_FIELD))


def _summary(label: str, values: list[float]) -> None:
    if not values:
        print(f"  {label}: aucune donnée")
        return
    p = sorted(values)
    n = len(p)
    print(f"  {label}: n={n}  min={p[0]:.1f}  P25={p[n//4]:.1f}  "
          f"médian={statistics.median(p):.1f}  P75={p[3*n//4]:.1f}  max={p[-1]:.1f}")


def main() -> int:
    if not JOURNAL_PATH.exists():
        print(f"❌ Journal introuvable : {JOURNAL_PATH}")
        return 1

    df = pd.read_csv(JOURNAL_PATH, dtype={"Ticker": str})

    # Diagnostic des snapshots dispo
    snapshots = list(iter_snapshots())
    if not snapshots:
        print("❌ Aucun snapshot universe_history — Q1/Q2 impossibles.")
        return 1
    first_snap_date = snapshots[0][0]
    last_snap_date = snapshots[-1][0]
    print(f"📊 Snapshots : {len(snapshots)} disponibles "
          f"({first_snap_date} → {last_snap_date})\n")

    # ── Q1 — Trades clos LOSS : TITAN au moment du stop ──────────────
    print("═" * 72)
    print("Q1 — Trades clos LOSS : TITAN au moment du stop")
    print("═" * 72)
    losses = df[df["Status"] == "LOSS"]
    if losses.empty:
        print("  ⚠️  0 trade LOSS dans le journal — impossible de répondre.")
        print("      → Reviens quand tu auras 5+ pertes pour calibrer.\n")
    else:
        titan_drops, titan_at_stop = [], []
        for _, row in losses.iterrows():
            ticker = str(row["Ticker"]).upper()
            entry_titan = _safe_float(row.get("Titan_Score_Entry"))
            exit_date = _parse_date(row.get("Exit_Date", ""))
            if entry_titan is None:
                print(f"  - {ticker}: pas de Titan_Score_Entry (trade pré-capture)")
                continue
            if exit_date is None:
                continue
            stop_titan, _ = _titan_at_date(ticker, exit_date)
            if stop_titan is None:
                print(f"  - {ticker}: pas de snapshot TITAN à {exit_date}")
                continue
            drop = entry_titan - stop_titan
            titan_drops.append(drop)
            titan_at_stop.append(stop_titan)
            print(f"  - {ticker}: entry={entry_titan:.1f} → stop={stop_titan:.1f} "
                  f"(drop={drop:+.1f})")
        print()
        _summary("TITAN au stop", titan_at_stop)
        _summary("Drop TITAN entry→stop", titan_drops)
        print()
        if titan_at_stop:
            med_at_stop = statistics.median(titan_at_stop)
            if med_at_stop > 75:
                print("  ⚠️  Médiane TITAN au stop > 75 → thèse intacte au stop prix")
                print("      Conclusion : SL prix coupe TROP TÔT. Stop thèse améliorerait.")
            elif med_at_stop > 60:
                print("  ⚠️  Médiane TITAN au stop ∈ [60,75] → thèse partiellement dégradée.")
                print("      Conclusion : zone grise — stop thèse seul insuffisant.")
            else:
                print("  ✅ Médiane TITAN au stop ≤ 60 → thèse était déjà cassée.")
                print("      Conclusion : SL prix sort au bon moment. Stop thèse REDONDANT.")
        print()

    # ── Q2 — Trades clos WIN : trajectoire TITAN pendant le hold ─────
    print("═" * 72)
    print("Q2 — Trades clos WIN : trajectoire TITAN pendant le hold")
    print("═" * 72)
    wins = df[df["Status"] == "WIN"]
    if wins.empty:
        print("  ⚠️  0 trade WIN dans le journal — impossible de répondre.\n")
    else:
        false_alarms_60 = 0  # trades qui auraient été fermés par seuil 60
        false_alarms_70 = 0
        for _, row in wins.iterrows():
            ticker = str(row["Ticker"]).upper()
            entry_titan = _safe_float(row.get("Titan_Score_Entry"))
            entry_d = _parse_date(row.get("Date", ""))
            exit_d = _parse_date(row.get("Exit_Date", ""))
            if entry_titan is None or entry_d is None or exit_d is None:
                continue
            hist = ticker_history(ticker, fields=[TITAN_FIELD],
                                   start=entry_d, end=exit_d)
            scores = [_safe_float(h.get(TITAN_FIELD)) for h in hist]
            scores = [s for s in scores if s is not None]
            if not scores:
                continue
            min_score = min(scores)
            crossed_60 = min_score < 60
            crossed_70 = min_score < 70
            if crossed_60:
                false_alarms_60 += 1
            if crossed_70:
                false_alarms_70 += 1
            warn = " ⚠️" if crossed_60 else ""
            print(f"  - {ticker} WIN: entry={entry_titan:.1f}, "
                  f"min pendant hold={min_score:.1f}{warn}")
        print()
        n_wins = len(wins)
        if n_wins:
            print(f"  Sur {n_wins} winners :")
            print(f"    - {false_alarms_60} ({100*false_alarms_60/n_wins:.0f}%) "
                  f"auraient été fermés par stop-thèse à 60")
            print(f"    - {false_alarms_70} ({100*false_alarms_70/n_wins:.0f}%) "
                  f"par stop-thèse à 70")
            if false_alarms_60 / n_wins > 0.20:
                print("  ⚠️  Trop de faux signaux au seuil 60 → le seuil mangerait l'alpha.")
            elif false_alarms_60 / n_wins > 0.10:
                print("  Tolérable au seuil 60, mais surveiller.")
            else:
                print("  ✅ Seuil 60 sûr — préserve les vrais winners.")
        print()

    # ── Q3 — Positions OPEN : drift TITAN entrée → aujourd'hui ───────
    print("═" * 72)
    print("Q3 — Positions OPEN : drift TITAN entrée → maintenant (early signal)")
    print("═" * 72)
    opens = df[df["Status"] == "OPEN"]
    if opens.empty:
        print("  Aucune position ouverte.\n")
    else:
        drifts = []
        for _, row in opens.iterrows():
            ticker = str(row["Ticker"]).upper()
            entry_titan = _safe_float(row.get("Titan_Score_Entry"))
            entry_f = _safe_float(row.get("F_Score_Entry"))
            if entry_titan is None:
                print(f"  - {ticker}: pas de Titan_Score_Entry (entrée pré-capture)")
                continue
            current_titan, current_f = _titan_at_date(ticker, last_snap_date)
            if current_titan is None:
                print(f"  - {ticker}: pas de snapshot récent")
                continue
            drift = current_titan - entry_titan
            f_drift = (current_f - entry_f) if (entry_f is not None and current_f is not None) else None
            drifts.append(drift)
            f_str = f"  F={entry_f:.0f}→{current_f:.0f}" if f_drift is not None else ""
            warn = ""
            if current_titan < 60:
                warn = "  ⚠️ < 60 (thèse à risque)"
            elif drift < -10:
                warn = "  ⚠️ drift > 10pts"
            print(f"  - {ticker}: entry={entry_titan:.1f} → now={current_titan:.1f} "
                  f"(drift={drift:+.1f}){f_str}{warn}")
        print()
        if drifts:
            _summary("Drift TITAN entrée→now", drifts)
            n_at_risk = sum(1 for d in drifts if d < -10)
            n_below_60 = sum(1 for _ in opens.iterrows()
                             if (s := _titan_at_date(
                                 str(_[1]["Ticker"]).upper(), last_snap_date)[0]) is not None
                             and s < 60)
            print(f"\n  Positions à risque (drift < -10) : {n_at_risk}/{len(drifts)}")
            print(f"  Positions sous seuil 60          : {n_below_60}/{len(drifts)}")
        print()

    # ── Synthèse ──
    print("═" * 72)
    print("SYNTHÈSE")
    print("═" * 72)
    n_closed = len(df[df["Status"].isin(["WIN", "LOSS"])])
    n_with_entry = len(df[df["Titan_Score_Entry"].notna()])
    print(f"  Trades clos avec entry score      : {n_closed}")
    print(f"  Trades total avec entry score     : {n_with_entry}")
    if n_closed < 10:
        print("\n  ⚠️  Échantillon trop faible (<10 trades clos) pour calibrer un seuil.")
        print("      Recommandation : réexécuter ce script dans 2-3 mois.")
        print("      D'ici là, garder Étape 2 (stop thèse) en attente.")
    else:
        print("\n  Échantillon suffisant pour un premier round de calibration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
