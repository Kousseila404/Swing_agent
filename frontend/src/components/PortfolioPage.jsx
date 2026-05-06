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
import { addTrade, closeTrade, fetchEquityCurve, fetchLtDecision, fetchPortfolio, fetchThesisStatus } from '../api/client';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { useSectorBenchmarkPortfolio } from '../hooks/useApi';
import ApiErrorBanner from './common/ApiErrorBanner';
import { PageSkeleton } from './common/Skeleton';
import PositionCard from './portfolio/PositionCard';
import TickerAnalysisModal from './TickerAnalysisModal';
import { holdingPeriod, parseNum, tradePnL, mergeLivePositions, toCsv } from '../utils/portfolio';

function downloadFile(filename, content, type = 'text/csv') {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

const THESIS_BADGE_PALETTE = {
  BROKEN:  { bg: 'rgba(248,113,113,0.20)', fg: '#f87171', icon: '🔴', short: 'BROKEN' },
  WARN:    { bg: 'rgba(251,191,36,0.20)',  fg: '#fbbf24', icon: '⚠️', short: 'WARN' },
  INTACT:  { bg: 'rgba(34,197,94,0.16)',   fg: '#22c55e', icon: '🟢', short: 'OK' },
  NO_DATA: null, // pas de badge si pas de data
};

// Refonte 2026-04-29 — décision LT Buffett. Source de vérité pour l'action
// recommandée sur une position OPEN (remplace la lecture isolée du thesis_status).
const LT_DECISION_PALETTE = {
  HOLD:              { bg: 'rgba(148,163,184,0.18)', fg: '#94a3b8', icon: '⏸',  short: 'HOLD' },
  ADD_ON:            { bg: 'rgba(59,130,246,0.20)',  fg: '#60a5fa', icon: '📈', short: 'ADD' },
  TRIM:              { bg: 'rgba(251,191,36,0.20)',  fg: '#fbbf24', icon: '✂️', short: 'TRIM' },
  EXIT_THESIS:       { bg: 'rgba(248,113,113,0.20)', fg: '#f87171', icon: '🧠', short: 'EXIT' },
  EXIT_VALUATION:    { bg: 'rgba(192,132,252,0.20)', fg: '#c084fc', icon: '💎', short: 'EXIT' },
  EXIT_CATASTROPHE:  { bg: 'rgba(239,68,68,0.30)',   fg: '#ef4444', icon: '🚨', short: 'EXIT' },
  NO_DATA:           null,
};

// Refonte 2026-04-29 étape 3 — catégorie sizing Buffett.
const BUFFETT_CATEGORY_PALETTE = {
  compounder:           { bg: 'rgba(34,197,94,0.20)',  fg: '#22c55e', icon: '🏔️', short: 'COMPOUNDER', tip: 'Q≥85 + P≥8 — sizing ×1.5 (Buffett concentre)' },
  high_quality:         { bg: 'rgba(59,130,246,0.18)', fg: '#60a5fa', icon: '⭐',  short: 'HIGH-Q',     tip: 'Q≥75 + P≥7 — sizing ×1.2' },
  high_quality_partial: { bg: 'rgba(59,130,246,0.18)', fg: '#60a5fa', icon: '⭐',  short: 'HIGH-Q',     tip: 'Q≥85 (sans F-score) — sizing ×1.2 prudent' },
  baseline:             null,
  baseline_no_data:     null,
  junior:               { bg: 'rgba(251,191,36,0.16)', fg: '#fbbf24', icon: '⚠',   short: 'JUNIOR',     tip: 'Q<50 ou P<4 — sizing ×0.7 (déconcentrer)' },
  junior_partial:       { bg: 'rgba(251,191,36,0.16)', fg: '#fbbf24', icon: '⚠',   short: 'JUNIOR',     tip: 'Score partiel défavorable — sizing ×0.7' },
  junk:                 { bg: 'rgba(248,113,113,0.18)', fg: '#f87171', icon: '🗑',   short: 'JUNK',       tip: 'Q<35 ET P<3 — sizing ×0.5 (à éviter)' },
};

function BuffettCategoryBadge({ category, sizeFactor }) {
  if (!category) return null;
  const p = BUFFETT_CATEGORY_PALETTE[category];
  if (!p) return null;
  const factorLabel = sizeFactor != null && sizeFactor !== 1.0 ? ` (×${sizeFactor})` : '';
  return (
    <span
      title={`${p.tip}${factorLabel}`}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 3,
        marginLeft: 6, padding: '0.1rem 0.4rem',
        fontSize: '0.62rem', fontWeight: 800, letterSpacing: '0.04em',
        borderRadius: 4, background: p.bg, color: p.fg,
        verticalAlign: 'middle', cursor: 'help',
      }}
    >
      {p.icon} {p.short}
    </span>
  );
}

function LtDecisionBadge({ decision }) {
  if (!decision) return null;
  const p = LT_DECISION_PALETTE[decision.action];
  if (!p) return null;
  const tooltipLines = [`Action LT : ${decision.action}`];
  if (decision.pct_gain != null) {
    tooltipLines.push(`P&L ${decision.pct_gain > 0 ? '+' : ''}${decision.pct_gain}%`);
  }
  for (const r of (decision.reasons || []).slice(0, 3)) tooltipLines.push(`• ${r}`);
  return (
    <span
      title={tooltipLines.join('\n')}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 3,
        marginLeft: 6, padding: '0.1rem 0.4rem',
        fontSize: '0.62rem', fontWeight: 800, letterSpacing: '0.04em',
        borderRadius: 4, background: p.bg, color: p.fg,
        verticalAlign: 'middle', cursor: 'help',
      }}
    >
      {p.icon} {p.short}
    </span>
  );
}

function ThesisBadge({ thesis }) {
  if (!thesis) return null;
  const p = THESIS_BADGE_PALETTE[thesis.status];
  if (!p) return null;
  const drift = thesis.drift || {};
  const tooltipLines = [];
  if (drift.titan != null) tooltipLines.push(`Drift TITAN ${drift.titan > 0 ? '+' : ''}${drift.titan} pts`);
  if (drift.f_score != null && drift.f_score !== 0) {
    tooltipLines.push(`F-Score ${drift.f_score > 0 ? '+' : ''}${drift.f_score}`);
  }
  for (const r of (thesis.reasons_break || []).slice(0, 2)) tooltipLines.push(`🔴 ${r}`);
  for (const r of (thesis.reasons_warn || []).slice(0, 2)) tooltipLines.push(`⚠ ${r}`);
  return (
    <span
      title={tooltipLines.join('\n') || `Thèse ${thesis.status}`}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 3,
        marginLeft: 6, padding: '0.1rem 0.4rem',
        fontSize: '0.62rem', fontWeight: 800, letterSpacing: '0.04em',
        borderRadius: 4, background: p.bg, color: p.fg,
        verticalAlign: 'middle', cursor: 'help',
      }}
    >
      {p.icon} {p.short}
    </span>
  );
}

export default function PortfolioPage() {
  const qc = useQueryClient();

  const portfolioQ = useQuery({ queryKey: ['portfolio'], queryFn: fetchPortfolio, refetchInterval: 15_000 });
  const curveQ     = useQuery({ queryKey: ['equity_curve'], queryFn: fetchEquityCurve, refetchInterval: 15_000 });
  const benchQ     = useSectorBenchmarkPortfolio();
  const thesisQ    = useQuery({ queryKey: ['thesis_status'], queryFn: fetchThesisStatus, refetchInterval: 5 * 60_000 });
  const ltQ        = useQuery({ queryKey: ['lt_decision'],   queryFn: fetchLtDecision,   refetchInterval: 5 * 60_000 });

  const thesisByTicker = useMemo(() => {
    const idx = {};
    for (const it of thesisQ.data?.items || []) {
      const t = String(it.ticker || '').toUpperCase();
      // En cas de doublon (multiple OPEN même ticker), garde le pire status.
      const order = { BROKEN: 3, WARN: 2, INTACT: 1, NO_DATA: 0 };
      const prev = idx[t];
      if (!prev || (order[it.status] ?? 0) > (order[prev.status] ?? 0)) {
        idx[t] = it;
      }
    }
    return idx;
  }, [thesisQ.data]);

  const ltByTicker = useMemo(() => {
    const idx = {};
    const order = { EXIT_CATASTROPHE: 4, EXIT_THESIS: 3, EXIT_VALUATION: 3, TRIM: 2, ADD_ON: 1, HOLD: 0, NO_DATA: -1 };
    for (const it of ltQ.data?.items || []) {
      const t = String(it.ticker || '').toUpperCase();
      const prev = idx[t];
      if (!prev || (order[it.action] ?? -1) > (order[prev.action] ?? -1)) {
        idx[t] = it;
      }
    }
    return idx;
  }, [ltQ.data]);

  const ltSummary = ltQ.data?.summary;
  const addOnTickers = ltSummary?.tickers_by_action?.ADD_ON || [];

  const [tab, setTab]                     = useState('open');
  // Refonte UI 2026-04-29 — vue cartes par défaut (4-15 positions),
  // tableau pour qui veut le mode dense.
  const [viewMode, setViewMode]           = useState(() => {
    try { return localStorage.getItem('portfolio_view_mode') || 'cards'; }
    catch { return 'cards'; }
  });
  const setViewModePersisted = (m) => {
    setViewMode(m);
    try { localStorage.setItem('portfolio_view_mode', m); } catch {}
  };
  const [closingTicker, setClosingTicker] = useState('');
  // Ticker pour modal d'analyse (clic sur ticker dans la carte ou le tableau).
  const [analysisTicker, setAnalysisTicker] = useState(null);
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

  // Index ticker → benchmark pour lookup O(1) en table.
  const benchByTicker = useMemo(() => {
    const map = {};
    for (const it of (benchQ.data?.items || [])) {
      if (it.ticker) map[it.ticker.toUpperCase()] = it;
    }
    return map;
  }, [benchQ.data]);

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

  if (portfolioQ.isLoading) return <PageSkeleton tiles={6} blockHeight={260} rows={5} />;
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
        {benchQ.data?.avg_alpha_pct != null && (
          <KPI
            label="Alpha moyen vs ETF"
            value={`${benchQ.data.avg_alpha_pct >= 0 ? '+' : ''}${benchQ.data.avg_alpha_pct.toFixed(2)}%`}
            color={benchQ.data.avg_alpha_pct >= 0 ? 'pos' : 'neg'}
          />
        )}
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
          {/* Refonte UI 2026-04-29 — bandeau compact (compteur + toggle vue) */}
          {livePositions.length > 0 && (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              flexWrap: 'wrap', gap: '0.6rem', margin: '0.5rem 0 1rem',
              padding: '0.55rem 0.85rem', borderRadius: 8,
              background: 'rgba(15,23,42,0.45)',
              border: '1px solid rgba(148,163,184,0.18)',
            }}>
              <div style={{ display: 'flex', gap: '0.7rem', flexWrap: 'wrap', fontSize: '0.78rem' }}>
                <span><strong style={{ color: '#fff' }}>{livePositions.length}</strong> position{livePositions.length > 1 ? 's' : ''}</span>
                {Object.entries(ltSummary?.counts || {})
                  .filter(([k]) => k !== 'NO_DATA')
                  .sort((a, b) => {
                    const ord = { EXIT_CATASTROPHE: 0, EXIT_THESIS: 1, EXIT_VALUATION: 2, TRIM: 3, ADD_ON: 4, HOLD: 5 };
                    return (ord[a[0]] ?? 99) - (ord[b[0]] ?? 99);
                  })
                  .map(([action, n]) => (
                    <span key={action} style={{ color: 'var(--text-muted)' }}>
                      • <strong style={{ color: '#cbd5e1' }}>{n}</strong>{' '}
                      {action === 'HOLD' ? 'hold'
                        : action === 'ADD_ON' ? '📈 add'
                        : action === 'TRIM' ? '✂️ trim'
                        : action.startsWith('EXIT') ? '🚪 exit' : action.toLowerCase()}
                    </span>
                  ))}
              </div>
              <div style={{ display: 'flex', gap: 4, padding: 2,
                background: 'rgba(0,0,0,0.25)', borderRadius: 6 }}>
                {[
                  { v: 'cards', label: '🃏 Cartes' },
                  { v: 'table', label: '📊 Tableau' },
                ].map(({ v, label }) => (
                  <button key={v}
                    onClick={() => setViewModePersisted(v)}
                    style={{
                      padding: '0.3rem 0.7rem', borderRadius: 4, border: 'none',
                      background: viewMode === v ? 'rgba(96,165,250,0.20)' : 'transparent',
                      color: viewMode === v ? '#60a5fa' : 'var(--text-muted)',
                      fontSize: '0.72rem', fontWeight: 700, cursor: 'pointer',
                    }}>{label}</button>
                ))}
              </div>
            </div>
          )}

          {/* Mode CARTES — vue par défaut, refonte Buffett-LT */}
          {viewMode === 'cards' && livePositions.length > 0 && (
            <div>
              {livePositions.map((p) => (
                <PositionCard
                  key={p.Ticker}
                  position={p}
                  lt={ltByTicker[String(p.Ticker || '').toUpperCase()]}
                  onClickClose={() => { setClosingTicker(p.Ticker); setCloseForm({ exit_price: p.current_price ?? p.Entry, result: 'WIN' }); }}
                  onTickerClick={(t) => setAnalysisTicker(t)}
                />
              ))}
              {/* Form de clôture inline si actif */}
              {closingTicker && (
                <div style={{
                  marginTop: '1rem', padding: '0.85rem 1rem', borderRadius: 8,
                  background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.30)',
                }}>
                  <strong>Clôture {closingTicker}</strong>
                  <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                    <input
                      type="number" step="0.01"
                      value={closeForm.exit_price}
                      onChange={e => setCloseForm({ ...closeForm, exit_price: e.target.value })}
                      placeholder="Prix de sortie"
                      style={{ padding: '0.3rem 0.5rem', borderRadius: 4, border: '1px solid var(--border)' }}
                    />
                    <select
                      value={closeForm.result}
                      onChange={e => setCloseForm({ ...closeForm, result: e.target.value })}
                      style={{ padding: '0.3rem 0.5rem', borderRadius: 4 }}
                    >
                      <option value="WIN">WIN</option>
                      <option value="LOSS">LOSS</option>
                    </select>
                    <button onClick={() => handleClose(closingTicker)} disabled={closeMut.isPending}
                      style={{ padding: '0.3rem 0.8rem', borderRadius: 4, background: 'var(--danger)', color: '#fff', border: 'none', cursor: 'pointer' }}
                    >Confirmer</button>
                    <button onClick={() => setClosingTicker('')}
                      style={{ padding: '0.3rem 0.8rem', borderRadius: 4, background: 'transparent', border: '1px solid var(--border)', cursor: 'pointer' }}
                    >Annuler</button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Mode TABLEAU — vue dense alternative */}
          {viewMode === 'table' && (
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
                    <th title="Alpha = return position − return ETF sectoriel sur la même fenêtre">vs ETF</th>
                    <th>Détention</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {livePositions.map((p) => {
                    const upnl = p.unrealized_pnl;
                    const pct  = p.pct_from_entry;
                    const dirIsLong = p.Direction === 'LONG';
                    return (
                      <tr key={p.Ticker} className="scan-row" id={`open-${p.Ticker}`}
                          data-ticker={p.Ticker}>
                        <td>
                          <strong
                            onClick={() => setAnalysisTicker(p.Ticker)}
                            style={{ cursor: 'pointer', textDecoration: 'underline dotted', textUnderlineOffset: 2 }}
                            title="Voir l'analyse complète"
                          >{p.Ticker}</strong>
                          {/* Mode tableau dense — un seul badge dominant : la décision LT.
                              Catégorie + thèse sont surfacées dans la vue cartes. */}
                          <LtDecisionBadge decision={ltByTicker[String(p.Ticker || '').toUpperCase()]} />
                        </td>
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
                        <td style={{ fontSize: '0.74rem' }}>
                          {(() => {
                            const b = benchByTicker[String(p.Ticker || '').toUpperCase()];
                            if (!b || b.error || b.return_pct == null) {
                              return <span style={{ color: 'var(--text-muted)' }}>—</span>;
                            }
                            const alpha = b.alpha_pct;
                            const hasAlpha = Number.isFinite(alpha);
                            const tone = hasAlpha
                              ? (alpha >= 0 ? 'var(--success)' : 'var(--danger)')
                              : 'var(--text-muted)';
                            return (
                              <div title={`Position ${b.return_pct >= 0 ? '+' : ''}${b.return_pct.toFixed(2)}% · ${b.etf || 'ETF ?'} ${b.etf_return_pct != null ? (b.etf_return_pct >= 0 ? '+' : '') + b.etf_return_pct.toFixed(2) + '%' : '—'}`}>
                                <div style={{ fontFamily: 'monospace', fontWeight: 600,
                                              color: b.return_pct >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                                  {b.return_pct >= 0 ? '+' : ''}{b.return_pct.toFixed(1)}%
                                </div>
                                {hasAlpha && (
                                  <div style={{ fontSize: '0.65rem', color: tone, fontWeight: 600 }}>
                                    {b.etf} {alpha >= 0 ? '+' : ''}{alpha.toFixed(1)}
                                  </div>
                                )}
                              </div>
                            );
                          })()}
                        </td>
                        <td style={{ fontSize: '0.74rem' }}>
                          {(() => {
                            const hp = holdingPeriod(p.Date);
                            if (!hp) return <span style={{ color: 'var(--text-muted)' }}>—</span>;
                            const tone = hp.ltcg_eligible ? 'var(--success)'
                                       : hp.days_to_ltcg <= 60 ? 'var(--warning)'
                                       : 'var(--text-muted)';
                            return (
                              <div title={hp.ltcg_eligible
                                ? `Position détenue ${hp.days_held}j — éligible LTCG (long-term capital gains, taxe réduite US)`
                                : `Position détenue ${hp.days_held}j — encore ${hp.days_to_ltcg}j avant LTCG`}>
                                <div style={{ fontFamily: 'monospace', fontWeight: 600 }}>
                                  {hp.days_held}j
                                </div>
                                <div style={{ fontSize: '0.65rem', color: tone, fontWeight: 600 }}>
                                  {hp.ltcg_eligible
                                    ? '✓ LTCG'
                                    : `LTCG dans ${hp.days_to_ltcg}j`}
                                </div>
                              </div>
                            );
                          })()}
                        </td>
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
          )}

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
      {/* ── Modal d'analyse ticker (clic ticker) ── */}
      {analysisTicker && (
        <TickerAnalysisModal
          ticker={analysisTicker}
          onClose={() => setAnalysisTicker(null)}
        />
      )}

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
