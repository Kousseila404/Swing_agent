import glob, os, json
import pandas as pd, numpy as np
D = os.path.dirname(os.path.abspath(__file__))
fs = sorted(glob.glob(f'{D}/raw/batch_*.csv.gz'))
parts = [pd.read_csv(f, index_col=0, parse_dates=True).astype('float32') for f in fs]
px = pd.concat(parts, axis=1)
px = px.loc[:, ~px.columns.duplicated()]
px = px.dropna(how='all', axis=1)
px.index.name = 'date'
px = px[px.index.dayofweek < 5]
# drop rows where SPY missing (holidays artefacts)
px = px[px['SPY'].notna()]
sec = json.load(open(f'{D}/sectors.json'))
missing = [t for t in sec if t not in px.columns]
print('shape', px.shape, 'missing', missing)
print('first valid date counts by year:', px.apply(lambda s: s.first_valid_index()).dt.year.value_counts().sort_index().to_dict())
out = os.path.join(os.path.dirname(D), 'shared', 'prices_daily.csv.gz')
px.to_csv(out + '.tmp', compression='gzip', float_format='%.4f')
os.replace(out + '.tmp', out)
print('written', out)
