import sys, numpy as np, pandas as pd
sys.path.insert(0, "/home/swing/swingquant/backend")
from modules import price_panel as pp
D="/tmp/claude-1000/-home-swing-swingquant/a57abce2-5a73-49c5-bc63-e73a4a7fdd25/scratchpad/agent_live/"
ser = pp._series()
closes = pd.DataFrame({t: pd.Series(v, index=pd.to_datetime(d)) for t, (d, v) in ser.items()}).sort_index().astype("float32")
closes.to_pickle(D+"closes.pkl"); print("closes", closes.shape, closes.index[0], closes.index[-1])
p = pd.read_pickle(D+"panel.pkl")
pd.set_option("display.width",220); pd.set_option("display.max_columns",40)
p["fin"] = p.sector.astype(str).eq("Financial Services")
dates = sorted(p.date.unique()); print("dates", len(dates))
V = ["ev_to_ebitda","forward_pe","price_to_book","free_cash_flow","market_cap","ev_to_revenue","trailing_pe","dividend_yield","operating_cash_flow","shares_outstanding_prev_year"]
print("\n== couverture (non-null) par date (1re, milieu, dernière), all / financières ==")
for d in [dates[0], dates[len(dates)//2], dates[-1]]:
    s = p[p.date==d]
    print(str(d)[:10], len(s), {c: f"{s[c].notna().mean():.2f}/{s.loc[s.fin,c].notna().mean():.2f}" for c in V})
s = p[p.date==dates[-1]]
print("\n== positifs (>0) dernière date ==", {c: f"{(s[c]>0).mean():.2f}" for c in ["ev_to_ebitda","forward_pe","price_to_book","free_cash_flow"]})
print("n fin", s.fin.sum(), "fin EV/EBITDA présent", s.loc[s.fin,"ev_to_ebitda"].notna().sum())
print("\nsecteurs dernière date:\n", s.sector.value_counts().to_string())
print("\n== quantiles dernière date ==")
print(s[["ev_to_ebitda","forward_pe","price_to_book","peg_ratio","ev_to_revenue","dividend_yield","debt_to_equity","free_cash_flow","market_cap","fundamentals_age_days","fundamentals_report_age_days","f_score","piotroski_score"]].quantile([0,.01,.05,.5,.95,.99,1]).T.to_string())
# dividend yield unités
dy = s.dividend_yield; print("\ndiv yield >1 :", (dy>1).sum(), " in (0,0.2]:", ((dy>0)&(dy<=0.2)).sum(), "==0:", (dy==0).sum())
print(s.loc[dy.nlargest(5).index, ["ticker","dividend_yield","payout_ratio"]].to_string())
# fcf yield aberrant
s2 = s.assign(fcfy = s.free_cash_flow/s.market_cap)
print("\nFCF yield quantiles", s2.fcfy.quantile([0,.01,.5,.99,1]).round(3).to_dict())
print(s2.loc[s2.fcfy.abs().nlargest(6).index, ["ticker","sector","free_cash_flow","market_cap","fcfy"]].to_string())
print(s.loc[s.ev_to_ebitda.nlargest(5).index, ["ticker","sector","ev_to_ebitda"]].to_string())
print(s.loc[s.price_to_book.nlargest(5).index, ["ticker","sector","price_to_book"]].to_string())
print(s.loc[s.forward_pe.nlargest(5).index, ["ticker","sector","forward_pe"]].to_string())
# Staleness : fréquence de changement quotidien par champ
print("\n== fraction de jours où le champ change (par ticker, médiane) ==")
pv = p.sort_values(["ticker","date"])
for c in ["current_price","market_cap","forward_pe","ev_to_ebitda","price_to_book","trailing_pe","free_cash_flow","dividend_yield","momentum_return_pct","fundamentals_age_days"]:
    ch = pv.groupby("ticker", observed=True)[c].apply(lambda x: (x.diff().fillna(0)!=0).mean())
    print(f"{c:28s} med {ch.median():.2f} p10 {ch.quantile(.1):.2f} p90 {ch.quantile(.9):.2f}")
# Le prix dans forward_pe suit-il current_price ? corr des log-diffs quand les deux changent
pv["dlp"] = np.log(pv.current_price).groupby(pv.ticker, observed=True).diff()
for c in ["market_cap","forward_pe","price_to_book","ev_to_ebitda","trailing_pe"]:
    dl = np.log(pv[c].where(pv[c]>0)).groupby(pv.ticker, observed=True).diff()
    m = dl.notna() & pv.dlp.notna() & (pv.dlp!=0)
    print(f"corr dlog({c}) vs dlog(price): {np.corrcoef(dl[m], pv.dlp[m])[0,1]:.2f}  frac zero-change when price changes {(dl[m]==0).mean():.2f}")
# Snapshot current_price vs panel close du même jour
lastd = dates[-5]
s = p[p.date==lastd].set_index("ticker")
i = closes.index.searchsorted(lastd, side="right")-1
print("\nsnapshot", str(lastd)[:10], "panel date utilisée", closes.index[i].date())
rel = (s.current_price / closes.iloc[i].reindex(s.index) - 1).abs()
print("écart |snap price / panel close -1| quantiles", rel.quantile([.5,.9,.99]).round(4).to_dict())
age = s.fundamentals_age_days; print("fundamentals_age_days quantiles", age.quantile([.1,.5,.9,1]).to_dict())
