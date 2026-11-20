import pandas as pd, numpy as np
p=pd.read_pickle('panel.pkl'); C=pd.read_pickle('closes.pkl')
s=p[p.date==p.date.max()].copy()
s['fa']=pd.to_datetime(s.fetched_at.astype(str), errors='coerce', utc=True).dt.tz_localize(None).dt.normalize()
print(s.fa.value_counts().sort_index().tail(10))
best = s.fa.max()
for fa in sorted(s.fa.dropna().unique())[-3:]:
    g=s[s.fa==fa].set_index('ticker')
    # dernière clôture disponible <= fa (ou veille)
    for lag_end in (0,1):
        i=C.index.searchsorted(fa, side='right')-1-lag_end
        n_avail=i+1
        res={}
        for skip in (0,21):
            for L in (125,150,168,180,188,n_avail-1):
                if i-L<0: continue
                r=(C.iloc[i-skip]/C.iloc[i-L]-1)*100
                d=(g.momentum_return_pct - r.reindex(g.index)).abs()
                res[(skip,L)]=round(float((d<0.5).mean()),2)
        hi=(C.iloc[i]/C.iloc[:i+1].max()).reindex(g.index)
        print(str(fa)[:10], 'end', C.index[i].date(), 'n_avail', n_avail, 'n', len(g), res,
              '52wh match(<1%)', round(float(((g.momentum_high_52w_ratio-hi).abs()<0.01).mean()),2))
