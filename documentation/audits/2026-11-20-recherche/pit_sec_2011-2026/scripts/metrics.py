"""From pit_raw_monthly + monthly prices -> pit_fundamentals_monthly.csv.gz (date,ticker,metrics) + returns matrix."""
import json, numpy as np, pandas as pd
B='/tmp/claude-1000/-home-swing-swingquant/a57abce2-5a73-49c5-bc63-e73a4a7fdd25/scratchpad/agent_pit'
u=json.load(open('/home/swing/swingquant/backend/data/universe.json'))['tickers']
def px(k):
    d=pd.read_csv(f'{B}/px_monthly_{k}.csv.gz',index_col=0,parse_dates=True)
    d.index=d.index+pd.offsets.MonthEnd(0); return d.loc[:'2026-10-31']
close,adj,spl=px('close'),px('adj'),px('split').fillna(0)
spl=spl.where(spl>0,1.0)
raw=pd.read_csv(f'{B}/pit_raw_monthly.csv.gz',parse_dates=['date'],low_memory=False)
for c in ['sh_dei_end','sh_cso_end','dilsh_end','vintage_filed','fy_end']: raw[c]=pd.to_datetime(raw[c])
raw=raw[raw.ticker.isin(close.columns)]
# ---------- shares in today's units (split-normalised) ----------
# factor after month m = product of splits in months > m
cum_after=spl[::-1].cumprod()[::-1].shift(-1).fillna(1.0)   # prod of splits strictly after month
def s_norm(row_t, col, refcol):
    ref=raw[refcol]+pd.offsets.MonthEnd(0)
    return ref
out=[]
for t,g in raw.groupby('ticker'):
    g=g.set_index('date').sort_index()
    ca=cum_after[t] if t in cum_after else pd.Series(1.0,index=close.index)
    ser={}
    for col,ref in (('sh_dei','sh_dei_end'),('sh_cso','vintage_filed'),('dilsh','vintage_filed')):
        r=g[ref]+pd.offsets.MonthEnd(0)
        f=r.map(lambda x: ca.get(x,np.nan) if pd.notna(x) else np.nan)
        ser[col]=g[col]*f
    S=pd.DataFrame(ser)
    S=S.where(S>0)
    prim=S.notna().sum().idxmax()
    sh=S[prim].copy()
    for c in S.columns:
        if c==prim: continue
        ov=S[prim].notna()&S[c].notna()
        k=(S[prim][ov]/S[c][ov]).median() if ov.sum()>=6 else 1.0
        sh=sh.fillna(S[c]*k)
    # clean glitches: deviation >40% from centred 7m rolling median
    med=sh.rolling(7,center=True,min_periods=3).median()
    sh=sh.where((sh/med-1).abs()<0.4)
    sh=sh.ffill(limit=3)
    g['sh_norm']=sh
    g['price']=close[t].reindex(g.index)
    out.append(g)
P=pd.concat(out).reset_index()
P['mcap']=P.sh_norm*P.price
# calibrate level against current universe market cap (dual-class / A-equivalent issues)
cal={}
for t,g in P.dropna(subset=['mcap']).groupby('ticker'):
    last=g.iloc[-1]; mc=u.get(t,{}).get('market_cap'); cp=u.get(t,{}).get('current_price')
    if mc and cp and last.sh_norm>0:
        k=mc/(last.sh_norm*cp)
        cal[t]=k if (k<0.33 or k>3) else 1.0
P['cal']=P.ticker.map(cal).fillna(1.0); P['mcap']=P.mcap*P.cal; P['sh_norm']=P.sh_norm*P.cal
print('calibrated tickers:',{k:round(v,3) for k,v in cal.items() if v!=1.0})
# ---------- accounting ----------
nz=lambda s: s.fillna(0)
lt=P.ltd.where(P.ltd.notna(), np.where(P.ltdnc.notna()|P.ltdc.notna(), nz(P.ltdnc)+nz(P.ltdc), np.nan))
st=np.where(P.dcur.notna(), (P.dcur-nz(P.ltdc)).clip(lower=0), np.where(P.stb.notna(),P.stb,nz(P.cp)))
P['debt']=nz(pd.Series(lt,index=P.index))+st
P['ev']=P.mcap+P.debt+nz(P.pref)-nz(P.cash)
P.loc[P.ev<=0.05*P.mcap,'ev']=np.nan
P['ebit']=P.oi.where(P.oi.notna(), P.pti+nz(P.intexp))
P['ebitda']=P.ebit+P.da
P['fcf']=P.ocf-nz(P.capex)
P['gp_']=P.gp.where(P.gp.notna(),P.rev-P.cogs)
P['gp_l1_']=P.gp_l1.where(P.gp_l1.notna(),P.rev_l1-P.cogs_l1)
P['ep']=P.ni/P.mcap; P['ebit_ev']=P.ebit/P.ev; P['ebitda_ev']=P.ebitda/P.ev; P['fcf_ev']=P.fcf/P.ev; P['sales_ev']=P.rev/P.ev
P['bm']=P['eq']/P.mcap
P['shy']=np.where(P.ocf.notna(),(nz(P['div'])+nz(P.bb))/P.mcap,np.nan)
P['gpa']=P.gp_/P.assets
opn=np.where(P.cogs.notna()&P.sga.notna(), P.rev-P.cogs-P.sga, np.where(P.gp_.notna()&P.sga.notna(), P.gp_-P.sga, P.oi))
P['op_num']=opn
P['op']=P.op_num/P.assets
P['cbop']=(P.op_num-(P.ni+nz(P.da)-P.ocf))/P.assets
avgA=(P.assets+P.assets_l1)/2
P['accruals']=(P.ni-P.ocf)/avgA
P['agr']=P.assets/P.assets_l1-1
P=P.sort_values(['ticker','date'])
P['sh12']=P.groupby('ticker').sh_norm.shift(12)
P['nsi']=np.log(P.sh_norm/P.sh12)
# Piotroski PIT
roa=P.ni/P.assets_l1; roa1=P.ni_l1/P.assets_l2
levn=P.ltdnc.where(P.ltdnc.notna(),P.ltd); levn1=P.ltdnc_l1.where(P.ltdnc_l1.notna(),P.ltd_l1)
lev=nz(levn)/P.assets; lev1=nz(levn1)/P.assets_l1
cr=P.acur/P.lcur; cr1=P.acur_l1/P.lcur_l1
gm=P.gp_/P.rev; gm1=P.gp_l1_/P.rev_l1
at=P.rev/P.assets_l1; at1=P.rev_l1/P.assets_l2
sig=pd.DataFrame({'f1':(roa>0).where(roa.notna()),'f2':(P.ocf>0).where(P.ocf.notna()),'f3':(roa>roa1).where(roa.notna()&roa1.notna()),
 'f4':(P.ocf>P.ni).where(P.ocf.notna()&P.ni.notna()),'f5':(lev<=lev1).where(P.assets.notna()&P.assets_l1.notna()),
 'f6':(cr>cr1).where(cr.notna()&cr1.notna()),'f7':(P.nsi<=0.005).where(P.nsi.notna()),'f8':(gm>gm1).where(gm.notna()&gm1.notna()),
 'f9':(at>at1).where(at.notna()&at1.notna())}).astype(float)
nv=sig.notna().sum(axis=1)
P['piotroski']=(sig.sum(axis=1)*9/nv).where(nv>=6)
roe0=P.ni/P['eq']; roe1=P.ni_l1/P.eq_l1; roe2=P.ni_l2/P.eq_l2
R=pd.concat([roe0,roe1,roe2],axis=1); okpos=(P[['eq','eq_l1','eq_l2']]>0).all(axis=1)
P['roe_stable']=(R.mean(axis=1)-R.std(axis=1)).where(okpos&R.notna().all(axis=1))
# momentum 12-1 from total-return prices
lr=adj
mom=(lr.shift(1)/lr.shift(12)-1)
P=P.merge(mom.stack().rename('mom').reset_index().rename(columns={'level_0':'date','Date':'date','level_1':'ticker'}),on=['date','ticker'],how='left')
P['sector']=P.ticker.map(lambda t:u[t].get('sector') or 'Unknown')
# ---- guards against share-count glitches (mcap implausible) ----
P=P.replace([np.inf,-np.inf],np.nan)
bad=(P.bm>5)|(P.ep>1)|(P.shy>0.5)|(P.sales_ev>15)|(P.mcap<2e8)
print('mcap-glitch ticker-months flagged:',int(bad.sum()))
for c in ['mcap','ev','ep','ebit_ev','ebitda_ev','fcf_ev','sales_ev','bm','shy']: P.loc[bad,c]=np.nan
P['nsi']=P.nsi.clip(-0.5,1.0)
keep=['date','ticker','sector','fy_end','vintage_filed','mcap','ev','debt','rev','gp_','oi','ebit','ebitda','ni','ocf','capex','fcf','div','bb','assets','eq',
 'ep','ebit_ev','ebitda_ev','fcf_ev','sales_ev','bm','shy','gpa','op','cbop','accruals','agr','nsi','piotroski','roe_stable','mom']
P[keep].to_csv(f'{B}/pit_fundamentals_monthly.csv.gz',index=False,float_format='%.6g')
ret=adj.pct_change().shift(-1)  # next-month return, indexed by signal date
ret.to_csv(f'{B}/fwd_returns_monthly.csv.gz',float_format='%.6g')
# coverage
cov=P.groupby(P.date.dt.year)[['mcap','ev','ep','ebit_ev','ebitda_ev','fcf_ev','sales_ev','bm','shy','gpa','op','cbop','accruals','agr','nsi','piotroski','roe_stable','mom']].apply(lambda d:d.notna().sum())
nprice=close.notna().sum(axis=1).groupby(close.index.year).sum()
print((cov.div(nprice.reindex(cov.index),axis=0)*100).round(0).to_string())
print('overall % ticker-months with price:',(P[['mcap','ep','ebitda_ev','bm','gpa','op','piotroski']].notna().sum()/close.loc['2010':].notna().sum().sum()*100).round(1).to_dict())
