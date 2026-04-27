import { useMemo } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { usePerformanceMetrics } from '../hooks/useApi';
import { histogramBins, computeUnderwater, INITIAL_CAPITAL } from '../utils/performance';

// Gradient statique hors render : évite de reparser le <defs> à chaque tick RQ.
const EQ_GRAD_DEFS = (
  <defs>
    <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%"   stopColor="var(--accent-primary)" stopOpacity={0.4} />
      <stop offset="100%" stopColor="var(--accent-primary)" stopOpacity={0} />
    </linearGradient>
  </defs>
);

function Kpi({ label, value, sub, subColor }) {
  return (
    <div className="status-chip" style={{ minWidth: 150 }}>
      <span className="sc-lbl">{label}</span>
      <span className="sc-val">{value}</span>
      {sub && <span style={{ fontSize: '0.72rem', color: subColor || 'var(--text-muted)' }}>{sub}</span>}
    </div>
  );
}

function ratingColor(name, v) {
  if (v == null) return 'var(--text-muted)';
  if (name === 'sharpe')  return v >= 1 ? 'var(--success)' : v >= 0.5 ? 'var(--warning)' : 'var(--danger)';
  if (name === 'sortino') return v >= 1.5 ? 'var(--success)' : v >= 0.7 ? 'var(--warning)' : 'var(--danger)';
  if (name === 'calmar')  return v >= 0.5 ? 'var(--success)' : v >= 0.25 ? 'var(--warning)' : 'var(--danger)';
  if (name === 'pf')      return v >= 1.5 ? 'var(--success)' : v >= 1.0 ? 'var(--warning)' : 'var(--danger)';
  return 'var(--text-muted)';
}

export default function PerformancePage() {
  const { data, isLoading, isError, refetch, isFetching } = usePerformanceMetrics();

  const equityCurve = useMemo(() => {
    if (!data?.equity_by_date) return [];
    return data.equity_by_date.map(([d, v]) => ({
      date: String(d).slice(0, 10),
      equity: Number(v),
    }));
  }, [data]);

  const underwater = useMemo(() => computeUnderwater(data?.equity_by_date || []), [data]);

  const pnlHist = useMemo(() => histogramBins(data?.pnl_raw || [], 20), [data]);

  const tickerRows = useMemo(() => {
    const obj = data?.per_ticker_pnl || {};
    return Object.entries(obj)
      .map(([ticker, stats]) => ({ ticker, ...stats }))
      .sort((a, b) => (b.total_pnl ?? 0) - (a.total_pnl ?? 0));
  }, [data]);

  if (isLoading) return <div className="loading-pulse"><div className="spinner" /><p>Calcul métriques…</p></div>;
  if (isError || !data) return (
    <div className="api-error">
      <div className="api-error-icon">⚠️</div>
      <p>Erreur /api/performance_metrics — vérifiez FastAPI + data/trade_journal.csv</p>
      <button className="action-btn" style={{ maxWidth: 200 }} onClick={() => refetch()}>Réessayer</button>
    </div>
  );

  const totalClosed = data.total_closed || 0;
  const hasData = totalClosed > 0;

  return (
    <div className="control-panel animate-fade-in">
      {/* ── KPIs ligne 1 : capital / PnL / win rate / profit factor ── */}
      <div className="cp-status-bar">
        <Kpi
          label="Capital"
          value={`$${(data.capital ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}`}
          sub={`${data.pnl_total >= 0 ? '+' : ''}${(data.pnl_total ?? 0).toFixed(2)}$`}
          subColor={data.pnl_total >= 0 ? 'var(--success)' : 'var(--danger)'}
        />
        <Kpi
          label="Trades clôturés"
          value={totalClosed}
          sub={`${data.wins}W / ${data.losses}L`}
        />
        <Kpi
          label="Win Rate"
          value={`${(data.win_rate ?? 0).toFixed(1)}%`}
          subColor={data.win_rate >= 50 ? 'var(--success)' : 'var(--warning)'}
        />
        <Kpi
          label="Profit Factor"
          value={(data.profit_factor ?? 0).toFixed(2)}
          subColor={ratingColor('pf', data.profit_factor)}
        />
        <Kpi
          label="Ouverts"
          value={data.open_count ?? 0}
        />
        <button className="scan-filter-btn" onClick={() => refetch()} disabled={isFetching}>
          {isFetching ? '⏳' : '🔄'}
        </button>
      </div>

      {/* ── KPIs ligne 2 : ratios risque-ajusté ── */}
      <div className="cp-status-bar">
        <Kpi
          label="Sharpe"
          value={(data.sharpe ?? 0).toFixed(2)}
          subColor={ratingColor('sharpe', data.sharpe)}
          sub="ann. (252j)"
        />
        <Kpi
          label="Sortino"
          value={(data.sortino ?? 0).toFixed(2)}
          subColor={ratingColor('sortino', data.sortino)}
          sub="downside only"
        />
        <Kpi
          label="Calmar"
          value={(data.calmar ?? 0).toFixed(2)}
          subColor={ratingColor('calmar', data.calmar)}
          sub="CAGR / MaxDD"
        />
        <Kpi
          label="Max Drawdown"
          value={`${(data.max_drawdown_pct ?? 0).toFixed(2)}%`}
          subColor={(data.max_drawdown_pct ?? 0) > -5 ? 'var(--success)' : (data.max_drawdown_pct ?? 0) > -15 ? 'var(--warning)' : 'var(--danger)'}
        />
        <Kpi
          label="Expectancy"
          value={`${(data.expectancy ?? 0).toFixed(2)}$`}
          subColor={data.expectancy >= 0 ? 'var(--success)' : 'var(--danger)'}
          sub="par trade"
        />
      </div>

      {/* ── KPIs ligne 3 : moyennes et streak ── */}
      <div className="cp-status-bar">
        <Kpi
          label="Avg Win"
          value={`${(data.avg_win ?? 0).toFixed(2)}$`}
          subColor="var(--success)"
        />
        <Kpi
          label="Avg Loss"
          value={`${(data.avg_loss ?? 0).toFixed(2)}$`}
          subColor="var(--danger)"
        />
        <Kpi
          label="Holding"
          value={`${(data.avg_holding_days ?? 0).toFixed(1)}j`}
          sub="moyenne"
        />
        <Kpi
          label="Streak"
          value={data.streak >= 0 ? `+${data.streak}` : String(data.streak)}
          subColor={data.streak >= 0 ? 'var(--success)' : 'var(--danger)'}
          sub={data.streak >= 0 ? 'wins consécutifs' : 'losses consécutifs'}
        />
      </div>

      {!hasData && (
        <div className="card cp-section-card">
          <div className="as-empty" style={{ padding: '3rem' }}>
            📊 Aucun trade clôturé — ajoutez des trades via le journal pour voir les métriques.
          </div>
        </div>
      )}

      {hasData && (
        <>
          {/* ── Courbe d'équité ── */}
          <div className="card cp-section-card">
            <div className="card-title">📈 Courbe d'équité</div>
            <div style={{ width: '100%', height: 280 }}>
              <ResponsiveContainer>
                <AreaChart data={equityCurve} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                  {EQ_GRAD_DEFS}
                  <CartesianGrid stroke="var(--panel-border)" strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fontSize: 11, fill: 'var(--text-muted)' }} />
                  <YAxis tick={{ fontSize: 11, fill: 'var(--text-muted)' }} domain={['dataMin - 500', 'dataMax + 500']} />
                  <Tooltip contentStyle={{ background: 'var(--panel-bg)', border: '1px solid var(--panel-border)', fontSize: '0.85rem' }} />
                  <ReferenceLine y={INITIAL_CAPITAL} stroke="var(--text-muted)" strokeDasharray="4 4" />
                  <Area type="monotone" dataKey="equity" stroke="var(--accent-primary)" fill="url(#eqGrad)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* ── Drawdown underwater ── */}
          <div className="card cp-section-card">
            <div className="card-title">🌊 Underwater (drawdown %)</div>
            <div style={{ width: '100%', height: 200 }}>
              <ResponsiveContainer>
                <AreaChart data={underwater} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="var(--panel-border)" strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fontSize: 11, fill: 'var(--text-muted)' }} />
                  <YAxis tick={{ fontSize: 11, fill: 'var(--text-muted)' }} tickFormatter={(v) => `${v.toFixed(1)}%`} />
                  <Tooltip
                    contentStyle={{ background: 'var(--panel-bg)', border: '1px solid var(--panel-border)', fontSize: '0.85rem' }}
                    formatter={(v) => [`${v.toFixed(2)}%`, 'DD']}
                  />
                  <Area type="monotone" dataKey="dd" stroke="var(--danger)" fill="var(--danger)" fillOpacity={0.25} strokeWidth={1.5} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* ── Distribution PnL ── */}
          <div className="card cp-section-card">
            <div className="card-title">📊 Distribution PnL par trade</div>
            <div style={{ width: '100%', height: 220 }}>
              <ResponsiveContainer>
                <BarChart data={pnlHist} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="var(--panel-border)" strokeDasharray="3 3" />
                  <XAxis dataKey="bin" tick={{ fontSize: 10, fill: 'var(--text-muted)' }} />
                  <YAxis tick={{ fontSize: 11, fill: 'var(--text-muted)' }} allowDecimals={false} />
                  <Tooltip contentStyle={{ background: 'var(--panel-bg)', border: '1px solid var(--panel-border)', fontSize: '0.85rem' }} />
                  <Bar dataKey="count">
                    {pnlHist.map((b) => <Cell key={b.bin} fill={b.color} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* ── PnL cumulatif (série brute) ── */}
          <div className="card cp-section-card">
            <div className="card-title">📉 PnL cumulatif (ordre chronologique)</div>
            <div style={{ width: '100%', height: 200 }}>
              <ResponsiveContainer>
                <LineChart data={(data.pnl_series || []).map((v, i) => ({ i: i + 1, cum: v }))} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="var(--panel-border)" strokeDasharray="3 3" />
                  <XAxis dataKey="i" tick={{ fontSize: 11, fill: 'var(--text-muted)' }} />
                  <YAxis tick={{ fontSize: 11, fill: 'var(--text-muted)' }} />
                  <Tooltip contentStyle={{ background: 'var(--panel-bg)', border: '1px solid var(--panel-border)', fontSize: '0.85rem' }} />
                  <ReferenceLine y={0} stroke="var(--text-muted)" />
                  <Line type="monotone" dataKey="cum" stroke="var(--accent-primary)" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* ── Breakdown par ticker ── */}
          <div className="card cp-section-card">
            <div className="card-title">🎯 PnL par ticker ({tickerRows.length})</div>
            {tickerRows.length === 0 ? (
              <div className="as-empty" style={{ padding: '2rem' }}>Aucun ticker clôturé.</div>
            ) : (
              <table className="scan-table">
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>Trades</th>
                    <th>W / L</th>
                    <th>Total PnL</th>
                    <th>Avg PnL</th>
                  </tr>
                </thead>
                <tbody>
                  {tickerRows.map((r) => (
                    <tr key={r.ticker} className="scan-row">
                      <td style={{ fontWeight: 600, fontFamily: 'monospace' }}>{r.ticker}</td>
                      <td>{(r.wins ?? 0) + (r.losses ?? 0)}</td>
                      <td style={{ fontSize: '0.85rem' }}>
                        <span style={{ color: 'var(--success)' }}>{r.wins}</span>
                        {' / '}
                        <span style={{ color: 'var(--danger)' }}>{r.losses}</span>
                      </td>
                      <td style={{ color: (r.total_pnl ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)', fontFamily: 'monospace' }}>
                        {(r.total_pnl ?? 0) >= 0 ? '+' : ''}{(r.total_pnl ?? 0).toFixed(2)}$
                      </td>
                      <td style={{ color: (r.avg_pnl ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)', fontFamily: 'monospace' }}>
                        {(r.avg_pnl ?? 0) >= 0 ? '+' : ''}{(r.avg_pnl ?? 0).toFixed(2)}$
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
