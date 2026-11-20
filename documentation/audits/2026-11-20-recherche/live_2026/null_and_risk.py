"""(1) distribution nulle : paniers top-30 cap 4 sur un score aléatoire PERSISTANT par ticker ;
   (2) IC des composantes du pilier risk (current/quick ratio) depuis le panel."""
from lib import *
import wf  # réutilise F, run (exécute wf : affichage ignoré)
rng = np.random.default_rng(0); allt = sorted(set().union(*[set(S.index) for S in wf.F.values()]))
ex = []
for i in range(300):
    z = pd.Series(rng.random(len(allt)), index=allt)
    for d in wf.dates: wf.F[d]["rnd"] = z.reindex(wf.F[d].index)
    s, ew = wf.run("rnd"); ex.append(((1 + s).prod() - (1 + ew).prod()) * 100)
ex = np.array(ex); print("\nNULL aléatoire persistant top30 cap4 : excès moyen %.2f, sd %.2f, p5 %.2f, p95 %.2f" % (ex.mean(), ex.std(), *np.percentile(ex, [5, 95])))
for v in (3.97, 10.04, -12.71): print(f"  rang percentile d'un excès {v:+.2f} : {(ex < v).mean():.2f}")
rows = []
for d in wf.dates:
    a = i0(d)
    if a + 21 >= len(DAYS): continue
    s = P[P.date == d].set_index("ticker"); s = s[~s.index.duplicated()]
    y = (C.iloc[a + 21] / C.iloc[a] - 1).reindex(s.index); sec = s.sector.astype(str)
    ysn = y - y.groupby(sec).transform("mean")
    for c in ["current_ratio", "quick_ratio", "debt_to_equity"]:
        x = s[c]; m = x.notna() & y.notna()
        rows.append(dict(c=c, ic_sn=spct(x[m], sec[m], 5).corr(ysn[m].rank())))
r = pd.DataFrame(rows).groupby("c").ic_sn.agg(["mean", lambda g: nw_t(g, 4)]); print("\nIC21 sn composantes risk (signe brut, plus haut = valeur haute):\n", r.round(3))
