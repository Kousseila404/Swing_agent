"""Extrait un panel long (date, ticker) des snapshots live — colonnes utiles seulement."""
import sys, time, gc
import pandas as pd
sys.path.insert(0, "/home/swing/swingquant/backend")
from modules import universe_history as uh
OUT = "/tmp/claude-1000/-home-swing-swingquant/a57abce2-5a73-49c5-bc63-e73a4a7fdd25/scratchpad/agent_live/panel.pkl"
NUM = ["current_price","market_cap","ev_to_ebitda","ev_to_revenue","forward_pe","trailing_pe","price_to_book","peg_ratio",
       "free_cash_flow","operating_cash_flow","net_income","dividend_yield","dividends_paid","payout_ratio",
       "shares_outstanding","shares_outstanding_prev_year","gross_margin","operating_margin","profit_margin",
       "return_on_equity","return_on_assets","revenue_growth","earnings_growth","debt_to_equity","current_ratio","quick_ratio",
       "f_score","momentum_return_pct","momentum_risk_adjusted","momentum_high_52w_ratio","momentum_volatility_pct",
       "beta","data_quality","fundamentals_age_days","fundamentals_report_age_days",
       "quality_score","value_score","risk_score","momentum_score","piotroski_score","growth_score","revisions_score",
       "sentiment_score","insider_score","titan_composite_score","titan_composite_legacy","titan_v15_score","v15_value_core"]
STR = ["sector","industry","fetched_at","source_provider","titan_weight_mode"]
frames = []
t0 = time.time()
for d in uh.list_live_snapshots():
    tk = (uh.read_snapshot(d) or {}).get("tickers") or {}
    rows = []
    for t, r in tk.items():
        row = {"ticker": t}
        for c in NUM:
            v = r.get(c)
            try: row[c] = float(v) if v is not None else None
            except (TypeError, ValueError): row[c] = None
        for c in STR: row[c] = r.get(c)
        row["sr_fallback"] = bool(r.get("sector_relative_fallback"))
        rows.append(row)
    df = pd.DataFrame(rows); df["date"] = pd.Timestamp(d)
    for c in NUM: df[c] = df[c].astype("float32")
    frames.append(df); del tk, rows; gc.collect()
p = pd.concat(frames, ignore_index=True)
for c in STR: p[c] = p[c].astype("category")
p.to_pickle(OUT)
print(p.shape, round(time.time()-t0), "s", p.memory_usage(deep=True).sum()/1e6, "MB")
