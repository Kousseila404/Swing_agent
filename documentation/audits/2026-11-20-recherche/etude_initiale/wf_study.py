"""Walk-forward : paniers hebdo (rebalance à la 1ʳᵉ clôture après le snapshot), 10 bps/turnover."""
import sys, math
import numpy as np, pandas as pd
sys.path.insert(0, "/home/swing/swingquant/backend")
from modules import universe_history as uh, price_panel as pp

ser = pp._series()
closes = pd.DataFrame({t: pd.Series(v, index=pd.to_datetime(d)) for t, (d, v) in ser.items()}).sort_index()
days = closes.index
PILLARS = ["quality_score","value_score","risk_score","momentum_score","piotroski_score","growth_score","revisions_score"]

live = uh.list_live_snapshots(); picked, last = [], None
for d in live:
    if last is None or (d - last).days >= 7: picked.append(d); last = d

frames = []
for d in picked:
    tk = (uh.read_snapshot(d) or {}).get("tickers") or {}
    df = pd.DataFrame.from_dict(tk, orient="index")
    keep = [c for c in PILLARS + ["titan_composite_score","sector","ev_to_ebitda","forward_pe","price_to_book","data_quality"] if c in df.columns]
    df = df[keep].copy(); df["date"] = d; frames.append(df)

def sec_pct(df, col, invert=False, positive_only=False):
    x = pd.to_numeric(df[col], errors="coerce")
    if positive_only: x = x.where(x > 0)
    if invert: x = -x
    return x.groupby(df["sector"]).rank(pct=True)

def i0_of(d): return days.searchsorted(pd.Timestamp(d), side="right")

# rendements période (snapshot k → snapshot k+1)
per = []
for k in range(len(picked) - 1):
    a, b = i0_of(picked[k]), i0_of(picked[k + 1])
    if b >= len(days): break
    per.append((closes.iloc[b] / closes.iloc[a] - 1))
nP = len(per)

# IC 21j réalisés (pour le walk-forward : connu seulement 21 séances plus tard)
def fwd21(k):
    a = i0_of(picked[k]);  return (closes.iloc[a + 21] / closes.iloc[a] - 1) if a + 21 < len(days) else None
ic_hist = {}  # k -> {pillar: ic}
for k, df in enumerate(frames):
    f = fwd21(k)
    if f is None: continue
    y = f.reindex(df.index); m0 = y.notna()
    ic_hist[k] = {p: pd.to_numeric(df.loc[m0, p], errors="coerce").rank().corr(y[m0].rank()) for p in PILLARS if p in df}

def avail_ic(k):
    """IC dont l'horizon 21 séances est clos AVANT la date k (pas de look-ahead)."""
    tk = i0_of(picked[k]); out = []
    for j, ics in ic_hist.items():
        if i0_of(picked[j]) + 21 <= tk: out.append(ics)
    return out

def score(name, k):
    df = frames[k]
    if name == "composite_prod": return pd.to_numeric(df["titan_composite_score"], errors="coerce")
    if name == "value_score":    return pd.to_numeric(df["value_score"], errors="coerce")
    if name == "value_raw_sn":
        return pd.concat([sec_pct(df,"ev_to_ebitda",True,True), sec_pct(df,"forward_pe",True,True),
                          sec_pct(df,"price_to_book",True,True)], axis=1).mean(axis=1)
    if name == "value_raw_sn_qfilter":
        s = score("value_raw_sn", k); q = sec_pct(df, "quality_score"); pio = pd.to_numeric(df["piotroski_score"], errors="coerce")
        return s.where((q > 0.2) & (pio.rank(pct=True) > 0.2))
    if name == "value_x_momentum":
        return pd.concat([score("value_raw_sn", k), sec_pct(df, "momentum_score")], axis=1).mean(axis=1)
    if name == "adaptive_ic":
        hist = avail_ic(k)
        if len(hist) < 6: return None
        mean_ic = pd.DataFrame(hist).tail(12).mean()
        w = mean_ic.clip(lower=0)
        if w.sum() <= 0: return None
        return sum(w[p] * sec_pct(df, p) for p in w.index if w[p] > 0) / w.sum()
    raise KeyError(name)

def run(name, top_n, start_k=0):
    rets, prev = [], set()
    for k in range(start_k, nP):
        r = per[k]
        if name == "universe_ew":
            pick = list(frames[k].index)
        else:
            s = score(name, k)
            if s is None: rets.append(np.nan); continue
            s = s.dropna(); s = s[s.index.isin(r.dropna().index)]
            pick = list(s.sort_values(ascending=False).index[:top_n])
        rr = r.reindex(pick).dropna()
        cur = set(rr.index); turn = 1.0 if not prev else len(cur ^ prev) / (2 * max(len(cur), 1))
        rets.append(rr.mean() - turn * 2 * 10e-4 * (0 if name == "universe_ew" else 1)); prev = cur
    return pd.Series(rets)

START = 9  # 1ʳᵉ date où adaptive_ic dispose d'assez d'historique ; même fenêtre pour tous
names = ["universe_ew","composite_prod","value_score","value_raw_sn","value_raw_sn_qfilter","value_x_momentum","adaptive_ic"]
ew = run("universe_ew", 0, START)
print(f"périodes hebdo évaluées : {len(ew)} ({picked[START]} → {picked[START+len(ew)]})")
print(f"{'stratégie':24s} {'N':>4s} {'total':>8s} {'vsEW':>7s} {'t_excès':>8s} {'hit':>5s} {'maxDD':>7s} {'1reM':>7s} {'2eM':>7s}")
for nm in names:
    for n in ([0] if nm == "universe_ew" else [20, 50, 100]):
        s = run(nm, n, START); m = s.notna()
        tot = (1 + s[m]).prod() - 1; ex = (s - ew)[m]
        t = ex.mean() / (ex.std(ddof=1) / math.sqrt(len(ex))) if len(ex) > 2 else float("nan")
        nav = (1 + s[m]).cumprod(); dd = (nav / nav.cummax() - 1).min()
        h = len(ex) // 2
        print(f"{nm:24s} {n:4d} {tot*100:+7.1f}% {(tot - ((1+ew[m]).prod()-1))*100:+6.1f} {t:+8.2f} {(ex>0).mean():5.2f} {dd*100:+6.1f}% "
              f"{((1+ex.iloc[:h]).prod()-1)*100:+6.1f} {((1+ex.iloc[h:]).prod()-1)*100:+6.1f}")

print("\n— variantes implémentables (pilier value_score existant + filtres) + plafond sectoriel —")
def vq(k, use_raw=False, qcut=0.2):
    df = frames[k]
    v = score("value_raw_sn", k) if use_raw else pd.to_numeric(df["value_score"], errors="coerce").groupby(df["sector"]).rank(pct=True)
    q = sec_pct(df, "quality_score"); pio = pd.to_numeric(df["piotroski_score"], errors="coerce").rank(pct=True)
    return v.where((q > qcut) & (pio > qcut))
def run_fn(fn, top_n, cap=None):
    rets, prev = [], set()
    for k in range(START, nP):
        r = per[k]; s = fn(k).dropna(); s = s[s.index.isin(r.dropna().index)].sort_values(ascending=False)
        pick, cnt = [], {}
        for t in s.index:
            sec = frames[k].at[t, "sector"]
            if cap and cnt.get(sec, 0) >= cap: continue
            cnt[sec] = cnt.get(sec, 0) + 1; pick.append(t)
            if len(pick) >= top_n: break
        rr = r.reindex(pick).dropna(); cur = set(rr.index)
        turn = 1.0 if not prev else len(cur ^ prev) / (2 * max(len(cur), 1))
        rets.append(rr.mean() - turn * 2 * 10e-4); prev = cur
    return pd.Series(rets)
for label, fn in [("value_score_sn+qfilter", lambda k: vq(k)), ("value_raw_sn+qfilter", lambda k: vq(k, True)),
                  ("value_raw_sn+qfilter q30", lambda k: vq(k, True, 0.3))]:
    for n, cap in [(20, None), (20, 3), (30, 3), (50, None), (50, 6)]:
        s = run_fn(fn, n, cap); ex = s - ew; tot = (1 + s).prod() - 1
        t = ex.mean() / (ex.std(ddof=1) / math.sqrt(len(ex))); nav = (1 + s).cumprod(); dd = (nav / nav.cummax() - 1).min(); h = len(ex)//2
        print(f"{label:26s} top{n:<3d} cap{str(cap):4s} total {tot*100:+6.1f}% vsEW {(tot-((1+ew).prod()-1))*100:+6.1f} t {t:+.2f} DD {dd*100:+.1f}% moitiés {((1+ex.iloc[:h]).prod()-1)*100:+.1f}/{((1+ex.iloc[h:]).prod()-1)*100:+.1f}")
