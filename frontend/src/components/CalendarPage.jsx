// CalendarPage — Catalyst calendar consolidé.
//
// Agrège earnings (positions OPEN + watchlist) et événements macro
// (FOMC/CPI/NFP) sur une fenêtre paramétrable. Endpoint /api/calendar.

import { useMemo, useState } from 'react';
import { useCatalystCalendar } from '../hooks/useApi';
import ApiErrorBanner from './common/ApiErrorBanner';
import EmptyState from './common/EmptyState';
import { PageSkeleton } from './common/Skeleton';
import TickerAnalysisModal from './TickerAnalysisModal';

const HORIZONS = [7, 14, 30, 60, 90];

const TYPE_TONE = {
  EARNINGS: { bg: 'rgba(96,165,250,0.15)',  fg: '#60a5fa', icon: '💼' },
  FOMC:     { bg: 'rgba(139,92,246,0.18)',  fg: '#a78bfa', icon: '🏛️' },
  CPI:      { bg: 'rgba(251,191,36,0.16)',  fg: '#fbbf24', icon: '📊' },
  NFP:      { bg: 'rgba(59,130,246,0.16)',  fg: '#3b82f6', icon: '👷' },
  MACRO:    { bg: 'rgba(148,163,184,0.10)', fg: 'var(--text-muted)', icon: '📅' },
};

const SCOPE_LABEL = {
  position:  { label: 'Position',  bg: 'rgba(34,197,94,0.16)',  fg: '#4ade80' },
  watchlist: { label: 'Watchlist', bg: 'rgba(168,85,247,0.16)', fg: '#c084fc' },
  macro:     { label: 'Macro',     bg: 'rgba(148,163,184,0.10)', fg: 'var(--text-muted)' },
};

function daysLabel(d) {
  if (d === 0)  return "Aujourd'hui";
  if (d === 1)  return 'Demain';
  if (d > 0)    return `J+${d}`;
  return `J${d}`;
}

function dateLabel(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('fr-FR', {
      weekday: 'short', day: '2-digit', month: 'short',
    });
  } catch { return iso; }
}

function ScopeBadges({ scope }) {
  if (!Array.isArray(scope) || scope.length === 0) return null;
  return (
    <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
      {scope.map(s => {
        const meta = SCOPE_LABEL[s] || {
          label: s, bg: 'rgba(148,163,184,0.10)', fg: 'var(--text-muted)',
        };
        return (
          <span key={s} style={{
            fontSize: '0.6rem', fontWeight: 700, padding: '0.08rem 0.4rem',
            borderRadius: 4, background: meta.bg, color: meta.fg,
            textTransform: 'uppercase', letterSpacing: '0.04em',
          }}>{meta.label}</span>
        );
      })}
    </span>
  );
}

function Kpi({ label, value, sub, tone }) {
  return (
    <div className="status-chip" style={{ minWidth: 140 }}>
      <span className="sc-lbl">{label}</span>
      <span className="sc-val" style={{ color: tone || 'var(--text-main)' }}>{value}</span>
      {sub && <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>{sub}</span>}
    </div>
  );
}

export default function CalendarPage() {
  const [horizon, setHorizon] = useState(30);
  const [openTicker, setOpenTicker] = useState(null);

  const calQ = useCatalystCalendar(horizon);

  // Group by date.
  const days = useMemo(() => {
    const events = calQ.data?.events || [];
    const map = new Map();
    for (const e of events) {
      if (!map.has(e.date)) {
        map.set(e.date, { date: e.date, days_delta: e.days_delta, items: [] });
      }
      map.get(e.date).items.push(e);
    }
    return [...map.values()].sort((a, b) => a.date.localeCompare(b.date));
  }, [calQ.data]);

  if (calQ.isLoading) {
    return <PageSkeleton tiles={4} blockHeight={220} rows={4} />;
  }

  if (calQ.isError) {
    return (
      <ApiErrorBanner
        msg={calQ.error?.message || 'Erreur calendrier'}
        onRetry={() => calQ.refetch()}
      />
    );
  }

  const data = calQ.data || {};
  const scope = data.scope || {};

  return (
    <div className="control-panel animate-fade-in">
      {/* ── KPIs ── */}
      <div className="cp-status-bar">
        <Kpi
          label="Événements"
          value={data.events?.length || 0}
          sub={`Sur ${horizon}j`}
        />
        <Kpi
          label="Earnings"
          value={scope.n_earnings || 0}
          sub={`${scope.n_open || 0} positions, ${scope.n_watchlist || 0} watch`}
          tone="var(--accent-primary)"
        />
        <Kpi
          label="Macro events"
          value={scope.n_macro || 0}
          sub="FOMC · CPI · NFP"
          tone="var(--accent-secondary)"
        />
        <Kpi
          label="Aujourd'hui"
          value={data.today || '—'}
          sub={data.today ? new Date(data.today).toLocaleDateString('fr-FR', { weekday: 'long' }) : ''}
        />
      </div>

      {/* ── Controls ── */}
      <div className="card cp-section-card">
        <div style={{ display: 'flex', justifyContent: 'space-between',
                      alignItems: 'center', marginBottom: '0.85rem',
                      flexWrap: 'wrap', gap: 8 }}>
          <div>
            <div className="card-title" style={{ marginBottom: 0 }}>
              📅 Catalyst Calendar
            </div>
            <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)',
                          marginTop: '0.25rem' }}>
              Earnings (positions + watchlist) + événements macro consolidés
            </div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            {HORIZONS.map(h => (
              <button
                key={h}
                className={`scan-filter-btn ${horizon === h ? 'active' : ''}`}
                onClick={() => setHorizon(h)}
              >
                {h}j
              </button>
            ))}
            <button className="scan-filter-btn"
                    onClick={() => calQ.refetch()}
                    disabled={calQ.isFetching}>
              {calQ.isFetching ? '⏳' : '🔄'}
            </button>
          </div>
        </div>

        {days.length === 0 ? (
          <EmptyState
            icon="🗓"
            title="Aucun catalyseur"
            desc={`Pas d'earnings ni d'événement macro sur les ${horizon} prochains jours dans ton univers (positions ouvertes + watchlist).`}
          />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {days.map(day => (
              <div key={day.date} style={{
                display: 'flex', gap: 14,
                background: 'var(--bg-tertiary)', borderRadius: 10,
                border: '1px solid var(--border)',
                padding: '0.85rem 1rem',
              }}>
                {/* Date column */}
                <div style={{
                  minWidth: 100, paddingRight: 12,
                  borderRight: '1px solid var(--border)',
                  display: 'flex', flexDirection: 'column', gap: 2,
                }}>
                  <div style={{
                    fontSize: '0.78rem', fontWeight: 700,
                    color: day.days_delta === 0 ? 'var(--accent-primary)'
                         : day.days_delta <= 1 ? 'var(--warning)'
                         : 'var(--text-main)',
                  }}>
                    {dateLabel(day.date)}
                  </div>
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)',
                                fontFamily: 'monospace' }}>
                    {day.date}
                  </div>
                  <div style={{
                    fontSize: '0.65rem', fontWeight: 600,
                    padding: '2px 6px', borderRadius: 4,
                    background: day.days_delta === 0 ? 'rgba(59,130,246,0.15)'
                              : day.days_delta <= 1 ? 'rgba(251,191,36,0.15)'
                              : 'rgba(148,163,184,0.10)',
                    color: day.days_delta === 0 ? 'var(--accent-primary)'
                         : day.days_delta <= 1 ? 'var(--warning)'
                         : 'var(--text-muted)',
                    alignSelf: 'flex-start', marginTop: 2,
                  }}>
                    {daysLabel(day.days_delta)}
                  </div>
                </div>

                {/* Events column */}
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {day.items.map((e, i) => {
                    const meta = TYPE_TONE[e.type] || TYPE_TONE.MACRO;
                    return (
                      <div key={`${e.date}-${e.type}-${e.ticker || i}`}
                           style={{
                             display: 'flex', alignItems: 'center',
                             gap: 10, flexWrap: 'wrap',
                           }}>
                        <span style={{
                          fontSize: '0.72rem', fontWeight: 700,
                          padding: '0.18rem 0.55rem', borderRadius: 5,
                          background: meta.bg, color: meta.fg,
                          letterSpacing: '0.04em',
                        }}>
                          {meta.icon} {e.type}
                        </span>
                        {e.ticker ? (
                          <button
                            type="button"
                            onClick={() => setOpenTicker(e.ticker)}
                            style={{
                              background: 'none', border: 'none', padding: 0,
                              color: 'var(--accent-primary)', cursor: 'pointer',
                              fontWeight: 700, fontSize: '0.88rem',
                              fontFamily: 'monospace',
                              textDecoration: 'underline dotted',
                              textUnderlineOffset: 3,
                            }}
                          >
                            {e.ticker}
                          </button>
                        ) : (
                          <span style={{ fontWeight: 600 }}>{e.label}</span>
                        )}
                        {e.ticker && (
                          <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                            {e.label}
                          </span>
                        )}
                        {e.sector && (
                          <span style={{
                            fontSize: '0.66rem', color: 'var(--text-muted)',
                            border: '1px solid var(--border)', borderRadius: 4,
                            padding: '0.05rem 0.4rem',
                          }}>
                            {e.sector}
                          </span>
                        )}
                        <ScopeBadges scope={e.scope} />
                        {e.blackout && (
                          <span style={{
                            fontSize: '0.62rem', fontWeight: 700,
                            padding: '0.08rem 0.4rem', borderRadius: 4,
                            background: 'rgba(239,68,68,0.18)',
                            color: 'var(--danger)',
                            border: '1px solid rgba(239,68,68,0.4)',
                            letterSpacing: '0.04em',
                          }}>🚫 BLACKOUT</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {openTicker && (
        <TickerAnalysisModal
          ticker={openTicker}
          onClose={() => setOpenTicker(null)}
        />
      )}
    </div>
  );
}
