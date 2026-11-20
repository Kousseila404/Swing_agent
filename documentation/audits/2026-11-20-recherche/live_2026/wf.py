"""Paniers hebdo top-N, plafond secteur, 10 bps/côté, vs univers EW. Scores figés a priori (aucun fit)."""
from lib import *
dates = weekly_dates()
F = {}
for d in dates:
    f = frame(d); sec = f.sector
    core = v15(f, gate=None)
    qp = avg([spct(f.quality_score, sec), (f.piotroski_score.rank()-0.5)/f.piotroski_score.notna().sum()], 1)
    S = pd.DataFrame(index=f.index); S["sector"] = sec
    S["legacy_composite"] = f.titan_composite_score
    S["V15"] = v15(f)
    S["V15_nogate"] = core
    S["V15+FCF"] = v15(f, ("ebitda_ev","fwd_ep","bp","fcf_yield"))
    S["V15+SHY"] = v15(f, ("ebitda_ev","fwd_ep","bp","sh_yield"))
    S["value6_broad"] = v15(f, ("ebitda_ev","fwd_ep","bp","fcf_yield","sales_ev","sh_yield"), min_k=3)
    S["value+quality_add"] = 0.5 * core + 0.5 * qp
    S["value+mom_add"] = 0.5 * core + 0.5 * spct(f.mom_stored_8_1, sec)
    S["V15_finvariant"] = v15(f, fin_variant=True)
    S["V15+lowvol_add"] = 0.5 * v15(f) + 0.5 * spct(f.low_vol_stored, sec)
    # sensibilité
    for g in (None, .10, .30): S[f"sens_gate{g}"] = v15(f, gate=g)
    S["sens_EVonly"] = v15(f, ("ebitda_ev",), min_k=1)
    S["sens_EV+PE"] = v15(f, ("ebitda_ev","fwd_ep"), min_k=1)
    F[d] = S

ends = [i0(d) for d in dates] + [len(DAYS) - 1]
def run(col, N=30, cap=4):
    rets, ew, prev = [], [], None
    for k, d in enumerate(dates):
        a, b = ends[k], ends[k + 1]
        if a >= b: continue
        r = (C.iloc[b] / C.iloc[a] - 1)
        S = F[d]; univ = S.index[S.index.isin(r.dropna().index)]
        ew.append(r.reindex(univ).mean())
        if col is None: rets.append(ew[-1]); continue
        s = S.loc[univ, col].dropna().sort_values(ascending=False)
        pick, cnt = [], {}
        for t in s.index:
            sc = S.at[t, "sector"]
            if cap and cnt.get(sc, 0) >= cap: continue
            cnt[sc] = cnt.get(sc, 0) + 1; pick.append(t)
            if len(pick) >= N: break
        cur = set(pick); turn = 0.5 if prev is None else len(cur ^ prev) / (2 * len(cur))
        rets.append(r.reindex(pick).mean() - 2 * turn * 10e-4); prev = cur
    return pd.Series(rets), pd.Series(ew)

def stats(col, N=30, cap=4):
    s, ew = run(col, N, cap); ex = s - ew; n = len(ex)
    tot = (1 + s).prod() - 1; tew = (1 + ew).prod() - 1
    nav = (1 + s).cumprod(); dd = (nav / nav.cummax() - 1).min()
    rel = (1 + s).cumprod() / (1 + ew).cumprod(); rdd = (rel / rel.cummax() - 1).min()
    th = [ex.iloc[i * n // 3:(i + 1) * n // 3] for i in range(3)]
    h = n // 2
    return dict(strat=col, N=N, cap=cap, weeks=n, total=tot*100, EW=tew*100, excess=(tot - tew)*100,
                t_ex=nw_t(ex, 1), hit=(ex > 0).mean(), maxDD=dd*100, relDD=rdd*100,
                H1=((1+s.iloc[:h]).prod()/(1+ew.iloc[:h]).prod()-1)*100, H2=((1+s.iloc[h:]).prod()/(1+ew.iloc[h:]).prod()-1)*100,
                T1=ex.iloc[:n//3].sum()*100, T2=ex.iloc[n//3:2*n//3].sum()*100, T3=ex.iloc[2*n//3:].sum()*100)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
print("dates", len(dates), dates[0].date(), "->", DAYS[-1].date())
main = ["legacy_composite","V15","V15_nogate","V15+FCF","V15+SHY","value6_broad","value+quality_add","value+mom_add","V15_finvariant","V15+lowvol_add"]
T = pd.DataFrame([stats(c) for c in main]); print(T.round(2).to_string(index=False))
print("\n— sensibilité V15 (top-30 cap 4 sauf mention) —")
sens = [stats(c) for c in ["sens_gateNone","sens_gate0.1","V15","sens_gate0.3","sens_EVonly","sens_EV+PE","V15+FCF"]]
sens += [stats("V15", 20, 3), stats("V15", 50, 7), stats("V15", 30, None), stats("legacy_composite", 20, 3), stats("legacy_composite", 50, 7)]
S2 = pd.DataFrame(sens); print(S2.round(2).to_string(index=False))
pd.concat([T, S2]).to_csv(D + "wf_summary.csv", index=False)
