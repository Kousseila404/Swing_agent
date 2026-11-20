# Vérifie que la réplique vectorisée == compute_v15_scores (prod) sur 3 dates
import sys; sys.path.insert(0, "/home/swing/swingquant/backend")
from lib import *
from modules.sector_metrics._scoring import compute_v15_scores
for d in weekly_dates()[::12]:
    s = P[P.date == d].set_index("ticker")
    rows = {t: {"sector": r.sector, "ev_to_ebitda": r.ev_to_ebitda, "forward_pe": r.forward_pe, "price_to_book": r.price_to_book,
                "quality_score": r.quality_score, "piotroski_score": r.piotroski_score} for t, r in s.iterrows()}
    prod = pd.Series({k: v["titan_v15_score"] for k, v in compute_v15_scores(rows).items()}, dtype=float)
    gate = pd.Series({k: v["v15_gate"] for k, v in compute_v15_scores(rows).items()})
    mine = v15(frame(d)) * 100
    print(d.date(), "spearman", round(prod.corr(mine, method="spearman"), 4), "n", prod.notna().sum(), mine.notna().sum(),
          "gated-out frac", round(1 - gate.mean(), 3), "max|diff|", round((prod - mine).abs().max(), 2))
