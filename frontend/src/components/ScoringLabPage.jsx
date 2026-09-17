// ScoringLabPage — edge du scoring sur la fenêtre live (audit 2026-09-17).
//
// Lit /api/scoring/lab (produit par `python -m modules.scoring_lab`, run_titan.sh).
// Une table, un verdict, zéro décision automatique : le but est de voir si
// « +18 % » veut dire quelque chose une fois comparé à l'univers équipondéré,
// au bottom-20 et aux piliers seuls. Styles : styles/cockpit.css.

import { useMemo } from 'react';
import { useScoringLab } from '../hooks/useApi';
import { fmtNum } from '../utils/format';
import { PageSkeleton } from './common/Skeleton';

const GROUP_LABEL = {
  basket: 'Taille de panier (composite prod)',
  baseline: 'Références',
  pillar: 'Un seul pilier',
  profile: 'Profils de poids alternatifs (recalculés, prod inchangée)',
};
const GROUP_ORDER = ['basket', 'baseline', 'pillar', 'profile'];

function pct(v, digits = 1) {
  if (v == null) return '—';
  return `${v > 0 ? '+' : ''}${fmtNum(v, digits)}%`;
}
function tone(v) {
  if (v == null) return '';
  return v > 0 ? 'pos' : v < 0 ? 'neg' : '';
}

export default function ScoringLabPage() {
  const q = useScoringLab();
  const data = q.data;
  const groups = useMemo(() => {
    const rows = data?.rows || [];
    return GROUP_ORDER.map((g) => ({ id: g, label: GROUP_LABEL[g], rows: rows.filter((r) => r.group === g) }))
      .filter((g) => g.rows.length > 0);
  }, [data]);

  if (q.isLoading) return <PageSkeleton tiles={3} blockHeight={200} rows={4} />;
  if (q.isError || !data) {
    return (
      <div className="cockpit">
        <section className="cockpit-card">
          <div className="cockpit-card-body">
            <p className="cockpit-note">
              Le Scoring Lab n'a pas encore été calculé. Il tourne le dimanche dans <code>run_titan.sh</code>,
              ou à la main : <code>python -m modules.scoring_lab</code> (≈ 5 min).
            </p>
          </div>
        </section>
      </div>
    );
  }

  const v = data.verdict || {};
  const strengthTone = v.strength === 'strong' ? 'ok' : v.strength === 'weak' ? 'warn' : 'danger';

  return (
    <div className="cockpit animate-fade-in">
      <header className="cockpit-head">
        <div>
          <div className="cockpit-kicker">Scoring Lab · calculé {String(data.computed_at || '').slice(0, 16).replace('T', ' ')}</div>
          <h2>Le scoring a-t-il un edge sur la fenêtre live ?</h2>
          <div className="cockpit-sub">
            Depuis {data.live_start} · {data.rebalance} · {data.slippage_bps} bps · snapshots live uniquement (sans look-ahead)
          </div>
        </div>
        <div className="pill-row">
          <span className={`pill pill--${strengthTone}`}>
            edge vs univers : {pct(v.edge_vs_universe_pct)} · {v.strength === 'strong' ? 'fort' : v.strength === 'weak' ? 'faible' : 'nul'}
          </span>
        </div>
      </header>

      <section className="cockpit-card">
        <div className="cockpit-card-head"><h3><span aria-hidden="true">🧭</span>Lecture</h3></div>
        <div className="cockpit-card-body">
          <ul className="check-list">
            {(v.messages || []).map((m, i) => <li key={i}><span className="pill pill--info">{i + 1}</span><span>{m}</span></li>)}
          </ul>
          <p className="cockpit-note">{data.caveat}</p>
        </div>
      </section>

      {groups.map((g) => (
        <section className="cockpit-card" key={g.id}>
          <div className="cockpit-card-head"><h3>{g.label}</h3></div>
          <div className="cockpit-card-body cockpit-card-body--flush">
            <table className="cockpit-table">
              <thead>
                <tr>
                  <th>Variante</th><th className="right">Rendement</th><th className="right">vs univers</th>
                  <th className="right">vs top-20</th><th className="right">Hit</th><th className="right">Max DD</th>
                  <th className="right">Sharpe</th><th className="right">Pire sem.</th><th className="right">Périodes</th>
                </tr>
              </thead>
              <tbody>
                {g.rows.map((r) => (
                  <tr key={r.id}>
                    <td className="ticker">{r.label}{r.error ? ` (erreur : ${r.error})` : ''}</td>
                    <td className={`num right ${tone(r.return_pct)}`}>{pct(r.return_pct)}</td>
                    <td className={`num right ${tone(r.excess_vs_universe_pct)}`}>{pct(r.excess_vs_universe_pct)}</td>
                    <td className={`num right ${tone(r.excess_vs_top20_pct)}`}>{pct(r.excess_vs_top20_pct)}</td>
                    <td className="num right">{r.hit_rate != null ? `${fmtNum(r.hit_rate * 100, 0)}%` : '—'}</td>
                    <td className="num right neg">{r.max_drawdown_pct != null ? `${fmtNum(r.max_drawdown_pct, 1)}%` : '—'}</td>
                    <td className="num right">{r.sharpe_weekly_ann != null ? fmtNum(r.sharpe_weekly_ann, 2) : '—'}</td>
                    <td className="num right neg">{r.worst_week_pct != null ? `${fmtNum(r.worst_week_pct, 1)}%` : '—'}</td>
                    <td className="num right">{r.periods ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}
