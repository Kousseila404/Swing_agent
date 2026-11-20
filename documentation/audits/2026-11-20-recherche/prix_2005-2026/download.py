import json, time, os, sys
import pandas as pd, numpy as np, yfinance as yf
sec = json.load(open('sectors.json'))
tickers = sorted(sec) + ['SPY', 'RSP']
os.makedirs('raw', exist_ok=True)
B = 40
for i in range(0, len(tickers), B):
    out = f'raw/batch_{i//B:03d}.csv.gz'
    if os.path.exists(out): continue
    batch = tickers[i:i+B]
    for attempt in range(3):
        try:
            df = yf.download(batch, start='2003-01-01', end='2026-11-21', auto_adjust=True,
                             progress=False, threads=False, group_by='column')
            c = df['Close']
            if isinstance(c, pd.Series): c = c.to_frame(batch[0])
            c = c.astype('float32')
            if c.notna().sum().sum() == 0: raise RuntimeError('empty')
            c.to_csv(out + '.tmp', compression='gzip'); os.rename(out + '.tmp', out)
            print(i, c.shape, 'missing:', [t for t in batch if t not in c or c[t].notna().sum() == 0], flush=True)
            break
        except Exception as e:
            print('err', i, e, flush=True); time.sleep(20 * (attempt + 1))
    time.sleep(4)
