import { useMemo, useRef, useState } from 'react';
import {
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
import { addTrade, closeTrade, fetchEquityCurve, fetchPortfolio } from '../api/client';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import ApiErrorBanner from './common/ApiErrorBanner';
import { parseNum, tradePnL, mergeLivePositions, toCsv } from '../utils/portfolio';

function downloadFile(filename, content, type = 'text/csv') {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function PortfolioPage() {
  const qc = useQueryClient();

  const portfolioQ = useQuery({ queryKey: ['portfolio'], queryFn: fetchPortfolio, refetchInterval: 15_000 });
  const curveQ     = useQuery({ queryKey: ['equity_curve'], queryFn: fetchEquityCurve, refetchInterval: 15_000 });

  const [tab, setTab]                     = useState('open');
  const [closingTicker, setClosingTicker] = useState('');
  const [closeForm, setCloseForm]         = useState({ exit_price: '', result: 'WIN' });
  const [addForm, setAddForm]             = useState({ ticker:'', direction:'LONG', entry:'', stop_loss:'', take_profit:'', size:1, signal:'MANUAL', sector:'' });

  // Toasts array : les actions concurrentes n'écrasent plus le message précédent
  // (ancien `msg` string s'auto-effaçait après 4s même si une nouvelle action
  // venait d'émettre un feedback).
  const [toasts, setToasts] = useState([]);
  const toastIdRef = useRef(0);
  const toast = (text, type = 'ok') => {
    const id = ++toastIdRef.current;
    setToasts(t => [...t, { id, text, type }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 4000);
  };

  const [filterStatus, setFilterStatus]       = useState('Tous');
  const [filterDirection, setFilterDirection] = useState('Tous');
  const [filterTicker, setFilterTicker]       = useState('Tous');

  const refetchAll = () => {
    qc.invalidateQueries({ queryKey: ['portfolio'] });
    qc.invalidateQueries({ queryKey: ['equity_curve'] });
    qc.invalidateQueries({ queryKey: ['status'] });
  };

  // useMutation → .isPending alimente disabled={} sur les boutons (anti-double-submit).
  // closeTrade/addTrade renvoient {ok, detail?, error?} donc on ne throw pas dans
  // mutationFn — on branche le succès/erreur sur res.ok dans onSuccess.
  const closeMut = useMutation({
    mutationFn: (payload) => closeTrade(payload),
    onSuccess: (res) => {
      if (res?.ok) {
        toast('✅ Trade clôturé', 'ok');
        setClosingTicker('');
        refetchAll();
      } else {
        toast(res?.detail || res?.error || 'Erreur clôture', 'err');
      }
    },
    onError: () => toast('Erreur réseau — clôture impossible', 'err'),
  });

  const addMut = useMutation({
    mutationFn: (payload) => addTrade(payload),
    onSuccess: (res) => {
      if (res?.ok) {
        toast('✅ Trade ajouté', 'ok');
        refetchAll();
      } else {
        toast(res?.detail || res?.error || 'Erreur ajout', 'err');
      }
    },
    onError: () => toast('Erreur réseau — ajout impossible', 'err'),
  });

  const handleClose = (ticker) => {
    const exit = parseFloat(closeForm.exit_price);
    if (!Number.isFinite(exit) || exit <= 0) {
      toast('Prix de sortie invalide', 'err');
      return;
    }
    closeMut.mutate({ ticker, exit_price: exit, result: closeForm.result });
  };

  const handleAdd = (ev) => {
    ev.preventDefault();
    const entry = parseFloat(addForm.entry);
    const sl    = parseFloat(addForm.stop_loss);
    const tp    = parseFloat(addForm.take_profit);
    const size  = parseInt(addForm.size, 10);
    if (![entry, sl, tp].every(n => Number.isFinite(n) && n > 0)) {
      toast('Entrée / SL / TP doivent être des prix valides > 0', 'err');
      return;
    }
    if (!Number.isFinite(size) || size < 1) {
      toast('Taille doit être un entier ≥ 1', 'err');
      return;
    }
    addMut.mutate({ ...addForm, entry, stop_loss: sl, take_profit: tp, size });
  };

  const data = portfolioQ.data;
  const curve = curveQ.data?.curve || [];

  const livePositions = useMemo(
    () => mergeLivePositions(data?.equity?.open_positions, data?.open_positions),
    [data],
  );

  const closedTrades = useMemo(() => data?.closed_trades || [], [data]);

  const tickerOptions = useMemo(() => {
    const set = new Set(closedTrades.map(t => t.Ticker).filter(Boolean));
    return ['Tous', ...Array.from(set).sort()];
  }, [closedTrades]);

  const filteredHistory = useMemo(() => {
    let rows = [...closedTrades].reverse();
    if (filterStatus !== 'Tous') {
      rows = rows.filter(t => {
        if (filterStatus === 'WIN')  return t.Status === 'WIN' || t.Status === 'TP';
        if (filterStatus === 'LOSS') return t.Status === 'LOSS' || t.Status === 'SL';
        return t.Status === filterStatus;
      });
    }
    if (filterDirection !== 'Tous') rows = rows.filter(t => t.Direction === filterDirection);
    if (filterTicker    !== 'Tous') rows = rows.filter(t => t.Ticker === filterTicker);
    return rows;
  }, [closedTrades, filterStatus, filterDirection, filterTicker]);

  const cumulativePnl = useMemo(() => {
    const sorted = [...closedTrades].sort((a, b) => (a.Exit_Date || '').localeCompare(b.Exit_Date || ''));
    const out = [];
    let cum = 0;
    for (let i = 0; i < sorted.length; i++) {
      const t = sorted[i];
      const pnl = tradePnL(t) ?? 0;
      cum += pnl;
      out.push({ i: i + 1, ticker: t.Ticker, exit: t.Exit_Date, pnl, cumulative: cum });
    }
    return out;
  }, [closedTrades]);

  const pnlStats = useMemo(() => {
    const wins  = closedTrades.filter(t => t.Status === 'WIN' || t.Status === 'TP');
    const losses = closedTrades.filter(t => t.Status === 'LOSS' || t.Status === 'SL');
    const sumWin = wins.reduce((s, t) => s + (tradePnL(t) ?? 0), 0);
    const sumLoss = Math.abs(losses.reduce((s, t) => s + (tradePnL(t) ?? 0), 0));
    const pf = sumLoss > 0 ? sumWin / sumLoss : (sumWin > 0 ? Infinity : 0);
    return { wins: wins.length, losses: losses.length, opens: livePositions.length, pf, sumWin, sumLoss };
  }, [closedTrades, livePositions]);

  if (portfolioQ.isLoading) return <div className="loading-pulse"><div className="spinner" /><p>Chargement du portfolio…</p></div>;
  if (portfolioQ.isError || !data) return <ApiErrorBanner msg={portfolioQ.error?.message || 'API indisponible — vérifiez que FastAPI tourne sur :8000'} onRetry={() => portfolioQ.refetch()} />;

  const { equity, stats } = data;
  const current = equity?.current_equity ?? 100000;
  const start   = equity?.starting_equity ?? 100000;
  const ddPct   = start > 0 ? Math.max(0, (start - current) / start * 100) : 0;

  return (
    <div className="portfolio-page animate-fade-in">
      {toasts.length > 0 && (
        <div style={{ position: 'fixed', top: '1rem', right: '1rem', zIndex: 1000, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {toasts.map(t => (
            <div
              key={t.id}
              className="api-toast"
              style={{
                borderLeft: `3px solid ${t.type === 'err' ? 'var(--danger)' : 'var(--success)'}`,
                background: t.type === 'err' ? 'rgba(239,68,68,0.12)' : 'rgba(16,185,129,0.12)',
              }}
            >
              {t.text}
            </div>
          ))}
        </div>
      )}

      {/* ── KPIs ── */}
      <div className="port-kpis">
        <KPI label="Capital compte" value={`$${current.toLocaleString(undefined, {maximumFractionDigits:0})}`} />
        <KPI label="P&L Réalisé" value={`${equity?.realized_pnl >= 0 ? '+' : ''}$${(equity?.realized_pnl ?? 0).toFixed(2)}`} color={equity?.realized_pnl >= 0 ? 'pos' : 'neg'} />
        <KPI label="P&L Non-réalisé" value={`${equity?.unrealized_pnl >= 0 ? '+' : ''}$${(equity?.unrealized_pnl ?? 0).toFixed(2)}`} color={equity?.unrealized_pnl >= 0 ? 'pos' : 'neg'} />
        <KPI label="Win Rate" value={`${stats?.win_rate ?? 0}%`} color="pos" />
        <KPI label="Positions ouvertes" value={`${livePositions.length} / 5`} />
        <KPI label="Trades clôturés" value={stats?.total_trades ?? 0} />
      </div>

      {/* ── Drawdown Gauge ── */}
      <div className="card">
        <h3 className="card-title">⚠️ Drawdown journalier</h3>
        <div className="risk-gauge">
          <div className="risk-bar-track">
            <div className="risk-bar-fill" style={{ width: `${Math.min(100, ddPct / 4 * 100)}%` }} />
          </div>
          <div className="risk-bar-labels"><span>$0</span><span>Limite FTMO: 4% ($4,000)</span></div>
        </div>
        <p className="risk-note">
          Drawdown actuel: <strong style={{ color: ddPct > 2 ? 'var(--danger)' : 'var(--success)' }}>
            {ddPct.toFixed(2)}%
          </strong> — {ddPct >= 4 ? '⛔ KILLSWITCH ACTIF' : ddPct >= 2 ? '⚠️ Vigilance' : '✅ Zone sûre'}
        </p>
      </div>

      {/* ── Courbe d'équité mini ── */}
      {curve.length > 0 && (
        <div className="card">
          <h3 className="card-title">📈 Courbe d'équité ({curve.length} trades)</h3>
          <EquitySpark curve={curve} startEquity={start} />
        </div>
      )}

      {/* ── Tabs ── */}
      <div className="port-tabs">
        <button id="tab-open"    className={`port-tab ${tab === 'open'    ? 'active' : ''}`} onClick={() => setTab('open')}>Positions ouvertes ({livePositions.length})</button>
        <button id="tab-history" className={`port-tab ${tab === 'history' ? 'active' : ''}`} onClick={() => setTab('history')}>Historique ({closedTrades.length})</button>
        <button id="tab-add"     className={`port-tab ${tab === 'add'     ? 'active' : ''}`} onClick={() => setTab('add')}>➕ Ajouter trade</button>
      </div>

      {/* ── Open Positions ── */}
      {tab === 'open' && (
        <>
          <div className="port-table-wrap">
            {livePositions.length === 0 ? (
              <div className="as-empty" style={{ padding: '3rem' }}>📭 Aucune position ouverte actuellement.</div>
            ) : (
              <table className="scan-table">
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>Dir</th>
                    <th>Entrée</th>
                    <th>Prix live</th>
                    <th>PnL latent</th>
                    <th>SL</th>
                    <th>TP</th>
                    <th>Taille</th>
                    <th>RR</th>
                    <th>Date</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {livePositions.map((p) => {
                    const upnl = p.unrealized_pnl;
                    const pct  = p.pct_from_entry;
                    const dirIsLong = p.Direction === 'LONG';
                    return (
                      <tr key={p.Ticker} className="scan-row" id={`open-${p.Ticker}`}>
                        <td><strong>{p.Ticker}</strong></td>
                        <td>
                          <span className="scan-signal-badge" style={{ color: dirIsLong ? 'var(--success)' : 'var(--danger)', borderColor: (dirIsLong ? 'var(--success)' : 'var(--danger)') + '50' }}>
                            {dirIsLong ? '▲' : '▼'} {p.Direction}
                          </span>
                        </td>
                        <td>${parseNum(p.Entry).toFixed(2)}</td>
                        <td>
                          {p.current_price != null
                            ? <span style={{ color: 'var(--text-main)' }}>${parseNum(p.current_price).toFixed(2)}</span>
                            : <span style={{ color: 'var(--text-muted)' }}>—</span>}
                        </td>
                        <td>
                          {upnl != null ? (
                            <span className={upnl >= 0 ? 'pos' : 'neg'} style={{ fontWeight: 600 }}>
                              {upnl >= 0 ? '+' : ''}${upnl.toFixed(2)}
                              {pct != null && <small style={{ marginLeft: 4, opacity: 0.7 }}>({pct >= 0 ? '+' : ''}{pct.toFixed(2)}%)</small>}
                            </span>
                          ) : <span style={{ color: 'var(--text-muted)' }}>—</span>}
                        </td>
                        <td style={{ color: 'var(--danger)' }}>${parseNum(p.Stop_Loss).toFixed(2)}</td>
                        <td style={{ color: 'var(--success)' }}>${parseNum(p.Take_Profit).toFixed(2)}</td>
                        <td>{p.Size}</td>
                        <td>{p.RR || '—'}</td>
                        <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{(p.Date || '').slice(0, 10)}</td>
                        <td>
                          {closingTicker === p.Ticker ? (
                            <div className="inline-close-form">
                              <input type="number" step="0.01" placeholder="Exit price" value={closeForm.exit_price} onChange={e => setCloseForm(f => ({ ...f, exit_price: e.target.value }))} className="mini-input" />
                              <select value={closeForm.result} onChange={e => setCloseForm(f => ({ ...f, result: e.target.value }))} className="scan-select" style={{ padding: '0.3rem', fontSize: '0.8rem' }}>
                                <option value="WIN">WIN</option>
                                <option value="LOSS">LOSS</option>
                              </select>
                              <button
                                className="btn-confirm-yes"
                                onClick={() => handleClose(p.Ticker)}
                                disabled={closeMut.isPending}
                                aria-label={`Confirmer la clôture de ${p.Ticker}`}
                                style={{ padding: '0.3rem 0.6rem', opacity: closeMut.isPending ? 0.5 : 1 }}
                              >{closeMut.isPending ? '…' : '✓'}</button>
                              <button
                                className="btn-confirm-no"
                                onClick={() => setClosingTicker('')}
                                disabled={closeMut.isPending}
                                aria-label="Annuler la clôture"
                                style={{ padding: '0.3rem 0.6rem' }}
                              >✕</button>
                            </div>
                          ) : (
                            <button
                              className="scan-analyze-btn"
                              onClick={() => { setClosingTicker(p.Ticker); setCloseForm({ exit_price: p.current_price ?? p.Entry, result: 'WIN' }); }}
                              id={`close-${p.Ticker}`}
                            >
                              Clôturer
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>

          {/* PnL latent bar chart */}
          {livePositions.some(p => p.unrealized_pnl != null) && (
            <div className="card" style={{ marginTop: '1rem' }}>
              <h3 className="card-title">💰 PnL latent par position</h3>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={livePositions.map(p => ({ ticker: p.Ticker, pnl: p.unrealized_pnl ?? 0 }))} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="rgba(148,163,184,0.12)" strokeDasharray="3 3" />
                  <XAxis dataKey="ticker" tick={{ fontSize: 11, fill: '#f59e0b' }} />
                  <YAxis tickFormatter={v => `$${v.toFixed(0)}`} tick={{ fontSize: 11 }} width={60} />
                  <Tooltip
                    formatter={v => [`${v >= 0 ? '+' : ''}$${Number(v).toFixed(2)}`, 'PnL latent']}
                    contentStyle={{ background: 'rgba(15,23,42,0.95)', border: '1px solid rgba(148,163,184,0.3)', borderRadius: 6, fontSize: 12 }}
                  />
                  <ReferenceLine y={0} stroke="rgba(148,163,184,0.5)" />
                  <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
                    {livePositions.map((p) => (
                      <Cell key={p.Ticker} fill={(p.unrealized_pnl ?? 0) >= 0 ? '#10b981' : '#ef4444'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}

      {/* ── History ── */}
      {tab === 'history' && (
        <>
          {/* Filters + stats */}
          <div className="card" style={{ marginBottom: '1rem' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', alignItems: 'center' }}>
              <FilterSelect label="Statut"    value={filterStatus}    options={['Tous','WIN','LOSS','OPEN']} onChange={setFilterStatus} />
              <FilterSelect label="Direction" value={filterDirection} options={['Tous','LONG','SHORT']}      onChange={setFilterDirection} />
              <FilterSelect label="Ticker"    value={filterTicker}    options={tickerOptions}                onChange={setFilterTicker} />
              <div style={{ marginLeft: 'auto', fontSize: '0.8rem', fontFamily: 'monospace', color: 'var(--text-muted)' }}>
                <span style={{ color: 'var(--success)' }}>{pnlStats.wins}W</span> /{' '}
                <span style={{ color: 'var(--danger)' }}>{pnlStats.losses}L</span> /{' '}
                <span style={{ color: 'var(--accent-secondary)' }}>{pnlStats.opens}O</span>
                &nbsp;·&nbsp; PF <b style={{ color: pnlStats.pf >= 2 ? 'var(--success)' : 'var(--warning)' }}>
                  {Number.isFinite(pnlStats.pf) ? pnlStats.pf.toFixed(2) : '∞'}
                </b>
              </div>
              <button
                className="scan-filter-btn"
                onClick={() => downloadFile(
                  `trades_${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '')}.csv`,
                  toCsv(filteredHistory, ['Date','Ticker','Direction','Entry','Stop_Loss','Take_Profit','Size','RR','Status','Exit_Price','Exit_Date']),
                )}
                disabled={filteredHistory.length === 0}
              >
                ⬇️ Export CSV
              </button>
            </div>
          </div>

          {/* Cumulative PnL chart */}
          {cumulativePnl.length > 0 && (
            <div className="card" style={{ marginBottom: '1rem' }}>
              <h3 className="card-title">📊 PnL cumulatif par trade</h3>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={cumulativePnl} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="rgba(148,163,184,0.12)" strokeDasharray="3 3" />
                  <XAxis dataKey="i" tick={{ fontSize: 11 }} />
                  <YAxis tickFormatter={v => `$${v.toFixed(0)}`} tick={{ fontSize: 11 }} width={60} />
                  <Tooltip
                    formatter={(v, n) => [`${v >= 0 ? '+' : ''}$${Number(v).toFixed(2)}`, n === 'cumulative' ? 'Cumulé' : 'Trade']}
                    labelFormatter={(i, items) => items?.[0] ? `#${i} · ${items[0].payload.ticker} (${items[0].payload.exit || '—'})` : `#${i}`}
                    contentStyle={{ background: 'rgba(15,23,42,0.95)', border: '1px solid rgba(148,163,184,0.3)', borderRadius: 6, fontSize: 12 }}
                  />
                  <ReferenceLine y={0} stroke="rgba(148,163,184,0.5)" />
                  <Line type="monotone" dataKey="cumulative" stroke="#38bdf8" strokeWidth={2} dot={{ r: 2 }} activeDot={{ r: 4 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          <div className="port-table-wrap">
            {filteredHistory.length === 0 ? (
              <div className="as-empty" style={{ padding: '3rem' }}>📭 Aucun trade ne correspond aux filtres.</div>
            ) : (
              <>
                <div style={{ padding: '0.25rem 0.5rem 0.5rem', fontSize: '0.75rem', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                  {filteredHistory.length} trade{filteredHistory.length > 1 ? 's' : ''} affiché{filteredHistory.length > 1 ? 's' : ''}
                </div>
                <table className="scan-table">
                  <thead>
                    <tr><th>Ticker</th><th>Direction</th><th>Entrée</th><th>Sortie</th><th>Taille</th><th>RR</th><th>P&L $</th><th>Résultat</th><th>Date sortie</th></tr>
                  </thead>
                  <tbody>
                    {filteredHistory.map((t, i) => {
                      const pnl = tradePnL(t);
                      const rowKey = `${t.Ticker || '?'}-${t.Exit_Date || t.Date || ''}-${t.Entry || ''}-${i}`;
                      return (
                        <tr key={rowKey} className="scan-row" id={`hist-${t.Ticker}-${i}`}>
                          <td><strong>{t.Ticker}</strong></td>
                          <td><span className="scan-signal-badge" style={{ color: t.Direction === 'LONG' ? 'var(--success)' : 'var(--danger)', borderColor: (t.Direction === 'LONG' ? 'var(--success)' : 'var(--danger)') + '50' }}>{t.Direction === 'LONG' ? '▲' : '▼'} {t.Direction}</span></td>
                          <td>${t.Entry}</td>
                          <td>${t.Exit_Price || '—'}</td>
                          <td>{t.Size}</td>
                          <td>{t.RR}</td>
                          <td className={pnl !== null ? (pnl >= 0 ? 'pos' : 'neg') : ''}>{pnl !== null ? `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}` : '—'}</td>
                          <td><span className={`result-badge ${(t.Status === 'WIN' || t.Status === 'TP') ? 'win' : 'loss'}`}>{(t.Status === 'WIN' || t.Status === 'TP') ? '✅ WIN' : '❌ LOSS'}</span></td>
                          <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{t.Exit_Date || '—'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </>
            )}
          </div>
        </>
      )}

      {/* ── Add Trade ── */}
      {tab === 'add' && (
        <div className="card">
          <h3 className="card-title">➕ Ajouter un trade manuel</h3>
          <form className="add-trade-form" onSubmit={handleAdd}>
            <div className="atf-row">
              <div className="atf-field">
                <label>Ticker</label>
                <input type="text" placeholder="NVDA" value={addForm.ticker} onChange={e => setAddForm(f => ({ ...f, ticker: e.target.value.toUpperCase() }))} className="mini-input" required />
              </div>
              <div className="atf-field">
                <label>Direction</label>
                <select value={addForm.direction} onChange={e => setAddForm(f => ({ ...f, direction: e.target.value }))} className="scan-select">
                  <option value="LONG">▲ LONG</option>
                  <option value="SHORT">▼ SHORT</option>
                </select>
              </div>
              <div className="atf-field">
                <label>Entrée $</label>
                <input type="number" step="0.01" placeholder="100.00" value={addForm.entry} onChange={e => setAddForm(f => ({ ...f, entry: e.target.value }))} className="mini-input" required />
              </div>
              <div className="atf-field">
                <label>Stop Loss $</label>
                <input type="number" step="0.01" placeholder="95.00" value={addForm.stop_loss} onChange={e => setAddForm(f => ({ ...f, stop_loss: e.target.value }))} className="mini-input" required />
              </div>
              <div className="atf-field">
                <label>Take Profit $</label>
                <input type="number" step="0.01" placeholder="110.00" value={addForm.take_profit} onChange={e => setAddForm(f => ({ ...f, take_profit: e.target.value }))} className="mini-input" required />
              </div>
              <div className="atf-field">
                <label>Taille</label>
                <input type="number" min="1" placeholder="1" value={addForm.size} onChange={e => setAddForm(f => ({ ...f, size: e.target.value }))} className="mini-input" required />
              </div>
              <div className="atf-field">
                <label>Signal</label>
                <select value={addForm.signal} onChange={e => setAddForm(f => ({ ...f, signal: e.target.value }))} className="scan-select">
                  <option value="MANUAL">MANUAL</option>
                  <option value="CHANDELIER_MOMENTUM">CHANDELIER</option>
                  <option value="MEAN_REVERSION">MEAN_REVERSION</option>
                  <option value="MOMENTUM_DIP">MOMENTUM_DIP</option>
                </select>
              </div>
            </div>
            <button
              type="submit"
              className="action-btn"
              disabled={addMut.isPending}
              style={{ maxWidth: '300px', marginTop: '1rem', opacity: addMut.isPending ? 0.6 : 1 }}
            >{addMut.isPending ? '⏳ Ajout…' : '➕ Ajouter le trade'}</button>
          </form>
        </div>
      )}

    </div>
  );
}

function KPI({ label, value, color }) {
  return (
    <div className="port-kpi">
      <span>{label}</span>
      <strong className={color || ''}>{value}</strong>
    </div>
  );
}

function FilterSelect({ label, value, options, onChange }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
      <span style={{ textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</span>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="scan-select"
        style={{ padding: '0.35rem 0.6rem', fontSize: '0.85rem', minWidth: 100 }}
      >
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
}

// Défs gradient statiques hors render : on switche l'URL via isUp plutôt que de
// reconstruire le <linearGradient> à chaque re-render.
const EQ_SPARK_DEFS = (
  <defs>
    <linearGradient id="eq-grad-up" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%"   stopColor="#10b981" stopOpacity="0.25" />
      <stop offset="100%" stopColor="#10b981" stopOpacity="0" />
    </linearGradient>
    <linearGradient id="eq-grad-down" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%"   stopColor="#ef4444" stopOpacity="0.25" />
      <stop offset="100%" stopColor="#ef4444" stopOpacity="0" />
    </linearGradient>
  </defs>
);

function EquitySpark({ curve, startEquity }) {
  const W = 800, H = 120;
  const equities = [startEquity, ...curve.map(c => c.equity)];
  // Guard : au moins 2 points pour tracer une ligne (sinon division par zéro
  // sur (equities.length - 1) et NaN dans le polyline).
  if (equities.length < 2) {
    return (
      <div style={{ padding: '1rem', color: 'var(--text-muted)', fontSize: '0.85rem', textAlign: 'center' }}>
        📊 Pas assez d'historique pour tracer la courbe (≥ 2 points requis).
      </div>
    );
  }
  const minE = Math.min(...equities);
  const maxE = Math.max(...equities);
  const range = maxE - minE || 1;
  const getX = i => (i / (equities.length - 1)) * W;
  const getY = e => H - ((e - minE) / range) * (H - 16) - 8;

  const pts = equities.map((e, i) => `${getX(i)},${getY(e)}`).join(' ');
  const last = equities[equities.length - 1];
  const isUp = last >= startEquity;
  const fillUrl   = isUp ? 'url(#eq-grad-up)' : 'url(#eq-grad-down)';
  const strokeCol = isUp ? '#10b981' : '#ef4444';

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: '120px' }}>
      {EQ_SPARK_DEFS}
      <polygon points={`0,${H} ${pts} ${W},${H}`} fill={fillUrl} />
      <polyline points={pts} fill="none" stroke={strokeCol} strokeWidth="2.5" strokeLinejoin="round" />
    </svg>
  );
}
