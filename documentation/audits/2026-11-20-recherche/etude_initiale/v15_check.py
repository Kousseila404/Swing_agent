import sys, math
import pandas as pd
sys.path.insert(0, "/home/swing/swingquant/backend")
exec(open("/tmp/claude-1000/-home-swing-swingquant/a57abce2-5a73-49c5-bc63-e73a4a7fdd25/scratchpad/wf_study.py").read().split("def sec_pct")[0])
from modules.sector_metrics._scoring import compute_v15_scores
def i0_of(d): return days.searchsorted(pd.Timestamp(d), side="right")
per = []
for k in range(len(picked) - 1):
    a, b = i0_of(picked[k]), i0_of(picked[k + 1])
    if b >= len(days): break
    per.append(closes.iloc[b] / closes.iloc[a] - 1)
V = []
for d in picked[:len(per)]:
    tk = (uh.read_snapshot(d) or {}).get("tickers") or {}
    v = compute_v15_scores(tk); V.append((pd.Series({k: x["titan_v15_score"] for k, x in v.items()}, dtype=float),
                                          pd.Series({k: tk[k].get("sector") for k in tk})))
def run(top_n, cap, start, mode):
    rets, prev = [], set()
    for k in range(start, len(per)):
        r = per[k]
        if mode == "ew":
            rr = r.reindex(list(V[k][0].index)).dropna(); rets.append(rr.mean()); continue
        s = V[k][0].dropna(); s = s[s.index.isin(r.dropna().index)].sort_values(ascending=False)
        pick, cnt = [], {}
        for t in s.index:
            sec = V[k][1].get(t)
            if cap and cnt.get(sec, 0) >= cap: continue
            cnt[sec] = cnt.get(sec, 0) + 1; pick.append(t)
            if len(pick) >= top_n: break
        rr = r.reindex(pick).dropna(); cur = set(rr.index)
        turn = 1.0 if not prev else len(cur ^ prev) / (2 * len(cur)); prev = cur
        rets.append(rr.mean() - turn * 2e-3)
    return pd.Series(rets)
for start, lab in [(0, "fenêtre live complète"), (9, "fenêtre walk-forward")]:
    ew = run(0, None, start, "ew"); ewt = (1 + ew).prod() - 1
    print(f"— {lab} ({len(ew)} sem.) univers EW {ewt*100:+.1f}%")
    for n, cap in [(20, 3), (30, 4), (50, 6)]:
        s = run(n, cap, start, "v15"); ex = s - ew; tot = (1 + s).prod() - 1; h = len(ex)//2
        nav = (1 + s).cumprod(); dd = (nav / nav.cummax() - 1).min()
        t = ex.mean() / (ex.std(ddof=1) / math.sqrt(len(ex)))
        print(f"  V15 top{n} cap{cap}: {tot*100:+6.1f}%  vsEW {(tot-ewt)*100:+6.1f}  t {t:+.2f}  DD {dd*100:+.1f}%  hit {(ex>0).mean():.2f}  moitiés {((1+ex.iloc[:h]).prod()-1)*100:+.1f}/{((1+ex.iloc[h:]).prod()-1)*100:+.1f}")
