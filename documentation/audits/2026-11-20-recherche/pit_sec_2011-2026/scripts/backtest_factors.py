"""Monthly factor backtest on PIT fundamentals. Main research script (run after build_pit.py and metrics.py)."""
import json, numpy as np, pandas as pd, sys
B='/tmp/claude-1000/-home-swing-swingquant/a57abce2-5a73-49c5-bc63-e73a4a7fdd25/scratchpad/agent_pit'
START=sys.argv[1] if len(sys.argv)>1 else '2011-01-31'
END='2026-09-30'
FEE=0.001
LARGE=int(sys.argv[2]) if len(sys.argv)>2 else 0   # robustness: keep only the LARGE biggest names by PIT mcap at each date
SUF=f'_large{LARGE}' if LARGE else ''
P=pd.read_csv(f'{B}/pit_fundamentals_monthly.csv.gz',parse_dates=['date'])
fwd=pd.read_csv(f'{B}/fwd_returns_monthly.csv.gz',index_col=0,parse_dates=True)
adj=pd.read_csv(f'{B}/px_monthly_adj.csv.gz',index_col=0,parse_dates=True); adj.index=adj.index+pd.offsets.MonthEnd(0)
u=json.load(open('/home/swing/swingquant/backend/data/universe.json'))['tickers']
sector=pd.Series({t:(u[t].get('sector') or 'Unknown') for t in u})
dates=fwd.loc[START:END].index
tick=[t for t in sector.index if t in fwd.columns]
R=fwd.loc[dates,tick].clip(-0.95,5.0)
priced=adj.loc[dates,tick].notna()&R.notna()
if LARGE:
    mc=P[P.date.isin(dates)].pivot(index='date',columns='ticker',values='mcap').reindex(index=dates,columns=tick)
    priced=priced&(mc.rank(axis=1,ascending=False)<=LARGE)
EW=R.where(priced).mean(axis=1)
SPY=fwd.loc[dates,'SPY']; RSP=fwd.loc[dates,'RSP']
P=P[P.date.isin(dates)&P.ticker.isin(tick)].copy()
P['mom']=P['mom']  # from prices
HI=['ep','ebit_ev','ebitda_ev','fcf_ev','sales_ev','bm','shy','gpa','op','cbop','piotroski','roe_stable','mom']
LO=['accruals','agr','nsi']
for c in LO: P[c]=-P[c]
FIN=P.sector.eq('Financial Services')
def ranks(df,cols,neutral):
    out={}
    for c in cols:
        if neutral:
            g=df.groupby(['date','sector'])[c]
            r=g.rank(pct=True); n=g.transform('count')
            out[c]=r.where(n>=5)
        else:
            out[c]=df.groupby('date')[c].rank(pct=True)
    return pd.DataFrame(out,index=df.index)
VAL=['ep','ebit_ev','ebitda_ev','fcf_ev','sales_ev','bm','shy']
QUAL=['gpa','op','cbop','accruals','agr','nsi','piotroski','roe_stable']
def mrank(df,cols,minn):
    m=df[cols].mean(axis=1); return m.where(df[cols].notna().sum(axis=1)>=minn)
def xrank(s): return s.groupby(P.date).rank(pct=True)
scores={}
for tag,neu in (('raw',False),('sn',True)):
    Rk=ranks(P,HI+LO,neu)
    for c in HI+LO: scores[f'{c}|{tag}']=Rk[c]
    vc=mrank(Rk,VAL,4)
    Rf=Rk.copy(); Rf.loc[FIN,['ebit_ev','ebitda_ev','fcf_ev','sales_ev']]=np.nan
    vcf=mrank(Rf,VAL,4).where(~FIN, mrank(Rf,['ep','bm'],2))
    qc=mrank(Rk,QUAL,4)
    scores[f'VALUE_comp|{tag}']=vc
    scores[f'VALUE_comp_finPB|{tag}']=vcf
    scores[f'QUALITY_comp|{tag}']=qc
    v15=mrank(Rk,['ebitda_ev','ep','bm'],2)
    qr=xrank(qc); pr=xrank(Rk['piotroski'])
    scores[f'A_V15_value_only|{tag}']=v15
    scores[f'A_V15_filtered|{tag}']=v15.where((qr>0.2)&(pr>0.2))
    gp=Rk['gpa'].fillna(Rk['op'])
    scores[f'B_value+GP|{tag}']=pd.concat([vcf,gp],axis=1).mean(axis=1).where(vcf.notna()&gp.notna())
    scores[f'C_value+quality|{tag}']=(0.5*vcf+0.5*qc)
    scores[f'D_value+mom|{tag}']=(0.5*vcf+0.5*Rk['mom'])
    scores[f'E_value_filtQ50|{tag}']=vcf.where(xrank(qc)>=0.5)
    scores[f'F_value+quality+mom|{tag}']=(vcf+qc+Rk['mom'])/3
    scores[f'G_value+GP+mom|{tag}']=(vcf+gp+Rk['mom'])/3
S=pd.DataFrame(scores); S['date']=P.date.values; S['ticker']=P.ticker.values
S.to_csv(f'{B}/factor_ranks_monthly{SUF}.csv.gz',index=False,float_format='%.4f')
SEC=sector.reindex(tick)
def nw_t(x,L=3):
    x=np.asarray(x,float); x=x[~np.isnan(x)]; n=len(x); m=x.mean(); e=x-m
    v=e@e/n
    for l in range(1,L+1): v+=2*(1-l/(L+1))*(e[l:]@e[:-l])/n
    return m/np.sqrt(v/n)
def maxdd(r):
    w=(1+r).cumprod(); return (w/w.cummax()-1).min()
def port(Sm,kind):
    rets=[];to=[];prev=None;names=[]
    for d in dates:
        s=Sm.loc[d].dropna(); s=s[priced.loc[d,s.index]]
        if len(s)<30: rets.append(np.nan); to.append(np.nan); prev=None; continue
        s=s.sort_values(ascending=False)
        if kind=='q5': sel=list(s.index[:max(1,int(len(s)*0.2))])
        else:
            sel=[];cnt={}
            for t in s.index:
                sc=SEC[t]
                if cnt.get(sc,0)>=4: continue
                sel.append(t); cnt[sc]=cnt.get(sc,0)+1
                if len(sel)==30: break
        w=pd.Series(1/len(sel),index=sel)
        if prev is None: tov=1.0
        else: tov=w.sub(prev,fill_value=0).abs().sum()
        r=(w*R.loc[d,sel]).sum()
        rets.append(r-FEE*tov); to.append(tov)
        g=w*(1+R.loc[d,sel]); prev=g/g.sum()
    return pd.Series(rets,index=dates),pd.Series(to,index=dates)
Rr=R.copy()
rows=[];MR={'EW_universe':EW,'SPY':SPY,'RSP':RSP}
yr=dates.year
SUB={'2011-14':(2011,2014),'2015-19':(2015,2019),'2020-22':(2020,2022),'2023-26':(2023,2026)}
for f in [c for c in S.columns if '|' in c]:
    Sm=S.pivot(index='date',columns='ticker',values=f).reindex(index=dates,columns=tick)
    # IC
    Rm=Rr.where(Sm.notna()&priced)
    ic=Sm.rank(axis=1).corrwith(Rm.rank(axis=1),axis=1,method='pearson')
    cov=(Sm.notna()&priced).sum(axis=1)
    rec={'factor':f,'IC_mean':ic.mean(),'IC_tNW':nw_t(ic),'IC_hit':(ic>0).mean(),'avg_names':cov.mean()}
    for kind in ('top30','q5'):
        r,tv=port(Sm,kind); MR[f'{f}#{kind}']=r
        ex=(r-EW).dropna()
        rec[f'{kind}_annret']=(1+r.dropna()).prod()**(12/len(r.dropna()))-1
        rec[f'{kind}_ex_ann']=ex.mean()*12; rec[f'{kind}_ex_t']=ex.mean()/ex.std()*np.sqrt(len(ex))
        rec[f'{kind}_IR']=ex.mean()/ex.std()*np.sqrt(12)
        rec[f'{kind}_sharpe']=r.mean()/r.std()*np.sqrt(12); rec[f'{kind}_maxDD']=maxdd(r.dropna())
        rel=(1+r.dropna())/(1+EW[r.dropna().index]); rec[f'{kind}_relDD']=(rel.cumprod()/rel.cumprod().cummax()-1).min()
        rec[f'{kind}_turnover_m']=tv.mean()
        ey=ex.groupby(ex.index.year).sum()
        rec[f'{kind}_yrs_pos']=(ey>0).mean()
        if kind=='top30':
            for y,v in ey.items(): rec[f'y{y}']=v
            for k,(a,b) in SUB.items():
                e=ex[(ex.index.year>=a)&(ex.index.year<=b)]; rec[f'sub{k}']=e.mean()*12
                i=ic[(ic.index.year>=a)&(ic.index.year<=b)]; rec[f'IC{k}']=i.mean()
    rows.append(rec); print(f, round(rec['IC_mean'],4), round(rec['IC_tNW'],2), round(rec['top30_ex_ann'],4), flush=True)
res=pd.DataFrame(rows); res.to_csv(f'{B}/factor_summary{SUF}.csv',index=False,float_format='%.4f')
pd.DataFrame(MR).to_csv(f'{B}/factor_monthly_returns{SUF}.csv',float_format='%.5f')
# survivorship: EW reconstructed vs RSP
d=(EW-RSP).dropna()
print('SURV EW vs RSP ann diff',d.mean()*12,'t',d.mean()/d.std()*np.sqrt(len(d)))
print((d.groupby(d.index.year).sum()).round(3).to_dict())
print('EW ann',(1+EW).prod()**(12/len(EW))-1,'RSP ann',(1+RSP.dropna()).prod()**(12/len(RSP.dropna()))-1,'SPY ann',(1+SPY).prod()**(12/len(SPY))-1)
# financial sector: EV-based vs P/B,E/P within financials
fi=P[FIN]
out=[]
for c in ['ep','bm','ebit_ev','ebitda_ev','fcf_ev','sales_ev','shy']:
    Sm=fi.pivot(index='date',columns='ticker',values=c).reindex(index=dates)
    Rm=Rr[Sm.columns].where(Sm.notna())
    ic=Sm.rank(axis=1).corrwith(Rm.rank(axis=1),axis=1); n=Sm.notna().sum(axis=1)
    ic=ic.where(n>=10)
    out.append({'metric':c,'IC_fin':ic.mean(),'t':nw_t(ic.dropna()),'n_avg':n.mean()})
print(pd.DataFrame(out).round(4).to_string())
pd.DataFrame(out).to_csv(f'{B}/financials_ic{SUF}.csv',index=False)
