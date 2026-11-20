"""Build point-in-time monthly fundamentals panel from SEC companyfacts extracts.

PIT rules:
 * availability = SEC `filed` date + 1 business day; signal at month-end d uses only facts with filed+1BD <= d.
 * restatements: for each (concept, start, end) the value kept is the one from the most recent filing
   available at d (higher-priority tag wins over fallback tags; revenue = max across revenue tags).
 * flows: TTM ending at latest fiscal period end E = annual (340-390d) if present, else
   YTD(E) + FY(prev) - YTD(prev-year same length), else sum of 4 chained discrete quarters (75-120d).
 * instants: value at the latest period end available; lags = period end closest to E-365 (+-45d).
 * staleness: fundamentals dropped if latest fiscal period end older than 400 days at d.
"""
import os, sys, json, gzip, csv, math
import numpy as np, pandas as pd
from datetime import date, timedelta
B='/tmp/claude-1000/-home-swing-swingquant/a57abce2-5a73-49c5-bc63-e73a4a7fdd25/scratchpad/agent_pit'
D=lambda s: date.fromisoformat(s)
FLOW={'rev':['Revenues','RevenueFromContractWithCustomerExcludingAssessedTax','RevenueFromContractWithCustomerIncludingAssessedTax','SalesRevenueNet','SalesRevenueGoodsNet','RevenuesNetOfInterestExpense'],
 'cogs':['CostOfRevenue','CostOfGoodsAndServicesSold','CostOfGoodsSold'],'gp':['GrossProfit'],'sga':['SellingGeneralAndAdministrativeExpense'],
 'oi':['OperatingIncomeLoss'],'ni':['NetIncomeLoss','ProfitLoss','NetIncomeLossAvailableToCommonStockholdersBasic'],
 'pti':['IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest'],'intexp':['InterestExpense'],
 'da':['DepreciationDepletionAndAmortization','DepreciationAndAmortization','DepreciationAmortizationAndAccretionNet','Depreciation'],
 'ocf':['NetCashProvidedByUsedInOperatingActivities','NetCashProvidedByUsedInOperatingActivitiesContinuingOperations'],
 'capex':['PaymentsToAcquirePropertyPlantAndEquipment','PaymentsToAcquireProductiveAssets'],
 'bb':['PaymentsForRepurchaseOfCommonStock'],'div':['PaymentsOfDividends','PaymentsOfDividendsCommonStock']}
INST={'assets':['Assets'],'acur':['AssetsCurrent'],'liab':['Liabilities'],'lcur':['LiabilitiesCurrent'],
 'eq':['StockholdersEquity','StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'],'pref':['PreferredStockValue'],
 'ltd':['LongTermDebt','LongTermDebtAndCapitalLeaseObligations'],'ltdnc':['LongTermDebtNoncurrent'],'ltdc':['LongTermDebtCurrent','LongTermDebtAndCapitalLeaseObligationsCurrent'],
 'dcur':['DebtCurrent'],'stb':['ShortTermBorrowings'],'cp':['CommercialPaper'],
 'cash':['CashAndCashEquivalentsAtCarryingValue','CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents','Cash'],
 'sh_dei':['EntityCommonStockSharesOutstanding'],'sh_cso':['CommonStockSharesOutstanding']}
AVG={'dilsh':['WeightedAverageNumberOfDilutedSharesOutstanding','WeightedAverageNumberOfSharesOutstandingBasic']}
T2C={}
for c,tags in list(FLOW.items())+list(AVG.items()):
    for p,t in enumerate(tags): T2C[t]=(c,p,'F')
for c,tags in INST.items():
    for p,t in enumerate(tags): T2C[t]=(c,p,'I')

def near(d1,d2,tol): return abs((d1-d2).days)<=tol
class Store:
    def __init__(s): s.f={}; s.i={}
    def add(s,c,p,kind,start,end,val,filed):
        if kind=='I':
            dd=s.i.setdefault(c,{}); key=end
        else:
            dd=s.f.setdefault(c,{}); key=(start,end)
        if c=='rev':  # max across tags, latest filed per tag
            m=dd.setdefault(key,{}); m[p]=val; return
        cur=dd.get(key)
        if cur is None or p<cur[0] or (p==cur[0]): dd[key]=(p,val)
    def fval(s,c):
        dd=s.f.get(c,{})
        if c=='rev': return {k:max(v.values()) for k,v in dd.items()}
        return {k:v[1] for k,v in dd.items()}

def ttm(per,E):
    """per: {(start,end):val}; return TTM ending at E (exact end) or nan."""
    byend={}
    for (st,en),v in per.items():
        if near(en,E,6): byend.setdefault('E',[]).append((st,en,v))
    if 'E' not in byend: return np.nan
    cands=byend['E']
    for st,en,v in cands:
        if 340<=(en-st).days<=390: return v
    # YTD route: longest record ending at E
    st,en,v=max(cands,key=lambda x:(x[1]-x[0]).days)
    L=(en-st).days
    if L<75: return np.nan
    A=None;P=None
    for (s2,e2),v2 in per.items():
        if 340<=(e2-s2).days<=390 and near(e2,st-timedelta(1),8): A=(s2,e2,v2)
    if A is not None:
        for (s3,e3),v3 in per.items():
            if near(s3,A[0],8) and abs((e3-s3).days-L)<=20 and near(e3,en-timedelta(365),12): P=v3
        if P is not None: return v+A[2]-P
    # chain of 4 discrete quarters
    q={}
    for (s2,e2),v2 in per.items():
        if 75<=(e2-s2).days<=120: q.setdefault(e2,[]).append((s2,v2))
    tot=0; e=en
    for k in range(4):
        hit=None
        for e2 in q:
            if near(e2,e,6): hit=q[e2][0]; break
        if hit is None: return np.nan
        tot+=hit[1]; e=hit[0]-timedelta(1)
    return tot

def latest_end(per):
    ends=sorted({en for (st,en) in per},reverse=True)
    return ends

def snap(store,asof):
    """compute fundamentals using current store content."""
    out={}
    # reference fiscal end E: latest end among income/cashflow concepts
    ends=set()
    for c in ('ni','rev','ocf','oi'):
        ends|={en for (st,en) in store.f.get(c,{})}
    if not ends: return None
    ends=sorted(ends,reverse=True)
    E=None
    nif=store.fval('ni'); 
    for e in ends[:3]:
        if not math.isnan(ttm(nif,e)) or not math.isnan(ttm(store.fval('rev'),e)) or not math.isnan(ttm(store.fval('ocf'),e)): E=e; break
    if E is None: return None
    out['fy_end']=E
    # lag reference ends
    def find_end(per,target,tol=20):
        best=None
        for (st,en) in per:
            if near(en,target,tol) and (best is None or abs((en-target).days)<abs((best-target).days)): best=en
        return best
    for c in FLOW:
        per=store.fval(c)
        out[c]=ttm(per,E) if per else np.nan
        if c in ('ni','ocf','rev','gp','cogs','sga','oi'):
            for lag,lab in ((365,'_l1'),(730,'_l2')):
                e1=find_end(per,E-timedelta(lag)) if per else None
                out[c+lab]=ttm(per,e1) if e1 else np.nan
    # instants
    for c in INST:
        dd=store.i.get(c,{})
        if not dd: out[c]=np.nan; out[c+'_end']=None; continue
        if c.startswith('sh'):   # share counts: latest cover/balance date, must be <= 400d old
            e0=max(dd); ok=(asof-e0).days<=400
        else:                    # balance sheet: must be the fiscal end E (+-20d) or within 100d before it
            cand=[e for e in dd if -100<=(e-E).days<=20]
            e0=max(cand) if cand else None; ok=e0 is not None
        if not ok: out[c]=np.nan; out[c+'_end']=None
        else: out[c]=dd[e0][1]; out[c+'_end']=e0
        if c in ('assets','acur','lcur','eq','ltd','ltdnc','sh_cso'):
            for lag,lab in ((365,'_l1'),(730,'_l2')):
                tgt=E-timedelta(lag); best=None
                for e in dd:
                    if near(e,tgt,45) and (best is None or abs((e-tgt).days)<abs((best-tgt).days)): best=e
                out[c+lab]=dd[best][1] if best else np.nan
    # diluted shares: latest-ending record
    dd=store.fval('dilsh')
    if dd:
        k=max(dd,key=lambda x:(x[1],-(x[1]-x[0]).days)); out['dilsh']=dd[k]; out['dilsh_end']=k[1]
        k1=find_end(dd,k[1]-timedelta(365),25)
        cand=[kk for kk in dd if kk[1]==k1] if k1 else []
        out['dilsh_l1']=dd[min(cand,key=lambda x:(x[1]-x[0]).days)] if cand else np.nan
    else: out['dilsh']=np.nan; out['dilsh_end']=None; out['dilsh_l1']=np.nan
    return out

def process(t,month_ends):
    fp=os.path.join(B,'facts',t+'.csv.gz')
    rows=[]
    with gzip.open(fp,'rt') as f:
        r=csv.reader(f); next(r)
        for tag,unit,st,en,val,fy,fpp,form,filed,accn in r:
            if tag not in T2C: continue
            c,p,kind=T2C[tag]
            if kind=='F' and not st: continue
            if c.startswith('sh') or c=='dilsh':
                if unit!='shares': continue
            elif unit!='USD': continue
            try: v=float(val)
            except: continue
            rows.append((D(filed),c,p,kind,D(st) if st else None,D(en),v))
    if not rows: return []
    rows.sort(key=lambda x:x[0])
    st=Store(); res=[]; i=0; n=len(rows)
    bd=pd.tseries.offsets.BDay(1)
    # vintage date -> available at filed+1BD
    snaps=[]
    while i<n:
        fd=rows[i][0]
        while i<n and rows[i][0]==fd:
            _,c,p,kind,s0,e0,v=rows[i]; st.add(c,p,kind,s0,e0,v,fd); i+=1
        avail=(pd.Timestamp(fd)+bd).date()
        snaps.append((avail,fd))
        # compute only if next filing is not on same avail day (lazy: compute for each vintage)
        snaps[-1]=(avail,snap(st,avail),fd)
    # map month-ends
    j=-1
    for me in month_ends:
        while j+1<len(snaps) and snaps[j+1][0]<=me: j+=1
        if j<0 or snaps[j][1] is None: continue
        s=snaps[j][1]
        if (me-s['fy_end']).days>400: continue
        d=dict(s); d['date']=me; d['ticker']=t; d['vintage_filed']=snaps[j][2]
        res.append(d)
    return res

if __name__=='__main__':
    me=[d.date() for d in pd.date_range('2009-12-31','2026-10-31',freq='ME')]
    u=json.load(open('/home/swing/swingquant/backend/data/universe.json'))['tickers']
    tick=sorted(t for t in u if os.path.exists(os.path.join(B,'facts',t+'.csv.gz')))
    if len(sys.argv)>1: tick=sys.argv[1].split(',')
    outp=os.path.join(B,'pit_raw_monthly.csv.gz'); first=True
    with gzip.open(outp+'.tmp','wt',newline='') as f:
        w=None
        for k,t in enumerate(tick):
            try: res=process(t,me)
            except Exception as e:
                print('ERR',t,repr(e),flush=True); continue
            if not res: print('EMPTY',t,flush=True); continue
            df=pd.DataFrame(res)
            cols=['date','ticker','vintage_filed','fy_end']
            for c in FLOW:
                cols.append(c)
                if c in ('ni','ocf','rev','gp','cogs','sga','oi'): cols+= [c+'_l1',c+'_l2']
            for c in INST:
                cols+=[c,c+'_end']
                if c in ('assets','acur','lcur','eq','ltd','ltdnc','sh_cso'): cols+=[c+'_l1',c+'_l2']
            cols+=['dilsh','dilsh_end','dilsh_l1']
            df=df.reindex(columns=cols)
            df.to_csv(f,header=first,index=False); first=False
            if k%50==0: print(k,t,len(df),flush=True)
    os.replace(outp+'.tmp',outp)
