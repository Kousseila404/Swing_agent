"""Features + utilitaires communs (panel snapshots live + panel de prix). Lecture seule."""
import math, numpy as np, pandas as pd
D = "/tmp/claude-1000/-home-swing-swingquant/a57abce2-5a73-49c5-bc63-e73a4a7fdd25/scratchpad/agent_live/"
P = pd.read_pickle(D + "panel.pkl")
C = pd.read_pickle(D + "closes.pkl").astype("float64")
DAYS = C.index
FIN = "Financial Services"

def weekly_dates():
    out, last = [], None
    n = P.groupby("date").size()
    for d in sorted(n[n >= 450].index):   # exclut 2026-07-14→17 (univers tronqué 96-370 tickers)
        d = pd.Timestamp(d)
        if last is None or (d - last).days >= 7: out.append(d); last = d
    return out

def i0(d):  # 1re clôture STRICTEMENT après la date du snapshot
    return DAYS.searchsorted(pd.Timestamp(d), side="right")

def pos(x): return x.where(x > 0)

def spct(x, sec, min_n=12):
    """percentile intra-secteur (0-1), fallback global si < min_n valeurs (= règle prod)."""
    x = pd.to_numeric(x, errors="coerce")
    n = x.notna().groupby(sec, observed=True).transform("sum")
    r = (x.groupby(sec, observed=True).rank() - 0.5) / n       # = formule prod (less + 0.5*equal)/n
    g = (x.rank() - 0.5) / x.notna().sum()
    return r.where(n >= min_n, g)

def avg(cols, min_k):
    df = pd.concat(cols, axis=1); k = df.notna().sum(axis=1)
    return df.mean(axis=1).where(k >= min_k)

def frame(d):
    """Une date de snapshot → DataFrame indexé ticker avec features brutes (plus haut = mieux)."""
    s = P[P.date == d].set_index("ticker").copy()
    s = s[~s.index.duplicated()]
    sec = s.sector.astype(str)
    mc = s.market_cap.astype("float64")
    f = pd.DataFrame(index=s.index); f["sector"] = sec
    f["fcf_yield"] = s.free_cash_flow / mc
    f["ocf_yield"] = s.operating_cash_flow / mc
    f["ebitda_ev"] = 1 / pos(s.ev_to_ebitda)            # = -EV/EBITDA en rang (positifs seulement)
    f["fwd_ep"] = 1 / pos(s.forward_pe)
    f["ttm_ep_ni"] = s.net_income / mc                  # E/P avec pertes (négatif) incluses
    f["bp"] = 1 / pos(s.price_to_book)
    f["sales_ev"] = 1 / pos(s.ev_to_revenue)
    f["div_yield"] = (s.dividends_paid.fillna(0) / mc).where(s.dividends_paid.notna() | s.dividend_yield.notna())
    iss = (s.shares_outstanding / s.shares_outstanding_prev_year - 1)
    f["net_buyback"] = -iss.where(iss.abs() < 1)
    f["sh_yield"] = f.div_yield + f.net_buyback
    f["gross_margin"] = s.gross_margin; f["op_margin"] = s.operating_margin
    f["roe"] = s.return_on_equity; f["roa"] = s.return_on_assets; f["f_score"] = s.f_score
    f["low_de"] = -s.debt_to_equity
    ok = (s.net_income.abs() > 0) & (s.profit_margin.abs() > 0.005) & (s.return_on_assets.abs() > 0.001)
    rev = (s.net_income / s.profit_margin).where(ok); assets = (s.net_income / s.return_on_assets).where(ok)
    gpa = (s.gross_margin * rev / assets).where((rev > 0) & (assets > 0))
    f["gpa"] = gpa.where(gpa.between(-2, 5))
    f["low_accruals"] = (-(s.net_income - s.operating_cash_flow) / assets).where(assets > 0)
    f["mom_stored_8_1"] = s.momentum_return_pct
    f["high52_stored"] = s.momentum_high_52w_ratio
    f["low_vol_stored"] = -s.momentum_volatility_pct
    f["low_beta"] = -s.beta
    # momentum / vol depuis le panel de prix, mesurés à la dernière clôture <= date (pas de look-ahead)
    j = DAYS.searchsorted(d, side="right") - 1
    def ret(a, b):
        if j - a < 0: return pd.Series(np.nan, index=s.index)
        return (C.iloc[j - b] / C.iloc[j - a] - 1).reindex(s.index)
    f["mom_6_1"] = ret(126, 21); f["mom_3_1"] = ret(63, 21)
    f["rev_1m"] = -ret(21, 0)
    if j >= 63:
        lr = np.log(C.iloc[j - 63:j + 1]).diff().iloc[1:]
        f["low_vol63"] = -(lr.std() * math.sqrt(252)).reindex(s.index)
    else: f["low_vol63"] = np.nan
    for c in ["quality_score","value_score","risk_score","momentum_score","piotroski_score","growth_score",
              "revisions_score","titan_composite_score"]:
        f[c] = s[c]
    f["_ev_ebitda"] = s.ev_to_ebitda; f["_fpe"] = s.forward_pe; f["_pb"] = s.price_to_book
    return f

def v15(f, fields=("ebitda_ev","fwd_ep","bp"), gate=0.20, cap=0.40, min_k=2, fin_variant=False):
    """Réplique vectorisée de compute_v15_scores (percentile 0-1). gate None = pas de filtre."""
    sec = f.sector
    cols = [spct(f[c], sec) for c in fields]
    core = avg(cols, min_k)
    if fin_variant:
        fin = sec.eq(FIN)
        alt = avg([spct(f["bp"], sec), spct(f["ttm_ep_ni"], sec)], 2)
        core = core.where(~fin, alt)
    if gate is None: return core
    q = spct(f.quality_score, sec); p = (f.piotroski_score.rank() - 0.5) / f.piotroski_score.notna().sum()
    ok = (q > gate) & (p > gate)
    return core.where(ok, np.minimum(core, cap))

def nw_t(s, lag):
    s = pd.Series(s).dropna().values; n = len(s)
    if n < 4: return float("nan")
    e = s - s.mean(); v = (e @ e) / n
    for L in range(1, lag + 1): v += 2 * (1 - L / (lag + 1)) * (e[L:] @ e[:-L]) / n
    return s.mean() / math.sqrt(v / n) if v > 0 else float("nan")
