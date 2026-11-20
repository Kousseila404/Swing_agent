"""Monthly prices via yfinance: split-adjusted Close, Adj Close (total return), Stock Splits. Batches of 40."""
import json, time, os, pandas as pd, yfinance as yf
B='/tmp/claude-1000/-home-swing-swingquant/a57abce2-5a73-49c5-bc63-e73a4a7fdd25/scratchpad/agent_pit'
u=json.load(open('/home/swing/swingquant/backend/data/universe.json'))['tickers']
tk=sorted(u)+['SPY','RSP']
out={'close':[],'adj':[],'split':[]}
for i in range(0,len(tk),40):
    b=tk[i:i+40]
    for a in range(4):
        try:
            d=yf.download(b,start='2008-01-01',end='2026-11-21',interval='1mo',auto_adjust=False,actions=True,progress=False,threads=False,group_by='column')
            break
        except Exception as e:
            print('retry',e); time.sleep(10*(a+1))
    out['close'].append(d['Close']); out['adj'].append(d['Adj Close']); out['split'].append(d['Stock Splits'] if 'Stock Splits' in d else pd.DataFrame(index=d.index,columns=b))
    print(i,d.shape,flush=True); time.sleep(3)
for k,v in out.items():
    pd.concat(v,axis=1).to_csv(os.path.join(B,f'px_monthly_{k}.csv.gz'))
