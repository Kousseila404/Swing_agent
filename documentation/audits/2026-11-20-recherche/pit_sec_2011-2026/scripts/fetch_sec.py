"""Download SEC companyfacts per ticker, keep only useful tags -> compact CSV, delete raw JSON."""
import json, os, sys, time, csv, gzip, urllib.request, urllib.error
B='/tmp/claude-1000/-home-swing-swingquant/a57abce2-5a73-49c5-bc63-e73a4a7fdd25/scratchpad/agent_pit'
UA={'User-Agent':'SwingQuant TITAN research@swingquant.local','Accept-Encoding':'gzip'}
TAGS={'us-gaap':['Revenues','RevenueFromContractWithCustomerExcludingAssessedTax','RevenueFromContractWithCustomerIncludingAssessedTax','SalesRevenueNet','SalesRevenueGoodsNet','RevenuesNetOfInterestExpense','InterestAndDividendIncomeOperating',
 'GrossProfit','CostOfRevenue','CostOfGoodsAndServicesSold','CostOfGoodsSold','CostOfServices','SellingGeneralAndAdministrativeExpense','ResearchAndDevelopmentExpense',
 'OperatingIncomeLoss','NetIncomeLoss','ProfitLoss','NetIncomeLossAvailableToCommonStockholdersBasic','IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest','InterestExpense','IncomeTaxExpenseBenefit',
 'DepreciationDepletionAndAmortization','DepreciationAndAmortization','DepreciationAmortizationAndAccretionNet','Depreciation',
 'NetCashProvidedByUsedInOperatingActivities','NetCashProvidedByUsedInOperatingActivitiesContinuingOperations','PaymentsToAcquirePropertyPlantAndEquipment','PaymentsToAcquireProductiveAssets',
 'Assets','AssetsCurrent','Liabilities','LiabilitiesCurrent','StockholdersEquity','StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest','PreferredStockValue','MinorityInterest',
 'LongTermDebt','LongTermDebtNoncurrent','LongTermDebtCurrent','DebtCurrent','ShortTermBorrowings','CommercialPaper','LongTermDebtAndCapitalLeaseObligations','LongTermDebtAndCapitalLeaseObligationsCurrent','DebtInstrumentCarryingAmount',
 'CashAndCashEquivalentsAtCarryingValue','CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents','Cash',
 'PaymentsForRepurchaseOfCommonStock','PaymentsOfDividends','PaymentsOfDividendsCommonStock','ProceedsFromIssuanceOfCommonStock',
 'WeightedAverageNumberOfDilutedSharesOutstanding','WeightedAverageNumberOfSharesOutstandingBasic','CommonStockSharesOutstanding'],
 'dei':['EntityCommonStockSharesOutstanding']}
FORMS={'10-K','10-Q','10-K/A','10-Q/A','10-KT','10-KT/A'}
def get(url,tries=6):
    for i in range(tries):
        try:
            r=urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=60)
            d=r.read()
            if r.headers.get('Content-Encoding')=='gzip': d=gzip.decompress(d)
            return d
        except urllib.error.HTTPError as e:
            if e.code==404: return None
            time.sleep(2**i)
        except Exception: time.sleep(2**i)
    return None
def main():
    u=json.load(open('/home/swing/swingquant/backend/data/universe.json'))['tickers']
    mp=os.path.join(B,'company_tickers.json')
    if not os.path.exists(mp): open(mp,'wb').write(get('https://www.sec.gov/files/company_tickers.json'))
    ct=json.load(open(mp)); t2c={}
    for v in ct.values(): t2c.setdefault(v['ticker'].upper(),v['cik_str'])
    log=open(os.path.join(B,'fetch_log.csv'),'a')
    for t in sorted(u):
        out=os.path.join(B,'facts',t+'.csv.gz')
        if os.path.exists(out): continue
        cik=t2c.get(t) or t2c.get(t.replace('.','-')) or t2c.get(t.replace('-','.'))
        if not cik: log.write(f'{t},nocik\n'); log.flush(); continue
        t0=time.time()
        d=get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json')
        if d is None: log.write(f'{t},fail\n'); log.flush(); continue
        raw=os.path.join(B,'raw',t+'.json'); open(raw,'wb').write(d); del d
        j=json.load(open(raw)); os.remove(raw)
        n=0
        with gzip.open(out+'.tmp','wt',newline='') as f:
            w=csv.writer(f); w.writerow(['tag','unit','start','end','val','fy','fp','form','filed','accn'])
            for ns,tags in TAGS.items():
                fz=j.get('facts',{}).get(ns,{})
                for tg in tags:
                    if tg not in fz: continue
                    for unit,arr in fz[tg]['units'].items():
                        for x in arr:
                            if x.get('form') not in FORMS: continue
                            w.writerow([tg,unit,x.get('start',''),x['end'],x['val'],x.get('fy',''),x.get('fp',''),x['form'],x['filed'],x.get('accn','')]); n+=1
        del j
        os.replace(out+'.tmp',out)
        log.write(f'{t},ok,{cik},{n}\n'); log.flush()
        time.sleep(max(0,0.3-(time.time()-t0)))
main()
