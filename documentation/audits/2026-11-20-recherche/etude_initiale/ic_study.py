"""Étude IC hors prod : quels signaux prédisent le rendement futur (snapshots live)."""
import sys, math
from datetime import date, timedelta
import numpy as np, pandas as pd
sys.path.insert(0, "/home/swing/swingquant/backend")
from modules import universe_history as uh, price_panel as pp

ser = pp._series()
closes = pd.DataFrame({t: pd.Series(v, index=pd.to_datetime(d)) for t, (d, v) in ser.items()}).sort_index()
days = closes.index

FIELDS = ["titan_composite_score","quality_score","value_score","risk_score","momentum_score","piotroski_score",
          "growth_score","revisions_score","insider_score",
          # bruts
          "momentum_return_pct","momentum_risk_adjusted","momentum_high_52w_ratio","momentum_volatility_pct",
          "forward_pe","ev_to_ebitda","price_to_book","peg_ratio","profit_margin","operating_margin","gross_margin",
          "earnings_growth","revenue_growth","return_on_equity","beta","market_cap","dividend_yield",
          "earnings_surprise_avg_4q","num_analysts","f_score"]
INVERT = {"forward_pe","ev_to_ebitda","price_to_book","peg_ratio","momentum_volatility_pct","beta"}  # plus bas = mieux

live = uh.list_live_snapshots()
# échantillonnage hebdo
picked, last = [], None
for d in live:
    if last is None or (d - last).days >= 7:
        picked.append(d); last = d

rows = []
for d in picked:
    snap = uh.read_snapshot(d) or {}
    tk = snap.get("tickers") or {}
    i0 = days.searchsorted(pd.Timestamp(d), side="right")  # 1ʳᵉ clôture APRÈS le snapshot (pas de look-ahead)
    for h in (5, 21):
        if i0 + h >= len(days): continue
        p0, p1 = closes.iloc[i0], closes.iloc[i0 + h]
        fwd = (p1 / p0 - 1)
        df = pd.DataFrame.from_dict(tk, orient="index")
        df = df[[c for c in FIELDS + ["sector"] if c in df.columns]].copy()
        df["fwd"] = fwd.reindex(df.index)
        df = df[df["fwd"].notna()]
        if len(df) < 100: continue
        df["fwd_sn"] = df["fwd"] - df.groupby("sector")["fwd"].transform("mean")
        for f in FIELDS:
            if f not in df.columns: continue
            x = pd.to_numeric(df[f], errors="coerce")
            if f in ("forward_pe","ev_to_ebitda","peg_ratio"): x = x.where(x > 0)
            if f in INVERT: x = -x
            m = x.notna()
            if m.sum() < 80: continue
            ic = x[m].rank().corr(df.loc[m, "fwd"].rank())
            xs = x[m] - x[m].groupby(df.loc[m, "sector"]).transform("mean")
            ic_sn = x[m].groupby(df.loc[m,"sector"]).rank(pct=True).corr(df.loc[m, "fwd_sn"].rank())
            rows.append({"date": d, "h": h, "field": f, "ic": ic, "ic_sn": ic_sn, "n": int(m.sum())})

res = pd.DataFrame(rows)
def nw_t(s, lag):
    s = s.dropna().values; n = len(s)
    if n < 4: return float("nan")
    m = s.mean(); e = s - m
    v = (e @ e) / n
    for L in range(1, lag + 1):
        w = 1 - L / (lag + 1); v += 2 * w * (e[L:] @ e[:-L]) / n
    return m / math.sqrt(v / n) if v > 0 else float("nan")

out = []
for (f, h), g in res.groupby(["field", "h"]):
    lag = 0 if h == 5 else 3
    out.append({"field": f, "h": h, "n_dates": len(g), "IC": g.ic.mean(), "t": nw_t(g.ic, lag),
                "IC_secneutre": g.ic_sn.mean(), "t_sn": nw_t(g.ic_sn, lag),
                "IC_1er_moitie": g.ic.iloc[:len(g)//2].mean(), "IC_2e_moitie": g.ic.iloc[len(g)//2:].mean()})
o = pd.DataFrame(out).sort_values(["h", "IC_secneutre"], ascending=[True, False])
pd.set_option("display.width", 200); pd.set_option("display.max_rows", 200)
print(f"dates hebdo: {len(picked)} ({picked[0]} → {picked[-1]})")
print(o.round(3).to_string(index=False))
o.to_csv(sys.argv[1] if len(sys.argv) > 1 else "/dev/null", index=False)
