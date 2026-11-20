"""Fetch missing tickers and predecessor CIKs (holding-company reorganisations) and merge into facts/<T>.csv.gz."""
import sys, os, json, csv, gzip
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
spec=importlib.util.spec_from_file_location('f', os.path.join(os.path.dirname(os.path.abspath(__file__)),'fetch_sec.py'))
src=open(spec.origin).read().replace('\nmain()\n','\n')
g={}; exec(compile(src,'fetch_sec','exec'),g)
B=g['B']; get=g['get']; TAGS=g['TAGS']; FORMS=g['FORMS']
EXTRA=[('AVB',915912),('EQR',906107),('EA',712515),('WBD',1437107),('KVUE',1944048),('PSKY',2041610),('PSKY',813828),
('APA',6769),('APO',1411494),('AVGO',1441634),('AVGO',1649338),('BG',1144519),('BLK',1364742),('CI',701221),('DIS',1001039),
('GOOGL',1288776),('GOOG',1288776),('MDT',64670),('ETN',31277),('STE',815065),('STE',1624899),('XOM',34088),('MRVL',1058057),('ICE',1174746),('TPL',97517)]
log=open(os.path.join(B,'fetch_log_extra.csv'),'a')
import time
for t,cik in EXTRA:
    d=get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json')
    if d is None: log.write(f'{t},{cik},fail\n'); log.flush(); continue
    j=json.loads(d); del d
    rows=[]
    for ns,tags in TAGS.items():
        fz=j.get('facts',{}).get(ns,{})
        for tg in tags:
            if tg not in fz: continue
            for unit,arr in fz[tg]['units'].items():
                for x in arr:
                    if x.get('form') not in FORMS: continue
                    rows.append([tg,unit,x.get('start',''),x['end'],x['val'],x.get('fy',''),x.get('fp',''),x['form'],x['filed'],x.get('accn','')])
    del j
    out=os.path.join(B,'facts',t+'.csv.gz'); old=[]
    if os.path.exists(out):
        with gzip.open(out,'rt') as f: old=list(csv.reader(f))[1:]
    with gzip.open(out+'.tmp','wt',newline='') as f:
        w=csv.writer(f); w.writerow(['tag','unit','start','end','val','fy','fp','form','filed','accn']); w.writerows(old+rows)
    os.replace(out+'.tmp',out)
    log.write(f'{t},{cik},ok,{len(rows)}\n'); log.flush(); time.sleep(0.4)
