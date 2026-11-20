"""Price-only factor study on current S&P500 universe (survivorship-biased).
Monthly (default) or weekly rebalance:  python factors.py [monthly|weekly]
"""
import os, sys, json
import numpy as np, pandas as pd

D = os.path.dirname(os.path.abspath(__file__))
FREQ = sys.argv[1] if len(sys.argv) > 1 else 'monthly'
ONLY = sys.argv[2].split(',') if len(sys.argv) > 2 else None
COST = 0.0010  # 10 bps per unit of one-way turnover
START = '2005-01-01'

px = pd.read_csv(os.path.join(os.path.dirname(D), 'shared', 'prices_daily.csv.gz'), index_col=0, parse_dates=True).astype('float32')
sec = json.load(open(f'{D}/sectors.json'))
spy = px['SPY'].astype('float64'); rsp = px['RSP'].astype('float64')
stocks = [t for t in sec if t in px.columns]
P = px[stocks].astype('float64')
sectors = pd.Series({t: sec[t] for t in stocks})
R = P.pct_change(fill_method=None)
rs = spy.pct_change()

# ---------- rebalance dates ----------
idx = P.index
if FREQ == 'monthly':
    reb = pd.Series(idx, index=idx).groupby(idx.to_period('M')).last()
else:
    reb = pd.Series(idx, index=idx).groupby(idx.to_period('W-FRI')).last()
reb = pd.DatetimeIndex(reb.values)
reb = reb[(reb >= pd.Timestamp(START) - pd.Timedelta(days=40))]
# drop last incomplete period (no full forward period)
reb = reb[:-1] if FREQ == 'monthly' else reb
pos = idx.get_indexer(reb)

# ---------- rolling daily stats (computed once, sampled at rebalance) ----------
vol252 = R.rolling(252, min_periods=200).std()
cov = R.rolling(252, min_periods=200).cov(rs)
beta252 = cov.div(rs.rolling(252, min_periods=200).var(), axis=0)
hi252 = P.rolling(252, min_periods=200).max()
ma200_spy = spy.rolling(200).mean()

def lag(k):
    return P.shift(k)

F = {}
F['mom12_1'] = lag(21) / lag(252) - 1
F['mom12_0'] = P / lag(252) - 1           # sans exclusion du dernier mois
F['mom6_1'] = lag(21) / lag(126) - 1
F['mom3_1'] = lag(21) / lag(63) - 1
F['mom12_1_voladj'] = F['mom12_1'] / (vol252 * np.sqrt(252))
F['hi52'] = P / hi252
F['rev1m'] = -(P / lag(21) - 1)
F['lowvol'] = -vol252
F['lowbeta'] = -beta252
F = {k: v.iloc[pos] for k, v in F.items()}
del vol252, cov, beta252, hi252

# residual momentum (Blitz-Huij-Martens): monthly returns, 36m regression on SPY (min 24),
# residual return over m-11..m-1 (skip last month) / residual std over the window
Pm = P.iloc[pos]; Sm = spy.iloc[pos]
if FREQ == 'monthly':
    rm = Pm.pct_change(fill_method=None); sm = Sm.pct_change()
    resmom = pd.DataFrame(np.nan, index=Pm.index, columns=Pm.columns)
    Y = rm.values; X = sm.values
    for i in range(36, len(Pm)):
        y = Y[i-35:i+1]; x = X[i-35:i+1]
        ok = ~np.isnan(y)
        n = ok.sum(0)
        xm = np.where(ok, x[:, None], np.nan)
        mx = np.nanmean(xm, 0); my = np.nanmean(y, 0)
        b = np.nansum((xm - mx) * (y - my), 0) / np.nansum((xm - mx) ** 2, 0)
        a = my - b * mx
        e = y - (a + b * x[:, None])
        sd = np.nanstd(e, 0, ddof=2)
        num = np.nansum(e[-12:-1], 0)  # months m-11..m-1, skipping month m
        v = num / sd
        v[(n < 24) | np.isnan(e[-12:-1]).any(0)] = np.nan
        resmom.iloc[i] = v
    F['resmom12_1'] = resmom
else:
    # weekly: reuse monthly values forward-filled is overkill; skip
    pass

# sector momentum: mean mom12_1 of sector, assigned to each stock
sm12 = F['mom12_1'].T.groupby(sectors).transform('mean').T
F['secmom12_1'] = sm12

# ---------- universe & forward returns ----------
fwd = Pm.shift(-1) / Pm - 1
# eligibility: price today, 252d of history, forward return defined
elig = Pm.notna() & lag(252).iloc[pos].notna() & fwd.notna()
keep = Pm.index >= pd.Timestamp(START)
Pm, fwd, elig = Pm[keep], fwd[keep], elig[keep]
F = {k: v[keep].where(elig) for k, v in F.items()}
dates = Pm.index
regime_bull = (spy.iloc[pos][keep] > ma200_spy.iloc[pos][keep]).values
ppy = 12 if FREQ == 'monthly' else 52

def pct_rank(df):
    return df.rank(axis=1, pct=True)

def sec_rank(df):
    return df.T.groupby(sectors).rank(pct=True).T

NEUTRAL_SKIP = {'secmom12_1'}
ranks = {}
for k, v in F.items():
    if ONLY and k not in ONLY: continue
    ranks[k] = pct_rank(v)
    if k not in NEUTRAL_SKIP:
        ranks[k + '_sn'] = sec_rank(v)

# EW universe benchmark
ew = fwd.where(elig).mean(axis=1)

def nw_t(x, L=None):
    x = np.asarray(x, float); x = x[~np.isnan(x)]; n = len(x)
    if L is None: L = 3 if FREQ == 'monthly' else 8
    m = x.mean(); e = x - m
    g = e @ e / n
    for l in range(1, L + 1):
        g += 2 * (1 - l / (L + 1)) * (e[l:] @ e[:-l]) / n
    return m / np.sqrt(g / n)

def top30_weights(score_row, sec_s, n=30, cap=4):
    s = score_row.dropna().sort_values(ascending=False)
    cnt = {}; pick = []
    for t in s.index:
        c = sec_s[t]
        if cnt.get(c, 0) < cap:
            pick.append(t); cnt[c] = cnt.get(c, 0) + 1
            if len(pick) == n: break
    return pick

def run(rk):
    ic = []; q5 = []; t30 = []; to5 = []; to30 = []
    prev5 = pd.Series(dtype=float); prev30 = pd.Series(dtype=float)
    for i, d in enumerate(dates):
        r = rk.loc[d]; f = fwd.loc[d]
        ok = r.notna() & f.notna()
        if ok.sum() < 50:
            ic.append(np.nan); q5.append(np.nan); t30.append(np.nan); to5.append(np.nan); to30.append(np.nan); continue
        rr = r[ok]; ff = f[ok]
        ic.append(np.corrcoef(rr.rank(), ff.rank())[0, 1])
        top = rr.index[rr >= rr.quantile(0.8)]
        w5 = pd.Series(1 / len(top), index=top)
        q5.append(ff[top].mean())
        to5.append(w5.sub(prev5, fill_value=0).abs().sum() / (2 if len(prev5) else 1))
        prev5 = w5
        pk = top30_weights(rr, sectors)
        w30 = pd.Series(1 / len(pk), index=pk)
        t30.append(ff[pk].mean())
        to30.append(w30.sub(prev30, fill_value=0).abs().sum() / (2 if len(prev30) else 1))
        prev30 = w30
    out = pd.DataFrame({'ic': ic, 'q5': q5, 't30': t30, 'to5': to5, 'to30': to30}, index=dates)
    # one-way turnover (fraction of book replaced) * 2 sides * 10bps
    out['q5_x'] = out.q5 - ew - 2 * COST * out.to5
    out['t30_x'] = out.t30 - ew - 2 * COST * out.to30
    out['t30_net'] = out.t30 - 2 * COST * out.to30
    return out

def ann(x):
    x = x.dropna(); return (1 + x).prod() ** (ppy / len(x)) - 1 if len(x) else np.nan

def maxdd(x):
    w = (1 + x.fillna(0)).cumprod(); return (w / w.cummax() - 1).min()

def sharpe(x):
    x = x.dropna(); return x.mean() / x.std() * np.sqrt(ppy)

SUB = [('2005-09', '2005', '2009'), ('2010-14', '2010', '2014'), ('2015-19', '2015', '2019'),
       ('2020-22', '2020', '2022'), ('2023-26', '2023', '2026')]

rows = []; monthly_x = {}; yearly = {}
for k, rk in ranks.items():
    o = run(rk)
    monthly_x[k] = o.q5_x
    yr = o.groupby(o.index.year).agg(ic=('ic', 'mean'), t30_x=('t30_x', lambda s: (1 + s).prod() - 1), q5_x=('q5_x', lambda s: (1 + s).prod() - 1))
    yearly[k] = yr
    row = dict(factor=k, n=int(o.ic.notna().sum()), ic=o.ic.mean(), ic_t=nw_t(o.ic),
               ic_recent=o.ic['2015':].mean(), ic_t_recent=nw_t(o.ic['2015':]),
               q5_x_ann=ann(o.q5_x), t30_x_ann=ann(o.t30_x), t30_x_ann_2015=ann(o.t30_x['2015':]),
               t30_x_t=nw_t(o.t30_x), ir30=sharpe(o.t30_x), sharpe30=sharpe(o.t30_net),
               ew_sharpe=sharpe(ew[o.index]), mdd30=maxdd(o.t30_net), mdd30_rel=maxdd(o.t30_x),
               to30=o.to30.mean(), hit_years=(yr.t30_x > 0).mean(), hit_years_ic=(yr.ic > 0).mean())
    for lab, a, b in SUB:
        s = o.loc[a:b]
        row[f'ic_{lab}'] = s.ic.mean(); row[f'x30_{lab}'] = ann(s.t30_x)
    rb = pd.Series(regime_bull, index=o.index)
    row['ic_bull'] = o.ic[rb].mean(); row['ic_bear'] = o.ic[~rb].mean()
    row['x30_bull'] = o.t30_x[rb].mean() * ppy; row['x30_bear'] = o.t30_x[~rb].mean() * ppy
    rows.append(row)
    print(k, f"IC {row['ic']:.4f} t {row['ic_t']:.2f} x30 {row['t30_x_ann']:.4f} hit {row['hit_years']:.2f}", flush=True)

res = pd.DataFrame(rows).set_index('factor')
suf = '' if FREQ == 'monthly' else '_weekly'
res.to_csv(f'{D}/factor_summary{suf}.csv', float_format='%.5f')
pd.DataFrame(monthly_x).to_csv(f'{D}/factor_{"monthly" if FREQ=="monthly" else "weekly"}_returns.csv', float_format='%.6f')
pd.concat({k: v for k, v in yearly.items()}, names=['factor', 'year']).to_csv(f'{D}/factor_yearly{suf}.csv', float_format='%.5f')
pd.DataFrame(monthly_x).corr().to_csv(f'{D}/factor_corr{suf}.csv', float_format='%.3f')
pd.DataFrame({'ew': ew, 'bull': regime_bull}).to_csv(f'{D}/ew_universe{suf}.csv')

if FREQ == 'monthly' and not ONLY:
    long = []
    for k, rk in ranks.items():
        s = rk.stack(); s.name = k; long.append(s)
    L = pd.concat(long, axis=1).astype('float32')
    L.index.names = ['date', 'ticker']
    L.to_csv(f'{D}/factor_ranks_monthly.csv.gz', float_format='%.4f', compression='gzip')
    # survivorship: EW universe (monthly rebalanced, all eligible incl. <252d? use all with price) vs RSP
    allfwd = (Pm.shift(-1) / Pm - 1)
    ew_all = allfwd.mean(axis=1)
    rspm = rsp.iloc[pos][keep]; rspf = rspm.shift(-1) / rspm - 1
    spym = spy.iloc[pos][keep]; spyf = spym.shift(-1) / spym - 1
    sv = pd.DataFrame({'ew_recon': ew_all, 'rsp': rspf, 'spy': spyf}).dropna()
    svy = sv.groupby(sv.index.year).apply(lambda g: (1 + g).prod() - 1)
    svy['gap'] = svy.ew_recon - svy.rsp
    svy.to_csv(f'{D}/survivorship_yearly.csv', float_format='%.4f')
    print(svy.round(4).to_string())
    for lab, a, b in SUB:
        s = sv.loc[a:b]; print('SURV', lab, 'ew', round(ann(s.ew_recon), 4), 'rsp', round(ann(s.rsp), 4), 'gap', round(ann(s.ew_recon) - ann(s.rsp), 4))
    print('SURV all ew', ann(sv.ew_recon), 'rsp', ann(sv.rsp), 'spy', ann(sv.spy))
print(res.round(4).T.to_string())
