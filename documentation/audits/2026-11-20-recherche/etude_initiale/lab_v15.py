import sys
sys.path.insert(0, "/home/swing/swingquant/backend")
from modules import scoring_lab as sl
from modules.backtest import run_titan_top_n
for vid in ("universe_ew", "top20", "v15_top30_cap4", "v15_top20"):
    v = next(x for x in sl.VARIANTS if x["id"] == vid)
    fn = sl.make_v15_rank_fn(v["v15_cap"]) if "v15_cap" in v else sl.make_rank_fn(field=v.get("field"), weights=v.get("weights"))
    res = run_titan_top_n(top_n=v["top_n"], benchmark=None, slippage_bps=10.0, publication_lag_days=5, rank_fn=fn)
    st = sl._live_stats(res.to_dict().get("periods") or [])
    print(vid, {k: st.get(k) for k in ("return_pct","periods","hit_rate","max_drawdown_pct","sharpe_weekly_ann","start","end")}, flush=True)
