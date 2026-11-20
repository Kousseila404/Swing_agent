import sys; from lib import *
SIG = ["fcf_yield","ocf_yield","ebitda_ev","fwd_ep","ttm_ep_ni","bp","sales_ev","div_yield","net_buyback","sh_yield",
       "val2","val3_v15core","val4_+fcf","val5_+fcf+sales","val5b_+fcf+shy","v15_prod",
       "gross_margin","op_margin","roe","roa","gpa","low_accruals","f_score","low_de",
       "mom_stored_8_1","mom_6_1","mom_3_1","rev_1m","high52_stored","low_vol_stored","low_vol63","low_beta",
       "quality_score","value_score","risk_score","momentum_score","piotroski_score","growth_score","revisions_score","titan_composite_score"]
dates = weekly_dates(); rows = []; gate_rate = []
for d in dates:
    a = i0(d)
    f = frame(d); sec = f.sector
    f["val2"] = avg([spct(f[c], sec) for c in ("ebitda_ev","fwd_ep")], 1)
    f["val3_v15core"] = v15(f, gate=None)
    f["val4_+fcf"] = v15(f, ("ebitda_ev","fwd_ep","bp","fcf_yield"), gate=None)
    f["val5_+fcf+sales"] = v15(f, ("ebitda_ev","fwd_ep","bp","fcf_yield","sales_ev"), gate=None, min_k=3)
    f["val5b_+fcf+shy"] = v15(f, ("ebitda_ev","fwd_ep","bp","fcf_yield","sh_yield"), gate=None, min_k=3)
    f["v15_prod"] = v15(f)
    q = spct(f.quality_score, sec); p = (f.piotroski_score.rank()-0.5)/f.piotroski_score.notna().sum()
    gate_rate.append(1 - ((q > .2) & (p > .2)).mean())
    for h in (5, 21):
        if a + h >= len(DAYS): continue
        y = (C.iloc[a + h] / C.iloc[a] - 1).reindex(f.index)
        m0 = y.notna(); ysn = y - y.groupby(sec).transform("mean")
        for c in SIG:
            x = pd.to_numeric(f[c], errors="coerce"); m = m0 & x.notna()
            if m.sum() < 150: continue
            ic = x[m].rank().corr(y[m].rank())
            ic_sn = spct(x[m], sec[m], min_n=5).corr(ysn[m].rank())
            rows.append(dict(date=d, h=h, sig=c, ic=ic, ic_sn=ic_sn, n=int(m.sum())))
r = pd.DataFrame(rows); r.to_csv(D + "ic_raw.csv", index=False)
out = []
for (c, h), g in r.groupby(["sig", "h"], sort=False):
    g = g.sort_values("date"); lag = 0 if h == 5 else 4; k = len(g) // 2
    out.append(dict(sig=c, h=h, nd=len(g), N=int(g.n.median()), IC=g.ic.mean(), t=nw_t(g.ic, lag), ICsn=g.ic_sn.mean(),
                    t_sn=nw_t(g.ic_sn, lag), sn_H1=g.ic_sn.iloc[:k].mean(), sn_H2=g.ic_sn.iloc[k:].mean(), pos_sn=(g.ic_sn > 0).mean()))
o = pd.DataFrame(out)
pd.set_option("display.width", 220); pd.set_option("display.max_rows", 300)
print("dates", len(dates), dates[0].date(), "->", dates[-1].date())
print("taux d'exclusion du gate V15 par date: min %.2f med %.2f max %.2f" % (min(gate_rate), np.median(gate_rate), max(gate_rate)))
for h in (5, 21): print(o[o.h == h].round(3).to_string(index=False))
o.to_csv(D + "ic_summary.csv", index=False)
